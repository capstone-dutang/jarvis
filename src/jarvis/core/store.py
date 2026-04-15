"""store_memory pipeline: validate → episode → entities → facts → async embedding."""

import asyncio
import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from jarvis.core.entity_resolution import compute_fuzzy_ratio, hybrid_score, is_cross_lingual, normalize_name
from jarvis.core.quote_verification import verify_quote
from jarvis.models.tables import (
    Embedding,
    Entity,
    EntityType,
    Episode,
    Fragment,
    FragmentType,
    KnowledgeFact,
    Session,
    TrustLevel,
)
from jarvis.schemas import (
    EntityHint,
    FactHint,
    StoredFactResponse,
    StoreMemoryRequest,
    StoreMemoryResponse,
)

logger = logging.getLogger(__name__)


async def get_or_create_session(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID | None,
    provider: str,
) -> Session:
    """Get existing session or create a new one."""
    if session_id:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if session:
            session.last_active_at = func.now()
            return session

    session = Session(
        workspace_id=workspace_id,
        provider=provider,
        client_type=provider,
    )
    db.add(session)
    await db.flush()
    return session


async def create_episode(
    db: AsyncSession,
    session: Session,
    workspace_id: uuid.UUID,
    transcript: str,
    summary: str,
    provider: str,
) -> Episode:
    """Create immutable episode from conversation transcript.

    Raw transcript is stored as-is in content. Normalized canonical format
    is stored in metadata_ for structured access.
    Based on: JARVIS_DEFINITIVE.md Section 16
    """
    from jarvis.core.normalization import normalize_transcript

    normalized = normalize_transcript(provider, transcript)

    episode = Episode(
        session_id=session.id,
        workspace_id=workspace_id,
        content=transcript,
        summary=summary,
        metadata_=normalized.model_dump(),
    )
    db.add(episode)
    await db.flush()
    return episode


async def resolve_entity(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    hint: EntityHint,
) -> tuple[Entity, bool]:
    """Resolve entity hint to existing or new entity.

    Returns (entity, is_new).
    3-stage pipeline based on: research/multilingual-kg lines 48-93

    Stage 1: Normalize + alias lookup
    Stage 2: pgvector cosine > 0.75 top-10 candidates (on Entity.name_embedding)
    Stage 3: Hybrid scoring (fuzzy + cosine) on candidates only
    """
    normalized = normalize_name(hint.name)

    # Stage 1: Exact normalized match
    result = await db.execute(
        select(Entity).where(
            Entity.workspace_id == workspace_id,
            Entity.name_normalized == normalized,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing, False

    # Stage 2: Embedding candidate retrieval via pgvector
    # Based on: research/multilingual-kg lines 66-76
    candidates: list[tuple[Entity, float]] = []
    try:
        from jarvis.core.embedding import embed_text

        name_vec = embed_text(normalized)
        result = await db.execute(
            text("""
                SELECT id, name, name_normalized,
                       1 - (name_embedding <=> cast(:vec as vector)) AS cos_sim
                FROM entities
                WHERE workspace_id = :ws
                  AND name_embedding IS NOT NULL
                  AND 1 - (name_embedding <=> cast(:vec as vector)) > 0.75
                ORDER BY name_embedding <=> cast(:vec as vector)
                LIMIT 10
            """),
            {"ws": str(workspace_id), "vec": str(name_vec)},
        )
        rows = result.fetchall()
        for row in rows:
            entity_result = await db.execute(select(Entity).where(Entity.id == row[0]))
            entity = entity_result.scalar_one_or_none()
            if entity:
                candidates.append((entity, float(row[3])))
    except Exception:
        # Fallback: if embedding not available, load all entities (Phase 1 compat)
        result = await db.execute(select(Entity).where(Entity.workspace_id == workspace_id))
        all_entities = result.scalars().all()
        candidates = [(e, 0.0) for e in all_entities]

    # Stage 3: Hybrid scoring on candidates
    best_match: Entity | None = None
    best_score = 0.0

    for candidate, cosine_sim in candidates:
        fuzzy_ratio = compute_fuzzy_ratio(normalized, candidate.name_normalized)
        cross = is_cross_lingual(normalized, candidate.name_normalized)
        score = hybrid_score(fuzzy_ratio, cosine_sim, cross)

        if score > best_score:
            best_score = score
            best_match = candidate

    # Threshold decisions
    if best_match and best_score >= 0.92:
        return best_match, False

    # Create new entity
    try:
        entity_type = EntityType(hint.entity_type)
    except ValueError:
        entity_type = EntityType.other

    entity = Entity(
        workspace_id=workspace_id,
        name=hint.name,
        name_normalized=normalized,
        entity_type=entity_type,
    )
    db.add(entity)
    await db.flush()
    return entity, True


async def _resolve_predicate(
    db: AsyncSession,
    entity_id: uuid.UUID,
    predicate: str,
) -> str:
    """Resolve predicate to existing one if semantically similar.

    Same pattern as entity resolution but for predicates:
    1. Exact match → use as-is
    2. Embedding similarity → if cosine > 0.85, use existing predicate
    3. No match → use the new predicate as-is

    This prevents supersede failures when AI uses "나이" once and "age" next time.
    """
    # Get all active predicates for this entity
    result = await db.execute(
        select(KnowledgeFact.predicate)
        .where(
            KnowledgeFact.entity_id == entity_id,
            KnowledgeFact.superseded_at.is_(None),
        )
        .distinct()
    )
    existing_predicates = [row[0] for row in result.fetchall()]

    if not existing_predicates:
        return predicate

    # Exact match
    if predicate in existing_predicates:
        return predicate

    # Fuzzy + embedding similarity
    best_match = predicate
    best_score = 0.0

    try:
        from jarvis.core.embedding import embed_text

        pred_vec = embed_text(predicate)

        for existing_pred in existing_predicates:
            existing_vec = embed_text(existing_pred)
            # Cosine similarity
            import numpy as np

            cos_sim = float(np.dot(pred_vec, existing_vec))

            # Also check fuzzy string similarity
            fuzzy = compute_fuzzy_ratio(predicate, existing_pred) / 100.0

            # Combined score (weighted toward embedding for semantic matching)
            score = 0.3 * fuzzy + 0.7 * cos_sim

            if score > best_score:
                best_score = score
                best_match = existing_pred
    except Exception:
        # Embedding not available — fuzzy only
        for existing_pred in existing_predicates:
            fuzzy = compute_fuzzy_ratio(predicate, existing_pred) / 100.0
            if fuzzy > best_score:
                best_score = fuzzy
                best_match = existing_pred

    # Threshold: if score > 0.85, treat as same predicate
    if best_score >= 0.85:
        logger.info("Predicate resolved: '%s' → '%s' (score=%.3f)", predicate, best_match, best_score)
        return best_match

    return predicate


def _classify_fragment_type(predicate: str) -> FragmentType:
    """Classify fragment type from predicate text."""
    pred_lower = predicate.lower()
    if any(kw in pred_lower for kw in ("prefer", "선호", "like", "dislike", "좋아", "싫어", "sentiment")):
        return FragmentType.preference
    if any(kw in pred_lower for kw in ("decide", "chose", "결정", "선택", "switch", "change")):
        return FragmentType.decision
    if any(kw in pred_lower for kw in ("error", "bug", "fail", "에러", "버그", "실패")):
        return FragmentType.error
    if any(kw in pred_lower for kw in ("step", "process", "how_to", "방법", "절차", "procedure")):
        return FragmentType.procedure
    if any(kw in pred_lower for kw in ("relates", "depends", "uses", "관계", "의존", "사용")):
        return FragmentType.relation
    return FragmentType.fact


async def _check_nli_contradictions(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    entity: Entity,
    new_fact: KnowledgeFact,
    resolved_predicate: str,
) -> None:
    """Check new fact against existing facts of the same entity using NLI.

    Only runs for facts with DIFFERENT predicates (same predicate already handled by supersede).
    Uses entity-blocking: only compare facts sharing the same entity.
    """
    try:
        from jarvis.core.embedding import embed_text
        from jarvis.core.nli_detection import ConflictType, detect_contradictions

        # Get active facts for same entity, different predicate
        result = await db.execute(
            select(KnowledgeFact).where(
                KnowledgeFact.entity_id == entity.id,
                KnowledgeFact.predicate != resolved_predicate,
                KnowledgeFact.superseded_at.is_(None),
                KnowledgeFact.id != new_fact.id,
            )
        )
        existing_facts = result.scalars().all()

        if not existing_facts:
            return

        # Build fact texts and compute cosine similarities
        new_text = f"{entity.name} {new_fact.predicate} {new_fact.object_value}"
        new_vec = embed_text(new_text)

        import numpy as np

        candidates: list[tuple[str, float]] = []
        candidate_facts: list[KnowledgeFact] = []

        for ef in existing_facts:
            ef_text = f"{entity.name} {ef.predicate} {ef.object_value}"
            ef_vec = embed_text(ef_text)
            cos_sim = float(np.dot(new_vec, ef_vec))
            # Only consider facts with some semantic overlap
            if cos_sim > 0.40:
                candidates.append((ef_text, cos_sim))
                candidate_facts.append(ef)

        if not candidates:
            return

        # Run NLI detection
        nli_results = detect_contradictions(new_text, candidates)

        for nli_result in nli_results:
            matching_fact = next(
                (cf for cf, (ct, _) in zip(candidate_facts, candidates, strict=True) if ct == nli_result.existing_fact_text),
                None,
            )
            if not matching_fact:
                continue

            if nli_result.conflict_type == ConflictType.contradiction_auto:
                # Auto supersede the contradicted fact
                matching_fact.superseded_at = func.now()
                matching_fact.valid_to = func.now()
                logger.info(
                    "NLI auto-supersede: '%s %s %s' contradicts '%s %s %s' (score=%.3f)",
                    entity.name, new_fact.predicate, new_fact.object_value,
                    entity.name, matching_fact.predicate, matching_fact.object_value,
                    nli_result.contradiction,
                )

            elif nli_result.conflict_type == ConflictType.contradiction_review:
                logger.warning(
                    "NLI review needed: '%s %s %s' may contradict '%s %s %s' (score=%.3f)",
                    entity.name, new_fact.predicate, new_fact.object_value,
                    entity.name, matching_fact.predicate, matching_fact.object_value,
                    nli_result.contradiction,
                )

            elif nli_result.conflict_type == ConflictType.duplicate:
                logger.info(
                    "NLI duplicate detected: '%s %s %s' ≈ '%s %s %s'",
                    entity.name, new_fact.predicate, new_fact.object_value,
                    entity.name, matching_fact.predicate, matching_fact.object_value,
                )

            elif nli_result.conflict_type == ConflictType.refinement:
                logger.info(
                    "NLI refinement: '%s %s %s' refines '%s %s %s'",
                    entity.name, new_fact.predicate, new_fact.object_value,
                    entity.name, matching_fact.predicate, matching_fact.object_value,
                )

    except ImportError:
        logger.debug("NLI or embedding not available, skipping contradiction check")
    except Exception:
        logger.exception("NLI contradiction check failed")


async def store_fact(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    entity: Entity,
    fact_hint: FactHint,
    episode: Episode,
    transcript: str,
) -> StoredFactResponse:
    """Store a knowledge fact with bitemporal supersede logic."""
    # Check source_quote grounding
    is_grounded = verify_quote(fact_hint.source_quote, transcript)
    trust = TrustLevel.grounded if is_grounded else TrustLevel.low_trust

    # Resolve predicate: "나이" and "age" → same predicate if semantically similar
    resolved_predicate = await _resolve_predicate(db, entity.id, fact_hint.predicate)

    # Supersede: find existing active fact with same entity + resolved predicate
    result = await db.execute(
        select(KnowledgeFact).where(
            KnowledgeFact.entity_id == entity.id,
            KnowledgeFact.predicate == resolved_predicate,
            KnowledgeFact.superseded_at.is_(None),
        )
    )
    existing = result.scalar_one_or_none()
    is_supersede = False

    if existing:
        existing.superseded_at = func.now()
        existing.valid_to = func.now()
        is_supersede = True

    new_fact = KnowledgeFact(
        workspace_id=workspace_id,
        entity_id=entity.id,
        predicate=resolved_predicate,
        object_value=fact_hint.object,
        source_episode_id=episode.id,
        source_quote=fact_hint.source_quote,
        trust_level=trust,
    )
    db.add(new_fact)
    await db.flush()

    # Create Fragment (dual store: KnowledgeFact + Fragment)
    fragment_content = f"{entity.name} {resolved_predicate} {fact_hint.object}"
    if len(fragment_content) > 500:
        fragment_content = fragment_content[:497] + "..."

    fragment = Fragment(
        workspace_id=workspace_id,
        content=fragment_content,
        fragment_type=_classify_fragment_type(resolved_predicate),
        keywords=[entity.name, resolved_predicate],
        importance=0.7 if trust == TrustLevel.grounded else 0.4,
        source_episode_id=episode.id,
        source_fact_id=new_fact.id,
    )
    db.add(fragment)

    # NLI contradiction detection — check against other facts of the same entity
    # Skip if we already did predicate-level supersede (same entity + same predicate)
    if not is_supersede:
        await _check_nli_contradictions(
            db, workspace_id, entity, new_fact, resolved_predicate
        )

    return StoredFactResponse(
        fact_id=new_fact.id,
        entity_name=entity.name,
        predicate=fact_hint.predicate,
        object_value=fact_hint.object,
        trust_level=trust.value,
        is_supersede=is_supersede,
    )


async def store_memory(db: AsyncSession, request: StoreMemoryRequest) -> StoreMemoryResponse:
    """Full store_memory pipeline (Transaction A — synchronous).

    1. Get/create session
    2. Create episode (immutable transcript)
    3. Resolve entities
    4. Store facts (with supersede)
    5. Return response

    Embedding (Transaction B) is handled asynchronously after commit.
    """
    session = await get_or_create_session(db, request.workspace_id, request.session_id, request.provider)

    episode = await create_episode(
        db,
        session,
        request.workspace_id,
        request.conversation_transcript,
        request.conversation_summary,
        request.provider,
    )

    # Resolve entities
    entities_created = 0
    entity_map: dict[str, Entity] = {}

    for hint in request.entities:
        entity, is_new = await resolve_entity(db, request.workspace_id, hint)
        entity_map[hint.name] = entity
        if is_new:
            entities_created += 1

    # Store facts
    stored_facts: list[StoredFactResponse] = []
    for fact_hint in request.facts:
        # Resolve subject entity (create if not already resolved)
        if fact_hint.subject not in entity_map:
            entity, is_new = await resolve_entity(
                db,
                request.workspace_id,
                EntityHint(name=fact_hint.subject, entity_type="other", source_quote=fact_hint.source_quote),
            )
            entity_map[fact_hint.subject] = entity
            if is_new:
                entities_created += 1

        entity = entity_map[fact_hint.subject]
        fact_resp = await store_fact(
            db,
            request.workspace_id,
            entity,
            fact_hint,
            episode,
            request.conversation_transcript,
        )
        stored_facts.append(fact_resp)

    await db.commit()

    # Transaction B — async embedding (fire-and-forget)
    asyncio.create_task(
        _generate_embeddings(
            request.workspace_id,
            episode,
            list(entity_map.values()),
            [f.fact_id for f in stored_facts],
        )
    )

    return StoreMemoryResponse(
        episode_id=episode.id,
        session_id=session.id,
        facts_stored=stored_facts,
        entities_resolved=len(entity_map) - entities_created,
        entities_created=entities_created,
    )


async def _generate_embeddings(
    workspace_id: uuid.UUID,
    episode: Episode,
    entities: list[Entity],
    fact_ids: list[uuid.UUID],
) -> None:
    """Transaction B — background embedding generation.

    Runs after the main transaction commits. Failures are logged but don't
    affect the stored data. Facts without embeddings are still searchable
    via FTS and graph traversal.
    """
    from jarvis.core.embedding import embed_text
    from jarvis.db import async_session_factory

    try:
        async with async_session_factory() as db:
            # Embed episode
            vec = embed_text(episode.summary or episode.content[:1000])
            db.add(
                Embedding(
                    workspace_id=workspace_id,
                    source_type="episode",
                    source_id=episode.id,
                    text_content=episode.summary or episode.content[:500],
                    vector=vec,
                )
            )

            # Embed entities — write to both Entity.name_embedding and Embedding table
            for entity in entities:
                vec = embed_text(entity.name)
                # Update Entity.name_embedding directly for Stage 2 resolution
                await db.execute(
                    text("UPDATE entities SET name_embedding = cast(:vec as vector) WHERE id = :eid"),
                    {"vec": str(vec), "eid": str(entity.id)},
                )
                db.add(
                    Embedding(
                        workspace_id=workspace_id,
                        source_type="entity",
                        source_id=entity.id,
                        text_content=entity.name,
                        vector=vec,
                    )
                )

            # Embed facts
            for fact_id in fact_ids:
                result = await db.execute(select(KnowledgeFact).where(KnowledgeFact.id == fact_id))
                fact: KnowledgeFact | None = result.scalar_one_or_none()
                if fact:
                    fact_text = f"{fact.predicate}: {fact.object_value}"
                    vec = embed_text(fact_text)
                    db.add(
                        Embedding(
                            workspace_id=workspace_id,
                            source_type="fact",
                            source_id=fact.id,
                            text_content=fact_text,
                            vector=vec,
                        )
                    )

            await db.commit()
            logger.info("Embeddings generated for episode %s", episode.id)
    except Exception:
        logger.exception("Failed to generate embeddings for episode %s", episode.id)
