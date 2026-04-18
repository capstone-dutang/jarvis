"""Re-seed JARVIS from pre-extracted JSON (no claude -p, $0 cost).

Reads preprocessed/extracted/sessions/*.json (already-extracted entities/facts/relations)
and preprocessed/sessions/*.json (raw transcripts), then replays the seed flow
through store.py so the NEW dedup + fact_episodes M:N path is exercised.

Target workspace is created fresh ('reseed-test'). Existing data is untouched.

Reports at the end:
  - entities created / deduped via resolve_entity
  - facts created / deduped via new store_fact logic
  - fact_episodes link distribution (how many facts have episode_count > 1)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from collections import Counter
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis",
)

from jarvis.core.store import create_episode, resolve_entity, store_fact, _store_relation
from jarvis.models.tables import Entity, Episode, FactEpisode, KnowledgeFact, Session, Workspace
from jarvis.schemas import EntityHint, FactHint, RelationHint

ROOT = Path(r"f:/brain/knowledge-extraction/preprocessed")
EXT_DIR = ROOT / os.environ.get("RESEED_EXT_DIR", "extracted") / "sessions"
RAW_DIR = ROOT / "sessions"
TARGET_WS_NAME = os.environ.get("RESEED_WS_NAME", "reseed-test")


def build_transcript(raw: dict) -> str:
    parts = []
    for turn in raw.get("turns", []):
        role = turn.get("role", "?")
        text_ = turn.get("text", "")
        parts.append(f"[{role}] {text_}")
    return "\n\n".join(parts)


async def ensure_test_workspace(db: AsyncSession) -> uuid.UUID:
    result = await db.execute(select(Workspace).where(Workspace.name == TARGET_WS_NAME))
    existing = result.scalar_one_or_none()
    if existing:
        await db.execute(delete(Workspace).where(Workspace.id == existing.id))
        await db.flush()

    ws = Workspace(name=TARGET_WS_NAME)
    db.add(ws)
    await db.flush()
    return ws.id


async def seed_session(db: AsyncSession, ws_id: uuid.UUID, ext: dict, raw: dict) -> dict:
    transcript = build_transcript(raw)
    extraction = ext.get("extraction", {})

    sess = Session(workspace_id=ws_id, provider="reseed", client_type="reseed")
    db.add(sess)
    await db.flush()

    episode = await create_episode(
        db, sess, ws_id, transcript,
        summary=ext.get("ai_title", "") or transcript[:200],
        provider="reseed",
    )

    stats = Counter()
    entity_map: dict[str, Entity] = {}

    for e in extraction.get("entities", []):
        etype = e.get("entity_type", "other")
        # Remap seeder-only types to core enum set
        etype = {
            "technology": "concept", "project": "concept", "resource": "concept",
            "person": "person", "organization": "organization",
            "location": "location", "event": "event", "concept": "concept",
            "preference": "preference", "procedure": "procedure",
        }.get(etype, "other")
        hint = EntityHint(name=e["name"], entity_type=etype, source_quote=e.get("source_quote", ""))
        try:
            ent, is_new = await resolve_entity(db, ws_id, hint)
            entity_map[e["name"]] = ent
            stats["entity_new" if is_new else "entity_dedup"] += 1
        except Exception as exc:
            stats["entity_error"] += 1
            print(f"   entity_error {e['name']}: {exc}")

    for f in extraction.get("facts", []):
        subject = f["subject"]
        if subject not in entity_map:
            ent, is_new = await resolve_entity(
                db, ws_id,
                EntityHint(name=subject, entity_type="other", source_quote=f.get("source_quote", "")),
            )
            entity_map[subject] = ent
            stats["entity_new" if is_new else "entity_dedup"] += 1
        try:
            resp = await store_fact(
                db, ws_id, entity_map[subject],
                FactHint(
                    subject=subject,
                    predicate=f["predicate"],
                    object=f["object"],
                    source_quote=f.get("source_quote", ""),
                ),
                episode, transcript,
            )
            if resp.is_supersede:
                stats["fact_supersede"] += 1
            else:
                stats["fact_stored"] += 1
        except Exception as exc:
            stats["fact_error"] += 1
            print(f"   fact_error {subject}.{f.get('predicate')}: {exc}")

    for r in extraction.get("relations", []):
        try:
            await _store_relation(
                db, ws_id,
                RelationHint(
                    from_entity=r.get("from_entity") or r.get("from"),
                    to_entity=r.get("to_entity") or r.get("to"),
                    relation_type=r.get("relation_type", "related_to"),
                    source_quote=r.get("source_quote", ""),
                ),
                entity_map, episode,
            )
            stats["relation_stored"] += 1
        except Exception as exc:
            stats["relation_error"] += 1

    return dict(stats)


async def main() -> None:
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    files = sorted(EXT_DIR.glob("*.json"))
    print(f"Processing {len(files)} sessions...\n")

    async with SessionLocal() as db:
        ws_id = await ensure_test_workspace(db)
        print(f"Workspace: {TARGET_WS_NAME} ({ws_id})\n")

        total = Counter()
        for ext_file in files:
            with open(ext_file, encoding="utf-8") as fp:
                ext = json.load(fp)
            raw_file = RAW_DIR / ext_file.name
            if not raw_file.exists():
                print(f"  SKIP {ext_file.name}: no raw transcript")
                continue
            with open(raw_file, encoding="utf-8") as fp:
                raw = json.load(fp)

            print(f"  {ext_file.name}...", end="", flush=True)
            stats = await seed_session(db, ws_id, ext, raw)
            print(f" {dict(stats)}")
            for k, v in stats.items():
                total[k] += v

        await db.commit()

        # Summary
        print(f"\n=== Totals ===")
        for k in sorted(total.keys()):
            print(f"  {k}: {total[k]}")

        # fact_episodes link distribution
        result = await db.execute(text("""
            SELECT fact_id, COUNT(*) AS ep_count
            FROM fact_episodes
            WHERE fact_id IN (SELECT id FROM knowledge_facts WHERE workspace_id = :ws)
            GROUP BY fact_id
        """), {"ws": str(ws_id)})
        rows = result.fetchall()
        ep_counts = Counter(r[1] for r in rows)
        print(f"\n=== fact_episodes link distribution ===")
        print(f"  total distinct facts with links: {len(rows)}")
        for c in sorted(ep_counts.keys()):
            print(f"  facts linked to {c} episode(s): {ep_counts[c]}")

        # Top 5 most-referenced facts
        print(f"\n=== Top 5 most-reinforced facts ===")
        result = await db.execute(text("""
            SELECT kf.id, e.name, kf.predicate, kf.object_value, COUNT(fe.*) AS cnt
            FROM knowledge_facts kf
            JOIN entities e ON e.id = kf.entity_id
            JOIN fact_episodes fe ON fe.fact_id = kf.id
            WHERE kf.workspace_id = :ws
            GROUP BY kf.id, e.name, kf.predicate, kf.object_value
            ORDER BY cnt DESC
            LIMIT 5
        """), {"ws": str(ws_id)})
        for row in result.fetchall():
            obj = row[3][:50]
            print(f"  [{row[4]}x] {row[1]}.{row[2]} = '{obj}'")

        # Distinct facts vs total fact stores
        result = await db.execute(text("SELECT COUNT(*) FROM knowledge_facts WHERE workspace_id = :ws"), {"ws": str(ws_id)})
        fact_rows = result.scalar_one()
        result = await db.execute(text("""
            SELECT COUNT(*) FROM fact_episodes
            WHERE fact_id IN (SELECT id FROM knowledge_facts WHERE workspace_id = :ws)
        """), {"ws": str(ws_id)})
        link_count = result.scalar_one()
        print(f"\n=== Dedup effect ===")
        print(f"  knowledge_facts rows: {fact_rows}")
        print(f"  fact_episodes links:  {link_count}")
        if fact_rows:
            ratio = link_count / fact_rows
            saved = link_count - fact_rows
            print(f"  avg links per fact:   {ratio:.2f}")
            print(f"  facts saved via dedup: {saved}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
