"""Bulk ingest of preprocessed session JSON files (no AI extraction).

Walks preprocessed/sessions/*.json and pushes each into JARVIS via the
ingest_transcript core function. Idempotent via content_hash dedup —
re-running skips already-ingested transcripts.

Usage:
    python scripts/ingest_preprocessed_bulk.py [--workspace-name vision-test] [--limit N]

Default workspace: 'vision-test' (created if missing). Keeps existing
'personal' / 'reseed-*' workspaces untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis",
)

# Set up path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jarvis.core.turn_ingest import ingest_preprocessed_file
from jarvis.models.tables import Workspace

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def ensure_workspace(db: AsyncSession, name: str):
    result = await db.execute(select(Workspace).where(Workspace.name == name))
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    ws = Workspace(name=name)
    db.add(ws)
    await db.flush()
    return ws


async def main(workspace_name: str, limit: int | None, sessions_dir: Path):
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    files = sorted(sessions_dir.glob("*.json"))
    if limit:
        files = files[:limit]
    logger.info("Found %d session files. Ingesting into '%s'...", len(files), workspace_name)

    stats = {"new": 0, "dup": 0, "error": 0, "turns": 0}
    t0 = time.time()

    async with SessionLocal() as db:
        ws = await ensure_workspace(db, workspace_name)
        await db.commit()

        for i, fp in enumerate(files):
            try:
                async with SessionLocal() as sub_db:
                    episode, turn_count, is_dup = await ingest_preprocessed_file(
                        sub_db, ws.id, fp,
                    )
                    await sub_db.commit()
                if is_dup:
                    stats["dup"] += 1
                else:
                    stats["new"] += 1
                    stats["turns"] += turn_count
                if (i + 1) % 10 == 0 or i + 1 == len(files):
                    elapsed = time.time() - t0
                    logger.info(
                        "  [%d/%d] new=%d dup=%d turns=%d (%.1fs)",
                        i + 1, len(files), stats["new"], stats["dup"], stats["turns"], elapsed,
                    )
            except Exception as exc:
                stats["error"] += 1
                logger.exception("  ERROR on %s: %s", fp.name, exc)

    await engine.dispose()
    logger.info("\n=== Done ===")
    logger.info("  workspace: %s", workspace_name)
    for k, v in stats.items():
        logger.info("  %s: %d", k, v)
    logger.info("  elapsed: %.1fs", time.time() - t0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-name", default="vision-test")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--sessions-dir",
        default=r"f:/brain/knowledge-extraction/preprocessed/sessions",
        type=Path,
    )
    args = parser.parse_args()
    asyncio.run(main(args.workspace_name, args.limit, args.sessions_dir))
