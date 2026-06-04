"""store_memory pipeline: validate → episode → entities → facts → async embedding."""

import asyncio
import hashlib
import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from jarvis.core.entity_resolution import normalize_name
from jarvis.core.quote_verification import verify_quote
from jarvis.models.tables import (
    Embedding,
    Entity,
    EntityRelation,
    EntityType,
    Episode,
    FactEpisode,
    Fragment,
    FragmentType,
    KnowledgeFact,
    RelationType,
    Session,
    TrustLevel,
)
from jarvis.schemas import (
    EntityHint,
    FactHint,
    RelationHint,
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

    content_hash = hashlib.sha256(transcript.encode()).hexdigest()

    # Dedup: same workspace + same content → return existing episode
    existing = await db.execute(
        select(Episode).where(
            Episode.workspace_id == workspace_id,
            Episode.content_hash == content_hash,
        )
    )
    dup = existing.scalar_one_or_none()
    if dup:
        logger.info("Duplicate episode skipped: workspace=%s, hash=%s", workspace_id, content_hash[:12])
        return dup

    normalized = normalize_transcript(provider, transcript)

    episode = Episode(
        session_id=session.id,
        workspace_id=workspace_id,
        content=transcript,
        content_hash=content_hash,
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
    """Resolve entity hint — Stage 1 only (diary model).

    Returns (entity, is_new).

    Stage 1 covers:
      - NFKC + lowercase normalization on entities.name_normalized
      - Hard-coded cross-lingual aliases (CROSS_LINGUAL_ALIASES, ALIAS_DICT)
      - User-/AI-asserted aliases via the entity_aliases table

    Embedding-based auto-merge (Stage 2/3 of the prior KG design) is disabled.
    Diary semantics: only merge when the user or AI explicitly asserts two
    names are the same. Surface-similar names like "MMR" vs "MMR scoring"
    stay distinct unless an alias is recorded.
    """
    normalized = normalize_name(hint.name)

    # Stage 1a: Exact normalized match on entities.name_normalized
    result = await db.execute(
        select(Entity).where(
            Entity.workspace_id == workspace_id,
            Entity.name_normalized == normalized,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        logger.info("Entity exact match: '%s' → existing id=%s", hint.name, existing.id)
        return existing, False

    # Stage 1b: Explicit alias match via entity_aliases table
    alias_lookup = await db.execute(
        text("""
            SELECT entity_id FROM entity_aliases
            WHERE workspace_id = :ws AND lower(alias) = :alias
            LIMIT 1
        """),
        {"ws": str(workspace_id), "alias": normalized},
    )
    alias_row = alias_lookup.fetchone()
    if alias_row:
        entity_result = await db.execute(select(Entity).where(Entity.id == alias_row[0]))
        aliased = entity_result.scalar_one_or_none()
        if aliased:
            logger.info("Entity alias match: '%s' → '%s' (via entity_aliases)", hint.name, aliased.name)
            return aliased, False

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

    # Anchor automaton caches entity names — invalidate so the next recall
    # rebuilds it with this new entity included.
    from jarvis.core.anchor_matching import invalidate as _invalidate_anchor_cache
    _invalidate_anchor_cache(workspace_id)

    # Synchronous name_embedding — ensures next resolve_entity() Stage 2 finds this entity.
    # Embedding table entry is created asynchronously by _generate_embeddings() (different purpose: recall search).
    try:
        from jarvis.core.embedding import embed_for_storage

        storage_vec = embed_for_storage(normalized)
        await db.execute(
            text("UPDATE entities SET name_embedding = cast(:vec as vector) WHERE id = :eid"),
            {"vec": str(storage_vec), "eid": str(entity.id)},
        )
    except Exception:
        logger.debug("Embedding not available for new entity '%s', will be set by background job", hint.name)

    logger.info("Entity created: '%s' (type=%s)", hint.name, entity_type.value)
    return entity, True


async def _resolve_predicate(
    db: AsyncSession,
    entity_id: uuid.UUID,
    predicate: str,
) -> str:
    """Resolve predicate — exact match only (diary model).

    Diary semantics: AI writes facts as time-accumulating entries; do not
    automatically collapse semantically-similar predicates ("bug_recall_fts"
    and "bug_recall_hnsw" must remain distinct facts). Embedding-based
    auto-mapping is disabled.
    """
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
    logger.info("Quote grounding: %s %s → %s", entity.name, fact_hint.predicate, trust.value)

    # Resolve predicate: "나이" and "age" → same predicate if semantically similar
    resolved_predicate = await _resolve_predicate(db, entity.id, fact_hint.predicate)

    # Dedup: same (entity, predicate, object_value) already active?
    # If yes, reuse that fact and just link this episode — no new row, no supersede.
    # This is how confidence accumulates: N episodes → 1 fact + N fact_episodes rows.
    dedup_result = await db.execute(
        select(KnowledgeFact).where(
            KnowledgeFact.entity_id == entity.id,
            KnowledgeFact.predicate == resolved_predicate,
            KnowledgeFact.object_value == fact_hint.object,
            KnowledgeFact.superseded_at.is_(None),
        )
    )
    dup = dedup_result.scalar_one_or_none()
    if dup is not None:
        await db.execute(
            text("""
                INSERT INTO fact_episodes (fact_id, episode_id, role)
                VALUES (:fid, :eid, 'reinforcing')
                ON CONFLICT (fact_id, episode_id) DO NOTHING
            """),
            {"fid": str(dup.id), "eid": str(episode.id)},
        )
        logger.info(
            "Fact deduped: %s %s '%s' — linked episode %s (reinforcing)",
            entity.name, resolved_predicate, dup.object_value[:60], episode.id,
        )
        return StoredFactResponse(
            fact_id=dup.id,
            entity_name=entity.name,
            predicate=fact_hint.predicate,
            object_value=fact_hint.object,
            trust_level=dup.trust_level.value,
            is_supersede=False,
        )

    # Diary model: do NOT supersede prior facts. Same (entity, predicate)
    # with a different object_value is treated as a new time-ordered entry
    # (e.g., "JARVIS deployed_on Oracle" on 3월, "JARVIS deployed_on GCP" on 4월
    # — both remain visible, the change-of-mind itself is the information).
    is_supersede = False

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

    db.add(FactEpisode(fact_id=new_fact.id, episode_id=episode.id, role="source"))

    # Create Fragment (dual store: KnowledgeFact + Fragment).
    # source_quote carries the natural-language context — prefer it over the
    # triple when it's a meaningful length. Fallback to triple for sparse hints.
    from jarvis.core.query_preprocessing import extract_keywords

    if fact_hint.source_quote and len(fact_hint.source_quote.strip()) >= 10:
        fragment_content = fact_hint.source_quote.strip()
    else:
        fragment_content = f"{entity.name} {resolved_predicate} {fact_hint.object}"
    if len(fragment_content) > 500:
        fragment_content = fragment_content[:497] + "..."

    # Keywords: entity + predicate + object + tokens extracted from fragment content.
    content_keywords = extract_keywords(fragment_content)
    base_keywords = [entity.name, resolved_predicate, fact_hint.object]
    fragment_keywords: list[str] = []
    seen_keywords: set[str] = set()
    for kw in base_keywords + content_keywords:
        k_lower = kw.lower()
        if k_lower and k_lower not in seen_keywords:
            seen_keywords.add(k_lower)
            fragment_keywords.append(kw)
        if len(fragment_keywords) >= 20:
            break

    fragment = Fragment(
        workspace_id=workspace_id,
        content=fragment_content,
        fragment_type=_classify_fragment_type(resolved_predicate),
        keywords=fragment_keywords,
        importance=0.7 if trust == TrustLevel.grounded else 0.4,
        source_episode_id=episode.id,
        source_fact_id=new_fact.id,
    )
    db.add(fragment)

    # Diary model: NLI contradiction auto-supersede is disabled. Contradictions
    # are themselves information ("X was true on day A, ¬X was concluded on day B");
    # both entries remain accessible. The detector function is kept for potential
    # future "highlight conflicting beliefs" UX, but does not mutate state.

    return StoredFactResponse(
        fact_id=new_fact.id,
        entity_name=entity.name,
        predicate=fact_hint.predicate,
        object_value=fact_hint.object,
        trust_level=trust.value,
        is_supersede=is_supersede,
    )


def _resolve_relation_type(type_str: str) -> RelationType:
    """Map free-form relation type string to enum."""
    type_lower = type_str.lower().replace("-", "_").replace(" ", "_")
    try:
        return RelationType(type_lower)
    except ValueError:
        return RelationType.related_to


async def _store_relation(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    rel_hint: RelationHint,
    entity_map: dict[str, Entity],
    episode: Episode,
) -> None:
    """Store an entity relation, resolving entity names to IDs."""
    # Resolve from/to entities
    from_entity = entity_map.get(rel_hint.from_entity)
    to_entity = entity_map.get(rel_hint.to_entity)

    if not from_entity:
        from_entity, _ = await resolve_entity(
            db, workspace_id,
            EntityHint(name=rel_hint.from_entity, entity_type="other", source_quote=rel_hint.source_quote),
        )
        entity_map[rel_hint.from_entity] = from_entity

    if not to_entity:
        to_entity, _ = await resolve_entity(
            db, workspace_id,
            EntityHint(name=rel_hint.to_entity, entity_type="other", source_quote=rel_hint.source_quote),
        )
        entity_map[rel_hint.to_entity] = to_entity

    if from_entity.id == to_entity.id:
        return  # Skip self-relations

    relation = EntityRelation(
        workspace_id=workspace_id,
        from_entity_id=from_entity.id,
        to_entity_id=to_entity.id,
        relation_type=_resolve_relation_type(rel_hint.relation_type),
        source_episode_id=episode.id,
    )
    db.add(relation)


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

    # Vision §1 — no server-side LLM calls. If caller omitted a summary, use a
    # plain transcript excerpt; never reach out to an inference API. Diary-mode
    # flows enforce non-empty summary via IngestAndIndexRequest.require_index_hints,
    # so this branch only fires for legacy /store callers.
    summary = request.conversation_summary
    if not summary.strip():
        transcript = request.conversation_transcript
        summary = transcript[:200] + "..." if len(transcript) > 200 else transcript

    episode = await create_episode(
        db,
        session,
        request.workspace_id,
        request.conversation_transcript,
        summary,
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

    # Store relations
    for rel_hint in request.relations:
        await _store_relation(db, request.workspace_id, rel_hint, entity_map, episode)

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
    from jarvis.core.embedding import embed_for_storage
    from jarvis.db import async_session_factory

    try:
        async with async_session_factory() as db:
            # Embed episode
            vec = embed_for_storage(episode.summary or episode.content[:1000])
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
                vec = embed_for_storage(entity.name)
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
                    vec = embed_for_storage(fact_text)
                    db.add(
                        Embedding(
                            workspace_id=workspace_id,
                            source_type="fact",
                            source_id=fact.id,
                            text_content=fact_text,
                            vector=vec,
                        )
                    )

            # Embed fragments — Section 8 dual-store: semantic search goes
            # through fragments (natural-language source_quote), not fact triples.
            if fact_ids:
                frag_result = await db.execute(
                    select(Fragment).where(Fragment.source_fact_id.in_(fact_ids))
                )
                for frag in frag_result.scalars().all():
                    vec = embed_for_storage(frag.content)
                    db.add(
                        Embedding(
                            workspace_id=workspace_id,
                            source_type="fragment",
                            source_id=frag.id,
                            text_content=frag.content,
                            vector=vec,
                        )
                    )

            await db.commit()
            logger.info("Embeddings generated for episode %s", episode.id)
    except Exception:
        logger.exception("Failed to generate embeddings for episode %s", episode.id)
