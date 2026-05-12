"""Per-turn AI cleanup — one turn at a time via claude -p.

The chunked approach failed: claude's schema discipline collapses on
long prompts. Per-turn keeps input tiny → output schema stable.

Flow:
  1. Parse jsonl → list of raw turn dicts
  2. For each turn: claude -p decides include/exclude + compressed text
  3. Skipped turns just dropped; kept turns get clean text
  4. Sequence renumbered, ingest at end
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis",
)

WS = os.environ.get("JARVIS_WORKSPACE_ID", "5f7aa78b-48d5-41e0-8ab3-45fb4a6ea550")
API = os.environ.get("JARVIS_API_URL", "http://127.0.0.1:8013/api/v1/memory")


PER_TURN_INSTRUCTIONS = """JARVIS 트랜스크립트 한 turn 정제.

한 turn을 받아서 사용자가 UI에서 자기 대화록 볼 때 의미 있는지 판정 + 압축한다.

제외 (include=false):
- "Your task is to create a detailed summary" 류 Claude Code 자동 시스템 prompt
- "Generate a suggestion" 류 suggestion-mode
- 슬래시 명령만 (/model, /context 등)
- thinking 블록만
- 비어있는 turn

포함 (include=true):
- 사용자 실제 입력
- AI의 결정/설명 응답
- tool_use → `[Read foo.py]` 같이 1줄 압축
- tool_result → 핵심 의미 1줄 (긴 코드 dump X)

출력 JSON만 (markdown fence/설명 X):
{"include": true/false, "compressed_text": "압축된 텍스트 (include=true일 때만, 아니면 빈 문자열)"}
"""


def call_claude_per_turn(turn_text: str, role: str, timeout: int = 180) -> dict:
    cmd = [
        "claude.cmd" if os.name == "nt" else "claude",
        "-p",
        "--append-system-prompt", PER_TURN_INSTRUCTIONS,
        "--output-format", "json",
    ]
    cwd_run = os.path.expandvars("%TEMP%") if os.name == "nt" else "/tmp"
    prompt = f"role: {role}\ntext:\n{turn_text[:6000]}"
    try:
        result = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            encoding="utf-8", timeout=timeout, cwd=cwd_run,
        )
    except subprocess.TimeoutExpired:
        return {"include": False, "compressed_text": "", "_error": "timeout"}
    if result.returncode != 0 or not result.stdout.strip():
        return {"include": False, "compressed_text": "", "_error": f"rc={result.returncode}"}
    try:
        outer = json.loads(result.stdout)
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
    except Exception as e:
        return {"include": False, "compressed_text": "", "_error": str(e)[:100]}


def parse_jsonl(path: Path) -> tuple[list[dict], dict]:
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


def ingest(metadata: dict, cleaned: list[dict], raw_path: str) -> dict:
    if not cleaned:
        return {"skipped": True, "reason": "empty after cleanup"}
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
                "timestamp": t["timestamp"],
            }
            for i, t in enumerate(cleaned)
        ],
        "metadata": {
            "cwd": metadata.get("cwd", ""),
            "git_branch": metadata.get("git_branch", ""),
            "ai_title": metadata.get("title", ""),
            "ingested_via": "ai_per_turn_cleanup",
        },
    }
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
    raw_turns, metadata = parse_jsonl(path)
    print(f"  parsed turns: {len(raw_turns)}")
    if not raw_turns:
        return {"skipped": True}

    cleaned = []
    excluded = 0
    errors = 0
    for i, turn in enumerate(raw_turns):
        result = call_claude_per_turn(turn["text"], turn["role"])
        if "_error" in result:
            errors += 1
            continue
        if result.get("include"):
            cleaned.append({
                "role": turn["role"],
                "text": result.get("compressed_text", turn["text"])[:4000],
                "timestamp": turn["timestamp"],
            })
        else:
            excluded += 1
        if (i + 1) % 10 == 0 or i + 1 == len(raw_turns):
            print(f"    [{i+1}/{len(raw_turns)}] cleaned={len(cleaned)} excluded={excluded} err={errors} ({time.time()-t_start:.1f}s)")

    print(f"  total: cleaned={len(cleaned)} excluded={excluded} err={errors}")
    res = ingest(metadata, cleaned, str(path))
    print(f"  ingest: {res}")
    print(f"  elapsed: {time.time()-t_start:.1f}s")
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
