"""Ingest raw transcripts via AI cleanup — the right flow per JARVIS §3.

The AI client (claude -p subprocess) reads raw transcript file, identifies
actual conversation (vs auto system prompts), and returns clean turns JSON.
We POST to /ingest-transcript with cleaned data.

This eliminates heuristic filtering entirely — AI judgment decides what's
real user/assistant exchange vs Claude Code background noise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis",
)

WS = os.environ.get("JARVIS_WORKSPACE_ID", "5f7aa78b-48d5-41e0-8ab3-45fb4a6ea550")


CLEANUP_INSTRUCTIONS = """JARVIS 트랜스크립트 정제 AI.

## 최종 목적
**사용자가 자비스 UI에서 자기 대화록을 읽을 때 깔끔하고 자연스러운 흐름**을 보게 만든다.
시스템 자동 prompt, 긴 tool result code dump, thinking 블록 같은 군더더기 제거.
사람이 읽었을 때 "아 그때 무슨 얘기 했고 뭘 했지"가 자연스럽게 드러나야 한다.

## 제외해야 할 것
- "Your task is to create a detailed summary of the conversation" 류 Claude Code 자동 시스템 prompt
- "Generate a suggestion" / "predict what the user" 류 suggestion-mode 메타 prompt
- 비어있거나 의례적인 turn ("/model", "/context" 등 슬래시 명령만 있는 turn)
- thinking 블록 (assistant 내부 사고 — 사용자 안 봄)
- 긴 tool result 코드/로그 dump (수십 줄 파일 내용 등) — 핵심 정보만 1-2줄 요약

## 포함하되 압축해야 할 것
- tool_use: `[Read F:\\foo\\bar.py]` 같이 도구+대상만 한 줄 요약
- tool_result: 의미 있는 결과만 짧게 (예: "파일에 함수 X 발견" 또는 "5개 파일 grep, 모두 일치"). 코드 본문 dump X
- 긴 assistant 응답: 핵심 결정/설명만 보존, 반복/장황한 부분 다이어트

## 그대로 포함
- 사용자가 실제로 입력한 모든 message (자연어 그대로)
- AI가 사용자에게 직접 한 결정/설명/제안 응답

## 출력 JSON — 필드명 정확히 지킬 것 (turn_index, idx 등 자유로운 이름 X)

```
{
  "session_metadata": {
    "session_id": "원본 sessionId",
    "title": "AI가 추정한 세션 제목 (없으면 빈 문자열)",
    "cwd": "원본 cwd",
    "git_branch": "원본 gitBranch (없으면 빈 문자열)",
    "entrypoint": "원본 entrypoint"
  },
  "turns": [
    {"sequence": 1, "role": "user", "text": "...", "timestamp": "2026-04-21T..."},
    {"sequence": 2, "role": "assistant", "text": "...", "timestamp": "..."}
  ],
  "filtered_out_count": 제외한 시스템/메타 turn 수
}
```

필드명 절대 규칙:
- `turns` 배열 안 각 원소는 정확히 4개 필드: `sequence` (정수, 1부터), `role` ('user' 또는 'assistant'), `text` (문자열), `timestamp` (ISO 8601)
- turn_index, idx, order, number 같은 다른 이름 절대 사용 금지
- thinking, tool_use 별도 구분 필요 없음 — text 안에 통합 (tool_use는 "[tool_use:이름] {간단요약}" 형태로 인라인 압축)

만약 trans가 너무 짧거나 의미 없으면 turns 배열을 빈 채로 반환.
출력은 정확히 위 JSON만 (markdown fence, 설명 텍스트 다 X)."""


def call_claude_cleanup(raw_jsonl_text: str, timeout: int = 240) -> dict:
    """Send raw jsonl content to claude -p for cleanup. Returns parsed JSON."""
    cmd = [
        "claude.cmd" if os.name == "nt" else "claude",
        "-p",
        "--append-system-prompt", CLEANUP_INSTRUCTIONS,
        "--output-format", "json",
    ]
    cwd_run = os.path.expandvars("%TEMP%") if os.name == "nt" else "/tmp"
    prompt = f"""# 트랜스크립트 정제 요청

아래는 raw jsonl 파일 내용이다. 위 instructions에 따라 정제한 JSON을 출력하라.

```
{raw_jsonl_text}
```
"""
    result = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True,
        encoding="utf-8", timeout=timeout, cwd=cwd_run,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude -p failed (rc={result.returncode}): {result.stderr[:500]}")
    try:
        outer = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"stdout not JSON: {result.stdout[:500]}")
    if isinstance(outer, dict):
        if "structured_output" in outer and isinstance(outer["structured_output"], dict):
            return outer["structured_output"]
        if "result" in outer:
            res = outer["result"]
            if isinstance(res, str):
                res = res.strip()
                if res.startswith("```"):
                    res = res.split("```", 2)[1]
                    if res.startswith("json\n"):
                        res = res[5:]
                    res = res.rsplit("```", 1)[0].strip()
                return json.loads(res)
            return res
    return outer


def _normalize_turns(turns: list) -> list:
    """Robust to AI using turn_index/idx/order instead of sequence; tolerate timestamp formats."""
    out = []
    for i, t in enumerate(turns, start=1):
        if not isinstance(t, dict):
            continue
        seq = t.get("sequence") or t.get("turn_index") or t.get("idx") or t.get("order") or i
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            seq = i
        role = (t.get("role") or "").lower()
        if role not in ("user", "assistant"):
            continue
        text_val = t.get("text") or t.get("content") or ""
        if isinstance(text_val, list):
            text_val = " ".join(
                (b.get("text", "") if isinstance(b, dict) else str(b)) for b in text_val
            )
        ts = t.get("timestamp") or t.get("ts") or t.get("time")
        if not ts:
            continue
        out.append({"sequence": seq, "role": role, "text": str(text_val), "timestamp": ts})
    return out


async def ingest_cleaned(workspace_id: str, cleaned: dict, raw_path: str) -> dict:
    """POST cleaned turns to /ingest-transcript API."""
    import urllib.request
    api = os.environ.get("JARVIS_API_URL", "http://127.0.0.1:8013/api/v1/memory")
    meta = cleaned.get("session_metadata", {}) or cleaned.get("metadata", {})
    raw_turns = cleaned.get("turns", [])
    turns = _normalize_turns(raw_turns)
    if not turns:
        return {"skipped": True, "reason": "empty after cleanup"}

    body = {
        "workspace_id": workspace_id,
        "provider": meta.get("entrypoint") or "claude-code",
        "source_session_id": meta.get("session_id", ""),
        "source_path": raw_path,
        "title": meta.get("title") or "",
        "turns": turns,
        "metadata": {
            "cwd": meta.get("cwd", ""),
            "git_branch": meta.get("git_branch", ""),
            "ai_title": meta.get("title", ""),
            "filtered_out_count": cleaned.get("filtered_out_count", 0),
            "ingested_via": "ai_cleanup",
        },
    }
    req = urllib.request.Request(
        f"{api}/ingest-transcript",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def process_file(path: Path, workspace_id: str) -> dict:
    raw = path.read_text(encoding="utf-8")
    # Cap raw at 600K chars to fit context (rare oversize)
    if len(raw) > 600_000:
        raw = raw[:600_000] + "\n[...truncated...]"
    t0 = time.time()
    print(f"\n=== {path.name} ===")
    print(f"  raw chars: {len(raw):,}")
    try:
        cleaned = call_claude_cleanup(raw)
    except Exception as exc:
        print(f"  CLEANUP ERROR: {exc}")
        return {"error": str(exc)}
    elapsed = time.time() - t0
    turns = cleaned.get("turns", [])
    filtered = cleaned.get("filtered_out_count", 0)
    print(f"  claude cleanup: {elapsed:.1f}s, turns={len(turns)}, filtered={filtered}")
    if turns:
        first = turns[0]
        last = turns[-1]
        print(f"  first: [{first['role']}] {(first.get('text') or '')[:100]}")
        print(f"  last:  [{last['role']}] {(last.get('text') or '')[:100]}")
    result = await ingest_cleaned(workspace_id, cleaned, str(path))
    print(f"  ingest: {result}")
    return result


async def main(jsonl_files: list[Path]):
    for fp in jsonl_files:
        await process_file(fp, WS)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("files", nargs="+", type=Path)
    args = p.parse_args()
    asyncio.run(main(args.files))
