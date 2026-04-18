"""Topic map builder — structural overview for a query, no fact details.

Reuses hybrid_search_sql + preprocess_query for Stage 1 candidate selection,
then aggregates to entity-centric structure. AI clients call this before
recall_memory to navigate unfamiliar topics with minimal tokens.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.schemas import TopicEntity, TopicMapResponse


@dataclass
class _EntityStat:
    name: str
    fact_count: int = 0
    max_rrf: float = 0.0


class _EntityMeta(TypedDict):
    name: str
    entity_type: str
    community_id: int | None
    workspace_fact_count: int
    out_degree: int

logger = logging.getLogger(__name__)

# Hard bounds (keep response small — goal is token savings).
TOPIC_POOL_SIZE = 50            # Stage 1 candidate pool for topic map
MAX_TOP_ENTITIES = 15           # Return at most N entities
MAX_TOP_PREDICATES = 10         # predicates from selected facts only


async def build_topic_map(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    query: str,
) -> TopicMapResponse:
    """Build a structural topic map for `query` (no fact details)."""
    from jarvis.core.embedding import embed_text
    from jarvis.core.query_preprocessing import preprocess_query
    from jarvis.core.recall import extract_query_entities, hybrid_search_sql

    preprocessed = preprocess_query(query)
    logger.info(
        "Topic map: query=%r, expanded=%s", query, preprocessed.expanded_terms,
    )

    query_vector: list[float] = []
    try:
        query_vector = embed_text(preprocessed.normalized)
    except Exception:
        logger.exception("Topic map embedding failed for '%s'", query[:50])

    seed_ids = await extract_query_entities(db, workspace_id, query_vector)

    rows = await hybrid_search_sql(
        db, workspace_id,
        query_text=preprocessed.normalized,
        fts_query=preprocessed.fts_query,
        query_vector=query_vector,
        seed_ids=seed_ids,
        limit=TOPIC_POOL_SIZE,
    )

    if not rows:
        return TopicMapResponse(
            query=query,
            expanded_terms=preprocessed.expanded_terms,
            total_candidates=0,
            total_fact_count=0,
            entities=[],
            distinct_communities=0,
            top_predicates=[],
            edge_count=0,
            isolated_entity_count=0,
            time_range_start=None,
            time_range_end=None,
        )

    # Aggregate per-entity stats from the candidate pool.
    # Row columns: (fact_id, entity_id, entity_name, predicate, object_value,
    #               rrf_score, sources, importance, fragment_type, last_accessed_at)
    entity_stats: dict[uuid.UUID, _EntityStat] = {}
    for row in rows:
        eid = row[1]
        stat = entity_stats.get(eid)
        if stat is None:
            stat = _EntityStat(name=row[2])
            entity_stats[eid] = stat
        stat.fact_count += 1
        rrf = float(row[5])
        if rrf > stat.max_rrf:
            stat.max_rrf = rrf

    # Primary: relevance (max_rrf); tiebreaker: fact_count. Prevents 'user'
    # (98 facts) from dominating unrelated queries.
    ranked_entity_ids = sorted(
        entity_stats.keys(),
        key=lambda i: (entity_stats[i].max_rrf, entity_stats[i].fact_count),
        reverse=True,
    )
    top_entity_ids = ranked_entity_ids[:MAX_TOP_ENTITIES]

    # Batch fetch entity metadata for top entities.
    top_entities = await _fetch_entity_metadata(db, top_entity_ids)

    # Merge with per-entity stats from pool.
    entities: list[TopicEntity] = []
    for eid in top_entity_ids:
        meta = top_entities.get(eid)
        if meta is None:
            continue
        stat = entity_stats[eid]
        entities.append(
            TopicEntity(
                name=meta["name"],
                entity_type=meta["entity_type"],
                fact_count_in_pool=stat.fact_count,
                workspace_fact_count=meta["workspace_fact_count"],
                out_degree=meta["out_degree"],
                community_id=meta["community_id"],
            ),
        )

    # Predicate distribution within the candidate pool only.
    pred_counter: Counter[str] = Counter(str(row[3]) for row in rows)
    top_predicates = pred_counter.most_common(MAX_TOP_PREDICATES)

    # Relation edges among top entities.
    edge_count, isolated_count = await _summarize_edges(
        db, workspace_id, top_entity_ids,
    )

    # Time range over pool fact_ids.
    fact_ids_in_pool = [row[0] for row in rows]
    time_start, time_end = await _fetch_time_range(db, fact_ids_in_pool)

    # Distinct communities among top entities (None excluded).
    distinct_communities = len({
        e.community_id for e in entities if e.community_id is not None
    })

    return TopicMapResponse(
        query=query,
        expanded_terms=preprocessed.expanded_terms,
        total_candidates=len(rows),
        total_fact_count=len(rows),
        entities=entities,
        distinct_communities=distinct_communities,
        top_predicates=top_predicates,
        edge_count=edge_count,
        isolated_entity_count=isolated_count,
        time_range_start=time_start,
        time_range_end=time_end,
    )


async def _fetch_entity_metadata(
    db: AsyncSession,
    entity_ids: list[uuid.UUID],
) -> dict[uuid.UUID, _EntityMeta]:
    """Batch-fetch entity_type, community_id, workspace-wide fact count, out-degree."""
    if not entity_ids:
        return {}
    id_literal = "ARRAY[" + ",".join(f"'{eid}'::uuid" for eid in entity_ids) + "]"
    sql = f"""
        SELECT e.id, e.name, e.entity_type::text, e.community_id,
               (SELECT COUNT(*) FROM knowledge_facts kf
                WHERE kf.entity_id = e.id AND kf.superseded_at IS NULL) AS ws_fact_count,
               (SELECT COUNT(*) FROM entity_relations r
                WHERE r.from_entity_id = e.id AND r.valid_to IS NULL) AS out_degree
        FROM entities e WHERE e.id = ANY({id_literal})
    """  # noqa: S608
    result = await db.execute(text(sql))
    out: dict[uuid.UUID, _EntityMeta] = {}
    for row in result.fetchall():
        out[row[0]] = _EntityMeta(
            name=str(row[1]),
            entity_type=str(row[2]),
            community_id=row[3],
            workspace_fact_count=int(row[4]),
            out_degree=int(row[5]),
        )
    return out


async def _summarize_edges(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    top_entity_ids: list[uuid.UUID],
) -> tuple[int, int]:
    """Return (edge_count, isolated_entity_count) for relations among top entities."""
    if len(top_entity_ids) < 2:
        return 0, len(top_entity_ids)
    id_literal = "ARRAY[" + ",".join(f"'{eid}'::uuid" for eid in top_entity_ids) + "]"
    sql = f"""
        SELECT from_entity_id, to_entity_id FROM entity_relations
        WHERE workspace_id = :ws
          AND from_entity_id = ANY({id_literal})
          AND to_entity_id = ANY({id_literal})
          AND valid_to IS NULL
    """  # noqa: S608
    result = await db.execute(text(sql), {"ws": str(workspace_id)})
    edges = result.fetchall()
    engaged_ids: set[uuid.UUID] = set()
    for from_id, to_id in edges:
        engaged_ids.add(from_id)
        engaged_ids.add(to_id)
    isolated_count = sum(1 for eid in top_entity_ids if eid not in engaged_ids)
    return len(edges), isolated_count


async def _fetch_time_range(
    db: AsyncSession,
    fact_ids: list[uuid.UUID],
) -> tuple[datetime | None, datetime | None]:
    """Min/max valid_from over the given fact ids."""
    if not fact_ids:
        return None, None
    id_literal = "ARRAY[" + ",".join(f"'{fid}'::uuid" for fid in fact_ids) + "]"
    sql = f"""
        SELECT MIN(valid_from), MAX(valid_from) FROM knowledge_facts
        WHERE id = ANY({id_literal})
    """  # noqa: S608
    result = await db.execute(text(sql))
    row = result.fetchone()
    if row is None:
        return None, None
    return row[0], row[1]
