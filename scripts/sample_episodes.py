"""Pick diverse sample episodes and print their first turns for classification."""

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

WS = "5f7aa78b-48d5-41e0-8ab3-45fb4a6ea550"

PATTERNS = [
    ("fundmessenger backend", r"F:\fundmessenger\backend"),
    ("brain (JARVIS 추정)", r"f:\brain"),
    ("brain/jarvis/frontend", r"F:\brain\jarvis\frontend"),
    ("자료구조", r"C:\Users\lhhh0\OneDrive\Desktop\자료구조"),
    ("newsdesk", r"F:\newsdesk"),
]


async def main():
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as db:
        for label, cwd_pattern in PATTERNS:
            r = await db.execute(text("""
                SELECT e.id, e.metadata->>'ai_title' AS title, e.metadata->>'cwd' AS cwd,
                       (SELECT COUNT(*) FROM turns t WHERE t.episode_id = e.id) AS tc
                FROM episodes e
                WHERE e.workspace_id = :ws
                  AND e.metadata->>'cwd' = :cwd
                ORDER BY tc DESC
                LIMIT 1
            """), {"ws": WS, "cwd": cwd_pattern})
            row = r.fetchone()
            if row:
                print(f"=== {label} ===")
                print(f"  ep_id: {row[0]}")
                print(f"  title: {row[1]}")
                print(f"  cwd:   {row[2]}")
                print(f"  turns: {row[3]}")
                r2 = await db.execute(text("""
                    SELECT role, text FROM turns WHERE episode_id = :eid
                    ORDER BY sequence LIMIT 4
                """), {"eid": str(row[0])})
                print("  first turns:")
                for t in r2.fetchall():
                    txt = t[1][:160].replace("\n", " ")
                    print(f"    [{t[0]}] {txt}")
                print()
    await engine.dispose()


asyncio.run(main())
