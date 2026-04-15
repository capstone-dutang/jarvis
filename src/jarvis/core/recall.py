"""recall_memory pipeline: hybrid search via SQL function → fact assembly.

Based on: research/2026-03-31-multilingual-kg-postgresql-research.md lines 142-191
Uses hybrid_graph_search SQL function for single-query 3-way RRF (vector + FTS + graph).
Falls back to Python-side search when SQL function is not available.
"""

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.config import settings
from jarvis.models.tables import (
    Entity,
    EntityRelation,
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
)


async def _hybrid_search_sql(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    query: str,
    query_vector: list[float],
    seed_ids: list[uuid.UUID],
    limit: int,
) -> list[tuple[uuid.UUID, uuid.UUID, str, str, str, float, str]]:
    """Call hybrid_graph_search SQL function for single-query RRF.

    Returns: list of (fact_id, entity_id, entity_name, predicate, object_value, rrf_score, sources)
    """
    seed_array = "{" + ",".join(str(s) for s in seed_ids) + "}" if seed_ids else "{}"

    result = await db.execute(
        text("""
            SELECT fact_id, entity_id, entity_name, predicate, object_value, rrf_score, sources
            FROM hybrid_graph_search(
                :ws, :query, cast(:vec as vector), cast(:seeds as uuid[]),
                :lim, 1.0, 1.0, 0.5, 2, :rrf_k
            )
        """),
        {
            "ws": str(workspace_id),
            "query": query,
            "vec": str(query_vector),
            "seeds": seed_array,
            "lim": limit,
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

    # Get episode excerpt
    episode_result = await db.execute(select(Episode).where(Episode.id == fact.source_episode_id))
    episode = episode_result.scalar_one()
    excerpt = episode.content[:500] if len(episode.content) > 500 else episode.content

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

    # Get related entities via relations
    rel_result = await db.execute(
        select(Entity.name)
        .join(EntityRelation, EntityRelation.to_entity_id == Entity.id)
        .where(
            EntityRelation.from_entity_id == fact.entity_id,
            EntityRelation.valid_to.is_(None),
        )
    )
    related = [row[0] for row in rel_result.fetchall()]

    return RecallFactResponse(
        entity=entity.name,
        predicate=fact.predicate,
        object_value=fact.object_value,
        grounded=fact.trust_level == TrustLevel.grounded,
        valid_from=fact.valid_from,
        evidence=EvidenceResponse(
            excerpt=excerpt,
            episode_id=fact.source_episode_id,
            recorded_at=fact.recorded_at,
        ),
        related_entities=related,
        history=history,
        score=score,
    )


async def _extract_query_entities(
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
        return []


async def recall_memory(db: AsyncSession, request: RecallMemoryRequest) -> RecallMemoryResponse:
    """Full recall pipeline: single SQL function call for hybrid search.

    1. Embed query
    2. Extract seed entities for graph expansion
    3. Call hybrid_graph_search SQL function (vector + FTS + graph → RRF)
    4. Build full fact responses with evidence and history
    """
    # Embed query
    query_vector: list[float] = []
    try:
        from jarvis.core.embedding import embed_text

        query_vector = embed_text(request.query)
    except Exception:
        pass

    # Extract seed entities for graph expansion
    seed_ids = await _extract_query_entities(db, request.workspace_id, query_vector)

    # Try SQL function first (1 DB round-trip)
    results: list[RecallFactResponse] = []
    try:
        rows = await _hybrid_search_sql(db, request.workspace_id, request.query, query_vector, seed_ids, request.limit)
        for row in rows:
            fact_id, entity_id, entity_name, predicate, object_value, rrf_score, sources = row
            fact_result = await db.execute(select(KnowledgeFact).where(KnowledgeFact.id == fact_id))
            fact: KnowledgeFact | None = fact_result.scalar_one_or_none()
            if fact:
                resp = await _build_fact_response(db, fact, float(rrf_score))
                results.append(resp)
    except Exception:
        # Fallback: simple ILIKE search
        fallback_rows = await _fallback_search(db, request.workspace_id, request.query, request.limit)
        for fact_id, score in fallback_rows:
            fact_result = await db.execute(select(KnowledgeFact).where(KnowledgeFact.id == fact_id))
            fact = fact_result.scalar_one_or_none()
            if fact:
                resp = await _build_fact_response(db, fact, float(score))
                results.append(resp)

    return RecallMemoryResponse(results=results)
