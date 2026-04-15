"""Background worker for processing uploaded episodes.

Single worker picks pending episodes one at a time, runs gap detection → extraction → store.
Started in server lifespan, never more than one instance.
"""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.store import store_memory
from jarvis.db import async_session_factory
from jarvis.models.tables import Entity, Episode, Fragment, KnowledgeFact
from jarvis.schemas import EntityHint, FactHint, StoreMemoryRequest

logger = logging.getLogger(__name__)

POLL_INTERVAL = 10  # seconds between checks when no pending episodes


async def process_episode(db: AsyncSession, episode: Episode) -> None:
    """Process a single episode: gap detection → extraction → store."""
    workspace_id = str(episode.workspace_id)
    transcript = episode.content

    # Get existing keywords from fragments
    frag_result = await db.execute(
        select(Fragment.keywords).where(Fragment.workspace_id == episode.workspace_id)
    )
    existing_keywords: set[str] = set()
    for row in frag_result.fetchall():
        if row[0]:
            existing_keywords.update(row[0])

    # Stage 1: Detect gaps
    from jarvis.core.gap_detection import detect_gaps

    gaps = detect_gaps(
        transcript=transcript,
        covered_turn_indices=set(),
        existing_keywords=existing_keywords,
    )

    logger.info(
        "Gap detection: episode=%s, recommendation=%s, coverage=%.2f, gaps=%d",
        episode.id, gaps.recommendation, gaps.coverage_ratio, len(gaps.gaps),
    )

    if gaps.recommendation == "skip":
        return

    # Stage 2: Extract from gaps using claude -p
    from jarvis.core.gap_extraction import extract_from_gaps

    gap_turns = [
        {"index": g.turn.turn_index, "text": g.turn.text}
        for g in gaps.gaps[:20]
    ]
    extracted = await extract_from_gaps(gap_turns, transcript)

    if not extracted:
        logger.info("Gap extraction: no facts extracted for episode=%s", episode.id)
        return

    # Stage 3: Reconcile with existing facts
    existing_facts_result = await db.execute(
        select(KnowledgeFact, Entity.name)
        .join(Entity, KnowledgeFact.entity_id == Entity.id)
        .where(
            KnowledgeFact.workspace_id == episode.workspace_id,
            KnowledgeFact.superseded_at.is_(None),
        )
    )
    existing_facts = [
        {"entity": name, "predicate": f.predicate, "object": f.object_value}
        for f, name in existing_facts_result.all()
    ]

    from jarvis.core.gap_extraction import reconcile_facts

    actions = await reconcile_facts(extracted, existing_facts)

    # Stage 4: Store ADD/UPDATE actions via store_memory pipeline
    actionable = [a for a in actions if a.get("action") in ("ADD", "UPDATE")]
    if actionable:
        store_request = StoreMemoryRequest(
            workspace_id=uuid.UUID(workspace_id),
            provider="manual",
            conversation_transcript=transcript[:10000],
            entities=[
                EntityHint(
                    name=a["entity"],
                    entity_type="concept",
                    source_quote=a.get("source_quote", ""),
                )
                for a in actionable
            ],
            facts=[
                FactHint(
                    subject=a["entity"],
                    predicate=a["predicate"],
                    object=a["object"],
                    source_quote=a.get("source_quote", ""),
                )
                for a in actionable
            ],
        )
        store_result = await store_memory(db, store_request)
        supersede_count = sum(1 for sf in store_result.facts_stored if sf.is_supersede)

        logger.info(
            "Gap extraction stored: episode=%s, facts=%d, supersedes=%d",
            episode.id, len(store_result.facts_stored), supersede_count,
        )
    else:
        logger.info("Gap reconciliation: all NOOP for episode=%s", episode.id)


async def episode_worker() -> None:
    """Background worker: pick pending episodes one at a time and process them."""
    logger.info("Episode worker started — waiting for pending episodes")

    while True:
        try:
            async with async_session_factory() as db:
                # Pick oldest pending episode
                result = await db.execute(
                    select(Episode)
                    .where(Episode.processing_status == "pending")
                    .order_by(Episode.created_at.asc())
                    .limit(1)
                )
                episode = result.scalar_one_or_none()

                if not episode:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                # Mark as processing
                episode.processing_status = "processing"
                await db.commit()

                logger.info("Worker processing: episode=%s (%d chars)", episode.id, len(episode.content))

                try:
                    await process_episode(db, episode)
                    episode.processing_status = "done"
                    logger.info("Worker done: episode=%s", episode.id)
                except Exception:
                    episode.processing_status = "failed"
                    logger.exception("Worker failed: episode=%s", episode.id)

                await db.commit()

        except asyncio.CancelledError:
            logger.info("Episode worker shutting down")
            return
        except Exception:
            logger.exception("Episode worker unexpected error — retrying in %ds", POLL_INTERVAL)
            await asyncio.sleep(POLL_INTERVAL)
