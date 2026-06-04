"""Sequential AI classification — process oldest unclassified episodes one by one.

Simulates real-use flow: user pushes one session at a time chronologically.
Each episode:
  1. Fetch full turns
  2. Read existing subjects in workspace
  3. claude -p classifies (full episode in context)
  4. Save via save_classify.py logic (direct, not API rate-limited)
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis",
)

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from ai_classify import (  # noqa: E402
    build_episode_prompt, call_claude_p,
    fetch_episode_data, fetch_existing_subjects,
)

WS = "5f7aa78b-48d5-41e0-8ab3-45fb4a6ea550"


async def get_pending_oldest(db: AsyncSession, limit: int) -> list[tuple[str, object, int, bool]]:
    """Episodes without classification, ordered by oldest first-turn timestamp.

    Returns (episode_id, first_ts, turn_count, is_meta) tuples.
    """
    r = await db.execute(text("""
        SELECT e.id::text, MIN(t.timestamp) AS first_ts, COUNT(t.id) AS tc,
               COALESCE((e.metadata->>'is_meta')::boolean, false) AS is_meta
        FROM episodes e
        LEFT JOIN turns t ON t.episode_id = e.id
        WHERE e.workspace_id = :ws
          AND COALESCE((e.metadata->>'is_meta')::boolean, false) = false
        GROUP BY e.id
        HAVING NOT EXISTS (
            SELECT 1 FROM turn_subjects ts
            JOIN turns tt ON tt.id = ts.turn_id
            WHERE tt.episode_id = e.id
        )
        ORDER BY first_ts ASC NULLS LAST
        LIMIT :n
    """), {"ws": WS, "n": limit})
    return [(row[0], row[1], row[2], row[3]) for row in r.fetchall()]


async def get_or_create_subject(db, name: str, parent_id, cache: dict) -> str:
    from jarvis.core.entity_resolution import normalize_name
    norm = normalize_name(name)
    if norm in cache:
        return cache[norm]
    r = await db.execute(text("""
        SELECT id::text FROM entities WHERE workspace_id = :ws AND name_normalized = :norm
    """), {"ws": WS, "norm": norm})
    row = r.fetchone()
    if row:
        cache[norm] = row[0]
        return row[0]
    new_id = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO entities (id, workspace_id, name, name_normalized, entity_type, parent_id)
        VALUES (:id, :ws, :name, :norm, 'concept', :pid)
    """), {"id": new_id, "ws": WS, "name": name, "norm": norm, "pid": parent_id})
    cache[norm] = new_id
    return new_id


async def save_classification(db: AsyncSession, episode_id: str, classification: dict, cache: dict) -> int:
    """Save subjects from classification result. Returns count of links."""
    subjects = classification.get("subjects", [])
    if not subjects:
        return 0
    # Resolve subject IDs (top-level first, then children)
    sids = []
    # Pass 1: parent=null
    for s in subjects:
        if s.get("parent") in (None, "", "null"):
            sid = await get_or_create_subject(db, s["name"], None, cache)
            sids.append(sid)
    # Pass 2: parent set
    from jarvis.core.entity_resolution import normalize_name
    for s in subjects:
        if s.get("parent") in (None, "", "null"):
            continue
        parent_norm = normalize_name(s["parent"])
        parent_id = cache.get(parent_norm)
        if not parent_id:
            # Lookup
            r = await db.execute(text("SELECT id::text FROM entities WHERE workspace_id = :ws AND name_normalized = :norm"),
                                 {"ws": WS, "norm": parent_norm})
            row = r.fetchone()
            parent_id = row[0] if row else None
        sid = await get_or_create_subject(db, s["name"], parent_id, cache)
        sids.append(sid)

    # Link all turns of episode to all resolved subjects
    if not sids:
        return 0
    subj_array = "ARRAY[" + ",".join(f"'{s}'::uuid" for s in sids) + "]"
    r = await db.execute(text(f"""
        INSERT INTO turn_subjects (turn_id, subject_id, workspace_id)
        SELECT t.id, s.subject_id, :ws
        FROM turns t
        CROSS JOIN UNNEST({subj_array}) AS s(subject_id)
        WHERE t.episode_id = :eid
        ON CONFLICT (turn_id, subject_id) DO NOTHING
        RETURNING turn_id
    """), {"ws": WS, "eid": episode_id})
    linked = len(r.fetchall())
    return linked


async def main(limit: int, dry_run: bool):
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cache: dict = {}

    async with SessionLocal() as db:
        eps = await get_pending_oldest(db, limit)
    print(f"Will classify {len(eps)} episodes (oldest first):")
    for eid, ts, tc, is_meta in eps:
        mark = " [META]" if is_meta else ""
        print(f"  {eid} [{tc:>4} turns] first_ts={ts}{mark}")
    print()

    for i, (eid, ts, tc, is_meta) in enumerate(eps):
        print(f"\n{'='*70}\n[{i+1}/{len(eps)}] {eid} ({tc} turns){' [META — skipping claude]' if is_meta else ''}")
        if is_meta:
            print(f"  is_meta=True → skip 분류 (no claude -p call)")
            continue
        async with SessionLocal() as db:
            metadata, turns = await fetch_episode_data(db, eid)
            existing = await fetch_existing_subjects(db)
        print(f"  cwd: {metadata['cwd']}")
        print(f"  existing subjects: {len(existing)}")

        t0 = time.time()
        try:
            result = call_claude_p(build_episode_prompt(metadata, turns, existing))
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue
        elapsed = time.time() - t0
        print(f"  claude -p: {elapsed:.1f}s")
        print(f"  summary: {result.get('summary')}")
        print(f"  subjects:")
        for s in result.get("subjects", []):
            p = f" (parent: {s['parent']})" if s.get("parent") else ""
            print(f"    - {s['name']}{p}")
        print(f"  keywords: {', '.join(result.get('keywords', []))}")
        print(f"  reasoning: {result.get('reasoning')}")

        if dry_run:
            print("  [DRY RUN — not saved]")
            continue

        async with SessionLocal() as db:
            linked = await save_classification(db, eid, result, cache)
            await db.commit()
        print(f"  SAVED: {linked} turn links")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(main(args.limit, args.dry_run))
