"""Bulk ingest of raw Claude Code .jsonl session files.

Parses ~/.claude/projects/**/*.jsonl, extracts user/assistant turns
(ignoring queue-operation, system, tool_use blocks etc.), and pushes into
JARVIS via ingest_transcript.

Usage:
    python scripts/ingest_jsonl_bulk.py [--workspace-name vision-test] [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis",
)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jarvis.core.turn_ingest import ingest_transcript
from jarvis.models.tables import Workspace

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("ingest_jsonl")
logger.setLevel(logging.INFO)


def parse_jsonl_file(file_path: Path) -> tuple[list[dict], dict | None]:
    """Parse one jsonl file → (turns, metadata).

    Filters for type ∈ {user, assistant}. Extracts plain text content.
    Skips tool_use/thinking blocks. Skips empty messages.
    """
    turns: list[dict] = []
    seq = 0
    session_id = None
    entrypoint = None
    cwd = None
    git_branch = None
    title = ""

    with open(file_path, encoding="utf-8") as fp:
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
                title = obj.get("title", "") or obj.get("content", "")
                continue

            if t not in ("user", "assistant"):
                continue

            if session_id is None:
                session_id = obj.get("sessionId")
                entrypoint = obj.get("entrypoint")
                cwd = obj.get("cwd")
                git_branch = obj.get("gitBranch")

            ts_str = obj.get("timestamp")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else None
            except Exception:
                ts = None
            if ts is None:
                continue

            msg = obj.get("message", {})
            content = msg.get("content")
            text_out = ""
            if isinstance(content, str):
                text_out = content
            elif isinstance(content, list):
                # Concatenate text blocks only
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            name = block.get("name", "tool")
                            inp = block.get("input", {})
                            parts.append(f"[tool_use:{name}] {json.dumps(inp, ensure_ascii=False)[:300]}")
                        elif block.get("type") == "tool_result":
                            res = block.get("content", "")
                            if isinstance(res, list):
                                res = " ".join(r.get("text", "") for r in res if isinstance(r, dict))
                            parts.append(f"[tool_result] {str(res)[:300]}")
                text_out = "\n".join(p for p in parts if p)

            if not text_out or not text_out.strip():
                continue

            seq += 1
            turns.append({
                "sequence": seq,
                "role": t,
                "text": text_out.strip(),
                "timestamp": ts,
            })

    metadata = {
        "source_file": str(file_path),
        "external_session_id": session_id,
        "entrypoint": entrypoint,
        "cwd": cwd,
        "git_branch": git_branch,
    }
    if title:
        metadata["ai_title"] = title
    return turns, metadata


async def ensure_workspace(db: AsyncSession, name: str):
    result = await db.execute(select(Workspace).where(Workspace.name == name))
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    ws = Workspace(name=name)
    db.add(ws)
    await db.flush()
    return ws


async def main(workspace_name: str, limit: int | None, base_dir: Path):
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    all_files = sorted(base_dir.glob("**/*.jsonl"))
    if limit:
        all_files = all_files[:limit]
    logger.info("Found %d jsonl files. Ingesting into '%s'...", len(all_files), workspace_name)

    stats = {"new": 0, "dup": 0, "error": 0, "empty": 0, "turns": 0}
    t0 = time.time()

    async with SessionLocal() as db:
        ws = await ensure_workspace(db, workspace_name)
        await db.commit()
        ws_id = ws.id

    for i, fp in enumerate(all_files):
        try:
            turns, metadata = parse_jsonl_file(fp)
            if not turns:
                stats["empty"] += 1
            else:
                async with SessionLocal() as sub_db:
                    episode, turn_count, is_dup = await ingest_transcript(
                        sub_db, ws_id, turns,
                        provider=(metadata.get("entrypoint") or "claude-code"),
                        title=(metadata.get("ai_title") or "") or f"jsonl-{fp.stem[:8]}",
                        metadata=metadata,
                    )
                    await sub_db.commit()
                if is_dup:
                    stats["dup"] += 1
                else:
                    stats["new"] += 1
                    stats["turns"] += turn_count
        except Exception as exc:
            stats["error"] += 1
            logger.exception("ERROR %s: %s", fp.name, exc)

        if (i + 1) % 50 == 0 or i + 1 == len(all_files):
            elapsed = time.time() - t0
            logger.info(
                "  [%d/%d] new=%d dup=%d empty=%d turns=%d err=%d (%.1fs)",
                i + 1, len(all_files), stats["new"], stats["dup"], stats["empty"],
                stats["turns"], stats["error"], elapsed,
            )

    await engine.dispose()
    logger.info("\n=== Done ===")
    for k, v in stats.items():
        logger.info("  %s: %d", k, v)
    logger.info("  elapsed: %.1fs", time.time() - t0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-name", default="vision-test")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--base-dir",
        default=str(Path.home() / ".claude" / "projects"),
        type=Path,
    )
    args = parser.parse_args()
    asyncio.run(main(args.workspace_name, args.limit, args.base_dir))
