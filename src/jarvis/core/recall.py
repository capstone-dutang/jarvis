"""recall_memory pipeline: hybrid search via SQL function → fact assembly.

Based on: research/2026-03-31-multilingual-kg-postgresql-research.md lines 142-191
Uses hybrid_graph_search SQL function for single-query 3-way RRF (vector + FTS + graph).
Falls back to Python-side search when SQL function is not available.
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.config import settings
from jarvis.models.tables import (
    Entity,
    Episode,
    KnowledgeFact,
    TrustLevel,
)
from jarvis.schemas import (
    EvidenceResponse,
    FactHistoryEntry,
    RecallFactResponse,
    RecallMemoryRequest,
    RecallMemoryResponse,
    RelatedEntity,
)

logger = logging.getLogger(__name__)


async def hybrid_search_sql(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    query_text: str,
    fts_query: str,
    query_vector: list[float],
    seed_ids: list[uuid.UUID],
    anchor_entity_ids: list[uuid.UUID],
    limit: int,
    anchor_hop_depth: int = 2,
) -> list[tuple[uuid.UUID, uuid.UUID, str, str, str, float, str, float | None, str | None, datetime | None]]:
    """Call hybrid_graph_search SQL function for single-query RRF.

    Returns rows of (fact_id, entity_id, entity_name, predicate, object_value,
    rrf_score, sources, importance, fragment_type, last_accessed_at).
    When anchor_entity_ids is empty, the SQL function falls back to broad search.
    """
    seed_literal = (
        "ARRAY[" + ",".join(f"'{s}'::uuid" for s in seed_ids) + "]"
        if seed_ids
        else "'{}'::uuid[]"
    )
    anchor_literal = (
        "ARRAY[" + ",".join(f"'{a}'::uuid" for a in anchor_entity_ids) + "]"
        if anchor_entity_ids
        else "'{}'::uuid[]"
    )
    sql = f"""
        SELECT fact_id, entity_id, entity_name, predicate, object_value, rrf_score, sources,
               importance, fragment_type, last_accessed_at
        FROM hybrid_graph_search(
            cast(:ws as uuid), :query, :fts, cast(:vec as vector), {seed_literal}, {anchor_literal},
            :lim, 1.0, 1.0, 0.5, 2, :anchor_hop, :rrf_k
        )
    """
    result = await db.execute(
        text(sql),
        {
            "ws": str(workspace_id),
            "query": query_text,
            "fts": fts_query,
            "vec": str(query_vector),
            "lim": limit,
            "anchor_hop": anchor_hop_depth,
            "rrf_k": settings.search_rrf_k,
        },
    )
    return result.fetchall()  # type: ignore[return-value]


async def _fallback_search(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    query: str,
    limit: int,
) -> list[tuple[uuid.UUID, float]]:
    """Fallback: ILIKE search across entity name + predicate + object_value + source_quote."""
    # Split query into words and match any word in any field
    words = [w.strip() for w in query.split() if w.strip()]
    if not words:
        words = [query]

    # Build WHERE clause: any word matches any of the 4 searchable fields
    conditions = []
    params: dict[str, object] = {"ws": str(workspace_id), "lim": limit}
    for i, word in enumerate(words):
        param = f"q{i}"
        params[param] = f"%{word}%"
        conditions.append(
            f"(e.name ILIKE :{param} OR kf.predicate ILIKE :{param} "
            f"OR kf.object_value ILIKE :{param} OR kf.source_quote ILIKE :{param})"
        )

    where_clause = " OR ".join(conditions)

    result = await db.execute(
        text(f"""
            SELECT kf.id, 1.0 AS score FROM knowledge_facts kf
            JOIN entities e ON e.id = kf.entity_id
            WHERE kf.workspace_id = :ws
              AND kf.superseded_at IS NULL
              AND ({where_clause})
            LIMIT :lim
        """),
        params,
    )
    return result.fetchall()  # type: ignore[return-value]


async def _build_fact_response(
    db: AsyncSession,
    fact: KnowledgeFact,
    score: float,
) -> RecallFactResponse:
    """Build a single fact response with evidence and history."""
    # Get entity name
    entity_result = await db.execute(select(Entity).where(Entity.id == fact.entity_id))
    entity = entity_result.scalar_one()

    # Most-recent linked episode + episode count (via fact_episodes M:N).
    # Falls back to fact.source_episode_id for safety during the transition.
    link_result = await db.execute(
        text("""
            SELECT episode_id, COUNT(*) OVER () AS total
            FROM fact_episodes
            WHERE fact_id = :fid
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"fid": str(fact.id)},
    )
    link_row = link_result.fetchone()
    if link_row:
        primary_episode_id = link_row[0]
        episode_count = int(link_row[1])
    else:
        primary_episode_id = fact.source_episode_id
        episode_count = 1 if fact.source_episode_id else 0

    # Excerpt: prefer fragment.content (natural-language passage) over episode content.
    # Episode content is 100KB+ of raw transcript; fragment is the curated passage
    # linked to this fact. Fall back to episode if no fragment linked.
    frag_result = await db.execute(
        text("""
            SELECT content FROM fragments
            WHERE source_fact_id = :fid
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"fid": str(fact.id)},
    )
    frag_row = frag_result.fetchone()
    if frag_row:
        excerpt = frag_row[0][:1000] if len(frag_row[0]) > 1000 else frag_row[0]
    elif primary_episode_id:
        episode_result = await db.execute(select(Episode).where(Episode.id == primary_episode_id))
        episode = episode_result.scalar_one()
        excerpt = episode.content[:500] if len(episode.content) > 500 else episode.content
    else:
        excerpt = ""

    # Get history (other facts with same entity + predicate)
    history_result = await db.execute(
        select(KnowledgeFact)
        .where(
            KnowledgeFact.entity_id == fact.entity_id,
            KnowledgeFact.predicate == fact.predicate,
            KnowledgeFact.id != fact.id,
        )
        .order_by(KnowledgeFact.valid_from.desc())
    )
    history_facts = history_result.scalars().all()
    history = [
        FactHistoryEntry(
            object_value=h.object_value,
            valid_from=h.valid_from,
            superseded_at=h.superseded_at,
        )
        for h in history_facts
    ]

    # Related entities: name + relation_type + fact_count (navigation hints)
    rel_result = await db.execute(
        text("""
            SELECT e.id, e.name, r.relation_type::text,
                   (SELECT COUNT(*) FROM knowledge_facts kf2
                    WHERE kf2.entity_id = e.id AND kf2.superseded_at IS NULL) AS fact_count
            FROM entity_relations r
            JOIN entities e ON e.id = r.to_entity_id
            WHERE r.from_entity_id = :from_id
              AND (r.valid_to IS NULL OR r.valid_to > now())
        """),
        {"from_id": str(fact.entity_id)},
    )
    related = [
        RelatedEntity(
            entity_id=row[0], name=row[1],
            relation_type=row[2], fact_count=int(row[3]),
        )
        for row in rel_result.fetchall()
    ]

    return RecallFactResponse(
        entity=entity.name,
        predicate=fact.predicate,
        object_value=fact.object_value,
        grounded=fact.trust_level == TrustLevel.grounded,
        valid_from=fact.valid_from,
        evidence=EvidenceResponse(
            excerpt=excerpt,
            episode_id=primary_episode_id or fact.source_episode_id,
            recorded_at=fact.recorded_at,
            episode_count=episode_count,
        ),
        related_entities=related,
        history=history,
        score=score,
    )


async def extract_query_entities(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    query_vector: list[float],
    top_k: int = 3,
) -> list[uuid.UUID]:
    """Find entities most similar to the query for graph seed.

    Uses pgvector cosine similarity on entity name embeddings.
    Returns up to top_k entity IDs as seed for graph expansion.
    """
    if not query_vector:
        return []
    try:
        result = await db.execute(
            text("""
                SELECT id FROM entities
                WHERE workspace_id = :ws
                  AND name_embedding IS NOT NULL
                ORDER BY name_embedding <=> cast(:vec as vector)
                LIMIT :k
            """),
            {"ws": str(workspace_id), "vec": str(query_vector), "k": top_k},
        )
        return [row[0] for row in result.fetchall()]
    except Exception:
        logger.exception("Failed to extract query entities for workspace=%s", workspace_id)
        return []


# Stage 1 candidate pool size. Research: LIMIT 100.
# request.limit is the final cap after MMR selection, not the Stage 1 pool size.
STAGE1_POOL_SIZE = 100


async def recall_memory(db: AsyncSession, request: RecallMemoryRequest) -> RecallMemoryResponse:
    """Full recall pipeline: hybrid search → MMR context assembly.

    1. Embed query
    2. Extract seed entities for graph expansion
    3. Call hybrid_graph_search SQL function (vector + FTS + graph → RRF) — pool=100
    4. MMR re-rank with community awareness + adaptive K
    5. Build full fact responses with evidence and history
    """
    from jarvis.core.context_assembly import FactCandidate, assemble_context
    from jarvis.core.query_preprocessing import preprocess_query_with_anchors

    # Preprocess + Aho-Corasick anchor extraction (Phase 1 entity-anchored retrieval)
    preprocessed = await preprocess_query_with_anchors(
        db, request.workspace_id, request.query,
    )
    logger.info(
        "Query expanded: %r → %s (fts=%r, anchors=%d)",
        request.query, preprocessed.expanded_terms, preprocessed.fts_query,
        len(preprocessed.anchor_entity_ids),
    )

    # Embed query (use normalized form)
    query_vector: list[float] = []
    try:
        from jarvis.core.embedding import embed_text

        query_vector = embed_text(preprocessed.normalized)
    except Exception:
        logger.exception("Query embedding failed for '%s'", request.query[:50])

    # Seed = Aho-Corasick anchors ∪ cosine top-3 (user decision: always merge both)
    cosine_seeds = await extract_query_entities(db, request.workspace_id, query_vector)
    seed_ids = list(dict.fromkeys(preprocessed.anchor_entity_ids + cosine_seeds))
    if seed_ids:
        logger.info(
            "Seeds: anchors=%d + cosine=%d → merged=%d",
            len(preprocessed.anchor_entity_ids), len(cosine_seeds), len(seed_ids),
        )

    # Stage 1: Hybrid search with fixed pool size (research: LIMIT 100)
    results: list[RecallFactResponse] = []
    try:
        # pgvector 0.8 iterative_scan — ensures HNSW + WHERE filter returns
        # k results even when the filter discards many candidates. SET LOCAL
        # scopes to this transaction, applied to the hybrid_graph_search call.
        await db.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
        rows = await hybrid_search_sql(
            db, request.workspace_id,
            query_text=preprocessed.normalized,
            fts_query=preprocessed.fts_query,
            query_vector=query_vector,
            seed_ids=seed_ids,
            anchor_entity_ids=preprocessed.anchor_entity_ids,
            limit=STAGE1_POOL_SIZE,
        )
        logger.info("Hybrid search: %d candidates for '%s'", len(rows), request.query[:50])

        # Build FactCandidate list for MMR
        candidates = [
            FactCandidate(
                fact_id=row[0],
                entity_id=row[1],
                entity_name=row[2],
                predicate=row[3],
                object_value=row[4],
                rrf_score=float(row[5]),
                importance=float(row[7]) if row[7] is not None else 0.5,
                fragment_type=row[8] if row[8] is not None else "fact",
                last_accessed_at=row[9],
            )
            for row in rows
        ]

        # Stage 2: MMR context assembly
        assembly = await assemble_context(
            db, request.workspace_id, candidates, limit=request.limit,
        )

        # Refresh last_accessed_at for facts we actually returned. MMR-selected
        # facts are the ones the user will see, so they reset the decay clock.
        fact_ids_accessed = [c.fact_id for c in assembly.selected]
        if fact_ids_accessed:
            try:
                id_literal = (
                    "ARRAY[" + ",".join(f"'{fid}'::uuid" for fid in fact_ids_accessed) + "]"
                )
                await db.execute(
                    text(
                        f"UPDATE knowledge_facts SET last_accessed_at = now() "
                        f"WHERE id = ANY({id_literal})"  # noqa: S608
                    )
                )
                await db.commit()
            except Exception:
                logger.exception("last_accessed_at update failed (non-fatal)")
                await db.rollback()

        # Build full fact responses for selected candidates
        for cand in assembly.selected:
            fact_result = await db.execute(select(KnowledgeFact).where(KnowledgeFact.id == cand.fact_id))
            fact: KnowledgeFact | None = fact_result.scalar_one_or_none()
            if fact:
                resp = await _build_fact_response(db, fact, cand.rrf_score)
                results.append(resp)

        return RecallMemoryResponse(
            results=results,
            coverage=assembly.coverage,
            structural_summary=assembly.structural_summary,
            pagination_token="more_available" if assembly.has_more else None,
            anchor_matched=bool(preprocessed.anchor_entity_ids),
        )
    except Exception:
        # Rollback aborted transaction before fallback
        await db.rollback()
        logger.exception("Hybrid search FAILED for '%s' — falling back to ILIKE", request.query[:50])
        fallback_rows = await _fallback_search(db, request.workspace_id, request.query, request.limit)
        for fact_id, score in fallback_rows:
            fact_result = await db.execute(select(KnowledgeFact).where(KnowledgeFact.id == fact_id))
            fact = fact_result.scalar_one_or_none()
            if fact:
                resp = await _build_fact_response(db, fact, float(score))
                results.append(resp)
        logger.info("Fallback search: %d results for '%s'", len(results), request.query[:50])

    return RecallMemoryResponse(
        results=results,
        anchor_matched=bool(preprocessed.anchor_entity_ids),
    )
