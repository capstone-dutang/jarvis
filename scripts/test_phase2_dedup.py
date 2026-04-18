"""Quick smoke test for Phase 2 dedup logic.

Scenario: twice store the same (entity, predicate, object_value) across 2 distinct
episodes. Expectation:
  - After store #1: 1 fact row + 1 fact_episodes link
  - After store #2: still 1 fact row, but 2 fact_episodes links (role='reinforcing')
  - Storing a different object_value for same (entity, predicate) → supersede

Runs against the dev DB directly. Cleans up test rows at the end.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis",
)

from jarvis.core.store import store_fact
from jarvis.models.tables import Entity, EntityType, Episode, KnowledgeFact, Session
from jarvis.schemas import FactHint


async def main() -> None:
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    ws_id = uuid.UUID("2d92735f-c858-4398-b4dd-d28423208e17")  # 'personal'

    async with SessionLocal() as db:
        sess = Session(
            id=uuid.uuid4(), workspace_id=ws_id,
            provider="phase2-dedup-test", client_type="test",
        )
        db.add(sess)
        await db.flush()

        # Create two test episodes
        ep1 = Episode(
            id=uuid.uuid4(), workspace_id=ws_id, session_id=sess.id,
            content="테스트 에피소드 1: 내가 SecondBrain을 Argos보다 먼저 택했다.",
            content_hash=f"test_ep1_{uuid.uuid4().hex[:8]}",
        )
        ep2 = Episode(
            id=uuid.uuid4(), workspace_id=ws_id, session_id=sess.id,
            content="테스트 에피소드 2: 다시 말하지만 SecondBrain이 Argos보다 우선순위다.",
            content_hash=f"test_ep2_{uuid.uuid4().hex[:8]}",
        )
        db.add(ep1)
        db.add(ep2)
        await db.flush()

        # Create a throwaway test entity
        name = f"TestDedupEntity_{uuid.uuid4().hex[:6]}"
        ent = Entity(
            id=uuid.uuid4(), workspace_id=ws_id,
            name=name, name_normalized=name.lower(),
            entity_type=EntityType.concept,
        )
        db.add(ent)
        await db.flush()

        # Store #1
        hint = FactHint(
            subject=ent.name, predicate="TEST_PREDICATE", object="valueA",
            source_quote="테스트 에피소드 1: 내가 SecondBrain을 Argos보다 먼저 택했다.",
        )
        r1 = await store_fact(db, ws_id, ent, hint, ep1, ep1.content)
        await db.flush()

        # Store #2 (same fact, different episode)
        r2 = await store_fact(db, ws_id, ent, hint, ep2, ep2.content)
        await db.flush()

        # Store #3 (same entity+predicate, different object → supersede)
        hint2 = FactHint(
            subject=ent.name, predicate="TEST_PREDICATE", object="valueB",
            source_quote="테스트 에피소드 2: 다시 말하지만 SecondBrain이 Argos보다 우선순위다.",
        )
        r3 = await store_fact(db, ws_id, ent, hint2, ep2, ep2.content)
        await db.flush()

        print(f"r1.fact_id: {r1.fact_id}")
        print(f"r2.fact_id: {r2.fact_id}  (same as r1? {r1.fact_id == r2.fact_id})")
        print(f"r3.fact_id: {r3.fact_id}  (supersede: {r3.is_supersede})")

        # Count links for r1's fact
        result = await db.execute(
            text("SELECT fact_id, episode_id, role FROM fact_episodes WHERE fact_id = :fid ORDER BY created_at"),
            {"fid": str(r1.fact_id)},
        )
        print("fact_episodes links for r1.fact_id:")
        for row in result.fetchall():
            print(f"  {row[0]}  ep={row[1]}  role={row[2]}")

        # Count total facts for this entity
        result = await db.execute(
            text("SELECT id, object_value, superseded_at FROM knowledge_facts WHERE entity_id = :eid ORDER BY valid_from"),
            {"eid": str(ent.id)},
        )
        print(f"knowledge_facts for test entity:")
        for row in result.fetchall():
            print(f"  {row[0]}  '{row[1]}'  superseded={row[2] is not None}")

        # Cleanup — CASCADE from sessions wipes episodes/facts/links/fragments
        await db.execute(text("DELETE FROM entities WHERE id = :eid"), {"eid": str(ent.id)})
        await db.execute(text("DELETE FROM sessions WHERE id = :sid"), {"sid": str(sess.id)})
        await db.commit()
        print("\nCleanup complete.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
