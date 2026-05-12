"""Ingest raw transcripts via AI cleanup — chunked sequential processing.

Handles arbitrarily large transcripts (3MB+) by:
  1. Parse jsonl → list of raw turns (cheap, our code)
  2. Slice into N-turn chunks (default 30)
  3. For each chunk sequentially, AI cleanup with prior-chunk preview as context
  4. Accumulate cleaned turns, normalize sequence numbers
  5. Single ingest call at the end

The AI sees ~30 turns at a time — well within context. Output schema stays
stable. Preserves the spirit of "AI 클라이언트가 정제" without choking.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis",
)

WS = os.environ.get("JARVIS_WORKSPACE_ID", "5f7aa78b-48d5-41e0-8ab3-45fb4a6ea550")
API = os.environ.get("JARVIS_API_URL", "http://127.0.0.1:8013/api/v1/memory")

CHUNK_SIZE = int(os.environ.get("CLEANUP_CHUNK_SIZE", "30"))  # turns per chunk
PREVIEW_LAST = 3  # how many last cleaned turns to show as context to next chunk


CHUNK_INSTRUCTIONS = """JARVIS 트랜스크립트 청크 정제 AI.

전체 트랜스크립트의 한 청크(연속된 N개 raw turn)를 받는다. 이 청크 안의 turn들 중 사용자가 UI에서 자기 대화록 볼 때 의미 있는 것만 정제해서 반환한다.

## 제외
- "Your task is to create a detailed summary" 류 Claude Code 자동 시스템 prompt
- "Generate a suggestion" 류 suggestion-mode
- 슬래시 명령만 있는 빈 turn (/model, /context, /clear 등)
- thinking 블록
- 매우 긴 tool result code/log dump — 핵심 한 줄로 요약

## 포함 (압축)
- tool_use → `[Read F:\\foo.py]` 같은 1줄 요약
- tool_result → 결과 의미만 한 줄
- 사용자 입력 message는 자연어 그대로
- AI 결정/설명 응답 → 핵심 보존, 장황한 부분 다이어트

## 출력 JSON — 필드명 정확히

```
{
  "cleaned_turns": [
    {"role": "user|assistant", "text": "...", "original_timestamp": "ISO..."}
  ],
  "filtered_count": 제외한 raw turn 수
}
```

`cleaned_turns`의 각 원소는 정확히 3개 필드: role, text, original_timestamp. sequence는 우리가 나중에 매김. 다른 필드 추가 X.
청크 내용이 전부 의례/시스템이면 cleaned_turns를 빈 배열로.
출력은 JSON만 (markdown fence/설명 X)."""


def parse_jsonl_to_raw_turns(path: Path) -> tuple[list[dict], dict]:
    """Parse jsonl → list of raw turn dicts + session metadata. No AI."""
    raw_turns = []
    metadata = {}
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            t = obj.get("type")
            if t == "ai-title":
                metadata["title"] = obj.get("title", "") or obj.get("content", "")
                continue
            if t not in ("user", "assistant"):
                continue
            if not metadata.get("session_id"):
                metadata["session_id"] = obj.get("sessionId", "")
                metadata["cwd"] = obj.get("cwd", "")
                metadata["git_branch"] = obj.get("gitBranch", "")
                metadata["entrypoint"] = obj.get("entrypoint", "")
            ts = obj.get("timestamp")
            if not ts:
                continue
            msg = obj.get("message", {})
            content = msg.get("content")
            # Pre-serialize complex content as text for AI to read
            if isinstance(content, list):
                parts = []
                for blk in content:
                    if isinstance(blk, dict):
                        bt = blk.get("type")
                        if bt == "text":
                            parts.append(blk.get("text", ""))
                        elif bt == "thinking":
                            parts.append(f"[thinking] {blk.get('thinking', '')[:200]}")
                        elif bt == "tool_use":
                            name = blk.get("name", "tool")
                            inp = blk.get("input", {})
                            parts.append(f"[tool_use:{name}] {json.dumps(inp, ensure_ascii=False)[:500]}")
                        elif bt == "tool_result":
                            res = blk.get("content", "")
                            if isinstance(res, list):
                                res = " ".join(r.get("text", "") for r in res if isinstance(r, dict))
                            parts.append(f"[tool_result] {str(res)[:500]}")
                text_val = "\n".join(p for p in parts if p)
            else:
                text_val = str(content or "")
            if not text_val.strip():
                continue
            raw_turns.append({"role": t, "text": text_val, "timestamp": ts})
    return raw_turns, metadata


def call_claude_chunk(raw_chunk: list[dict], prior_preview: list[dict]) -> dict:
    """Send one chunk to claude -p, return cleaned_turns."""
    cmd = [
        "claude.cmd" if os.name == "nt" else "claude",
        "-p",
        "--append-system-prompt", CHUNK_INSTRUCTIONS,
        "--output-format", "json",
    ]
    cwd_run = os.path.expandvars("%TEMP%") if os.name == "nt" else "/tmp"

    preview_text = ""
    if prior_preview:
        preview_text = "\n## 직전 청크의 마지막 정제 turns (참고용 — 다시 출력하지 말 것)\n"
        for t in prior_preview:
            preview_text += f"  - [{t['role']}] {t['text'][:150]}\n"

    raw_text = ""
    for i, t in enumerate(raw_chunk):
        raw_text += f"\n--- raw turn {i+1} (ts={t['timestamp']}, role={t['role']}) ---\n{t['text'][:3000]}\n"

    prompt = f"""# 청크 정제 요청
{preview_text}

## 이 청크의 raw turns ({len(raw_chunk)}개)
{raw_text}

위 raw turns를 정제하라. 출력은 JSON만."""

    result = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True,
        encoding="utf-8", timeout=240, cwd=cwd_run,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude -p rc={result.returncode}: stderr={result.stderr[:300]} stdout={result.stdout[:200]}")
    if not result.stdout.strip():
        raise RuntimeError(f"empty stdout. stderr={result.stderr[:500]}")
    try:
        outer = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"stdout not JSON ({e}): {result.stdout[:500]}")
    if "structured_output" in outer and isinstance(outer["structured_output"], dict):
        return outer["structured_output"]
    res = outer.get("result", "")
    if isinstance(res, str):
        res = res.strip()
        if res.startswith("```"):
            res = res.split("```", 2)[1]
            if res.startswith("json\n"):
                res = res[5:]
            res = res.rsplit("```", 1)[0].strip()
        return json.loads(res)
    return res


def post_ingest(metadata: dict, cleaned_turns: list[dict], raw_path: str) -> dict:
    body = {
        "workspace_id": WS,
        "provider": metadata.get("entrypoint") or "claude-code",
        "source_session_id": metadata.get("session_id", ""),
        "source_path": raw_path,
        "title": metadata.get("title") or "",
        "turns": [
            {
                "sequence": i + 1,
                "role": t["role"],
                "text": t["text"],
                "timestamp": t.get("original_timestamp") or t.get("timestamp"),
            }
            for i, t in enumerate(cleaned_turns)
        ],
        "metadata": {
            "cwd": metadata.get("cwd", ""),
            "git_branch": metadata.get("git_branch", ""),
            "ai_title": metadata.get("title", ""),
            "ingested_via": "ai_cleanup_chunked",
            "chunks_processed": metadata.get("chunks_count", 0),
            "filtered_total": metadata.get("filtered_total", 0),
        },
    }
    if not body["turns"]:
        return {"skipped": True, "reason": "empty after cleanup"}
    req = urllib.request.Request(
        f"{API}/ingest-transcript",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def process_file(path: Path) -> dict:
    print(f"\n=== {path.name} ===")
    print(f"  raw size: {path.stat().st_size:,} bytes")
    t_start = time.time()

    raw_turns, metadata = parse_jsonl_to_raw_turns(path)
    print(f"  parsed turns: {len(raw_turns)}")
    if not raw_turns:
        return {"skipped": True, "reason": "no turns"}

    # Chunk
    chunks = [raw_turns[i:i+CHUNK_SIZE] for i in range(0, len(raw_turns), CHUNK_SIZE)]
    print(f"  chunks: {len(chunks)} (size={CHUNK_SIZE})")

    all_cleaned = []
    filtered_total = 0
    for ci, chunk in enumerate(chunks):
        t_c = time.time()
        prior_preview = all_cleaned[-PREVIEW_LAST:] if all_cleaned else []
        try:
            result = call_claude_chunk(chunk, prior_preview)
        except Exception as exc:
            print(f"  chunk {ci+1}/{len(chunks)} ERROR: {exc}")
            continue
        cleaned = result.get("cleaned_turns", [])
        filtered = result.get("filtered_count", len(chunk) - len(cleaned))
        all_cleaned.extend(cleaned)
        filtered_total += filtered
        elapsed = time.time() - t_c
        print(f"  chunk {ci+1}/{len(chunks)}: in={len(chunk)} → out={len(cleaned)} (filtered={filtered}, {elapsed:.1f}s)")

    print(f"  total cleaned turns: {len(all_cleaned)} / raw {len(raw_turns)} (filtered {filtered_total})")
    metadata["chunks_count"] = len(chunks)
    metadata["filtered_total"] = filtered_total

    res = post_ingest(metadata, all_cleaned, str(path))
    print(f"  ingest result: {res}")
    print(f"  total elapsed: {time.time() - t_start:.1f}s")
    return res


def main(files: list[Path]):
    for fp in files:
        try:
            process_file(fp)
        except Exception as exc:
            print(f"FAILED {fp.name}: {exc}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("files", nargs="+", type=Path)
    args = p.parse_args()
    main(args.files)
