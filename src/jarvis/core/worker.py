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
from jarvis.schemas import EntityHint, FactHint, RelationHint, StoreMemoryRequest

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
        # Path B 에피소드는 store_memory 호출 기록이 없으므로 전체가 미커버.
        # set()이 정확한 동작. 추후 Path A 에피소드에서는
        # source_episode_id로 커버된 턴을 추적할 수 있음.
        covered_turn_indices=set(),
        existing_keywords=existing_keywords,
    )

    logger.info(
        "Gap detection: episode=%s, recommendation=%s, coverage=%.2f, gaps=%d",
        episode.id, gaps.recommendation, gaps.coverage_ratio, len(gaps.gaps),
    )

    if gaps.recommendation == "skip":
        return

    # Stage 2: Extract entities/facts/relations from gaps (Haiku)
    from jarvis.core.gap_extraction import extract_from_gaps, reconcile_facts

    # No hard cap on gap count — send all detected gaps.
    # Research #4 decision logic already controls volume via recommendation
    # (skip/gap_fill/full_extract) and Stage 1-4 progressive filtering.
    gap_turns = [
        {"index": g.turn.turn_index, "text": g.turn.text}
        for g in gaps.gaps
    ]

    # Fetch existing entities for canonical name consistency
    existing_entities_result = await db.execute(
        select(Entity.name, Entity.entity_type)
        .where(Entity.workspace_id == episode.workspace_id)
    )
    existing_entity_names = [
        {"name": name, "type": etype.value}
        for name, etype in existing_entities_result.all()
    ]

    extraction = await extract_from_gaps(
        gap_turns, transcript, existing_entities=existing_entity_names,
    )

    if not extraction.facts and not extraction.entities:
        logger.info("Gap extraction: nothing extracted for episode=%s", episode.id)
        return

    logger.info(
        "Gap extraction: episode=%s, entities=%d, facts=%d, relations=%d",
        episode.id, len(extraction.entities), len(extraction.facts), len(extraction.relations),
    )

    # Stage 3: Reconcile facts against existing memories (Sonnet)
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

    actions = await reconcile_facts(extraction.facts, existing_facts)

    # Stage 4: Store ADD/UPDATE actions via store_memory pipeline
    actionable = [a for a in actions if a.get("action") in ("ADD", "UPDATE")]
    if actionable:
        # entity_type lookup: reconcile_facts returns no entity_type,
        # so we look it up from extraction.entities
        entity_type_map = {e.name: e.entity_type for e in extraction.entities}

        # Merge entities: facts' subjects + extraction entities not in facts
        entity_names_from_actions = {a["entity"] for a in actionable}
        action_entities = [
            EntityHint(
                name=a["entity"],
                entity_type=entity_type_map.get(a["entity"], "concept"),
                source_quote=a.get("source_quote", ""),
            )
            for a in actionable
        ]
        extra_entities = [
            EntityHint(
                name=e.name,
                entity_type=e.entity_type,
                source_quote=e.source_quote,
            )
            for e in extraction.entities
            if e.name not in entity_names_from_actions
        ]

        store_request = StoreMemoryRequest(
            workspace_id=uuid.UUID(workspace_id),
            provider="manual",
            conversation_transcript=transcript,
            entities=action_entities + extra_entities,
            facts=[
                FactHint(
                    subject=a["entity"],
                    predicate=a["predicate"],
                    object=a["object"],
                    source_quote=a.get("source_quote", ""),
                )
                for a in actionable
            ],
            # Relations는 reconcile 없이 전부 ADD.
            # 리서치 #4에서 reconciliation은 facts 전용.
            # relation 중복은 store.py의 _store_relation()에서 처리됨.
            relations=[
                RelationHint(
                    from_entity=r.from_entity,
                    to_entity=r.to_entity,
                    relation_type=r.relation_type,
                    source_quote=r.source_quote,
                )
                for r in extraction.relations
            ],
        )
        store_result = await store_memory(db, store_request)
        supersede_count = sum(1 for sf in store_result.facts_stored if sf.is_supersede)

        logger.info(
            "Gap extraction stored: episode=%s, facts=%d, supersedes=%d, relations=%d",
            episode.id, len(store_result.facts_stored), supersede_count, len(extraction.relations),
        )
    else:
        logger.info("Gap reconciliation: all NOOP for episode=%s", episode.id)


async def _reindex_hnsw() -> None:
    """Rebuild HNSW indexes after batch embedding insertions.

    Uses REINDEX INDEX CONCURRENTLY to avoid blocking concurrent reads.
    Requires raw connection with AUTOCOMMIT — REINDEX CONCURRENTLY cannot
    run inside a transaction block.
    """
    from sqlalchemy import text as sa_text

    from jarvis.db import engine as async_engine

    for index_name in ("ix_embedding_vector_hnsw", "ix_entity_name_embedding_hnsw"):
        try:
            async with async_engine.connect() as conn:
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                await conn.execute(sa_text(f"REINDEX INDEX CONCURRENTLY {index_name}"))  # noqa: S608
            logger.info("HNSW reindex complete: %s", index_name)
        except Exception:
            logger.exception("HNSW reindex failed: %s", index_name)


async def episode_worker() -> None:
    """Background worker: pick pending episodes one at a time and process them."""
    logger.info("Episode worker started — waiting for pending episodes")
    processed_since_last_reindex = False

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
                    if processed_since_last_reindex:
                        await _reindex_hnsw()
                        processed_since_last_reindex = False
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                # Mark as processing
                episode.processing_status = "processing"
                await db.commit()

                logger.info("Worker processing: episode=%s (%d chars)", episode.id, len(episode.content))

                try:
                    await process_episode(db, episode)
                    episode.processing_status = "done"
                    processed_since_last_reindex = True
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
