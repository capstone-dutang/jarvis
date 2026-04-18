"""follow_relation: walk the knowledge graph one hop from a named entity.

Completes the "navigation" axis of JARVIS's 4-axis retrieval model:
  - explore_topic: broad reconnaissance
  - recall_memory: specific anchor + facts
  - follow_relation: THIS — move along a relation to neighbor entities
  - get_episode_excerpt / search_passages: deep narrative

Use case: recall_memory returns `related_entities` as hints — `follow_relation`
turns those hints into navigable steps. Given a starting entity, return its
1-hop neighbors grouped by relation_type, each with a brief top-facts snapshot
so the AI can decide whether to drill further (via recall_memory on that
neighbor's name).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class FactBrief:
    predicate: str
    object_value: str
    grounded: bool
    valid_from: datetime


@dataclass
class RelatedNode:
    entity_id: uuid.UUID
    entity_name: str
    entity_type: str | None
    relation_type: str
    direction: str  # "out" (self → other) or "in" (other → self)
    fact_count: int  # active facts on the neighbor (workspace-wide)
    top_facts: list[FactBrief]  # up to 3 most recent active facts


@dataclass
class FollowRelationResult:
    anchor_entity_id: uuid.UUID
    anchor_entity_name: str
    total_neighbors: int
    neighbors: list[RelatedNode]
    relation_type_counts: dict[str, int]  # breakdown by relation_type


async def _resolve_anchor(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    entity: str,
) -> tuple[uuid.UUID, str, str | None] | None:
    """Accept either UUID string or entity name, return (id, name, type).

    Name match is exact (case-insensitive). Callers who need fuzzy matching
    should call recall_memory first to get the canonical entity_id.
    """
    # Try UUID first
    try:
        eid = uuid.UUID(entity)
        result = await db.execute(
            text("""
                SELECT id, name, entity_type::text
                FROM entities WHERE id = :id AND workspace_id = :ws
            """),
            {"id": str(eid), "ws": str(workspace_id)},
        )
        row = result.fetchone()
        if row:
            return row[0], row[1], row[2]
    except (ValueError, TypeError):
        pass

    # Fall back to name match (case-insensitive)
    result = await db.execute(
        text("""
            SELECT id, name, entity_type::text
            FROM entities
            WHERE workspace_id = :ws AND LOWER(name) = LOWER(:name)
            LIMIT 1
        """),
        {"name": entity, "ws": str(workspace_id)},
    )
    row = result.fetchone()
    if row:
        return row[0], row[1], row[2]

    # Final fallback: entity_aliases (e.g. "자비스" → JARVIS)
    result = await db.execute(
        text("""
            SELECT e.id, e.name, e.entity_type::text
            FROM entity_aliases a
            JOIN entities e ON e.id = a.entity_id
            WHERE a.workspace_id = :ws AND LOWER(a.alias) = LOWER(:name)
            LIMIT 1
        """),
        {"name": entity, "ws": str(workspace_id)},
    )
    row = result.fetchone()
    if row:
        return row[0], row[1], row[2]
    return None


async def follow_relation(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    entity: str,  # UUID or exact entity name
    direction: str = "both",  # "out" | "in" | "both"
    relation_type: str | None = None,  # filter to one relation_type if given
    limit: int = 10,
) -> FollowRelationResult | None:
    """Walk 1 hop from anchor entity, return grouped neighbors with top facts."""
    anchor = await _resolve_anchor(db, workspace_id, entity)
    if anchor is None:
        return None
    anchor_id, anchor_name, _anchor_type = anchor

    # Build UNION of outgoing and incoming relations per direction request.
    # Each row: (neighbor_id, neighbor_name, neighbor_type, relation_type, direction)
    direction_clauses: list[str] = []
    if direction in ("out", "both"):
        direction_clauses.append("""
            SELECT r.to_entity_id AS neighbor_id,
                   e.name AS neighbor_name,
                   e.entity_type::text AS neighbor_type,
                   r.relation_type::text AS relation_type,
                   'out' AS direction,
                   r.valid_from AS rel_valid_from
            FROM entity_relations r
            JOIN entities e ON e.id = r.to_entity_id
            WHERE r.workspace_id = :ws
              AND r.from_entity_id = :anchor
              AND (r.valid_to IS NULL OR r.valid_to > now())
        """)
    if direction in ("in", "both"):
        direction_clauses.append("""
            SELECT r.from_entity_id AS neighbor_id,
                   e.name AS neighbor_name,
                   e.entity_type::text AS neighbor_type,
                   r.relation_type::text AS relation_type,
                   'in' AS direction,
                   r.valid_from AS rel_valid_from
            FROM entity_relations r
            JOIN entities e ON e.id = r.from_entity_id
            WHERE r.workspace_id = :ws
              AND r.to_entity_id = :anchor
              AND (r.valid_to IS NULL OR r.valid_to > now())
        """)

    if not direction_clauses:
        return FollowRelationResult(
            anchor_entity_id=anchor_id, anchor_entity_name=anchor_name,
            total_neighbors=0, neighbors=[], relation_type_counts={},
        )

    union_sql = " UNION ALL ".join(direction_clauses)
    type_filter_sql = " AND relation_type = :rtype " if relation_type else ""
    sql = f"""
        WITH raw AS ({union_sql})
        SELECT neighbor_id, neighbor_name, neighbor_type, relation_type, direction,
               MAX(rel_valid_from) AS latest_rel_at
        FROM raw
        WHERE 1=1 {type_filter_sql}
        GROUP BY neighbor_id, neighbor_name, neighbor_type, relation_type, direction
        ORDER BY latest_rel_at DESC
        LIMIT :lim
    """
    params: dict[str, object] = {
        "ws": str(workspace_id),
        "anchor": str(anchor_id),
        "lim": limit,
    }
    if relation_type:
        params["rtype"] = relation_type

    result = await db.execute(text(sql), params)
    rows = result.fetchall()

    if not rows:
        return FollowRelationResult(
            anchor_entity_id=anchor_id, anchor_entity_name=anchor_name,
            total_neighbors=0, neighbors=[], relation_type_counts={},
        )

    # Fetch top facts and total fact_count for each neighbor in one go
    neighbor_ids = list({r[0] for r in rows})
    id_literal = "ARRAY[" + ",".join(f"'{nid}'::uuid" for nid in neighbor_ids) + "]"

    # Active fact count per neighbor
    count_result = await db.execute(
        text(f"""
            SELECT entity_id, COUNT(*)
            FROM knowledge_facts
            WHERE workspace_id = :ws
              AND superseded_at IS NULL
              AND entity_id = ANY({id_literal})
            GROUP BY entity_id
        """),
        {"ws": str(workspace_id)},
    )
    fact_count_map: dict[uuid.UUID, int] = {r[0]: r[1] for r in count_result.fetchall()}

    # Top 3 recent active facts per neighbor (row_number partition)
    facts_result = await db.execute(
        text(f"""
            SELECT entity_id, predicate, object_value, trust_level::text, valid_from
            FROM (
                SELECT kf.entity_id, kf.predicate, kf.object_value,
                       kf.trust_level, kf.valid_from,
                       ROW_NUMBER() OVER (
                           PARTITION BY kf.entity_id
                           ORDER BY kf.valid_from DESC
                       ) AS rn
                FROM knowledge_facts kf
                WHERE kf.workspace_id = :ws
                  AND kf.superseded_at IS NULL
                  AND kf.entity_id = ANY({id_literal})
            ) t
            WHERE t.rn <= 3
            ORDER BY entity_id, valid_from DESC
        """),
        {"ws": str(workspace_id)},
    )
    top_facts_map: dict[uuid.UUID, list[FactBrief]] = {}
    for row in facts_result.fetchall():
        top_facts_map.setdefault(row[0], []).append(
            FactBrief(
                predicate=row[1],
                object_value=row[2][:150] if row[2] and len(row[2]) > 150 else (row[2] or ""),
                grounded=(row[3] == "grounded"),
                valid_from=row[4],
            )
        )

    neighbors: list[RelatedNode] = []
    rel_type_counts: dict[str, int] = {}
    for row in rows:
        nid, nname, ntype, rtype, dir_, _ = row
        neighbors.append(
            RelatedNode(
                entity_id=nid,
                entity_name=nname,
                entity_type=ntype,
                relation_type=rtype,
                direction=dir_,
                fact_count=fact_count_map.get(nid, 0),
                top_facts=top_facts_map.get(nid, []),
            )
        )
        rel_type_counts[rtype] = rel_type_counts.get(rtype, 0) + 1

    return FollowRelationResult(
        anchor_entity_id=anchor_id,
        anchor_entity_name=anchor_name,
        total_neighbors=len(neighbors),
        neighbors=neighbors,
        relation_type_counts=rel_type_counts,
    )
