"""AI-based subject classification via claude -p subprocess.

Mimics real production cycle: each episode goes through Claude judgment,
just like when user says '올려' in real workflow.

Workflow:
  1. Fetch episode metadata + sample turns + existing subjects from DB
  2. Build prompt with rules + JSON schema
  3. Invoke `claude -p` subprocess (this IS the user's AI in real use)
  4. Parse structured JSON output
  5. Save to DB via /classify-turns API (or direct SQL)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis",
)

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

WS = "5f7aa78b-48d5-41e0-8ab3-45fb4a6ea550"


JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "이 episode에서 사용자와 AI가 무엇을 했는지 한 문장 요약 (한국어, 50-150자)",
        },
        "subjects": {
            "type": "array",
            "description": "이 episode가 속하는 주제들. 한 episode가 여러 주제 다루면 복수. 각 주제는 name (canonical 이름) + parent (부모 주제명, 없으면 null)",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "parent": {"type": ["string", "null"]},
                },
                "required": ["name", "parent"],
            },
        },
        "keywords": {
            "type": "array",
            "description": "이 episode의 핵심 키워드/엔티티 5-10개 (구체적 명사 중심)",
            "items": {"type": "string"},
        },
        "reasoning": {
            "type": "string",
            "description": "이렇게 분류한 이유 (1-3문장)",
        },
    },
    "required": ["summary", "subjects", "keywords", "reasoning"],
}


CLASSIFICATION_INSTRUCTIONS = """JARVIS 주제 분류 AI. 주어진 JSON 형식대로만 출력.

핵심 룰:
- subjects: 한 episode가 속하는 주제들. canonical 이름. 부모-자식 관계 활용.
- **계층 최대 2 레벨**: top-level (parent=null) + 직속 child만. 손자 X.
- **기존 subject 목록 매칭이 최우선**: 비슷한 표기/의미 있으면 그 이름 그대로 사용.
- 펀드메신저/fundmessenger 같은 언어 변형은 합리적으로 매칭.
- 기존에 매칭 없고 단독적이면 새 subject. 이름은 episode 내용에서 자연스럽게 추출.
- 도메인 무관: 프로젝트일 수도, 일기/심리/관계/공부 등 어떤 도메인이든 가능.
- 의례적이거나 도구 출력만 있는 episode면 subjects 빈 배열.

**중요 — Claude Code 자동 시스템 prompt 처리**:
- "Your task is to create a detailed summary of the conversation"
- "Before providing your final summary, wrap your analysis in <analysis>"
- "Generate a suggestion" 류
이런 turn들은 **사용자가 직접 보낸 게 아니라 Claude Code가 자동 발송하는 시스템 메시지**다.
그 안에 이전 작업의 회상이 풍부해도, **이 episode의 작업 주제로 쓰면 안 된다**.
이 episode에서 실제로 **새로 코드 작성/문서 편집/대화가 발생한 turn**만 분류 근거로 사용.
시스템 자동 summary 요청만 있고 실제 작업이 없으면 subjects=[]로 skip."""


# Known Claude Code auto-system prompts — these are not user requests
SYSTEM_PROMPT_MARKERS = [
    "Your task is to create a detailed summary of the conversation",
    "Before providing your final summary, wrap your analysis in",
    "Generate a suggestion based on",
    "predict what the user would most likely say next",
    "Suggestion-mode",
    "Based on the conversation history, predict",
]


def _is_system_auto_turn(text: str) -> bool:
    """Detect Claude Code auto-generated system prompts (vs real user input)."""
    if not text:
        return False
    head = text[:500].lower()
    return any(marker.lower() in head for marker in SYSTEM_PROMPT_MARKERS)


def build_episode_prompt(metadata: dict, all_turns: list, existing_subjects: list) -> str:
    sub_lines = []
    for s in existing_subjects:
        prefix = f"(parent: {s['parent_name']}) " if s.get("parent_name") else ""
        sub_lines.append(f"  - {prefix}{s['name']} ({s['turn_count']} turns)")
    subjects_text = "\n".join(sub_lines) if sub_lines else "  (없음 — 첫 분류)"

    # Filter out auto-system turns (their content is unreliable classification signal)
    filtered_turns = [t for t in all_turns if not _is_system_auto_turn(t.get("text", ""))]
    skipped = len(all_turns) - len(filtered_turns)
    skip_note = f" ({skipped}개 시스템 자동 prompt 제외됨)" if skipped > 0 else ""

    # Full turns — let claude read the whole conversation.
    # Hard cap per turn at 4000 chars to avoid pathological cases (massive code dumps).
    turn_lines = []
    total_chars = 0
    HARD_CAP_TOTAL = 800_000  # ~200K tokens estimate
    PER_TURN_CAP = 4000
    truncated = False
    for t in filtered_turns:
        txt = t["text"]
        if len(txt) > PER_TURN_CAP:
            txt = txt[:PER_TURN_CAP] + f"\n[...{len(t['text']) - PER_TURN_CAP}자 생략...]"
        line = f"[seq {t['sequence']} {t['role']}] {txt}"
        total_chars += len(line)
        if total_chars > HARD_CAP_TOTAL:
            truncated = True
            break
        turn_lines.append(line)
    truncation_note = f"\n\n[NOTE] 너무 길어서 {len(filtered_turns) - len(turn_lines)} turns가 생략됐습니다.{skip_note}" if truncated else (f"\n\n[NOTE]{skip_note}" if skip_note else "")

    return f"""# Episode 분류 요청{truncation_note}

## 메타데이터
- episode_id: {metadata['episode_id']}
- cwd: {metadata.get('cwd', '?')}
- ai_title: {metadata.get('title', '(없음)')}
- entrypoint: {metadata.get('entry', '?')}
- git_branch: {metadata.get('branch', '?')}
- turn_count: {metadata['turn_count']}

## 워크스페이스의 기존 주제 (canonical 이름 그대로 사용)
{subjects_text}

## 샘플 turns (첫/중간/끝 발췌)
{chr(10).join(turn_lines)}

## 출력 형식 — 정확히 이 JSON 형식만 (다른 텍스트/마크다운 fence 없이)

```
{{
  "summary": "이 episode 한 문장 요약 (한국어, 50~150자)",
  "subjects": [
    {{"name": "주제 이름", "parent": null}},
    {{"name": "하위 주제 이름", "parent": "부모 주제 이름"}}
  ],
  "keywords": ["키워드1", "키워드2", "..."],
  "reasoning": "이렇게 분류한 이유 1-3문장"
}}
```

규칙 재확인:
- subjects 빈 배열 = 분류 불가 (의례 응답, 도구 출력만 등)
- parent는 다른 subjects 항목의 name 중 하나 또는 null
- 기존 주제 목록에 매칭되는 것 있으면 그 이름 정확히 그대로 사용 (오타/변형 X)
- 펀드메신저/fundmessenger 같은 변형 본 적 없는 episode면 "fundmessenger" 같은 canonical로 통일
- canonical 결정 자유 — 단, 그 후 일관되게 같은 이름 사용

지금 즉시 위 JSON만 답하시오."""


async def fetch_episode_data(db: AsyncSession, episode_id: str) -> tuple[dict, list]:
    r = await db.execute(text("""
        SELECT e.metadata->>'cwd', e.metadata->>'ai_title',
               e.metadata->>'entrypoint', e.metadata->>'git_branch',
               (SELECT COUNT(*) FROM turns t WHERE t.episode_id = e.id) AS tc
        FROM episodes e WHERE e.id = :eid
    """), {"eid": episode_id})
    row = r.fetchone()
    if not row:
        return None, []

    cwd, title, entry, branch, tc = row
    metadata = {
        "episode_id": episode_id,
        "cwd": cwd, "title": title, "entry": entry, "branch": branch,
        "turn_count": tc,
    }

    # Full episode — all turns. claude has 1M context.
    # If extremely long (>500K chars), truncate per-turn text to keep within bounds.
    r2 = await db.execute(text("""
        SELECT sequence, role, text FROM turns
        WHERE episode_id = :eid ORDER BY sequence
    """), {"eid": episode_id})
    all_turns = [{"sequence": x[0], "role": x[1], "text": x[2]} for x in r2.fetchall()]
    return metadata, all_turns


async def fetch_existing_subjects(db: AsyncSession) -> list[dict]:
    r = await db.execute(text("""
        SELECT e.id::text, e.name, p.name AS parent_name,
               COALESCE(tc.cnt, 0) AS turn_count
        FROM entities e
        LEFT JOIN entities p ON p.id = e.parent_id
        LEFT JOIN (
            SELECT subject_id, COUNT(*) AS cnt
            FROM turn_subjects WHERE workspace_id = :ws
            GROUP BY subject_id
        ) tc ON tc.subject_id = e.id
        WHERE e.workspace_id = :ws
          AND COALESCE(tc.cnt, 0) > 0
        ORDER BY tc.cnt DESC
    """), {"ws": WS})
    return [{"subject_id": x[0], "name": x[1], "parent_name": x[2], "turn_count": x[3]}
            for x in r.fetchall()]


def call_claude_p(prompt: str, timeout: int = 240) -> dict:
    """Invoke `claude -p`, prompt via stdin. Schema instructions in prompt body."""
    cmd = [
        "claude.cmd" if os.name == "nt" else "claude",
        "-p",
        "--append-system-prompt", CLASSIFICATION_INSTRUCTIONS,
        "--output-format", "json",
    ]
    cwd_run = os.path.expandvars("%TEMP%") if os.name == "nt" else "/tmp"
    result = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True,
        encoding="utf-8", timeout=timeout, cwd=cwd_run,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude -p failed (rc={result.returncode}). stderr={result.stderr[:500]}. stdout={result.stdout[:500]}")
    # claude --output-format json wraps: {"type":"result","result": "<json string>", "structured_output": {...}}
    # Debug print on error
    try:
        outer = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"stdout not JSON ({e}): {result.stdout[:1500]}")

    # Prefer structured_output (schema-validated). Fallback to result (string).
    if isinstance(outer, dict):
        if "structured_output" in outer and isinstance(outer["structured_output"], dict):
            return outer["structured_output"]
        if "result" in outer:
            res = outer["result"]
            if isinstance(res, str):
                # Strip markdown fences if present
                res = res.strip()
                if res.startswith("```"):
                    res = res.split("```", 2)[1]
                    if res.startswith("json\n"):
                        res = res[5:]
                    res = res.rsplit("```", 1)[0].strip()
                return json.loads(res)
            return res
    return outer


async def classify_one(episode_id: str, verbose: bool = True) -> dict:
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as db:
        metadata, samples = await fetch_episode_data(db, episode_id)
        if metadata is None:
            print(f"NOT_FOUND {episode_id}")
            return {}
        existing = await fetch_existing_subjects(db)

    prompt = build_episode_prompt(metadata, samples, existing)
    if verbose:
        print(f"\n{'='*70}")
        print(f"EPISODE: {episode_id}")
        print(f"  cwd: {metadata['cwd']}")
        print(f"  title: {metadata.get('title')}")
        print(f"  turn_count: {metadata['turn_count']}")
        print(f"  existing subjects: {len(existing)}")

    result = call_claude_p(prompt)
    if verbose:
        print(f"\n--- Claude 분류 결과 ---")
        print(f"  summary: {result.get('summary')}")
        print(f"  subjects:")
        for s in result.get("subjects", []):
            p = f" (parent: {s['parent']})" if s.get("parent") else ""
            print(f"    - {s['name']}{p}")
        print(f"  keywords: {', '.join(result.get('keywords', []))}")
        print(f"  reasoning: {result.get('reasoning')}")

    await engine.dispose()
    return {"episode_id": episode_id, "metadata": metadata, "classification": result}


async def main(episode_ids: list[str]):
    results = []
    for eid in episode_ids:
        try:
            r = await classify_one(eid)
            results.append(r)
        except Exception as exc:
            print(f"  ERROR {eid}: {exc}")
            results.append({"episode_id": eid, "error": str(exc)})

    # Save results to file for review
    out_path = Path(os.environ.get("TEMP", "/tmp")) / "ai_classify_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("episode_ids", nargs="+")
    args = p.parse_args()
    asyncio.run(main(args.episode_ids))
