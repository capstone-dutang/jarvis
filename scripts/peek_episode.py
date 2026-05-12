"""Print episode metadata + sample turns for classification by AI agents.

Usage:
    python scripts/peek_episode.py <episode_id>
    python scripts/peek_episode.py <episode_id> --sample-size 10
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis",
)

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


async def main(episode_id: str, sample_size: int = 6):
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as db:
        r = await db.execute(text("""
            SELECT e.metadata->>'ai_title' AS title,
                   e.metadata->>'cwd' AS cwd,
                   e.metadata->>'git_branch' AS branch,
                   e.metadata->>'entrypoint' AS entry,
                   (SELECT COUNT(*) FROM turns t WHERE t.episode_id = e.id) AS tc,
                   e.created_at
            FROM episodes e WHERE e.id = :eid
        """), {"eid": episode_id})
        row = r.fetchone()
        if not row:
            print(f"NOT_FOUND")
            return
        print(f"episode_id: {episode_id}")
        print(f"title: {row[0]}")
        print(f"cwd: {row[1]}")
        print(f"git_branch: {row[2]}")
        print(f"entrypoint: {row[3]}")
        print(f"turn_count: {row[4]}")
        print(f"created_at: {row[5]}")
        print()

        # First N turns
        r2 = await db.execute(text("""
            SELECT sequence, role, text FROM turns
            WHERE episode_id = :eid ORDER BY sequence LIMIT :n
        """), {"eid": episode_id, "n": sample_size})
        print(f"=== first {sample_size} turns ===")
        for t in r2.fetchall():
            txt = t[2][:400].replace("\n", " ")
            print(f"  [seq {t[0]} {t[1]}] {txt}")

        # Middle sample
        if row[4] > sample_size * 2:
            r3 = await db.execute(text("""
                SELECT sequence, role, text FROM turns
                WHERE episode_id = :eid ORDER BY sequence
                OFFSET :off LIMIT 3
            """), {"eid": episode_id, "off": row[4] // 2 - 1})
            print(f"\n=== middle 3 turns (offset {row[4]//2 - 1}) ===")
            for t in r3.fetchall():
                txt = t[2][:400].replace("\n", " ")
                print(f"  [seq {t[0]} {t[1]}] {txt}")

        # Last N turns
        if row[4] > sample_size:
            r4 = await db.execute(text("""
                SELECT sequence, role, text FROM turns
                WHERE episode_id = :eid ORDER BY sequence DESC LIMIT :n
            """), {"eid": episode_id, "n": sample_size})
            print(f"\n=== last {sample_size} turns ===")
            for t in reversed(r4.fetchall()):
                txt = t[2][:400].replace("\n", " ")
                print(f"  [seq {t[0]} {t[1]}] {txt}")
    await engine.dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("episode_id")
    p.add_argument("--sample-size", type=int, default=6)
    args = p.parse_args()
    asyncio.run(main(args.episode_id, args.sample_size))
