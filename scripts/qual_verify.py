"""Qualitative verification — pick episodes, fetch turns AND classification, output side-by-side for human review.

The classifier's output (summary/subjects/keywords) needs to be compared
against actual turn content. Heuristic checks don't catch hallucinations.
"""

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


async def show(db, eid: str):
    r = await db.execute(text("""
        SELECT e.metadata->>'cwd', e.metadata->>'ai_title',
               (SELECT COUNT(*) FROM turns t WHERE t.episode_id = e.id) AS tc
        FROM episodes e WHERE e.id = :eid
    """), {"eid": eid})
    row = r.fetchone()
    if not row:
        return
    cwd, title, tc = row
    print(f"\n{'='*70}")
    print(f"episode_id: {eid}")
    print(f"cwd: {cwd}")
    print(f"title: {title}")
    print(f"turn_count: {tc}")

    # Subjects this episode is linked to
    r2 = await db.execute(text("""
        SELECT DISTINCT e.name, parent.name AS parent_name
        FROM turn_subjects ts
        JOIN turns t ON t.id = ts.turn_id
        JOIN entities e ON e.id = ts.subject_id
        LEFT JOIN entities parent ON parent.id = e.parent_id
        WHERE t.episode_id = :eid
        ORDER BY parent_name NULLS FIRST, e.name
    """), {"eid": eid})
    print("\n분류된 subjects:")
    for row in r2.fetchall():
        p = f" (parent: {row[1]})" if row[1] else " (top-level)"
        print(f"  - {row[0]}{p}")

    # Show first 8 + middle 3 + last 5 turns for human review
    r3 = await db.execute(text("""
        SELECT sequence, role, text FROM turns
        WHERE episode_id = :eid ORDER BY sequence
    """), {"eid": eid})
    all_turns = r3.fetchall()

    if tc <= 16:
        # Show all
        sample = all_turns
        print(f"\n--- 모든 {len(sample)} turns ---")
    else:
        # First 5, middle 3, last 5
        mid = tc // 2 - 1
        sample = list(all_turns[:5]) + list(all_turns[mid:mid+3]) + list(all_turns[-5:])
        print(f"\n--- 샘플 (first 5 + middle 3 + last 5) ---")

    for t in sample:
        text_clip = t[2][:400].replace("\n", " ")
        print(f"  [seq {t[0]} {t[1]}] {text_clip}")


async def main():
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as db:
        # Strategy: pick variety
        # 1. One short episode (3-5 turns)
        # 2. One mid episode (30-60 turns)
        # 3. One long episode (100+ turns)
        # 4. One multi-subject episode (3+ subjects)
        # 5. One zero-subject (SAVED:0) — was it really meta?
        r = await db.execute(text("""
            WITH classified AS (
                SELECT DISTINCT e.id, COUNT(t.id) AS tc,
                       (SELECT COUNT(DISTINCT ts.subject_id)
                        FROM turn_subjects ts JOIN turns tt ON tt.id = ts.turn_id
                        WHERE tt.episode_id = e.id) AS subj_cnt
                FROM episodes e
                LEFT JOIN turns t ON t.episode_id = e.id
                WHERE e.workspace_id = :ws
                GROUP BY e.id
            )
            -- Picks
            (SELECT id::text FROM classified WHERE tc BETWEEN 3 AND 7 AND subj_cnt > 0 ORDER BY random() LIMIT 1)
            UNION ALL
            (SELECT id::text FROM classified WHERE tc BETWEEN 30 AND 60 AND subj_cnt > 0 ORDER BY random() LIMIT 1)
            UNION ALL
            (SELECT id::text FROM classified WHERE tc > 100 AND subj_cnt > 0 ORDER BY random() LIMIT 1)
            UNION ALL
            (SELECT id::text FROM classified WHERE subj_cnt >= 4 ORDER BY random() LIMIT 1)
            UNION ALL
            (SELECT id::text FROM classified WHERE subj_cnt = 0 AND tc > 5 ORDER BY random() LIMIT 1)
        """), {"ws": WS})
        eids = [row[0] for row in r.fetchall()]

    for eid in eids:
        async with SessionLocal() as db:
            await show(db, eid)
    await engine.dispose()


asyncio.run(main())
