"""Helper: parse jsonl and dump user/assistant turns as readable text for AI review."""

import argparse
import json
import sys
from pathlib import Path


def parse(path: Path):
    metadata = {}
    turns = []
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
            c = msg.get("content")
            if isinstance(c, list):
                parts = []
                for blk in c:
                    if isinstance(blk, dict):
                        bt = blk.get("type")
                        if bt == "text":
                            parts.append(blk.get("text", ""))
                        elif bt == "thinking":
                            parts.append(f"[thinking] {blk.get('thinking', '')[:300]}")
                        elif bt == "tool_use":
                            parts.append(f"[tool_use:{blk.get('name', '')}] {json.dumps(blk.get('input', {}), ensure_ascii=False)[:400]}")
                        elif bt == "tool_result":
                            r = blk.get("content", "")
                            if isinstance(r, list):
                                r = " ".join(rr.get("text", "") for rr in r if isinstance(rr, dict))
                            parts.append(f"[tool_result] {str(r)[:400]}")
                tx = "\n".join(p for p in parts if p)
            else:
                tx = str(c or "")
            turns.append({"role": t, "text": tx, "timestamp": ts})
    return metadata, turns


def main():
    p = argparse.ArgumentParser()
    p.add_argument("path", type=Path)
    p.add_argument("--max-text", type=int, default=2000)
    args = p.parse_args()
    metadata, turns = parse(args.path)

    print("=== metadata ===")
    for k, v in metadata.items():
        print(f"  {k}: {v}")
    print(f"\n=== {len(turns)} turns ===")
    for i, t in enumerate(turns):
        print(f"\n--- turn {i+1} [{t['role']}] ts={t['timestamp']} ---")
        print(t["text"][:args.max_text])


if __name__ == "__main__":
    main()
