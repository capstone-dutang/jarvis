"""Conservative jsonl cleanup — drop noise, preserve meaning.

Drops:
- thinking blocks (assistant internal, never shown to user)
- explicit Claude Code auto-system prompts (well-known string patterns)
- empty / trivial transition turns ("읽어볼게요" 류 1줄 안내)

Preserves:
- All user input
- All assistant text (decisions, explanations, reports)
- tool_use as a single-line summary [도구: 인자 요약]
- tool_result with leading content (default first 600 chars + truncation marker)
  — long but readable; raw_content carries full original

Output: list of cleaned turns + raw jsonl text. Both POSTed to /ingest-transcript.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

SYSTEM_PROMPT_MARKERS = (
    "Your task is to create a detailed summary of the conversation",
    "Before providing your final summary, wrap your analysis in",
    "Generate a suggestion based on",
    "predict what the user would most likely say next",
    "Suggestion-mode",
)

TRANSITION_PATTERNS = (
    # Very short assistant turns that only announce next action
    "관련 파일들을 직접 읽어볼게요",
    "이제 ",
    "확인해볼게요",
)


def is_system_auto(text: str) -> bool:
    head = (text or "")[:400].lower()
    return any(m.lower() in head for m in SYSTEM_PROMPT_MARKERS)


def is_trivial_transition(text: str, role: str) -> bool:
    """Short assistant 'reading...' / 'checking...' turn with no content."""
    if role != "assistant":
        return False
    t = (text or "").strip()
    if len(t) > 80:
        return False
    return any(p in t for p in TRANSITION_PATTERNS)


def clean_text_from_blocks(content, tool_result_chars: int = 600) -> str:
    """Format message.content (list of blocks or str) into single readable string.

    Excludes thinking. Compresses tool_use to one line. Preserves tool_result up to N chars.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts = []
    for blk in content:
        if not isinstance(blk, dict):
            continue
        bt = blk.get("type")
        if bt == "text":
            parts.append(blk.get("text", ""))
        elif bt == "thinking":
            # Drop entirely
            continue
        elif bt == "tool_use":
            name = blk.get("name", "tool")
            inp = blk.get("input", {})
            # One-line summary; keep file_path / command / pattern visible
            key_hints = []
            for k in ("file_path", "command", "pattern", "path", "query"):
                if k in inp:
                    val = str(inp[k])[:200]
                    key_hints.append(f"{k}={val}")
                    break
            if not key_hints:
                key_hints.append(json.dumps(inp, ensure_ascii=False)[:200])
            parts.append(f"[{name}] {' '.join(key_hints)}")
        elif bt == "tool_result":
            res = blk.get("content", "")
            if isinstance(res, list):
                res = " ".join(r.get("text", "") for r in res if isinstance(r, dict))
            res_str = str(res or "")
            if len(res_str) > tool_result_chars:
                res_str = res_str[:tool_result_chars] + f"\n[...{len(res_str) - tool_result_chars}자 생략, raw_content에서 확인...]"
            parts.append(f"[결과] {res_str}")
    return "\n".join(p for p in parts if p.strip())


def parse_and_clean(path: Path) -> tuple[list[dict], dict, str]:
    """Returns (cleaned_turns, metadata, raw_jsonl_text)."""
    metadata = {}
    cleaned = []
    seq = 0
    # PG rejects 0x00 in UTF8 text columns. Strip NUL bytes from raw + turns at parse time.
    raw_text = path.read_text(encoding="utf-8").replace("\x00", "")

    with open(path, encoding="utf-8") as fp:
        for line in fp:
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            if t == "ai-title":
                metadata["title"] = o.get("aiTitle") or o.get("title") or o.get("content", "")
                continue
            if t not in ("user", "assistant"):
                continue
            if not metadata.get("cwd"):
                metadata["cwd"] = o.get("cwd", "")
                metadata["session_id"] = o.get("sessionId", "")
                metadata["entrypoint"] = o.get("entrypoint", "")
                metadata["git_branch"] = o.get("gitBranch", "")
            ts = o.get("timestamp")
            if not ts:
                continue
            msg = o.get("message", {})
            txt = clean_text_from_blocks(msg.get("content"))
            if not txt.strip():
                continue
            # Drop system auto prompts (always user role in jsonl)
            if t == "user" and is_system_auto(txt):
                continue
            if is_trivial_transition(txt, t):
                continue
            seq += 1
            cleaned.append({
                "sequence": seq,
                "role": t,
                "text": txt.replace("\x00", ""),
                "timestamp": ts,
            })
    return cleaned, metadata, raw_text


def main():
    p = argparse.ArgumentParser()
    p.add_argument("path", type=Path)
    p.add_argument("--workspace-id", default=os.environ.get("JARVIS_WORKSPACE_ID", "71a0ddee-a88c-4ca3-978a-ee5c61e5ed63"))
    p.add_argument("--api", default=os.environ.get("JARVIS_API_URL", "http://127.0.0.1:8014/api/v1/memory"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--save-cleaned", type=Path, help="Write cleaned turns to file (one per ---boundary)")
    args = p.parse_args()

    cleaned, metadata, raw = parse_and_clean(args.path)
    print(f"=== {args.path.name} ===")
    print(f"  raw chars: {len(raw):,}")
    print(f"  cleaned turns: {len(cleaned)}")
    print(f"  cwd: {metadata.get('cwd')}")
    print(f"  title: {metadata.get('title')}")

    if args.save_cleaned:
        lines = [f"# cleaned from {args.path.name} ({len(cleaned)} turns)\n# cwd: {metadata.get('cwd')}\n# title: {metadata.get('title')}\n"]
        for t in cleaned:
            lines.append(f"\n--- seq {t['sequence']} [{t['role']}] {t['timestamp']} ---")
            lines.append(t["text"])
        args.save_cleaned.write_text("\n".join(lines), encoding="utf-8")
        print(f"  saved cleaned to: {args.save_cleaned}")

    if args.dry_run:
        print("\n--- first 5 cleaned turns ---")
        for t in cleaned[:5]:
            print(f"\n[seq {t['sequence']} {t['role']}]")
            print(t["text"][:500])
        return

    body = {
        "workspace_id": args.workspace_id,
        "provider": metadata.get("entrypoint") or "claude-code",
        "source_session_id": metadata.get("session_id", ""),
        "source_path": str(args.path).replace("\\", "/"),
        "title": metadata.get("title", ""),
        "turns": cleaned,
        "raw_content": raw,
        "metadata": {
            "cwd": metadata.get("cwd", ""),
            "git_branch": metadata.get("git_branch", ""),
            "ai_title": metadata.get("title", ""),
            "ingested_via": "ai_conservative_cleanup",
            "cleaned_turn_count": len(cleaned),
            "raw_chars": len(raw),
        },
    }
    req = urllib.request.Request(
        f"{args.api}/ingest-transcript",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    print(f"\n--- ingest result ---")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
