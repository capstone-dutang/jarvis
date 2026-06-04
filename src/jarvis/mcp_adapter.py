"""MCP tool definitions for JARVIS.

11 tools, grouped by use:
  Ingest          jarvis_log_diary (primary), jarvis_store_memory (legacy)
  Workspace       jarvis_manage_workspace, jarvis_initialize_memory
  Recall          jarvis_recall_memory, jarvis_explore_topic
  Drill down      jarvis_search_passages, jarvis_search_episodes,
                  jarvis_get_episode_excerpt, jarvis_follow_relation
  UI              jarvis_open_ui

Diary vision (research/2026-05-12-llm-diary-vision.md, ACTIVE_ROADMAP.md):
the AI client writes the user's diary — turns, summary, keywords, subjects,
optional entity/fact/relation index — in one `jarvis_log_diary` call.

References:
- research/2026-03-31-mcp-server-implementation-research.md
  · Tool descriptions: L204-215 ("Use this when..." pattern)
  · Error handling: L237-245 (prescriptive ToolError messages)
- definitive doc §9 — response limit ~25,000 tokens.
"""

import uuid

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select

from jarvis.core.episode_excerpt import get_episode_excerpt as _get_episode_excerpt
from jarvis.core.follow_relation import follow_relation as _follow_relation
from jarvis.core.passage_search import search_passages as _search_passages
from jarvis.core.recall import recall_memory as _recall
from jarvis.core.store import store_memory as _store
from jarvis.core.topic_map import build_topic_map as _build_topic_map
from jarvis.core.workspace_summary import list_workspaces_rich as _list_workspaces_rich
from jarvis.db import async_session_factory
from jarvis.models.tables import Entity, KnowledgeFact, Workspace
from jarvis.schemas import (
    DailySummaryInput,
    EntityHint,
    FactHint,
    IngestAndIndexRequest,
    RecallMemoryRequest,
    RelationHint,
    StoreMemoryRequest,
    TurnInput,
)

# Max response size: ~25,000 tokens ≈ 100,000 chars (definitive doc Section 9)
MAX_RESPONSE_CHARS = 100_000

# TODO: Re-enable OAuth after MCP logic verification
# auth_server_provider=JarvisOAuthProvider(),
# auth=AuthSettings(issuer_url=..., required_scopes=["mcp:tools"]),
mcp = FastMCP(
    "JARVIS Memory",
    stateless_http=True,
    # Railway 등 프록시/커스텀 도메인 환경: MCP SDK(>=1.23)의 DNS rebinding
    # 방어가 localhost 외 Host 헤더를 421로 막으므로 비활성화한다.
    # (공개 데모 MCP — OAuth 미사용이라 추가 노출 위험 없음.)
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    instructions=(
        "JARVIS — the user's AI keeps a diary of their conversations in the cloud.\n"
        "\n"
        "Routine flow:\n"
        "1. At session start: jarvis_initialize_memory(workspace='name') — load context.\n"
        "2. When the user says 'save this to JARVIS' / '오늘 대화 올려' / similar:\n"
        "   → jarvis_log_diary(...) — one call that captures turns + summary + keywords + entities/facts/relations.\n"
        "   ★ Do NOT upload raw transcripts/jsonl. Reconstruct `turns` from your own\n"
        "     context: user utterances verbatim, your own turns condensed. Works the\n"
        "     same in Claude app (no local file access) — that is the point.\n"
        "3. When the user references past conversation ('그때 뭐였지', 'last time we discussed X'):\n"
        "   → jarvis_recall_memory(workspace, query) — returns the most relevant facts.\n"
        "   → If recall is too narrow, jarvis_explore_topic first; if you need narrative\n"
        "     context behind a fact, jarvis_search_passages or jarvis_get_episode_excerpt.\n"
        "\n"
        "Diary semantics: facts accumulate over time. Old beliefs are NOT replaced when\n"
        "new ones contradict them — both stay, because a change of mind is itself\n"
        "information. The user reads this back like a journal.\n"
        "\n"
        "jarvis_store_memory is legacy (entity/fact/relation only). Prefer jarvis_log_diary\n"
        "for any new ingest — it covers the full diary entry in one call.\n"
        "\n"
        "When the user arrives without a clear target ('오늘 뭐 해야 하지', 'brief me',\n"
        "'자비스에 뭐 있어'), prefer jarvis_brief_me over jarvis_initialize_memory —\n"
        "the latter loads protocol guidance, the former returns actual recommendations\n"
        "with ASCII briefing card + top thread + next-action priorities."
    ),
)


async def _resolve_workspace(workspace: str) -> uuid.UUID:
    """Resolve workspace name or UUID string to UUID.

    If it looks like a UUID, use it directly.
    Otherwise, look up by name (case-insensitive).
    """
    # Try UUID first
    try:
        return uuid.UUID(workspace)
    except ValueError:
        pass

    # Look up by name
    async with async_session_factory() as db:
        result = await db.execute(select(Workspace).where(Workspace.name.ilike(workspace)))
        ws = result.scalar_one_or_none()
        if ws:
            return ws.id

    raise ValueError(f"Workspace '{workspace}' not found. Use manage_workspace to create one.")


def _fmt_ws_active_block(ws: dict) -> list[str]:
    """Format one active workspace row as markdown lines.

    출력 형식 (활성):
      #### {name}
      {description}
      📚 {ep} 일기 · {turn:,} turn · 최근 {YYYY-MM-DD}
      주요 주제: {top · top · top}
    """
    lines: list[str] = [f"#### {ws['name']}"]
    if ws.get("description"):
        lines.append(ws["description"])
    last = ws.get("last_activity")
    if last is not None:
        try:
            last_str = last.date().isoformat()
        except AttributeError:
            last_str = str(last)[:10]
    else:
        last_str = "활동 없음"
    ep = int(ws.get("episode_count") or 0)
    tu = int(ws.get("turn_count") or 0)
    lines.append(f"📚 {ep} 일기 · {tu:,} turn · 최근 {last_str}")
    tops = ws.get("top_subjects") or []
    if tops:
        lines.append("주요 주제: " + " · ".join(tops))
    return lines


def _fmt_ws_hidden_line(ws: dict) -> str:
    """Format one hidden workspace as a compact bullet line."""
    desc = ws.get("description") or "(설명 없음)"
    return f"- {ws['name']} — {desc}"


async def _render_workspaces_rich() -> str:
    """Build the shared rich workspace listing used by initialize_memory(no ws)
    and manage_workspace(action='list').

    Sections:
      ## 워크스페이스 목록
      ### 활성 (N)   — full block per ws
      ### Hidden (M) — compact bullet per ws
      → 가장 활성적인 워크스페이스: <name>
      → initialize_memory(workspace='이름') 으로 컨텍스트 로드
    """
    async with async_session_factory() as db:
        rows = await _list_workspaces_rich(db, include_hidden=True)

    if not rows:
        return (
            "워크스페이스가 아직 없어요.\n\n"
            "_새로 만들기: manage_workspace(action='create', name='my-project')_"
        )

    active = [r for r in rows if r.get("status") == "active"]
    hidden = [r for r in rows if r.get("status") != "active"]

    out: list[str] = ["## 워크스페이스 목록", ""]

    if active:
        out.append(f"### 활성 ({len(active)})")
        out.append("")
        for ws in active:
            out.extend(_fmt_ws_active_block(ws))
            out.append("")

    if hidden:
        out.append(f"### Hidden ({len(hidden)})")
        out.append("")
        for ws in hidden:
            out.append(_fmt_ws_hidden_line(ws))
        out.append("")

    top_active = active[0]["name"] if active else (rows[0]["name"] if rows else None)
    if top_active:
        out.append(f"→ 가장 활성적인 워크스페이스: **{top_active}** (최근 활동 기준)")
    out.append(
        "→ `initialize_memory(workspace='이름')` 으로 컨텍스트 로드"
    )

    return "\n".join(out)


# ── Tool 1: Workspace Management ──


@mcp.tool(name="jarvis_manage_workspace")
async def manage_workspace(
    action: str,
    name: str = "",
    new_name: str = "",
) -> str:
    """Manage workspaces — create, list, switch, or rename.

    Triggers (자연어 매핑 — 다음 표현 받으면 이 도구):
    • '워크스페이스 새로 만들어 X' / 'create workspace X'
    • '현재 워크스페이스 뭐야' / 'what workspace am I in'
    • '워크스페이스 X로 바꿔' / 'switch to workspace X'
    • '워크스페이스 목록' / 'list workspaces'
    • '워크스페이스 이름 바꿔 X → Y' / 'rename workspace X to Y'

    Use this when: the user wants to create a new workspace, see their workspaces,
    switch to a different workspace, or rename one.

    Args:
        action: One of "list", "create", "switch", "rename"
        name: Workspace name (for create, switch, rename)
        new_name: New name (only for rename action)
    """
    if action == "list":
        return await _render_workspaces_rich()

    async with async_session_factory() as db:
        if action == "create":
            if not name:
                return "Failed: name is required. Example: manage_workspace(action='create', name='my-project')"
            # Check duplicate
            existing = await db.execute(select(Workspace).where(Workspace.name.ilike(name)))
            if existing.scalar_one_or_none():
                return f"Workspace '{name}' already exists. Use manage_workspace(action='switch', name='{name}') to switch to it."
            ws = Workspace(name=name)
            db.add(ws)
            await db.commit()
            return f"Created workspace '{name}'. All memory tools will now use this workspace."

        elif action == "switch":
            if not name:
                return "Failed: name is required. Use manage_workspace(action='list') to see available workspaces."
            try:
                ws_id = await _resolve_workspace(name)
                result = await db.execute(select(Workspace).where(Workspace.id == ws_id))
                found_ws = result.scalar_one_or_none()
                if found_ws:
                    return f"Switched to workspace '{found_ws.name}'. Use initialize_memory(workspace='{found_ws.name}') to load context."
                return f"Workspace '{name}' not found."
            except ValueError as e:
                return str(e)

        elif action == "rename":
            if not name or not new_name:
                return "Failed: both name and new_name are required."
            try:
                ws_id = await _resolve_workspace(name)
                result = await db.execute(select(Workspace).where(Workspace.id == ws_id))
                found_ws = result.scalar_one_or_none()
                if found_ws:
                    found_ws.name = new_name
                    await db.commit()
                    return f"Renamed workspace '{name}' → '{new_name}'."
                return f"Workspace '{name}' not found."
            except ValueError as e:
                return str(e)

        else:
            return f"Unknown action '{action}'. Use one of: list, create, switch, rename."


# ── Tool 2: Initialize Memory ──


@mcp.tool(name="jarvis_initialize_memory")
async def initialize_memory(workspace: str = "") -> str:
    """Initialize memory session. Call this at the start of every conversation.

    Triggers (자연어 매핑 — 다음 표현 받으면 이 도구):
    • '자비스 켜줘' / 'start JARVIS' / 'wake up JARVIS'
    • '자비스 시작' / 'initialize JARVIS'
    • '안녕' / 'hi' (세션 첫 인사 시)
    • 세션 시작 자동 호출 (workspace 명시 없을 때 워크스페이스 목록 반환)

    Returns a proactive workspace briefing the AI can immediately report from:
      - Ops infra (UI URL, MCP endpoint, health check, docker-compose path)
      - Recent facts (top 10 active)
      - Active subjects (top 10 by turn_count)
      - Recent daily summaries (last 7 days)
      - Yesterday's work (episodes + per-subject daily summaries)
      - Today's stats (episode / turn counts)
      - Pending alert (local conversations not yet ingested, if any)
      - Diary protocol + natural-language command shortcuts

    If no workspace is specified, shows available workspaces.

    Args:
        workspace: Workspace name (or UUID). Leave empty to list workspaces.
    """
    if not workspace:
        # Rich workspace listing — same content as manage_workspace(action='list').
        return await _render_workspaces_rich()

    try:
        ws_id = await _resolve_workspace(workspace)
    except ValueError as e:
        return str(e)

    recent_summary = ""
    workspace_name = workspace
    try:
        async with async_session_factory() as db:
            # Get workspace name
            ws_result = await db.execute(select(Workspace).where(Workspace.id == ws_id))
            ws = ws_result.scalar_one_or_none()
            if ws:
                workspace_name = ws.name

            from sqlalchemy import text as _sql_text

            sections: list[str] = []

            # ── P1: Active ops infra (one-line snapshot from JARVIS facts) ──
            # Predicates pulled from b62e97d4 (JARVIS subject) — surfaced so a new
            # session AI can answer "어디 떠 있어 / 어떻게 띄워" without recall.
            infra_keys = (
                "ui_url",
                "mcp_endpoint",
                "how_to_check_health",
                "docker_compose_path",
                "how_to_start_ui",
            )
            try:
                infra_rows = await db.execute(
                    _sql_text("""
                        SELECT kf.predicate, kf.object_value
                        FROM knowledge_facts kf
                        JOIN entities e ON e.id = kf.entity_id
                        WHERE kf.workspace_id = :ws
                          AND kf.superseded_at IS NULL
                          AND e.name = 'JARVIS'
                          AND kf.predicate = ANY(:preds)
                        ORDER BY kf.recorded_at DESC
                    """),
                    {"ws": str(ws_id), "preds": list(infra_keys)},
                )
                infra_map: dict[str, str] = {}
                for r in infra_rows.fetchall():
                    # Keep the most-recent value per predicate (ORDER BY desc above).
                    if r[0] not in infra_map:
                        infra_map[r[0]] = r[1]
                if infra_map:
                    ordered = [
                        f"- {k}: {infra_map[k]}" for k in infra_keys if k in infra_map
                    ]
                    sections.append("Ops infra (live):\n" + "\n".join(ordered))
            except Exception:
                pass

            # Recent facts (top 10 active)
            fact_result = await db.execute(
                select(KnowledgeFact, Entity.name)
                .join(Entity, KnowledgeFact.entity_id == Entity.id)
                .where(
                    KnowledgeFact.workspace_id == ws_id,
                    KnowledgeFact.superseded_at.is_(None),
                )
                .order_by(KnowledgeFact.recorded_at.desc())
                .limit(10)
            )
            fact_rows = fact_result.all()
            if fact_rows:
                fact_lines = [
                    f"- {name} {fact.predicate} {fact.object_value}"
                    for fact, name in fact_rows
                ]
                sections.append("Recent facts:\n" + "\n".join(fact_lines))

            # ── P1: Yesterday's work — episodes + per-subject daily summaries ──
            try:
                yest_ep_rows = await db.execute(
                    _sql_text("""
                        SELECT id, summary
                        FROM episodes
                        WHERE workspace_id = :ws
                          AND (metadata->>'deleted' IS DISTINCT FROM 'true')
                          AND created_at::date = (CURRENT_DATE - INTERVAL '1 day')
                        ORDER BY created_at DESC
                        LIMIT 3
                    """),
                    {"ws": str(ws_id)},
                )
                yest_eps = yest_ep_rows.fetchall()
                yest_dss_rows = await db.execute(
                    _sql_text("""
                        SELECT e.name, dss.summary
                        FROM daily_subject_summaries dss
                        JOIN entities e ON e.id = dss.subject_id
                        WHERE dss.workspace_id = :ws
                          AND dss.date = (CURRENT_DATE - INTERVAL '1 day')
                        ORDER BY dss.turn_count DESC
                        LIMIT 5
                    """),
                    {"ws": str(ws_id)},
                )
                yest_dss = yest_dss_rows.fetchall()
                if yest_eps or yest_dss:
                    parts: list[str] = []
                    if yest_eps:
                        ep_lines = [
                            f"- episode {r[0]}: {(r[1] or '')[:200]}"
                            for r in yest_eps
                        ]
                        parts.append("episodes (top 3):\n" + "\n".join(ep_lines))
                    if yest_dss:
                        dss_lines = [
                            f"- {r[0]}: {(r[1] or '')[:150]}" for r in yest_dss
                        ]
                        parts.append("per-subject (top 5):\n" + "\n".join(dss_lines))
                    sections.append("Yesterday's work:\n" + "\n\n".join(parts))
            except Exception:
                pass

            # ── P1: Today's stats — episode / turn counts ──
            try:
                today_stats = await db.execute(
                    _sql_text("""
                        SELECT
                          (SELECT COUNT(*) FROM episodes
                            WHERE workspace_id = :ws
                              AND (metadata->>'deleted' IS DISTINCT FROM 'true')
                              AND created_at::date = CURRENT_DATE) AS ep_cnt,
                          (SELECT COUNT(*) FROM turns
                            WHERE workspace_id = :ws
                              AND created_at::date = CURRENT_DATE) AS turn_cnt
                    """),
                    {"ws": str(ws_id)},
                )
                row = today_stats.fetchone()
                if row and (row[0] or row[1]):
                    sections.append(
                        f"Today's stats: episodes={row[0]}, turns={row[1]}"
                    )
                else:
                    sections.append(
                        "Today's stats: episodes=0, turns=0 — 본대화 아직 안 올림."
                    )
            except Exception:
                pass

            # ── P1: Local-only / not-ingested ledger alert ──
            try:
                pending = await db.execute(
                    _sql_text("""
                        SELECT COUNT(*)
                        FROM ingest_ledger
                        WHERE workspace_id = :ws
                          AND status = 'local_only_not_ingested'
                    """),
                    {"ws": str(ws_id)},
                )
                pending_n = pending.scalar() or 0
                if pending_n > 0:
                    sections.append(
                        f"⚠ Pending: {pending_n} local conversation(s) not yet "
                        f"ingested (ingest_ledger status='local_only_not_ingested'). "
                        f"Tell the user — they may want '자비스에 올려줘'."
                    )
            except Exception:
                pass

            # Active subjects (top-level, top 10 by turn_count) — phase 4 C3 해소.
            subj_rows = await db.execute(
                _sql_text("""
                    SELECT e.id, e.name, COALESCE(tc.cnt, 0) AS turn_count
                    FROM entities e
                    LEFT JOIN (
                        SELECT subject_id, COUNT(*) AS cnt
                        FROM turn_subjects
                        WHERE workspace_id = :ws
                        GROUP BY subject_id
                    ) tc ON tc.subject_id = e.id
                    WHERE e.workspace_id = :ws
                      AND e.parent_id IS NULL
                      AND COALESCE(tc.cnt, 0) > 0
                    ORDER BY turn_count DESC
                    LIMIT 10
                """),
                {"ws": str(ws_id)},
            )
            subjects_rs = subj_rows.fetchall()
            if subjects_rs:
                subj_lines = [
                    f"- {r[1]} ({r[2]} turns, subject_id={r[0]})"
                    for r in subjects_rs
                ]
                sections.append("Active subjects:\n" + "\n".join(subj_lines))

            # Recent daily summaries (last 7 days, top 15 by date desc) — phase 4 C3.
            dss_rows = await db.execute(
                _sql_text("""
                    SELECT dss.date, e.name, dss.summary, dss.turn_count
                    FROM daily_subject_summaries dss
                    JOIN entities e ON e.id = dss.subject_id
                    WHERE dss.workspace_id = :ws
                      AND dss.date >= (CURRENT_DATE - INTERVAL '7 days')
                    ORDER BY dss.date DESC, dss.turn_count DESC
                    LIMIT 15
                """),
                {"ws": str(ws_id)},
            )
            dss = dss_rows.fetchall()
            if dss:
                dss_lines = [
                    f"- {r[0].isoformat() if hasattr(r[0], 'isoformat') else r[0]} · "
                    f"{r[1]} ({r[3]} turns): {r[2][:80]}"
                    for r in dss
                ]
                sections.append("Recent daily summaries (7d):\n" + "\n".join(dss_lines))

            if sections:
                # P5: 섹션 사이에 ─── 구분선 — 가독성 + AI 가 섹션 경계 인식 쉽게.
                recent_summary = "\n\n───\n\n".join(sections)
    except Exception:
        pass

    protocol = (
        f"Active workspace: {workspace_name}\n\n"
        "Diary protocol (you are the wiki editor; the server is LLM-free):\n"
        f"- Pass workspace='{workspace_name}' in all jarvis_* calls.\n"
        "\n"
        "When the user asks you to save the conversation to JARVIS:\n"
        "  Step 1 — re-read the wiki before writing today's page:\n"
        "    For each major topic in the conversation, call jarvis_recall_memory(query)\n"
        "    to see what facts the wiki already holds. Note which existing facts are\n"
        "    REINFORCED, REFINED, or made OBSOLETE by today's conversation.\n"
        "    Also: confirm the current `cumulative_summary` per subject and per ws\n"
        "    (returned by recall + brief). You will EXTEND these, not replace them.\n"
        "  Step 2 — call jarvis_log_diary(...) ONCE with:\n"
        "    • turns: cleaned turns (drop thinking blocks, auto-system prompts,\n"
        "      trivial transition messages; preserve user input + your decisions/\n"
        "      explanations/reports)\n"
        "    • summary: length proportional to conversation (no fixed length)\n"
        "    • keywords: count proportional to entity density (no fixed range)\n"
        "    • entities/facts/relations: be a wiki editor.\n"
        "        - Reuse entity names you saw in recall; let the resolver dedupe.\n"
        "        - Same (subject, predicate) with new object → add as fresh entry.\n"
        "          Diary mode keeps both; the timeline IS the value.\n"
        "        - New connection between entities → put it in `relations` with\n"
        "          an explicit relation_type so it surfaces as a bidirectional\n"
        "          wikilink in the entity page UI.\n"
        "    • daily_summaries: when the user says '오늘 정리해' / '이 날 요약',\n"
        "      include [{subject_id, date, summary, turn_count}] so the right\n"
        "      sidebar '요약' panel can show them.\n"
        "    • subject_summaries (RECOMMENDED — 누적 요약 갱신):\n"
        "      [{subject_name, cumulative_summary, turn_count_today, date?}, ...].\n"
        "      각 subject 의 '지금까지 + 오늘' 흐름 한 단락 (300자 내외).\n"
        "      회상으로 본 기존 cumulative 에 오늘 작업을 합쳐 한 문단으로 갱신.\n"
        "      활발히 다룬 subject 만 (top 2~5) — 나머지는 기존 값 유지.\n"
        "    • workspace_summary (RECOMMENDED): ws 전체 누적 요약 한 단락 (150자).\n"
        "      subject_summaries 와 동일 흐름 — 회상 → 합성 → 갱신.\n"
        "    • raw_content: full untruncated transcript when you have it.\n"
        "\n"
        "  WHY (누적 요약): Brief 의 ws chip / 다른 세션의 recall 첫 컨텍스트가\n"
        "    이 cumulative 를 읽음. 매번 안 갱신하면 다른 세션 AI 가 '지난 작업'\n"
        "    을 못 봄. 사용자가 일일이 안 알려줘도 AI 끼리 진행 상태가 이어지는\n"
        "    핵심 — 일기 저장 = 누적 요약 갱신.\n"
        "\n"
        "When the user references past conversation:\n"
        "- jarvis_recall_memory(query) — hybrid recall over facts (entity-anchored).\n"
        "- jarvis_explore_topic if the topic is unfamiliar; jarvis_search_passages\n"
        "  or jarvis_get_episode_excerpt when you need the narrative behind a fact.\n"
        "\n"
        "── Natural-language command shortcuts (user → tool) ──\n"
        "• '자비스에 올려줘' / 'save this to JARVIS' → jarvis_log_diary (wiki editor flow)\n"
        "• '회상해줘' / '그때 뭐였지' / 'last time' → jarvis_recall_memory(query)\n"
        "• 'UI 띄워줘' / 'open JARVIS' → jarvis_open_ui(workspace='...')\n"
        "  (returns URL + OS launch command; AI client should ALSO call Bash to actually open the browser)\n"
        "• '오늘 정리해' / '이번 주 정리' → jarvis_log_diary with daily_summaries=[...]\n"
        "• '장부 보여줘' / 'show the ingest ledger' → GET /api/v1/ingest-ledger (or UI ledger tab)\n"
        "• '어제 뭐 했지' → jarvis_recall_memory(query='어제 작업') or read the\n"
        "  \"Yesterday's work\" section above (already loaded by this call).\n"
        "• '오늘 뭐 해야 하지' / '최근에 뭐 했지' / '자비스에 뭐 있어' /\n"
        "  '브리핑' / 'brief me' / 'status' / '어디까지 했지'\n"
        "  → jarvis_brief_me()  — cross-ws 활성 분포 + 최근 작업 Top 3 + 다음 추천 3개.\n"
        "  특정 ws 깊게: jarvis_brief_me(workspace_name='ai-argos', detail='deep').\n"
        "  사용자가 어디서부터 시작할지 모를 때 초기 진입점으로 권장."
    )

    if recent_summary:
        return f"{recent_summary}\n\n───\n\n{protocol}"
    return protocol


# ── Tool 3: Store Memory ──


@mcp.tool(name="jarvis_store_memory")
async def store_memory(
    workspace: str,
    provider: str,
    conversation_transcript: str,
    entities: list[dict[str, str]],
    facts: list[dict[str, str]],
    relations: list[dict[str, str]] | None = None,
    conversation_summary: str = "",
) -> str:
    """[LEGACY — backed by deprecated /api/v1/memory/store] Entity/fact/relation only.

    Triggers: (legacy — 일반 사용자 자연어 명령은 jarvis_log_diary 로 라우팅.
    이 도구는 이미 ingest 된 episode 에 추가 triple 만 박을 때 한정).

    Prefer jarvis_log_diary for any new ingest — it covers the full diary entry
    (turns + raw + summary + keywords + subjects + entities/facts/relations) in
    one call, matching the diary-model vision (research/2026-05-12-llm-diary-vision.md).

    Use this tool ONLY to add extra structured triples to an episode that was
    already ingested by jarvis_log_diary. Do not use it for first-time ingest
    of a conversation — that flow has no turns, no summary, no subject mapping.

    Predicate convention: write distinct facts under distinct predicates.
    "JARVIS bug_fts_missing", "JARVIS bug_hnsw_stale" — not all under "bug_fixed".
    Diary mode accumulates fact entries; there's no auto-collapse.

    Args:
        workspace: Workspace name (or UUID)
        provider: AI provider (openai, anthropic, google, manual)
        conversation_transcript: Raw conversation text (for source_quote grounding)
        entities: List of {name, entity_type, source_quote}
        facts: List of {subject, predicate, object, temporal, source_quote}
        relations: List of {from_entity, to_entity, relation_type, source_quote}
        conversation_summary: Brief summary of the conversation segment
    """
    if len(conversation_transcript) > 50000:
        return (
            "Failed to store memory: transcript too long. "
            f"Try again with content under 50000 characters. "
            f"Current length: {len(conversation_transcript)}"
        )

    try:
        ws_id = await _resolve_workspace(workspace)

        request = StoreMemoryRequest(
            workspace_id=ws_id,
            provider=provider,
            conversation_transcript=conversation_transcript,
            entities=[EntityHint(**e) for e in entities],
            facts=[FactHint(**f) for f in facts],
            relations=[RelationHint(**r) for r in (relations or [])],
            conversation_summary=conversation_summary,
        )

        async with async_session_factory() as db:
            result = await _store(db, request)

        return (
            f"Stored {len(result.facts_stored)} facts, "
            f"resolved {result.entities_resolved} entities, "
            f"created {result.entities_created} new entities. "
            f"Episode: {result.episode_id}"
        )
    except Exception as e:
        return (
            f"Failed to store memory: {e}. "
            "Check that workspace exists and all required fields are present. "
            "Required entity fields: name, entity_type, source_quote. "
            "Required fact fields: subject, predicate, object, source_quote."
        )


# ── Tool 3b: Log Diary (single-call ingest + index) ──


@mcp.tool(name="jarvis_log_diary")
async def log_diary(
    workspace: str,
    summary: str,
    keywords: list[str],
    diary_entry: str,
    human_summary: str,
    turns: list[dict],
    raw_content: str = "",
    title: str = "",
    provider: str = "claude-code",
    source_session_id: str = "",
    source_path: str = "",
    daily_summaries: list[dict] | None = None,
    entities: list[dict] | None = None,
    facts: list[dict] | None = None,
    relations: list[dict] | None = None,
    existing_subject_links: list[dict] | None = None,
    new_subjects: list[dict] | None = None,
    subject_summaries: list[dict] | None = None,
    workspace_summary: str | None = None,
) -> str:
    """Log one diary entry to JARVIS — and act as the **wiki editor** for the
    knowledge graph this entry touches.

    Triggers (자연어 매핑 — 다음 표현 받으면 이 도구):
    • '자비스에 올려줘' / '자비스에 올려' / 'save this to JARVIS'
    • '이거 자비스에 박아' / '이 대화 자비스에 기록해'
    • '오늘 대화 정리해서 자비스에 올려' / 'log today's chat to JARVIS'
    • '일기 써줘' / 'write diary' / '오늘 일지 남겨'
    • '방금 한 거 저장' / 'save what we just did'

    Use this when the user asks to save the current (or a specific) conversation
    to JARVIS — e.g. "자비스에 올려", "save this to JARVIS", "log today's chat".

    ── ROLE: wiki editor, not just diary writer ──
    JARVIS is a self-organizing knowledge wiki (Karpathy-style LLM wiki, Korean
    diary flavor). When you log a diary entry, you are NOT only writing today's
    page — you are also updating the entity pages that this conversation touched.
    The server stays LLM-free; you, the client AI, do the wiki editing in-context.

    Recommended workflow before calling this tool:
      1. jarvis_initialize_memory(workspace) — load existing subject tree + recent facts.
      2. For each major topic in the conversation:
           jarvis_recall_memory(workspace, "<topic>") to see what the wiki
           already says. Note which existing facts are reinforced, contradicted,
           refined, or made obsolete by today's conversation.
      3. (Optional) jarvis_explore_topic / jarvis_search_passages for narrative
           context behind a fact.
      4. Compose `facts` and `relations` so the diary entry advances the wiki:
           - Reinforcing fact → add a fresh entry under the same (subject, predicate).
           - Refined fact → add the more precise form; both stay (timeline).
           - Contradicted/obsolete fact → add the new value AND say so in
             `source_quote` so future readers see the evolution.
           - New connection between two entities → put it in `relations`
             with an explicit `relation_type` so it shows up as a bidirectional
             wikilink in the entity page UI.

    REQUIRED — the server rejects the call if any of these are missing
    (IngestAndIndexRequest.require_index_hints):
      - summary: non-empty (≥30 chars recommended). The diary entry is
        unfindable without it.
      - keywords: at least 3. Proportional to content density beyond that.
      - subject mapping: at least one of `existing_subject_links` or
        `new_subjects` (use the workspace subject tree).
      - diary_entry: 800~1200자 **제3자 객관 사건 일지** (반드시 작성). 핵심:
          "나는 ~했다 / 사용자가 ~했다" 식의 화자 중심 1인칭·서사가 아니라,
          **그 날 무슨 일이 일어났고 무엇이 결정·발견됐는지**를 사건 중심으로
          담백하게 기록한다. 행위자(나/사용자)를 주어로 반복하지 말고, 사건·결정·
          결과를 주어로 — "정규식 정제로는 한계가 있어 워크플로 방식으로 전환됐다",
          "검수 결과 61%가 도구 산출물이었고 2,326개가 숨겨졌다" 식.
          금지: 문학적·감상적 표현("하루의 분기점은 마지막이었다", "사용자는 멈추지
          않고", "~한 셈이다"). 사실 전달이 목적이지 글짓기가 아니다.
          시간/인과 순으로 결정·근거·결과를 적되, 사람이 읽고 "그 날 무슨 일이
          있었는지" 객관적으로 파악되게. summary는 색인용, diary_entry는 메인 UI
          노출용. 첫 줄 "[YYYY-MM-DD 한 줄 제목]", 둘째 줄부터 본문. 펜스 X.
      - human_summary: 사람용 2~3줄(100~200자) 짧은 요약 (반드시 작성). UI
          사이드바·위키 모달에 노출 — 사람이 한눈에 "이 날 뭐 했는지" 파악용.
          평이한 한국어, AI 보고체/약어 금지. diary_entry(1000자)를 한 호흡으로
          압축한 형태. summary·diary_entry와 별개의 셋째 텍스트.

    What to pass:
      - turns: 원본 트랜스크립트를 그대로 올리지 마라. raw jsonl/rollout 파일을
        읽지도 마라 — Claude 앱처럼 raw 접근이 없는 환경에서도 자비스는 동일하게
        동작해야 한다. 대신 **지금 네 컨텍스트(방금까지의 대화)를 기억으로
        재구성**해서 turns를 만든다:
          · role=user(사용자 발화): **verbatim(그대로)** 보존. 결정·지시·질문·
            감정·욕설을 드러낸 발화는 한 마디도 바꾸거나 빠뜨리지 마라. 짧은
            발화("ㄱㄱ", "이거 난해함")도 원문 그대로.
          · role=assistant(AI 발화): **핵심을 충실히** 담아라. 무엇을 왜 했는지,
            어떤 결정·진단·근거·설명이었는지 다음 세션이 읽고 이해할 만큼. 압축
            대상은 장황한 도구 로그/grep 출력/반복뿐이고, 판단·설명·결론은 살린다.
            **과도한 축약 금지** — 사용자 발화에 비해 AI 발화가 한 줄로 쪼그라들면
            맥락이 사라진다. 사용자 verbatim과 균형이 맞게 2~5문장 수준으로.
          · 도구 호출/결과(tool_use, tool_result)를 별도 turn으로 넣지 마라.
            필요하면 직전 assistant 발화 안에 한 줄로 녹여라.
        타이밍이 중요하다 — 대화 직후 컨텍스트가 생생할 때 올려야 재구성이
        정확하다. 시간이 지나 컨텍스트가 압축된 뒤엔 정확도가 떨어진다.
        Each turn: {sequence:int, role:"user"|"assistant", text:str,
        timestamp:ISO-8601 string}
      - summary: free-text overview. Length proportional to the content —
        a few sentences for short chats, a paragraph or two for longer ones,
        chaptered for very long sessions.
      - keywords: keywords/entity names. Count proportional to entity density,
        minimum 3.
      - raw_content: 비워 둬라(기본값 ""). 원본 트랜스크립트 통째 적재는
        폐기됐다 — 토큰을 크게 낭비하고(파일 읽기 + 전송 이중 비용) raw 접근이
        없는 환경에서 불가능하다. 자비스는 raw가 아니라 네가 재구성한 turns로
        동작한다.
      - entities/facts/relations (optional but RECOMMENDED for wiki growth):
          - entities: [{name, entity_type, source_quote}] — entities the
            conversation introduces or revisits. The server resolves to existing
            entities by name (incl. aliases), so you don't have to dedup.
          - facts: [{subject, predicate, object, confidence, source_quote}].
            Diary mode accumulates entries — same (subject, predicate) with a
            different object stays as a timeline entry. Reuse stable predicate
            names so timelines actually form.
          - relations: [{from_entity, to_entity, relation_type, source_quote}].
            Each relation becomes a bidirectional wikilink in the entity page UI.
            Use this to encode connections you noticed across topics.
      - daily_summaries (optional): [{subject_id, date 'YYYY-MM-DD',
        summary, turn_count}] — typically supplied when reflecting on a day.
        These power the right-sidebar "요약" panel in the web UI.
      - existing_subject_links: [{subject_id: UUID,
        turn_sequences: [int, ...]}, ...] — link these turns to an existing
        subject. turn_sequences are the turn.sequence numbers from this call.
        Call jarvis_initialize_memory or the subjects API first to discover
        existing subjects.
      - new_subjects: [{name: str, parent_id: UUID | None,
        turn_sequences: [int, ...]}, ...] — create a subject and link these
        turns to it in one shot. Use this when no existing subject fits.
      - subject_summaries (RECOMMENDED — 누적 요약):
        [{subject_name: str, cumulative_summary: str,
          turn_count_today: int, date?: 'YYYY-MM-DD'}, ...]
        "이 subject 가 지금까지 어디까지 왔고 오늘 뭐가 더해졌나" — 한 단락 300자
        내외. 단순 "오늘 한 일" 아님. **기존 cumulative + 오늘** 누적.

        호출 흐름 (의무):
          1. 이 일기 작성 전 jarvis_recall_memory(workspace, "<subject>") 또는
             jarvis_brief_me(workspace_name=...) 호출해 기존 cumulative_summary 와
             최근 일기 회상.
          2. 회상 결과 + 오늘 작업을 합쳐 각 subject 별 "지금까지 + 오늘" 누적
             문단 작성 (300자 내외).
          3. 기존 cumulative 가 없으면 신규 작성.
          4. 호출자가 turn 으로 활발하게 다룬 subject 만 (top 2~5개) 보내면 됨.
             나머지는 기존 값 유지.

        BAD: "오늘 백테스트 1년 분석 결정함."
        GOOD: "Argos 는 OOS 누수 3회 발견 후 클린 하니스 구축 (2026-04~05).
               5월 27 known-place gate 연구로 방향엣지無 재확인. 오늘 백테스트 1년
               거래내역 직접 분석 결정."
      - workspace_summary (RECOMMENDED — ws 전체 누적 요약):
        ws 전반의 "지금까지 흐름" 한 단락 (150자, 1~2문장). subject_summaries 와
        동일 흐름으로 회상 → 합성 → 갱신. None 이면 기존 값 유지, 빈 문자열이면
        의도적으로 비움.

      WHY (subject_summaries + workspace_summary):
        Brief 의 ws chip "sub line" / 다른 세션의 recall 첫 컨텍스트가 이
        cumulative 를 읽음. 이걸 안 갱신하면 다른 세션 AI 가 "지난 작업" 을
        못 봄. 사용자가 일일이 안 알려줘도 AI 끼리 진행 상태가 이어지는 핵심.

    What NOT to pass:
      - Heuristic-truncated content. Keep meaning; remove only noise.
      - "Summary" that is just the first turn. Write a real summary.
      - Empty subject mapping. The server will reject the call.
      - Random new entity names when an existing entity already fits. Let the
        resolver dedupe; reuse names you saw in recall_memory.

    Returns: episode_id + counts (turns / facts / entities / relations /
    created_subjects / linked_turns / daily_summaries_upserted).

    Args:
        workspace: Workspace name (or UUID)
        summary: Diary entry summary; length proportional to body
        keywords: Keywords; count proportional to entity density
        turns: Cleaned turn list (see above)
        raw_content: Full raw transcript (recommended)
        title: Optional short title
        provider: AI client identifier (default "claude-code")
        source_session_id: External session id for traceability
        source_path: Source file path for traceability
        daily_summaries: Optional [{subject_id, date, summary, turn_count}]
        entities: Optional [{name, entity_type, source_quote}]
        facts: Optional [{subject, predicate, object, temporal, source_quote}]
        relations: Optional [{from_entity, to_entity, relation_type, source_quote}]
        existing_subject_links: Optional [{subject_id, turn_sequences}]
        new_subjects: Optional [{name, parent_id?, turn_sequences}]
        subject_summaries: Optional [{subject_name, cumulative_summary,
            turn_count_today, date?}] — per-subject 누적 요약 갱신 (기존+오늘).
            None 이면 갱신 안 함; 들어온 subject 만 upsert, 나머지는 기존 유지.
        workspace_summary: Optional str — ws 전체 누적 요약 한 단락 (150자).
            "" (빈 문자열) 이면 의도적으로 비움; 인자 자체가 안 오면 기존 유지.

    Example (누적 요약 포함 — 권장 호출 형태):
        jarvis_log_diary(
            workspace="ai-argos",
            summary="오늘 백테스트 1년 거래내역 직접 분석하기로 결정 ...",
            keywords=["argos", "백테스트", "거래내역", "OOS", "MFE"],
            turns=[...],
            existing_subject_links=[
                {"subject_id": "1594...", "turn_sequences": [0,1,2,3]},
            ],
            subject_summaries=[
                {
                    "subject_name": "Argos",
                    "cumulative_summary": (
                        "Argos 는 OOS 누수 3회 발견 후 클린 하니스 구축 "
                        "(2026-04~05). 5월 27 known-place gate 연구로 방향엣지無 "
                        "재확인. 오늘 백테스트 1년 거래내역 직접 분석 결정."
                    ),
                    "turn_count_today": 4,
                },
            ],
            workspace_summary=(
                "Argos BTC 자동매매 연구. OOS·맥락 피처·MFE·MAE 축으로 "
                "확정엣지 검증 중. 현재 1년 백테스트 분석 단계."
            ),
        )
    """
    try:
        ws_id = await _resolve_workspace(workspace)

        # 누적 요약 새 필드 — backend (schemas.IngestAndIndexRequest) 가
        # 받아 server-side 에서 processed. mcp_adapter 는 그대로 전달만.
        # workspace_summary: None ⇒ 인자 자체를 안 넘겨 schema default (None,
        # "기존 유지") 가 동작. 빈 문자열은 의도적으로 비우는 시그널이라
        # 그대로 전달해야 함.
        extra_kwargs: dict = {}
        if subject_summaries is not None:
            extra_kwargs["subject_summaries"] = subject_summaries
        if workspace_summary is not None:
            extra_kwargs["workspace_summary"] = workspace_summary

        request = IngestAndIndexRequest(
            workspace_id=ws_id,
            provider=provider,
            source_session_id=source_session_id,
            source_path=source_path,
            title=title,
            summary=summary,
            keywords=keywords,
            diary_entry=diary_entry,
            human_summary=human_summary,
            turns=[TurnInput(**t) for t in turns],
            raw_content=raw_content or None,
            existing_links=existing_subject_links or [],
            new_subjects=new_subjects or [],
            daily_summaries=[DailySummaryInput(**d) for d in (daily_summaries or [])],
            entities=[EntityHint(**e) for e in (entities or [])],
            facts=[FactHint(**f) for f in (facts or [])],
            relations=[RelationHint(**r) for r in (relations or [])],
            **extra_kwargs,
        )

        # Call the unified endpoint logic directly via the same code path
        from jarvis.core.reflect import save_summaries as _save_summaries
        from jarvis.core.subjects import classify_turns as _classify_turns
        from jarvis.core.turn_ingest import (
            ingest_transcript as _ingest,
        )
        from jarvis.core.turn_ingest import (
            resolve_turn_sequences as _resolve_seqs,
        )

        async with async_session_factory() as db:
            turn_dicts = [
                {"sequence": t.sequence, "role": t.role, "text": t.text, "timestamp": t.timestamp}
                for t in request.turns
            ]
            metadata: dict = {}
            if request.source_session_id:
                metadata["external_session_id"] = request.source_session_id
            if request.source_path:
                metadata["source_path"] = request.source_path
            if request.summary:
                metadata["summary"] = request.summary
            if request.keywords:
                metadata["keywords"] = request.keywords

            episode, turn_count, is_dup, seq_to_id = await _ingest(
                db, ws_id, turn_dicts,
                provider=request.provider,
                title=request.title or request.summary[:200],
                summary=request.summary,
                diary_entry=request.diary_entry,
                human_summary=request.human_summary,
                metadata=metadata,
                raw_content=request.raw_content,
            )

            # turn-level subject classification — phase 4 C2 해소.
            created_subjects = 0
            linked_turns = 0
            if request.existing_links or request.new_subjects:
                el = [_resolve_seqs(s, seq_to_id) for s in request.existing_links]
                ns = [_resolve_seqs(s, seq_to_id) for s in request.new_subjects]
                info = await _classify_turns(
                    db, ws_id,
                    existing_links=el,
                    new_subjects=ns,
                )
                created_subjects = info.get("created_subjects", 0)
                linked_turns = info.get("linked_turns", 0)

            summaries_upserted = 0
            if request.daily_summaries:
                items = [
                    {
                        "subject_id": s.subject_id,
                        "date": s.date,
                        "summary": s.summary,
                        "turn_count": s.turn_count,
                        "unique_turn_count": s.unique_turn_count,
                    }
                    for s in request.daily_summaries
                ]
                summaries_upserted = await _save_summaries(db, ws_id, items)

            # --- Stage 3b: subject_summaries cumulative upsert (optional) ---
            # SubjectSummary speaks subject_name (AI-facing); resolve → subject_id
            # by looking up entities in this workspace. Misses are skipped + logged
            # so one typo doesn't fail the whole diary call. Mirrors memory.py
            # L649~692 (the only authoritative Stage 3b path).
            subject_summaries_upserted = 0
            if request.subject_summaries:
                import logging as _logging
                from datetime import date as _date_cls

                _logger = _logging.getLogger(__name__)

                name_rows = await db.execute(
                    select(Entity.id, Entity.name, Entity.name_normalized)
                    .where(Entity.workspace_id == ws_id),
                )
                name_to_id: dict[str, uuid.UUID] = {}
                norm_to_id: dict[str, uuid.UUID] = {}
                for eid, ename, enorm in name_rows.all():
                    name_to_id[ename] = eid
                    if enorm:
                        norm_to_id[enorm] = eid

                today_str = _date_cls.today().isoformat()
                cum_items: list[dict[str, object]] = []
                for ss in request.subject_summaries:
                    sid = name_to_id.get(ss.subject_name)
                    if sid is None:
                        sid = norm_to_id.get(ss.subject_name.strip().lower())
                    if sid is None:
                        _logger.warning(
                            "subject_summaries: unknown subject_name=%r in ws=%s — skipped",
                            ss.subject_name, ws_id,
                        )
                        continue
                    cum_items.append({
                        "subject_id": sid,
                        "date": ss.date or today_str,
                        "summary": ss.cumulative_summary,
                        "turn_count": int(ss.turn_count_today or 0),
                        "unique_turn_count": int(ss.turn_count_today or 0),
                    })
                if cum_items:
                    subject_summaries_upserted = await _save_summaries(
                        db, ws_id, cum_items,
                    )

            # --- Stage 3c: workspaces.cumulative_summary UPDATE (optional) ---
            # `None` ⇒ caller did not opt in, leave column alone. Any string
            # (incl. empty) ⇒ explicit replace. Mirrors memory.py L698~708.
            workspace_summary_updated = False
            if request.workspace_summary is not None:
                from sqlalchemy import text as _sql_text

                await db.execute(
                    _sql_text(
                        "UPDATE workspaces SET cumulative_summary = :cs WHERE id = :wid"
                    ),
                    {"cs": request.workspace_summary, "wid": str(ws_id)},
                )
                workspace_summary_updated = True

            entities_resolved = 0
            entities_created = 0
            facts_stored: list = []
            relations_stored = 0
            if request.entities or request.facts or request.relations:
                transcript_for_store = request.raw_content or "\n\n".join(
                    f"[{t.role}] {t.text}" for t in request.turns
                )
                sm_req = StoreMemoryRequest(
                    workspace_id=ws_id,
                    session_id=episode.session_id,
                    provider=request.provider,
                    conversation_transcript=transcript_for_store,
                    entities=request.entities,
                    facts=request.facts,
                    relations=request.relations,
                    conversation_summary=request.summary,
                )
                sm_resp = await _store(db, sm_req)
                entities_resolved = sm_resp.entities_resolved
                entities_created = sm_resp.entities_created
                facts_stored = sm_resp.facts_stored
                relations_stored = len(request.relations)

            await db.commit()

        ep_short = str(episode.id)[:8]
        dup_note = " _(중복 감지 — 기존 episode 에 합쳐졌어요)_" if is_dup else ""
        total_entities = entities_resolved + entities_created
        ws_flag = 1 if workspace_summary_updated else 0
        return (
            "✓ 자비스 일기 등록 완료\n"
            f"- 일기 id: `{ep_short}` ({episode.id}){dup_note}\n"
            f"- 정제된 turn: {turn_count}건\n"
            f"- 추출된 사실: {len(facts_stored)}건 "
            f"({total_entities} entity, 신규 {entities_created} · "
            f"{relations_stored} relation)\n"
            f"- 일별 요약: {summaries_upserted}건 upsert\n"
            f"- 누적 요약: subject {subject_summaries_upserted}건 upsert · "
            f"workspace {ws_flag}건 갱신\n"
            f"- 주제 분류: 신규 subject {created_subjects}개 · "
            f"turn {linked_turns}개 연결\n"
            f"- 워크스페이스: `{workspace}`"
        )
    except Exception as e:
        return (
            f"✗ 일기 등록 실패: {e}\n\n"
            "_워크스페이스가 존재하는지, 그리고 turn 형식이_ "
            "`{sequence, role, text, timestamp}` _인지 확인해 주세요._"
        )


# ── Tool 4: Recall Memory ──


@mcp.tool(name="jarvis_recall_memory")
async def recall_memory(
    workspace: str,
    query: str,
    limit: int = 10,
) -> str:
    """Recall relevant memories by entity-anchored hybrid search.

    Triggers (자연어 매핑 — 다음 표현 받으면 이 도구):
    • '그때 뭐였지' / 'what was that' / '그거 다시 알려줘'
    • 'X 회상해줘' / 'recall X' / 'X 기억해?'
    • 'last time we discussed X' / 'X 마지막으로 언제 얘기했지'
    • '어제 무슨 결정 했지' / 'what did we decide yesterday'
    • '왜 X 했지' / 'why did we do X'
    • 'X 에 대해 자비스가 뭐 알고 있어' / 'what does JARVIS know about X'

    Use this when you need context from past conversations — at the start of a
    new session, when the user references past decisions ("그때 뭐였지", "last
    time we discussed X"), or when you need background to answer better.

    How it ranks: query → entity seed → hybrid (PGroonga FTS + pgvector + graph
    walk over entity_relations) → top fact triples grounded by source quotes.
    Results show `entity predicate object [grounded|low_trust] (since YYYY-MM-DD)`,
    optionally with related entities and superseded history.

    If results are too narrow → call jarvis_explore_topic first to see the
    structural map. If you need the narrative behind a fact → jarvis_search_passages
    or jarvis_get_episode_excerpt.

    Args:
        workspace: Workspace name (or UUID)
        query: Natural language query describing what you want to recall
        limit: Maximum number of results (1-50)
    """
    try:
        ws_id = await _resolve_workspace(workspace)

        request = RecallMemoryRequest(
            workspace_id=ws_id,
            query=query,
            limit=min(limit, 50),
        )

        async with async_session_factory() as db:
            result = await _recall(db, request)

        if not result.results:
            return "No relevant memories found."

        # Build metadata header (v1: structural_summary + coverage)
        header_lines: list[str] = []
        if result.structural_summary:
            header_lines.append(result.structural_summary)
        header = "\n".join(header_lines)

        lines: list[str] = []
        if result.anchor_matched:
            lines.append("_:anchor: 질의가 특정 entity 로 잡혔어요_")
        else:
            lines.append("_:broad: 앵커 entity 가 안 잡혀서 넓게 훑었어요_")
        lines.append("")

        for r in result.results:
            grounded_tag = "grounded" if r.grounded else "low_trust"
            ep_short = str(r.evidence.episode_id)[:8]
            pred_pretty = r.predicate.replace("_", " ")
            lines.append(
                f"- **{r.entity}** {pred_pretty}: {r.object_value} "
                f"_(출처 {r.valid_from.date()} · ep={ep_short} · {grounded_tag})_"
            )
            if r.related_entities:
                rel_repr = ", ".join(
                    f"{rel.name} ({rel.relation_type}, {rel.fact_count}f)"
                    for rel in r.related_entities[:5]
                )
                lines.append(f"  - 연관: {rel_repr}")
            if r.history:
                for h in r.history:
                    lines.append(
                        f"  - 이전 값: {h.object_value} _(until {h.superseded_at})_"
                    )

        body = "\n".join(lines)
        footer = ""
        if result.pagination_token == "more_available":
            footer = "\n\n_더 많은 사실이 있어요 — 질의를 좁히면 더 구체적으로 찾을 수 있어요._"

        if header:
            response = f"**회상 결과** — {header}\n\n{body}{footer}"
        else:
            response = f"**회상 결과**\n\n{body}{footer}"

        if len(response) > MAX_RESPONSE_CHARS:
            response = (
                response[:MAX_RESPONSE_CHARS]
                + "\n\n_결과가 잘렸어요 — 질의를 좁혀 다시 회상해 주세요._"
            )

        return response
    except Exception as e:
        return (
            f"회상 실패: {e}\n\n_워크스페이스가 존재하는지 확인하고, "
            "질의를 더 짧고 구체적으로 다시 시도해 주세요._"
        )


# ── Tool 5: Explore Topic (structural map, no fact details) ──


@mcp.tool(name="jarvis_explore_topic")
async def explore_topic(workspace: str, query: str) -> str:
    """Get a structural map of a topic before diving into details.

    Triggers (자연어 매핑 — 다음 표현 받으면 이 도구):
    • 'X 주변에 뭐 있어' / 'what's around X'
    • 'X 관련 모든 거 모아줘' / 'gather everything about X'
    • 'X 전체 그림 보여줘' / 'show me the big picture of X'
    • 'X 토픽 맵' / 'topic map for X'
    • (recall_memory 가 너무 좁을 때 먼저 호출)

    Use this when: you need to explore a topic you're unfamiliar with — it
    returns entities, community tags, and predicate distribution without fact
    details, so you can scan the landscape cheaply. Then call recall_memory
    with a narrower query for the specific entities/predicates you care about.

    Do NOT use this when: you already know the entity name and want its facts
    (call recall_memory directly), or for trivial lookups.

    Args:
        workspace: Workspace name (or UUID)
        query: Natural language query describing the topic to explore
    """
    try:
        ws_id = await _resolve_workspace(workspace)

        async with async_session_factory() as db:
            topic_map = await _build_topic_map(db, ws_id, query)

        if topic_map.total_candidates == 0:
            return f"No entities found for topic '{query}'. Try a broader query."

        lines: list[str] = []
        expanded_repr = ", ".join(topic_map.expanded_terms) if topic_map.expanded_terms else query
        lines.append(f'Topic map for "{query}" (expanded: {expanded_repr})')
        lines.append(
            f"Pool: {topic_map.total_candidates} candidates, "
            f"{topic_map.distinct_communities} distinct communities, "
            f"{topic_map.isolated_entity_count} entities isolated"
        )

        lines.append(f"\nTop entities ({len(topic_map.entities)}):")
        for e in topic_map.entities:
            comm = f"comm={e.community_id}" if e.community_id is not None else "comm=none"
            lines.append(
                f"  - {e.name} [{e.entity_type}, {comm}] "
                f"pool_facts={e.fact_count_in_pool}, ws_facts={e.workspace_fact_count}, "
                f"out_degree={e.out_degree}"
            )

        if topic_map.top_predicates:
            pred_repr = ", ".join(f"{p} ({c})" for p, c in topic_map.top_predicates)
            lines.append(f"\nTop predicates in pool:\n  {pred_repr}")

        if topic_map.time_range_start and topic_map.time_range_end:
            lines.append(
                f"\nTime range: {topic_map.time_range_start:%Y-%m-%d %H:%M} "
                f"→ {topic_map.time_range_end:%Y-%m-%d %H:%M}"
            )
        lines.append(f"Edges between top entities: {topic_map.edge_count}")

        lines.append(
            "\nNext: call recall_memory with a specific entity or predicate for details."
        )

        response = "\n".join(lines)
        if len(response) > MAX_RESPONSE_CHARS:
            response = response[:MAX_RESPONSE_CHARS] + "\n\n[Truncated.]"
        return response
    except Exception as e:
        return f"Failed to explore topic: {e}. Check that workspace exists."


# ── Tool 6: Search Passages (narrative/episodic layer) ──


@mcp.tool(name="jarvis_search_passages")
async def search_passages_tool(workspace: str, query: str, limit: int = 10) -> str:
    """Semantic search over raw conversation passages — bypasses anchor filter.

    Triggers (자연어 매핑 — 다음 표현 받으면 이 도구):
    • 'X 키워드 본문에서 찾아줘' / 'find X in raw text'
    • 'X 정확히 언제 말 했지' / 'when exactly did we say X'
    • 'X 의 이유/근거 본문에서' / 'why behind X in passages'
    • (recall_memory 가 빈약/구조만 줄 때 fallback)

    Use this when: recall_memory returns structured facts but you need the
    "why/decision/reasoning" behind them. Returns natural-language passages
    ranked by semantic similarity with links back to episode and fact.

    Do NOT use this as a default — recall_memory is cheaper and returns
    structured facts. Fall back to this when recall misses narrative context.

    Args:
        workspace: Workspace name (or UUID)
        query: Natural language query for the reasoning/decision you want
        limit: Max passages to return (1-50)
    """
    try:
        ws_id = await _resolve_workspace(workspace)
        async with async_session_factory() as db:
            hits = await _search_passages(db, ws_id, query, limit=limit)
        if not hits:
            return f"No passages found for '{query}'."

        lines = [f"Passages for '{query}' (ranked by semantic similarity):"]
        for i, h in enumerate(hits, 1):
            link = ""
            if h.entity_name and h.predicate:
                link = f" [{h.entity_name} {h.predicate}]"
            content = h.content if len(h.content) <= 400 else h.content[:400] + "..."
            lines.append(f"\n{i}. sim={h.similarity:.3f}{link} (ep={h.episode_id})")
            lines.append(f"   {content}")

        response = "\n".join(lines)
        if len(response) > MAX_RESPONSE_CHARS:
            response = response[:MAX_RESPONSE_CHARS] + "\n\n[Truncated.]"
        return response
    except Exception as e:
        return f"Failed to search passages: {e}."


# ── Tool 6b: Search Episodes (raw PGroonga FTS over transcripts) — phase 4 C1 ──


@mcp.tool(name="jarvis_search_episodes")
async def search_episodes_tool(workspace: str, query: str, limit: int = 10) -> str:
    """PGroonga full-text search over raw episode transcripts.

    Triggers (자연어 매핑 — 다음 표현 받으면 이 도구):
    • 'X 라는 단어 나온 대화 찾아줘' / 'find conversations mentioning X'
    • 'X 키워드 episode 검색' / 'episode FTS for X'
    • (recall_memory + search_passages 둘 다 미스 났을 때 raw FTS fallback)

    Use this when you suspect the user's question hits a keyword that lives
    in past raw conversations but recall_memory misses it (because no entity
    was extracted for that keyword). Bypasses the knowledge_facts layer and
    searches episodes.content + episodes.summary directly with PGroonga.

    Korean and English both work — Korean tokens via mecab, English via the
    default Groonga tokenizer. Each hit shows episode_id (first 8 chars),
    score, matched field, summary, and a short snippet around the first
    match. Drill in further with jarvis_get_episode_excerpt(episode_id).

    Do NOT use as a default search — recall_memory is cheaper and structured.
    Fall back to this AFTER recall_memory if results were thin or off-topic.

    Args:
        workspace: Workspace name (or UUID)
        query: Natural language query
        limit: Max hits (1-50, default 10)
    """
    try:
        ws_id = await _resolve_workspace(workspace)

        from jarvis.core.query_preprocessing import preprocess_query
        from jarvis.core.raw_search import search_episode_content

        pq = preprocess_query(query)
        async with async_session_factory() as db:
            hits = await search_episode_content(db, ws_id, pq.fts_query, limit=limit)

        if not hits:
            return f"No episodes matching '{query}' in raw transcripts."

        lines = [f"Raw episode matches for '{query}' (fts: {pq.fts_query}):"]
        for i, h in enumerate(hits, 1):
            lines.append(
                f"\n{i}. ep={h.episode_id} score={h.score:.1f} field={h.matched_field}"
            )
            if h.summary:
                lines.append(f"   summary: {h.summary[:120]}")
            lines.append(f"   snippet: {h.snippet[:240]}")

        response = "\n".join(lines)
        if len(response) > MAX_RESPONSE_CHARS:
            response = response[:MAX_RESPONSE_CHARS] + "\n\n[Truncated.]"
        return response
    except Exception as e:
        return f"Failed to search episodes: {e}."


# ── Tool 7: Get Episode Excerpt (drill into one episode's transcript) ──


@mcp.tool(name="jarvis_get_episode_excerpt")
async def get_episode_excerpt_tool(
    workspace: str,
    episode_id: str,
    query: str,
    max_chars: int = 2000,
    mode: str = "relevant",
) -> str:
    """Pull a query-relevant passage out of a single episode's raw transcript.

    Triggers (자연어 매핑 — 다음 표현 받으면 이 도구):
    • '그 episode 본문 좀 보여줘' / 'show me that episode'
    • '그때 정확히 뭐라고 했지' / 'what exactly did we say'
    • '그 ep 의 X 부분 보고 싶어' / 'show X part of that episode'
    • (recall_memory 결과의 ep=... id 받아서 drill-in 할 때)

    Use this after recall_memory when you have a fact's episode_id and need
    the surrounding reasoning — the "why/decision/comparison" that lives in
    the original conversation but didn't make it into extracted facts.

    Do NOT use this for general search (use recall_memory or search_passages).
    This is for drilling into ONE known episode when you need the full context.

    Args:
        workspace: Workspace name or UUID
        episode_id: Specific episode to drill into (from recall_memory result)
        query: Natural language query to locate relevant passage in the episode
        max_chars: Size budget for excerpt (200-10000, default 2000)
        mode: "relevant" = keyword-scored passage picks (default),
              "full" or "head" = episode prefix
    """
    try:
        ws_id = await _resolve_workspace(workspace)
        try:
            ep_uuid = uuid.UUID(episode_id)
        except (ValueError, TypeError):
            return f"Invalid episode_id '{episode_id}' (expected UUID)."

        async with async_session_factory() as db:
            result = await _get_episode_excerpt(
                db, ws_id, ep_uuid, query, max_chars=max_chars, mode=mode,
            )
        if result is None:
            return f"Episode {episode_id} not found in workspace."

        header_parts = [
            f"Episode {result.episode_id}",
            f"mode={result.mode}",
            f"total={result.total_length}ch",
            f"passages={result.passage_count}",
        ]
        if result.matched_keywords:
            header_parts.append(f"matched=[{', '.join(result.matched_keywords[:5])}]")
        if result.summary:
            header_parts.append(f"summary={result.summary[:60]}")
        header = " · ".join(header_parts)

        response = f"{header}\n\n{result.excerpt}"
        if len(response) > MAX_RESPONSE_CHARS:
            response = response[:MAX_RESPONSE_CHARS] + "\n\n[Truncated by MCP cap.]"
        return response
    except Exception as e:
        return f"Failed to get episode excerpt: {e}."


# ── Tool 8: Follow Relation (1-hop graph walk) ──


@mcp.tool(name="jarvis_follow_relation")
async def follow_relation_tool(
    workspace: str,
    entity: str,
    direction: str = "both",
    relation_type: str = "",
    limit: int = 10,
) -> str:
    """Walk one hop from an entity to see its direct neighbors with top facts.

    Triggers (자연어 매핑 — 다음 표현 받으면 이 도구):
    • 'X 와 연결된 거 뭐 있어' / 'what's connected to X'
    • 'X 이웃 보여줘' / 'show neighbors of X'
    • 'X 와 Y 관계' / 'relation between X and Y'
    • (recall 으로 entity 알아낸 뒤 그래프 탐색)

    Use this when recall_memory surfaced an entity and you want to navigate
    the graph — find related entities, see what relations they have, and
    drill into them. Returns neighbors grouped by relation_type with a
    3-fact snapshot per neighbor so you can decide where to step next.

    Do NOT use as a default search (use recall_memory). Use this AFTER you
    have a specific entity and want to explore its neighborhood.

    Args:
        workspace: Workspace name or UUID
        entity: Entity name (exact) or UUID to anchor the walk
        direction: "out" (self→other), "in" (other→self), "both" (default)
        relation_type: Filter to one relation type (e.g. "related_to"),
                       or empty string for all types
        limit: Max neighbors to return (1-50, default 10)
    """
    try:
        ws_id = await _resolve_workspace(workspace)
        rtype: str | None = relation_type.strip() if relation_type and relation_type.strip() else None
        async with async_session_factory() as db:
            result = await _follow_relation(
                db, ws_id, entity,
                direction=direction, relation_type=rtype, limit=limit,
            )
        if result is None:
            return f"Entity '{entity}' not found in workspace."
        if result.total_neighbors == 0:
            return f"No neighbors for '{result.anchor_entity_name}' (direction={direction})."

        lines: list[str] = [
            f"Neighbors of '{result.anchor_entity_name}' "
            f"({result.total_neighbors} total, by relation: "
            f"{dict(sorted(result.relation_type_counts.items(), key=lambda x: -x[1]))})"
        ]
        for n in result.neighbors:
            arrow = "→" if n.direction == "out" else "←"
            type_tag = f"[{n.entity_type}]" if n.entity_type else ""
            lines.append(
                f"\n{arrow} {n.relation_type} · {n.entity_name} {type_tag} "
                f"({n.fact_count} facts)"
            )
            for f in n.top_facts:
                trust = "✓" if f.grounded else "?"
                val = f.object_value if len(f.object_value) <= 80 else f.object_value[:80] + "..."
                lines.append(f"    {trust} {f.predicate}: {val}")
            if not n.top_facts:
                lines.append("    (no active facts)")

        lines.append(
            "\nNext: call recall_memory or get_episode_excerpt on one of these "
            "entities to dig deeper."
        )
        response = "\n".join(lines)
        if len(response) > MAX_RESPONSE_CHARS:
            response = response[:MAX_RESPONSE_CHARS] + "\n\n[Truncated.]"
        return response
    except Exception as e:
        return f"Failed to follow relation: {e}."


# ── Tool 11: Open UI ──


@mcp.tool(name="jarvis_open_ui")
async def open_ui(workspace: str = "") -> str:
    """Surface the JARVIS web UI URL + host-OS launch command so the AI client
    can open the browser for the user.

    Triggers (자연어 매핑 — 다음 표현 받으면 이 도구):
    • 'UI 띄워줘' / '자비스 UI 띄워' / '자비스 화면 보여줘'
    • 'open jarvis' / 'launch jarvis ui' / 'show jarvis ui'
    • '자비스 보여줘' / '자비스 열어'

    The MCP server runs inside Docker — it CANNOT spawn a browser on the host.
    So this tool returns the URL + the platform-specific launch command, and
    expects the AI client to ALSO call its Bash tool with the appropriate
    command (start / open / xdg-open) to actually open the browser.

    Args:
        workspace: Optional workspace name. If provided, the URL hash will
                   focus that workspace (#workspace=<name>). Leave empty for
                   the default Home Dashboard.

    NOTE: If running outside the container, the AI client should ALSO call
    Bash with the platform-appropriate launch command (start / open /
    xdg-open) for guaranteed browser launch. The MCP response is the URL +
    guidance, not the actual launch.
    """
    base_url = "http://localhost:8002/"
    ws_name = workspace.strip()
    url = f"{base_url}#workspace={ws_name}" if ws_name else base_url
    active_ws = ws_name or "자비스프로젝트"

    return (
        f"자비스 UI 준비 완료 — {url}\n"
        "\n"
        "사용자 OS 명령으로 자동 오픈 (AI client가 Bash 도구로 실행):\n"
        f"- Windows: start {url}\n"
        f"- macOS: open {url}\n"
        f"- Linux: xdg-open {url}\n"
        "\n"
        "첫 화면은 Home Dashboard (누적 통계 / 최근 7일 카드 / On This Day / Top Wiki Entities).\n"
        f"활성 워크스페이스: '{active_ws}'.\n"
        "\n"
        "_'지금 뭐 해야 하지?' 식 진입이면 jarvis_brief_me() 가 ASCII 브리핑 카드 + 다음 추천을 한 번에 줘요._"
    )


# ── Tool 12: Brief Me (Today's Brief — '지금 뭐 해야 하지?') ──


@mcp.tool(name="jarvis_brief_me")
async def brief_me(
    workspace_name: str = "",
    detail: str = "brief",
    include_hidden: bool = False,
) -> str:
    """'지금 뭐 해야 하지?' 한 마디에 자비스가 내미는 ASCII 브리핑 카드.

    워크스페이스 미지정이면 활성 ws 전체 분포 + 최근 작업 Top 3 + 다음 추천 3개.
    워크스페이스 지정이면 그 ws 만 깊게 (엔티티 허브 + 열린 항목 + 최근 에피소드).

    Triggers (자연어 매핑 — 다음 표현 받으면 이 도구):
    • '오늘 뭐 해야 하지?' / '오늘 뭐 하면 좋을까' / '뭐부터 할까'
    • '최근 뭐 했지?' / '요즘 뭐 했지' / '지난 며칠 작업' / '어디까지 했지'
    • '자비스에 뭐 있어?' / '뭐 들어있어' / '자비스 현황' / '자비스 상태'
    • 'brief me' / 'briefing' / 'status' / 'what should I do'
    • '브리핑' / '브리핑 줘' / '오늘 브리핑'
    • 'ai-argos 깊게' / 'ai-argos 정리' (→ workspace_name='ai-argos', detail='deep')

    Use this when:
    • 사용자가 어디서부터 시작할지 모를 때 — 자비스가 우선순위 추천
    • 며칠 만에 돌아와 어디까지 진행됐는지 회상하고 싶을 때
    • 특정 ws 안 '지금 열린 이슈' 가 뭔지 빠르게 보고 싶을 때 (workspace_name 지정)

    Do NOT use this when:
    • 사용자가 특정 주제/엔티티 회상을 명시 → `jarvis_recall_memory` 또는
      `jarvis_explore_topic`
    • 특정 날짜의 본대화 본문이 필요 → `jarvis_get_episode_excerpt`
    • 일기 저장 명령 → `jarvis_log_diary`

    Args:
        workspace_name: 깊게 볼 ws 이름. 빈 문자열이면 cross-ws 모드 (전체 분포).
                        ws 이름은 `jarvis_initialize_memory` 응답이나
                        `jarvis_manage_workspace(action='list')` 에서 확인.
        detail: 'brief' (기본) — 각 항목 한 줄 요약만.
                'deep' — recent_threads 에 episode_id/fact_id 펼침,
                          최근 에피소드 5건 (vs brief 3건) 표시.
        include_hidden: 기본 False. True 면 archived/hidden ws 도 분포에 포함.

    Returns:
        ASCII 브리핑 카드 (markdown code block 안 box-drawing) + 추천 요약 텍스트.
        본문 폭 64자 고정 — monospace 폰트에서만 정렬 보장.
        백엔드 JSON 데이터는 GET /api/v1/memory/brief (UI 가 쓰는 채널).

    Example:
        - jarvis_brief_me()
          → cross-ws · 활성 ws 분포 + Top 3 thread + 추천 3개
        - jarvis_brief_me(workspace_name='ai-argos', detail='deep')
          → ai-argos deep · 엔티티 허브 + open_question + 최근 ep 5건 + 추천 3개
    """
    detail_norm = (detail or "").strip().lower()
    if detail_norm not in ("brief", "deep"):
        return (
            f"Failed to brief: detail='{detail}' 인식 불가. "
            "'brief' 또는 'deep' 중 하나로 호출해 주세요."
        )

    try:
        ws_id: uuid.UUID | None = None
        if workspace_name and workspace_name.strip():
            try:
                ws_id = await _resolve_workspace(workspace_name.strip())
            except ValueError:
                return (
                    f"Failed to brief: 워크스페이스 '{workspace_name}' 없음. "
                    "`jarvis_manage_workspace(action='list')` 로 가용 ws 확인."
                )

        # Lazy import — backend agent 가 brief_engine.py 를 같은 시점에 추가.
        try:
            from jarvis.core.brief_engine import compute_brief
        except ImportError as ie:
            return (
                f"Failed to brief: brief_engine 모듈 미탑재 ({ie}). "
                "백엔드 배포가 아직 안 끝났을 수 있어요 — 잠시 후 다시 시도해 주세요."
            )

        async with async_session_factory() as db:
            payload = await compute_brief(
                db,
                workspace_id=ws_id,
                workspace_name=workspace_name.strip() or None,
                detail=detail_norm,  # type: ignore[arg-type]
                include_hidden=include_hidden,
            )
    except Exception as e:
        return (
            f"Failed to brief: {e}. "
            "DB 연결 또는 워크스페이스 ID 문제일 수 있어요 — "
            "`jarvis_initialize_memory` 먼저 호출해서 상태 확인 부탁드려요."
        )

    ascii_text: str = payload.get("ascii_text", "") or ""
    recs = payload.get("next_recommendations") or []
    rec_lines: list[str] = []
    for r in recs[:3]:
        rec_lines.append(
            f"  {r.get('rank', '?')}. [{r.get('workspace_name', '?')}] "
            f"{r.get('title', '')} _({r.get('reason_code', '?')})_"
        )
    rec_summary = "\n".join(rec_lines) if rec_lines else "  (추천 없음 — 신호 부족)"

    dq = payload.get("data_quality") or {}
    warnings = dq.get("warnings") or []
    warn_block = ""
    if warnings:
        warn_block = "\n\n_데이터 품질 경고:_\n" + "\n".join(f"  • {w}" for w in warnings)

    mode = payload.get("mode", "cross")
    tw = payload.get("target_workspace")
    tw_hint = f" · ws: {tw.get('name')}" if tw else ""

    body = (
        f"```\n{ascii_text}\n```\n\n"
        f"**다음 추천 (mode={mode}{tw_hint})**\n{rec_summary}"
        f"{warn_block}\n\n"
        "_데이터 채널: `POST /api/v1/memory/brief` (UI 가 같이 쓰는 JSON). "
        "특정 항목 더 보려면 `jarvis_explore_topic` / `jarvis_recall_memory`._"
    )
    if len(body) > MAX_RESPONSE_CHARS:
        body = body[:MAX_RESPONSE_CHARS] + "\n\n_결과가 잘렸어요 — detail='brief' 권장._"
    return body
