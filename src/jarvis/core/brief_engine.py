"""Today's Brief — '지금 뭐 해야 하지?' 답을 만드는 엔진.

단일 진실원: compute_brief() 가 dict 하나를 만들고, MCP 도구 / API endpoint /
UI 카드 / ASCII 렌더가 모두 이걸 펼쳐서 쓴다.

쿼리 전략:
- workspace_id 미지정 (cross-ws): 활성 ws 전체에 대해 list_workspaces_rich
  와 같은 상관 서브쿼리 패턴으로 단일 SQL 패스. N+1 없음.
- workspace_id 지정 (deep ws): 그 ws 안에서 entity hub / 최근 episode /
  open_question / status fact / 최근 daily summary 를 모은다.
"""

from __future__ import annotations

import logging
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

Detail = Literal["brief", "deep"]
Mode = Literal["cross", "deep"]

# brief open-items whitelist — only GENUINELY actionable/open predicates.
# Point-in-time state snapshots (status / in_progress / implementation_status /
# completion_status / current_focus) are deliberately EXCLUDED: in a diary that
# accumulates history, an old "status" describes the past, not an open item, so
# surfacing it as a "next step" is a category error (it made resolved 2-month-old
# states show as current — fixed 2026-06-04).
_BRIEF_PREDICATES: tuple[str, ...] = (
    "open_question",
    "next_step",
    "pending",
    "todo",
    "goal",
    "blocker",
    "decision_pending",
)

# Deadline keyword pattern (SQL SIMILAR TO).
_DEADLINE_PAT = (
    "%(얼마 안|deadline|이번주|내일|urgent|마감|EOD|this week|"
    "오늘까지|금일까지|tomorrow)%"
)


# ── Dataclasses (internal — Pydantic 모델은 schemas.py 가 따로 가짐) ──


@dataclass
class ActiveWorkspaceSignal:
    """활성 ws 한 줄 분포 — cross mode 의 핵심 입력."""

    id: uuid.UUID
    name: str
    status: str
    description: str | None
    activity_tag: str
    ep_count_total: int
    ep_count_7d: int
    ep_count_today: int
    ep_count_yesterday: int
    turn_count_7d: int
    last_activity: datetime | None
    top_subjects_14d: list[str] = field(default_factory=list)
    signal_line: str = ""
    # Cumulative narrative from workspaces.cumulative_summary. Empty string
    # ⇒ ws has no rolling summary yet; UI renders 1-line chip in that case.
    cumulative_summary: str = ""


@dataclass
class RecentThread:
    workspace_id: uuid.UUID
    workspace_name: str
    subject_id: uuid.UUID
    subject_name: str
    turn_count_14d: int
    last_active_date: str
    summary: str
    is_summary_missing: bool = False
    episode_ids: list[uuid.UUID] = field(default_factory=list)
    fact_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class OpenItem:
    fact_id: uuid.UUID
    entity_name: str
    predicate: str
    object_value: str
    recorded_at: datetime
    source_quote: str | None = None


@dataclass
class Recommendation:
    rank: int
    title: str
    reason_code: str
    workspace_id: uuid.UUID
    workspace_name: str
    target_kind: str  # 'workspace' | 'subject' | 'entity' | 'episode'
    target_id: uuid.UUID | None
    target_date: str | None
    detail: str


@dataclass
class DataQuality:
    ws_count: int
    fact_count_active: int = 0
    summary_coverage: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _RecCandidate:
    source: str  # 'predicate' | 'deadline' | 'dormant_active' | 'fallback_top' | 'summary_missing'
    payload: dict[str, Any]


# ── Public entry ──


async def compute_brief(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID | None = None,
    workspace_name: str | None = None,
    detail: Detail = "brief",
    include_hidden: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Brief data bundle. ASCII rendering happens inside.

    See module docstring for the contract.
    """
    today = today or datetime.now(timezone.utc).date()

    target_ws: dict[str, Any] | None = None
    if workspace_id is not None:
        row = await db.execute(
            text("SELECT id, name FROM workspaces WHERE id = :wid"),
            {"wid": str(workspace_id)},
        )
        r = row.fetchone()
        if r is not None:
            target_ws = {"id": r[0], "name": r[1]}
        else:
            # Fall back to cross mode if id is bogus.
            workspace_id = None

    if workspace_id is None and workspace_name:
        row = await db.execute(
            text("SELECT id, name FROM workspaces WHERE LOWER(name) = LOWER(:n)"),
            {"n": workspace_name},
        )
        r = row.fetchone()
        if r is not None:
            workspace_id = r[0]
            target_ws = {"id": r[0], "name": r[1]}

    if workspace_id is not None and target_ws is not None:
        payload = await _compute_deep(db, target_ws, today, detail)
    else:
        payload = await _compute_cross(db, today, detail, include_hidden)

    payload["ascii_text"] = render_brief_ascii(payload, mode=payload["mode"])
    return payload


# ── cross-ws mode ──


async def _compute_cross(
    db: AsyncSession,
    today: date,
    detail: Detail,
    include_hidden: bool,
) -> dict[str, Any]:
    signals = await _query_active_workspaces(db, today, include_hidden)
    active_ws = [_tag_signal(s, today) for s in signals]

    recent_threads = await _query_recent_threads_cross(db, today, active_ws)

    candidates = await _collect_candidates(db, today, active_ws, target_ws_id=None)
    recommendations = _pick_top3(candidates, active_ws, target_ws_id=None)

    quality = await _data_quality(db, today, active_ws)

    return {
        "mode": "cross",
        "generated_at": datetime.now(timezone.utc),
        "today": today,
        "target_workspace": None,
        "active_workspaces": [_dump_signal(s) for s in active_ws],
        "recent_threads": [_dump_thread(t) for t in recent_threads],
        "open_questions": [],
        "next_recommendations": [_dump_recommendation(r) for r in recommendations],
        "data_quality": _dump_quality(quality),
    }


async def _query_active_workspaces(
    db: AsyncSession,
    today: date,
    include_hidden: bool,
) -> list[dict[str, Any]]:
    """Single SQL pass for activity distribution. N+1 free."""
    status_filter = "" if include_hidden else "WHERE w.status = 'active'"
    rows = await db.execute(
        text(
            f"""
            SELECT
              w.id, w.name, w.status, w.description, w.cumulative_summary,
              (SELECT COUNT(*) FROM episodes e
                 WHERE e.workspace_id = w.id
                   AND (e.metadata->>'deleted' IS DISTINCT FROM 'true')) AS ep_count_total,
              (SELECT COUNT(*) FROM episodes e
                 WHERE e.workspace_id = w.id
                   AND (e.metadata->>'deleted' IS DISTINCT FROM 'true')
                   AND DATE(e.created_at AT TIME ZONE 'UTC')
                       >= (:today)::date - 6) AS ep_count_7d,
              (SELECT COUNT(*) FROM episodes e
                 WHERE e.workspace_id = w.id
                   AND (e.metadata->>'deleted' IS DISTINCT FROM 'true')
                   AND DATE(e.created_at AT TIME ZONE 'UTC')
                       = (:today)::date) AS ep_count_today,
              (SELECT COUNT(*) FROM episodes e
                 WHERE e.workspace_id = w.id
                   AND (e.metadata->>'deleted' IS DISTINCT FROM 'true')
                   AND DATE(e.created_at AT TIME ZONE 'UTC')
                       = (:today)::date - 1) AS ep_count_yesterday,
              (SELECT COUNT(*) FROM turns t
                 WHERE t.workspace_id = w.id
                   AND DATE(t.timestamp AT TIME ZONE 'UTC')
                       >= (:today)::date - 6) AS turn_count_7d,
              (SELECT COUNT(*) FROM turns t
                 WHERE t.workspace_id = w.id
                   AND DATE(t.timestamp AT TIME ZONE 'UTC')
                       = (:today)::date) AS turn_count_today,
              (SELECT MAX(t.timestamp) FROM turns t
                 WHERE t.workspace_id = w.id) AS last_activity,
              (SELECT array_agg(name ORDER BY tc DESC)
                 FROM (
                   SELECT e.name, COUNT(ts.turn_id) AS tc
                   FROM entities e
                   JOIN turn_subjects ts ON ts.subject_id = e.id
                   JOIN turns t ON t.id = ts.turn_id
                   WHERE e.workspace_id = w.id
                     AND e.parent_id IS NULL
                     AND e.entity_type = 'concept'
                     AND DATE(t.timestamp AT TIME ZONE 'UTC')
                         >= (:today)::date - 13
                   GROUP BY e.name
                   ORDER BY tc DESC
                   LIMIT 3
                 ) sub) AS top_subjects_14d
            FROM workspaces w
            {status_filter}
            ORDER BY (w.status = 'active') DESC,
                     last_activity DESC NULLS LAST,
                     w.name ASC
            """
        ),
        {"today": today},
    )
    out: list[dict[str, Any]] = []
    for r in rows.mappings():
        out.append(dict(r))
    return out


def _tag_signal(row: dict[str, Any], today: date) -> ActiveWorkspaceSignal:
    """Apply HOT/ACTIVE/QUIET/DORMANT rule + signal_line."""
    last = row.get("last_activity")
    ep_today = int(row.get("ep_count_today") or 0)
    turn_today = int(row.get("turn_count_today") or 0)
    ep_7d = int(row.get("ep_count_7d") or 0)
    ep_yest = int(row.get("ep_count_yesterday") or 0)

    activity_tag = "DORMANT"
    if ep_today >= 3 or turn_today >= 20:
        activity_tag = "HOT"
    elif last is not None:
        # last_activity 가 datetime — tz-aware vs naive 가능
        now = datetime.now(timezone.utc)
        if last.tzinfo is None:
            last_aware = last.replace(tzinfo=timezone.utc)
        else:
            last_aware = last
        delta = now - last_aware
        if delta <= timedelta(hours=48):
            activity_tag = "ACTIVE"
        elif delta <= timedelta(days=7):
            activity_tag = "QUIET"
        else:
            activity_tag = "DORMANT"

    # signal_line — ASCII 카드의 우측 신호
    parts: list[str] = [f"{int(row.get('ep_count_total') or 0)} ep"]
    if ep_today > 0:
        parts.append(f"오늘 {ep_today}건")
    elif ep_yest > 0:
        parts.append(f"어제 {ep_yest}건")
    elif ep_7d > 0:
        parts.append(f"7d {ep_7d}건")
    signal_line = " · ".join(parts)

    return ActiveWorkspaceSignal(
        id=row["id"],
        name=row["name"],
        status=row.get("status") or "active",
        description=row.get("description"),
        activity_tag=activity_tag,
        ep_count_total=int(row.get("ep_count_total") or 0),
        ep_count_7d=ep_7d,
        ep_count_today=ep_today,
        ep_count_yesterday=ep_yest,
        turn_count_7d=int(row.get("turn_count_7d") or 0),
        last_activity=last,
        top_subjects_14d=list(row.get("top_subjects_14d") or []),
        signal_line=signal_line,
        cumulative_summary=row.get("cumulative_summary") or "",
    )


async def _query_recent_threads_cross(
    db: AsyncSession,
    today: date,
    active_ws: list[ActiveWorkspaceSignal],
) -> list[RecentThread]:
    """Top 3 recent threads across all active ws.

    Primary path: daily_subject_summaries (last 14d) JOIN turn count.
    Fallback: turn_subjects volume only (summary blank).
    """
    if not active_ws:
        return []
    ws_ids = [str(w.id) for w in active_ws]
    ws_map = {str(w.id): w.name for w in active_ws}

    # Primary — has summary
    rows = await db.execute(
        text(
            """
            SELECT
              e.workspace_id AS ws_id,
              e.id AS subject_id,
              e.name AS subject_name,
              MAX(dss.date)::text AS last_date,
              (
                SELECT dss2.summary
                FROM daily_subject_summaries dss2
                WHERE dss2.subject_id = e.id
                  AND dss2.workspace_id = e.workspace_id
                ORDER BY dss2.date DESC
                LIMIT 1
              ) AS summary,
              (
                SELECT COUNT(*)
                FROM turn_subjects ts
                JOIN turns t ON t.id = ts.turn_id
                WHERE ts.subject_id = e.id
                  AND DATE(t.timestamp AT TIME ZONE 'UTC')
                      >= (:today)::date - 13
              ) AS turn_count_14d
            FROM daily_subject_summaries dss
            JOIN entities e ON e.id = dss.subject_id
            WHERE e.workspace_id = ANY(:ws_ids)
              AND e.parent_id IS NULL
              AND e.entity_type = 'concept'
              AND dss.date >= (:today)::date - 13
            GROUP BY e.workspace_id, e.id, e.name
            ORDER BY turn_count_14d DESC NULLS LAST,
                     last_date DESC NULLS LAST
            LIMIT 3
            """
        ),
        {"today": today, "ws_ids": ws_ids},
    )
    threads: list[RecentThread] = []
    seen: set[uuid.UUID] = set()
    for r in rows.mappings():
        sid = r["subject_id"]
        if sid in seen:
            continue
        seen.add(sid)
        threads.append(
            RecentThread(
                workspace_id=r["ws_id"],
                workspace_name=ws_map.get(str(r["ws_id"]), "?"),
                subject_id=sid,
                subject_name=r["subject_name"],
                turn_count_14d=int(r["turn_count_14d"] or 0),
                last_active_date=str(r["last_date"]) if r["last_date"] else "",
                summary=(r["summary"] or "")[:300],
                is_summary_missing=False,
            )
        )

    # Fallback — fill to 3 with turn-volume-only threads (no summary).
    if len(threads) < 3:
        need = 3 - len(threads)
        fb_rows = await db.execute(
            text(
                """
                SELECT
                  e.workspace_id AS ws_id,
                  e.id AS subject_id,
                  e.name AS subject_name,
                  MAX(t.timestamp)::date::text AS last_date,
                  COUNT(ts.turn_id) AS turn_count_14d
                FROM entities e
                JOIN turn_subjects ts ON ts.subject_id = e.id
                JOIN turns t ON t.id = ts.turn_id
                WHERE e.workspace_id = ANY(:ws_ids)
                  AND e.parent_id IS NULL
                  AND e.entity_type = 'concept'
                  AND DATE(t.timestamp AT TIME ZONE 'UTC')
                      >= (:today)::date - 13
                GROUP BY e.workspace_id, e.id, e.name
                ORDER BY turn_count_14d DESC
                LIMIT 6
                """
            ),
            {"today": today, "ws_ids": ws_ids},
        )
        for r in fb_rows.mappings():
            if r["subject_id"] in seen:
                continue
            seen.add(r["subject_id"])
            tc = int(r["turn_count_14d"] or 0)
            threads.append(
                RecentThread(
                    workspace_id=r["ws_id"],
                    workspace_name=ws_map.get(str(r["ws_id"]), "?"),
                    subject_id=r["subject_id"],
                    subject_name=r["subject_name"],
                    turn_count_14d=tc,
                    last_active_date=str(r["last_date"]) if r["last_date"] else "",
                    summary=f"(요약 없음 — turn {tc}건)",
                    is_summary_missing=True,
                )
            )
            if len(threads) >= 3:
                break
    return threads[:3]


# ── deep mode ──


async def _compute_deep(
    db: AsyncSession,
    target_ws: dict[str, Any],
    today: date,
    detail: Detail,
) -> dict[str, Any]:
    ws_id = target_ws["id"]
    # Reuse the cross-mode aggregator but filtered to one ws — we get all
    # the activity counts + tag + signal line uniformly.
    signals_raw = await _query_active_workspaces_single(db, today, ws_id)
    if signals_raw:
        signal = _tag_signal(signals_raw[0], today)
        active_ws = [signal]
    else:
        active_ws = []

    recent_threads = await _query_recent_threads_deep(db, today, ws_id, active_ws)

    open_questions = await _query_open_items(db, ws_id)

    ep_limit = 5 if detail == "deep" else 3
    last_episodes = await _query_last_episodes(db, ws_id, limit=ep_limit)

    candidates = await _collect_candidates(db, today, active_ws, target_ws_id=ws_id)
    recommendations = _pick_top3(candidates, active_ws, target_ws_id=ws_id)

    quality = await _data_quality(db, today, active_ws)

    payload: dict[str, Any] = {
        "mode": "deep",
        "generated_at": datetime.now(timezone.utc),
        "today": today,
        "target_workspace": {"id": ws_id, "name": target_ws["name"]},
        "active_workspaces": [_dump_signal(s) for s in active_ws],
        "recent_threads": [_dump_thread(t) for t in recent_threads],
        "open_questions": [_dump_open_item(o) for o in open_questions],
        "next_recommendations": [_dump_recommendation(r) for r in recommendations],
        "data_quality": _dump_quality(quality),
        "last_episodes": last_episodes,  # deep-only extra
    }
    return payload


async def _query_active_workspaces_single(
    db: AsyncSession,
    today: date,
    ws_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Same shape as _query_active_workspaces but filtered to one id."""
    rows = await db.execute(
        text(
            """
            SELECT
              w.id, w.name, w.status, w.description, w.cumulative_summary,
              (SELECT COUNT(*) FROM episodes e
                 WHERE e.workspace_id = w.id
                   AND (e.metadata->>'deleted' IS DISTINCT FROM 'true')) AS ep_count_total,
              (SELECT COUNT(*) FROM episodes e
                 WHERE e.workspace_id = w.id
                   AND (e.metadata->>'deleted' IS DISTINCT FROM 'true')
                   AND DATE(e.created_at AT TIME ZONE 'UTC')
                       >= (:today)::date - 6) AS ep_count_7d,
              (SELECT COUNT(*) FROM episodes e
                 WHERE e.workspace_id = w.id
                   AND (e.metadata->>'deleted' IS DISTINCT FROM 'true')
                   AND DATE(e.created_at AT TIME ZONE 'UTC')
                       = (:today)::date) AS ep_count_today,
              (SELECT COUNT(*) FROM episodes e
                 WHERE e.workspace_id = w.id
                   AND (e.metadata->>'deleted' IS DISTINCT FROM 'true')
                   AND DATE(e.created_at AT TIME ZONE 'UTC')
                       = (:today)::date - 1) AS ep_count_yesterday,
              (SELECT COUNT(*) FROM turns t
                 WHERE t.workspace_id = w.id
                   AND DATE(t.timestamp AT TIME ZONE 'UTC')
                       >= (:today)::date - 6) AS turn_count_7d,
              (SELECT COUNT(*) FROM turns t
                 WHERE t.workspace_id = w.id
                   AND DATE(t.timestamp AT TIME ZONE 'UTC')
                       = (:today)::date) AS turn_count_today,
              (SELECT MAX(t.timestamp) FROM turns t
                 WHERE t.workspace_id = w.id) AS last_activity,
              (SELECT array_agg(name ORDER BY tc DESC)
                 FROM (
                   SELECT e.name, COUNT(ts.turn_id) AS tc
                   FROM entities e
                   JOIN turn_subjects ts ON ts.subject_id = e.id
                   JOIN turns t ON t.id = ts.turn_id
                   WHERE e.workspace_id = w.id
                     AND e.parent_id IS NULL
                     AND e.entity_type = 'concept'
                     AND DATE(t.timestamp AT TIME ZONE 'UTC')
                         >= (:today)::date - 13
                   GROUP BY e.name
                   ORDER BY tc DESC
                   LIMIT 3
                 ) sub) AS top_subjects_14d
            FROM workspaces w
            WHERE w.id = :wid
            """
        ),
        {"today": today, "wid": str(ws_id)},
    )
    return [dict(r) for r in rows.mappings()]


async def _query_recent_threads_deep(
    db: AsyncSession,
    today: date,
    ws_id: uuid.UUID,
    active_ws: list[ActiveWorkspaceSignal],
) -> list[RecentThread]:
    ws_name = active_ws[0].name if active_ws else "?"
    # Same primary/fallback path but scoped to one ws.
    rows = await db.execute(
        text(
            """
            SELECT
              e.id AS subject_id,
              e.name AS subject_name,
              MAX(dss.date)::text AS last_date,
              (
                SELECT dss2.summary
                FROM daily_subject_summaries dss2
                WHERE dss2.subject_id = e.id
                ORDER BY dss2.date DESC
                LIMIT 1
              ) AS summary,
              (
                SELECT COUNT(*)
                FROM turn_subjects ts
                JOIN turns t ON t.id = ts.turn_id
                WHERE ts.subject_id = e.id
                  AND DATE(t.timestamp AT TIME ZONE 'UTC')
                      >= (:today)::date - 13
              ) AS turn_count_14d
            FROM daily_subject_summaries dss
            JOIN entities e ON e.id = dss.subject_id
            WHERE e.workspace_id = :wid
              AND e.parent_id IS NULL
              AND e.entity_type = 'concept'
              AND dss.date >= (:today)::date - 13
            GROUP BY e.id, e.name
            ORDER BY turn_count_14d DESC NULLS LAST,
                     last_date DESC NULLS LAST
            LIMIT 3
            """
        ),
        {"today": today, "wid": str(ws_id)},
    )
    threads: list[RecentThread] = []
    seen: set[uuid.UUID] = set()
    for r in rows.mappings():
        seen.add(r["subject_id"])
        threads.append(
            RecentThread(
                workspace_id=ws_id,
                workspace_name=ws_name,
                subject_id=r["subject_id"],
                subject_name=r["subject_name"],
                turn_count_14d=int(r["turn_count_14d"] or 0),
                last_active_date=str(r["last_date"]) if r["last_date"] else "",
                summary=(r["summary"] or "")[:300],
                is_summary_missing=False,
            )
        )

    if len(threads) < 3:
        fb_rows = await db.execute(
            text(
                """
                SELECT
                  e.id AS subject_id,
                  e.name AS subject_name,
                  MAX(t.timestamp)::date::text AS last_date,
                  COUNT(ts.turn_id) AS turn_count_14d
                FROM entities e
                JOIN turn_subjects ts ON ts.subject_id = e.id
                JOIN turns t ON t.id = ts.turn_id
                WHERE e.workspace_id = :wid
                  AND e.parent_id IS NULL
                  AND e.entity_type = 'concept'
                  AND DATE(t.timestamp AT TIME ZONE 'UTC')
                      >= (:today)::date - 13
                GROUP BY e.id, e.name
                ORDER BY turn_count_14d DESC
                LIMIT 6
                """
            ),
            {"today": today, "wid": str(ws_id)},
        )
        for r in fb_rows.mappings():
            if r["subject_id"] in seen:
                continue
            seen.add(r["subject_id"])
            tc = int(r["turn_count_14d"] or 0)
            threads.append(
                RecentThread(
                    workspace_id=ws_id,
                    workspace_name=ws_name,
                    subject_id=r["subject_id"],
                    subject_name=r["subject_name"],
                    turn_count_14d=tc,
                    last_active_date=str(r["last_date"]) if r["last_date"] else "",
                    summary=f"(요약 없음 — turn {tc}건)",
                    is_summary_missing=True,
                )
            )
            if len(threads) >= 3:
                break
    return threads[:3]


async def _query_open_items(
    db: AsyncSession,
    ws_id: uuid.UUID,
    limit: int = 10,
) -> list[OpenItem]:
    rows = await db.execute(
        text(
            """
            SELECT f.id AS fact_id,
                   e.name AS entity_name,
                   f.predicate,
                   f.object_value,
                   f.recorded_at,
                   f.source_quote
            FROM knowledge_facts f
            JOIN entities e ON e.id = f.entity_id
            LEFT JOIN LATERAL (
                SELECT MAX(t.timestamp) AS work_ts
                FROM turns t WHERE t.episode_id = f.source_episode_id
            ) wt ON TRUE
            WHERE f.workspace_id = :wid
              AND f.superseded_at IS NULL
              AND f.predicate = ANY(:preds)
            -- Order by the source episode's actual WORK date, not recorded_at:
            -- a bulk-indexed archive has all recorded_at ≈ today, so recorded_at
            -- cannot tell recent work from old. work_ts surfaces the freshest
            -- genuinely-open items first.
            ORDER BY wt.work_ts DESC NULLS LAST, f.recorded_at DESC
            LIMIT :lim
            """
        ),
        {
            "wid": str(ws_id),
            "preds": list(_BRIEF_PREDICATES),
            "lim": limit,
        },
    )
    out: list[OpenItem] = []
    for r in rows.mappings():
        out.append(
            OpenItem(
                fact_id=r["fact_id"],
                entity_name=r["entity_name"],
                predicate=r["predicate"],
                object_value=(r["object_value"] or "")[:280],
                recorded_at=r["recorded_at"],
                source_quote=(r["source_quote"] or None),
            )
        )
    return out


async def _query_last_episodes(
    db: AsyncSession,
    ws_id: uuid.UUID,
    limit: int = 5,
) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            """
            SELECT id,
                   created_at,
                   summary
            FROM episodes
            WHERE workspace_id = :wid
              AND (metadata->>'deleted' IS DISTINCT FROM 'true')
            ORDER BY created_at DESC
            LIMIT :lim
            """
        ),
        {"wid": str(ws_id), "lim": limit},
    )
    out: list[dict[str, Any]] = []
    for r in rows.mappings():
        out.append(
            {
                "episode_id": str(r["id"]),
                "created_at": r["created_at"],
                "summary": (r["summary"] or "")[:200],
            }
        )
    return out


# ── Candidate collection + scoring ──


async def _collect_candidates(
    db: AsyncSession,
    today: date,
    active_ws: list[ActiveWorkspaceSignal],
    *,
    target_ws_id: uuid.UUID | None,
) -> list[_RecCandidate]:
    candidates: list[_RecCandidate] = []

    # ws_id (uuid OR str) → ws_name lookup so candidates carry human labels.
    ws_name_map: dict[str, str] = {}
    for w in active_ws:
        ws_name_map[str(w.id)] = w.name
    if target_ws_id is not None and str(target_ws_id) not in ws_name_map:
        # fetch the name for the target ws (deep mode where active_ws may be empty)
        row = await db.execute(
            text("SELECT name FROM workspaces WHERE id = :wid"),
            {"wid": str(target_ws_id)},
        )
        nm = row.scalar()
        if nm:
            ws_name_map[str(target_ws_id)] = nm

    def _enrich(payload: dict[str, Any]) -> dict[str, Any]:
        wsid = payload.get("workspace_id")
        if wsid is not None and "workspace_name" not in payload:
            payload["workspace_name"] = ws_name_map.get(str(wsid), _short_uuid(wsid))
        return payload

    # (a) brief-relevant predicates
    if target_ws_id is None:
        ws_ids = [str(w.id) for w in active_ws]
        if ws_ids:
            rows = await db.execute(
                text(
                    """
                    SELECT f.id AS fact_id, e.id AS entity_id, e.name AS entity_name,
                           e.workspace_id, f.predicate, f.object_value, f.recorded_at
                    FROM knowledge_facts f
                    JOIN entities e ON e.id = f.entity_id
                    WHERE f.superseded_at IS NULL
                      AND f.predicate = ANY(:preds)
                      AND f.workspace_id = ANY(:ws_ids)
                    ORDER BY f.recorded_at DESC
                    LIMIT 20
                    """
                ),
                {"preds": list(_BRIEF_PREDICATES), "ws_ids": ws_ids},
            )
            for r in rows.mappings():
                candidates.append(_RecCandidate(source="predicate", payload=_enrich(dict(r))))
    else:
        rows = await db.execute(
            text(
                """
                SELECT f.id AS fact_id, e.id AS entity_id, e.name AS entity_name,
                       e.workspace_id, f.predicate, f.object_value, f.recorded_at
                FROM knowledge_facts f
                JOIN entities e ON e.id = f.entity_id
                WHERE f.superseded_at IS NULL
                  AND f.predicate = ANY(:preds)
                  AND f.workspace_id = :wid
                ORDER BY f.recorded_at DESC
                LIMIT 20
                """
            ),
            {"preds": list(_BRIEF_PREDICATES), "wid": str(target_ws_id)},
        )
        for r in rows.mappings():
            candidates.append(_RecCandidate(source="predicate", payload=_enrich(dict(r))))

    # (b) deadline keyword
    if target_ws_id is None:
        ws_ids = [str(w.id) for w in active_ws]
        if ws_ids:
            rows = await db.execute(
                text(
                    """
                    SELECT f.id AS fact_id, e.id AS entity_id, e.name AS entity_name,
                           e.workspace_id, f.predicate, f.object_value, f.recorded_at
                    FROM knowledge_facts f
                    JOIN entities e ON e.id = f.entity_id
                    WHERE f.superseded_at IS NULL
                      AND f.workspace_id = ANY(:ws_ids)
                      AND (f.object_value SIMILAR TO :pat OR e.name SIMILAR TO :pat)
                    ORDER BY f.recorded_at DESC
                    LIMIT 20
                    """
                ),
                {"ws_ids": ws_ids, "pat": _DEADLINE_PAT},
            )
            for r in rows.mappings():
                candidates.append(_RecCandidate(source="deadline", payload=_enrich(dict(r))))
    else:
        rows = await db.execute(
            text(
                """
                SELECT f.id AS fact_id, e.id AS entity_id, e.name AS entity_name,
                       e.workspace_id, f.predicate, f.object_value, f.recorded_at
                FROM knowledge_facts f
                JOIN entities e ON e.id = f.entity_id
                WHERE f.superseded_at IS NULL
                  AND f.workspace_id = :wid
                  AND (f.object_value SIMILAR TO :pat OR e.name SIMILAR TO :pat)
                ORDER BY f.recorded_at DESC
                LIMIT 20
                """
            ),
            {"wid": str(target_ws_id), "pat": _DEADLINE_PAT},
        )
        for r in rows.mappings():
            candidates.append(_RecCandidate(source="deadline", payload=_enrich(dict(r))))

    # (c) dormant-active ws — cross only
    if target_ws_id is None:
        for w in active_ws:
            avg_per_day_7d = (w.ep_count_7d / 7.0) if w.ep_count_7d else 0.0
            if (
                w.ep_count_yesterday == 0
                and w.ep_count_today == 0
                and avg_per_day_7d >= 1.0
            ):
                candidates.append(
                    _RecCandidate(
                        source="dormant_active",
                        payload={
                            "workspace_id": w.id,
                            "workspace_name": w.name,
                            "avg_per_day_7d": avg_per_day_7d,
                        },
                    )
                )

    # (e) fallback — top thread without today's summary in top-7d-ws
    if target_ws_id is None and active_ws:
        top_ws = max(active_ws, key=lambda w: w.ep_count_7d, default=None)
        if top_ws is not None and top_ws.ep_count_7d > 0:
            rows = await db.execute(
                text(
                    """
                    SELECT e.id AS subject_id, e.name AS subject_name,
                           COUNT(ts.turn_id) AS turn_count
                    FROM entities e
                    JOIN turn_subjects ts ON ts.subject_id = e.id
                    JOIN turns t ON t.id = ts.turn_id
                    LEFT JOIN daily_subject_summaries d
                      ON d.subject_id = e.id AND d.date = (:today)::date
                    WHERE e.workspace_id = :wsid
                      AND e.parent_id IS NULL
                      AND e.entity_type = 'concept'
                      AND DATE(t.timestamp AT TIME ZONE 'UTC')
                          >= (:today)::date - 6
                      AND d.id IS NULL
                    GROUP BY e.id, e.name
                    ORDER BY turn_count DESC
                    LIMIT 3
                    """
                ),
                {"wsid": str(top_ws.id), "today": today},
            )
            for r in rows.mappings():
                candidates.append(
                    _RecCandidate(
                        source="fallback_top",
                        payload={
                            "subject_id": r["subject_id"],
                            "subject_name": r["subject_name"],
                            "turn_count": int(r["turn_count"] or 0),
                            "workspace_id": top_ws.id,
                            "workspace_name": top_ws.name,
                        },
                    )
                )

    # (f) summary_missing — active ws with no daily_summary in last 14d
    if target_ws_id is None:
        for w in active_ws:
            if w.ep_count_7d < 5:
                continue
            row = await db.execute(
                text(
                    """
                    SELECT COUNT(*) AS c
                    FROM daily_subject_summaries d
                    JOIN entities e ON e.id = d.subject_id
                    WHERE e.workspace_id = :wsid
                      AND d.date >= (:today)::date - 13
                    """
                ),
                {"wsid": str(w.id), "today": today},
            )
            c = int(row.scalar() or 0)
            if c == 0:
                candidates.append(
                    _RecCandidate(
                        source="summary_missing",
                        payload={
                            "workspace_id": w.id,
                            "workspace_name": w.name,
                            "ep_count_7d": w.ep_count_7d,
                        },
                    )
                )

    return candidates


def _score(c: _RecCandidate) -> float:
    base = {
        "predicate": 100.0,
        "deadline": 90.0,
        "dormant_active": 60.0,
        "fallback_top": 40.0,
        "summary_missing": 30.0,
    }
    s = base.get(c.source, 0.0)

    if c.source == "predicate":
        pred = c.payload.get("predicate", "")
        if pred == "open_question":
            s += 25
        elif pred == "next_step":
            s += 20
        elif pred == "in_progress":
            s += 15
        elif pred == "current_focus":
            s += 15
        elif pred == "pending":
            s += 10
        elif pred == "todo":
            s += 10
        elif pred == "goal":
            s += 10
        recorded_at = c.payload.get("recorded_at")
        if isinstance(recorded_at, datetime):
            now = datetime.now(timezone.utc)
            ra = recorded_at if recorded_at.tzinfo else recorded_at.replace(tzinfo=timezone.utc)
            age_days = (now - ra).total_seconds() / 86400.0
            if age_days < 1:
                s += 15
            elif age_days < 7:
                s += 5
            elif age_days > 30:
                s -= 10

    if c.source == "deadline":
        blob = ((c.payload.get("object_value") or "")
                + " " + (c.payload.get("entity_name") or "")).lower()
        if any(k in blob for k in ("오늘", "today", "eod")):
            s += 20
        elif any(k in blob for k in ("내일", "tomorrow")):
            s += 15
        elif any(k in blob for k in ("이번주", "this week")):
            s += 10

    return s


def _pick_top3(
    candidates: list[_RecCandidate],
    active_ws: list[ActiveWorkspaceSignal],
    *,
    target_ws_id: uuid.UUID | None,
) -> list[Recommendation]:
    if not candidates:
        return _empty_fallback(active_ws, target_ws_id)

    def _ts(c: _RecCandidate) -> float:
        ra = c.payload.get("recorded_at")
        if isinstance(ra, datetime):
            return ra.timestamp()
        return 0.0

    candidates.sort(key=lambda c: (-_score(c), -_ts(c)))

    picked: list[_RecCandidate] = []
    ws_count: dict[Any, int] = {}
    # First pass: ws cap 2
    allow_cap = (target_ws_id is None and len(active_ws) > 1)
    for c in candidates:
        wsid = c.payload.get("workspace_id")
        if allow_cap and ws_count.get(wsid, 0) >= 2:
            continue
        picked.append(c)
        ws_count[wsid] = ws_count.get(wsid, 0) + 1
        if len(picked) == 3:
            break
    # Second pass: lift cap
    if len(picked) < 3:
        for c in candidates:
            if c in picked:
                continue
            picked.append(c)
            if len(picked) == 3:
                break

    return [_to_recommendation(c, rank=i + 1) for i, c in enumerate(picked[:3])]


def _to_recommendation(c: _RecCandidate, *, rank: int) -> Recommendation:
    wsid = c.payload.get("workspace_id")
    ws_name = c.payload.get("workspace_name") or _short_uuid(wsid)

    if c.source == "predicate":
        pred = c.payload.get("predicate", "")
        ent_name = c.payload.get("entity_name", "")
        title_label = {
            "open_question": "open_question 해소",
            "next_step": "next_step 실행",
            "in_progress": "진행 중 작업 이어가기",
            "current_focus": "current_focus 종료",
            "pending": "pending 정리",
            "todo": "todo 정리",
            "goal": "goal 점검",
            "status": "status 갱신",
            "implementation_status": "구현 진행 점검",
            "completion_status": "완료 상태 점검",
        }.get(pred, f"{pred} 해소")
        title = f"{ws_name} · {title_label}"
        detail_line = f"{ent_name} — {c.payload.get('object_value', '')}"[:120]
        return Recommendation(
            rank=rank,
            title=title[:80],
            reason_code=pred or "predicate",
            workspace_id=wsid,
            workspace_name=ws_name,
            target_kind="entity",
            target_id=c.payload.get("entity_id"),
            target_date=None,
            detail=detail_line,
        )

    if c.source == "deadline":
        ent_name = c.payload.get("entity_name", "")
        title = f"{ws_name} · 마감 임박 — {ent_name}"[:80]
        detail_line = (c.payload.get("object_value") or "")[:120]
        return Recommendation(
            rank=rank,
            title=title,
            reason_code="deadline_keyword",
            workspace_id=wsid,
            workspace_name=ws_name,
            target_kind="entity",
            target_id=c.payload.get("entity_id"),
            target_date=None,
            detail=detail_line,
        )

    if c.source == "dormant_active":
        avg = float(c.payload.get("avg_per_day_7d", 0.0))
        title = f"{ws_name} 어제 활동 없음"
        detail_line = f"평소 평균 {avg:.1f}/일 — 재개 시점일 수 있어요"
        return Recommendation(
            rank=rank,
            title=title[:80],
            reason_code="dormant_active_ws",
            workspace_id=wsid,
            workspace_name=ws_name,
            target_kind="workspace",
            target_id=wsid,
            target_date=None,
            detail=detail_line,
        )

    if c.source == "fallback_top":
        sub_name = c.payload.get("subject_name", "")
        tc = int(c.payload.get("turn_count") or 0)
        title = f"{ws_name} · {sub_name} 이어 작업"[:80]
        detail_line = f"최근 7d turn {tc}건, 오늘 summary 아직 없음"
        return Recommendation(
            rank=rank,
            title=title,
            reason_code="fallback_top_thread",
            workspace_id=wsid,
            workspace_name=ws_name,
            target_kind="subject",
            target_id=c.payload.get("subject_id"),
            target_date=None,
            detail=detail_line,
        )

    if c.source == "summary_missing":
        ep_7d = int(c.payload.get("ep_count_7d") or 0)
        title = f"{ws_name} 의 daily_summary 누락 점검"
        detail_line = f"14d 안 ep {ep_7d}건이지만 summary 0건"
        return Recommendation(
            rank=rank,
            title=title[:80],
            reason_code="summary_missing",
            workspace_id=wsid,
            workspace_name=ws_name,
            target_kind="workspace",
            target_id=wsid,
            target_date=None,
            detail=detail_line,
        )

    # Should not reach
    return Recommendation(
        rank=rank,
        title="(알 수 없는 추천)",
        reason_code=c.source,
        workspace_id=wsid,
        workspace_name=ws_name,
        target_kind="workspace",
        target_id=wsid,
        target_date=None,
        detail="",
    )


def _empty_fallback(
    active_ws: list[ActiveWorkspaceSignal],
    target_ws_id: uuid.UUID | None,
) -> list[Recommendation]:
    if not active_ws:
        return [
            Recommendation(
                rank=1,
                title="활성 ws 가 없어요",
                reason_code="no_active_ws",
                workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
                workspace_name="(없음)",
                target_kind="workspace",
                target_id=None,
                target_date=None,
                detail="jarvis_manage_workspace(action='create', workspace='my-ws') 로 시작",
            )
        ]
    # Generic empty — encourage diary
    w = active_ws[0]
    return [
        Recommendation(
            rank=1,
            title=f"{w.name} · 첫 일기를 자비스에 남겨주세요",
            reason_code="no_facts",
            workspace_id=w.id,
            workspace_name=w.name,
            target_kind="workspace",
            target_id=w.id,
            target_date=None,
            detail="jarvis_log_diary 로 오늘 작업 기록 시작",
        )
    ]


async def _data_quality(
    db: AsyncSession,
    today: date,
    active_ws: list[ActiveWorkspaceSignal],
) -> DataQuality:
    if not active_ws:
        return DataQuality(ws_count=0)
    ws_ids = [str(w.id) for w in active_ws]
    # active fact count
    row = await db.execute(
        text(
            """
            SELECT COUNT(*)
            FROM knowledge_facts
            WHERE workspace_id = ANY(:ids)
              AND superseded_at IS NULL
            """
        ),
        {"ids": ws_ids},
    )
    fact_count = int(row.scalar() or 0)

    # summary_coverage per ws
    cov_rows = await db.execute(
        text(
            """
            SELECT w.name, COUNT(d.id) AS c
            FROM workspaces w
            LEFT JOIN daily_subject_summaries d
              ON d.workspace_id = w.id
              AND d.date >= (:today)::date - 13
            WHERE w.id = ANY(:ids)
            GROUP BY w.name
            """
        ),
        {"ids": ws_ids, "today": today},
    )
    coverage = {r[0]: int(r[1] or 0) for r in cov_rows.fetchall()}

    warnings: list[str] = []
    for w in active_ws:
        if w.ep_count_7d >= 5 and coverage.get(w.name, 0) == 0:
            warnings.append(
                f"{w.name}: 7d ep {w.ep_count_7d}건이지만 daily_summary 0건"
            )

    return DataQuality(
        ws_count=len(active_ws),
        fact_count_active=fact_count,
        summary_coverage=coverage,
        warnings=warnings,
    )


# ── Dict dump helpers (Pydantic 가 받기 좋게) ──


def _dump_signal(s: ActiveWorkspaceSignal) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "status": s.status,
        "description": s.description,
        "activity_tag": s.activity_tag,
        "ep_count_total": s.ep_count_total,
        "ep_count_7d": s.ep_count_7d,
        "ep_count_today": s.ep_count_today,
        "ep_count_yesterday": s.ep_count_yesterday,
        "turn_count_7d": s.turn_count_7d,
        "last_activity": s.last_activity,
        "top_subjects_14d": s.top_subjects_14d,
        "signal_line": s.signal_line,
        "cumulative_summary": s.cumulative_summary,
    }


def _dump_thread(t: RecentThread) -> dict[str, Any]:
    return {
        "workspace_id": t.workspace_id,
        "workspace_name": t.workspace_name,
        "subject_id": t.subject_id,
        "subject_name": t.subject_name,
        "turn_count_14d": t.turn_count_14d,
        "last_active_date": t.last_active_date,
        "summary": t.summary,
        "is_summary_missing": t.is_summary_missing,
        "episode_ids": t.episode_ids,
        "fact_ids": t.fact_ids,
    }


def _dump_open_item(o: OpenItem) -> dict[str, Any]:
    return {
        "fact_id": o.fact_id,
        "entity_name": o.entity_name,
        "predicate": o.predicate,
        "object_value": o.object_value,
        "recorded_at": o.recorded_at,
        "source_quote": o.source_quote,
    }


def _dump_recommendation(r: Recommendation) -> dict[str, Any]:
    return {
        "rank": r.rank,
        "title": r.title,
        "reason_code": r.reason_code,
        "workspace_id": r.workspace_id,
        "workspace_name": r.workspace_name,
        "target_kind": r.target_kind,
        "target_id": r.target_id,
        "target_date": r.target_date,
        "detail": r.detail,
    }


def _dump_quality(q: DataQuality) -> dict[str, Any]:
    return {
        "ws_count": q.ws_count,
        "fact_count_active": q.fact_count_active,
        "summary_coverage": q.summary_coverage,
        "warnings": q.warnings,
    }


def _short_uuid(u: Any) -> str:
    s = str(u or "")
    return s[:8] if s else "?"


# ── ASCII rendering ──


BOX_WIDTH = 60  # inner content width (excluding the two │ chars)


def render_brief_ascii(payload: dict[str, Any], *, mode: Mode) -> str:
    """Render the brief payload as a 62-char-wide box-drawing card.

    Box total width = 62 (│ + 60 inner + │). Lines never exceed inner width;
    overflow is truncated with '…'.
    """
    today_str = payload["today"].isoformat() if hasattr(payload["today"], "isoformat") else str(payload["today"])
    target = payload.get("target_workspace")
    if mode == "deep" and target:
        title = f"JARVIS BRIEFING · {today_str} · ws: {target['name']}"
    else:
        title = f"JARVIS BRIEFING · {today_str} · cross-ws"

    lines: list[str] = []
    # ── header
    lines.append(_box_header(title))

    # blank
    lines.append(_box_line(""))

    if mode == "cross":
        _render_cross_body(lines, payload)
    else:
        _render_deep_body(lines, payload)

    # footer
    lines.append(_box_footer("자비스 메모리 · http://localhost:8002/"))
    return "\n".join(lines)


def _render_cross_body(lines: list[str], payload: dict[str, Any]) -> None:
    active = payload.get("active_workspaces") or []
    threads = payload.get("recent_threads") or []
    recs = payload.get("next_recommendations") or []

    # Active workspaces
    if not active:
        lines.append(_box_line("활성 워크스페이스 없음"))
        lines.append(_box_line(
            "  jarvis_manage_workspace(action='list', include_hidden=True)"
        ))
    else:
        lines.append(_box_line(f"활성 워크스페이스 ({len(active)})"))
        for w in active:
            tag = w.get("activity_tag", "")
            icon = {"HOT": "◆", "ACTIVE": "⚡", "QUIET": "·", "DORMANT": " "}.get(tag, " ")
            head = f"  {icon} {w['name']}  [{tag}]  {w.get('signal_line', '')}"
            lines.append(_box_line(head))
            tops = w.get("top_subjects_14d") or []
            if tops:
                sub = "    └ " + " · ".join(tops)
                lines.append(_box_line(sub))
    lines.append(_box_line(""))

    # Recent threads
    lines.append(_box_line("최근 작업 흐름 (Top 3, 14d)"))
    if not threads:
        lines.append(_box_line("  (최근 14일 작업 없음)"))
    else:
        for i, t in enumerate(threads, 1):
            head = f"  {_circle(i)} {t['workspace_name']} · {t['subject_name']}"
            head = head.rstrip()
            tc = t.get("turn_count_14d", 0)
            head_full = f"{head}    {tc} turns"
            lines.append(_box_line(head_full))
            summary = t.get("summary") or ""
            lines.append(_box_line(f"    └ {summary}"))
    lines.append(_box_line(""))

    # Recommendations
    lines.append(_box_line("다음 추천"))
    if not recs:
        lines.append(_box_line("  (추천 신호 없음 — jarvis_log_diary 로 오늘 기록)"))
    else:
        for r in recs:
            lines.append(_box_line(f"  {r['rank']}. {r['title']}"))
            lines.append(_box_line(f"     └ {r['detail']}"))
    lines.append(_box_line(""))

    # Hint
    lines.append(_box_line("drill-down: jarvis_brief_me(workspace_name='...')"))
    lines.append(_box_line("UI 열기  : jarvis_open_ui()"))


def _render_deep_body(lines: list[str], payload: dict[str, Any]) -> None:
    active = payload.get("active_workspaces") or []
    threads = payload.get("recent_threads") or []
    opens = payload.get("open_questions") or []
    last_eps = payload.get("last_episodes") or []
    recs = payload.get("next_recommendations") or []

    if active:
        w = active[0]
        if w.get("description"):
            lines.append(_box_line(w["description"]))
        sig = (
            f"활동: {w['ep_count_total']} ep · 어제 {w['ep_count_yesterday']}건 · "
            f"오늘 {w['ep_count_today']}건 · [{w['activity_tag']}]"
        )
        lines.append(_box_line(sig))
        lines.append(_box_line(""))

    # Recent threads
    lines.append(_box_line("최근 작업 흐름"))
    if not threads:
        lines.append(_box_line("  (최근 14일 작업 없음)"))
    else:
        for i, t in enumerate(threads, 1):
            head = f"  {_circle(i)} {t['subject_name']}    {t.get('turn_count_14d', 0)} turns"
            lines.append(_box_line(head))
            lines.append(_box_line(f"    └ {t.get('summary', '')}"))
    lines.append(_box_line(""))

    # Open items
    lines.append(_box_line("열린 항목 (open_question / next_step / 등)"))
    if not opens:
        lines.append(_box_line("  (열린 항목 없음)"))
    else:
        for o in opens[:5]:
            head = f"  • [{o['predicate']}] {o['entity_name']}"
            lines.append(_box_line(head))
            obj = o.get("object_value") or ""
            lines.append(_box_line(f"    └ {obj}"))
    lines.append(_box_line(""))

    # Last episodes
    lines.append(_box_line(f"최근 에피소드 (Top {len(last_eps)})"))
    if not last_eps:
        lines.append(_box_line("  (에피소드 없음)"))
    else:
        for ep in last_eps:
            created = ep.get("created_at")
            if isinstance(created, datetime):
                ts = created.strftime("%Y-%m-%d %H:%M")
            else:
                ts = str(created)[:16]
            head = f"  ├ {ts} · {ep.get('summary', '')}"
            lines.append(_box_line(head))
    lines.append(_box_line(""))

    # Recommendations
    lines.append(_box_line("다음 추천"))
    if not recs:
        lines.append(_box_line("  (추천 신호 없음)"))
    else:
        for r in recs:
            lines.append(_box_line(f"  {r['rank']}. {r['title']}"))
            lines.append(_box_line(f"     └ {r['detail']}"))


# ── Box drawing primitives ──


def _circle(n: int) -> str:
    return ["①", "②", "③", "④", "⑤"][n - 1] if 1 <= n <= 5 else f"{n}."


def _ascii_width(s: str) -> int:
    """Display width via Unicode East Asian Width.

    W/F (Wide/Fullwidth) → 2, anything else → 1. Ambiguous (box-drawing,
    middle dot, circled digits, ◆) treated as 1 since the UI's monospace font
    renders them in one cell. ⚡ (U+26A1) is EAW=W so it correctly takes 2.
    """
    w = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ("W", "F") else 1
    return w


def _truncate(s: str, width: int) -> str:
    """Truncate to fit `width` display columns. Adds '…' when cut."""
    if _ascii_width(s) <= width:
        return s
    out: list[str] = []
    used = 0
    for ch in s:
        cw = _ascii_width(ch)
        if used + cw > width - 1:  # reserve 1 for ellipsis
            out.append("…")
            used += 1
            break
        out.append(ch)
        used += cw
    return "".join(out)


def _pad_right(s: str, width: int) -> str:
    s2 = _truncate(s, width)
    pad = width - _ascii_width(s2)
    if pad < 0:
        pad = 0
    return s2 + (" " * pad)


def _box_line(s: str) -> str:
    return "│ " + _pad_right(s, BOX_WIDTH - 2) + " │"


def _box_header(title: str) -> str:
    # ╭─── {title} ─...─╮  — total visual width must equal _box_line (= BOX_WIDTH + 2)
    label = _truncate(title, BOX_WIDTH - 8)
    left = 3  # "───" before " title "
    right = BOX_WIDTH - left - 2 - _ascii_width(label)  # -2 for the 2 spaces
    if right < 1:
        right = 1
    return "╭" + ("─" * left) + " " + label + " " + ("─" * right) + "╮"


def _box_footer(label: str) -> str:
    label_t = _truncate(label, BOX_WIDTH - 8)
    left = 3
    right = BOX_WIDTH - left - 2 - _ascii_width(label_t)
    if right < 1:
        right = 1
    return "╰" + ("─" * left) + " " + label_t + " " + ("─" * right) + "╯"


# Compatibility aliases for design names.
generate_brief = compute_brief
