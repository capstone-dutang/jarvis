"""hybrid_search v4: anchor_entity_ids hard filter + anchor_hop_depth

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-04-18

Phase 1 Sub-Phase C. Adds entity-anchored Stage 1 filter.

Signature change (11 → 13 params): p_anchor_entity_ids UUID[], p_anchor_hop_depth INT.
When p_anchor_entity_ids is non-empty, graph_facts + vector_facts + fts_facts
CTEs are all restricted to entities within p_anchor_hop_depth hops of the
anchors (bidirectional BFS over entity_relations). When empty, falls back
to the broad (whole-workspace) behavior from v3.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "h8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "g7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the 11-param v3 signature. CREATE OR REPLACE cannot alter param list.
    op.execute(
        "DROP FUNCTION IF EXISTS hybrid_graph_search("
        "UUID, TEXT, TEXT, vector, UUID[], INT, FLOAT, FLOAT, FLOAT, INT, INT)"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION hybrid_graph_search(
            p_workspace_id UUID,
            p_query_text TEXT,
            p_fts_query TEXT,
            p_query_embed vector(384),
            p_seed_ids UUID[],
            p_anchor_entity_ids UUID[],
            p_match_count INT DEFAULT 20,
            p_graph_weight FLOAT DEFAULT 1.0,
            p_vector_weight FLOAT DEFAULT 1.0,
            p_fts_weight FLOAT DEFAULT 0.5,
            p_max_depth INT DEFAULT 2,
            p_anchor_hop_depth INT DEFAULT 2,
            p_rrf_k INT DEFAULT 60
        ) RETURNS TABLE(
            fact_id UUID,
            entity_id UUID,
            entity_name TEXT,
            predicate TEXT,
            object_value TEXT,
            rrf_score NUMERIC,
            sources TEXT,
            importance FLOAT,
            fragment_type TEXT,
            last_accessed_at TIMESTAMPTZ
        )
        LANGUAGE sql STABLE AS $$
        WITH RECURSIVE anchor_neighborhood AS (
            -- Seed: anchor entities themselves (depth 0)
            SELECT unnest(p_anchor_entity_ids) AS eid, 0 AS depth
            WHERE p_anchor_entity_ids IS NOT NULL
              AND cardinality(p_anchor_entity_ids) > 0

            UNION

            -- BFS neighbors (bidirectional — relations are directed but we walk both ways)
            SELECT CASE WHEN r.from_entity_id = an.eid
                        THEN r.to_entity_id
                        ELSE r.from_entity_id END AS eid,
                   an.depth + 1
            FROM anchor_neighborhood an
            JOIN entity_relations r
              ON (r.from_entity_id = an.eid OR r.to_entity_id = an.eid)
            WHERE an.depth < p_anchor_hop_depth
              AND r.workspace_id = p_workspace_id
              AND (r.valid_to IS NULL OR r.valid_to > now())
        ),
        graph_walk AS (
            SELECT r.to_entity_id AS eid, 1 AS depth,
                   ARRAY[r.from_entity_id, r.to_entity_id] AS path
            FROM entity_relations r
            WHERE r.workspace_id = p_workspace_id
              AND r.from_entity_id = ANY(p_seed_ids)
              AND (r.valid_to IS NULL OR r.valid_to > now())

            UNION ALL

            SELECT r.to_entity_id, gw.depth + 1, gw.path || r.to_entity_id
            FROM entity_relations r
            JOIN graph_walk gw ON r.from_entity_id = gw.eid
            WHERE r.workspace_id = p_workspace_id
              AND gw.depth < p_max_depth
              AND NOT (r.to_entity_id = ANY(gw.path))
              AND (r.valid_to IS NULL OR r.valid_to > now())
        ),
        graph_facts AS (
            SELECT kf.id AS fact_id, kf.entity_id,
                   ROW_NUMBER() OVER (ORDER BY MIN(gw.depth)) AS rank_ix
            FROM graph_walk gw
            JOIN knowledge_facts kf ON kf.entity_id = gw.eid
            WHERE kf.workspace_id = p_workspace_id
              AND kf.superseded_at IS NULL
              AND (
                  cardinality(COALESCE(p_anchor_entity_ids, ARRAY[]::UUID[])) = 0
                  OR kf.entity_id IN (SELECT eid FROM anchor_neighborhood)
              )
            GROUP BY kf.id, kf.entity_id
        ),
        vector_facts AS (
            SELECT f.source_fact_id AS fact_id,
                   ROW_NUMBER() OVER (ORDER BY emb.vector <=> p_query_embed) AS rank_ix
            FROM embeddings emb
            JOIN fragments f ON f.id = emb.source_id
            JOIN knowledge_facts kf ON kf.id = f.source_fact_id
            WHERE emb.workspace_id = p_workspace_id
              AND emb.source_type = 'fragment'
              AND f.source_fact_id IS NOT NULL
              AND kf.superseded_at IS NULL
              AND (
                  cardinality(COALESCE(p_anchor_entity_ids, ARRAY[]::UUID[])) = 0
                  OR kf.entity_id IN (SELECT eid FROM anchor_neighborhood)
              )
            ORDER BY emb.vector <=> p_query_embed
            LIMIT p_match_count * 3
        ),
        fts_facts AS (
            SELECT kf.id AS fact_id,
                   ROW_NUMBER() OVER (ORDER BY pgroonga_score(kf.tableoid, kf.ctid) DESC) AS rank_ix
            FROM knowledge_facts kf
            JOIN entities e ON e.id = kf.entity_id
            WHERE kf.workspace_id = p_workspace_id
              AND kf.superseded_at IS NULL
              AND (kf.object_value &@~ p_fts_query
                   OR kf.source_quote &@~ p_fts_query
                   OR e.name &@~ p_fts_query)
              AND (
                  cardinality(COALESCE(p_anchor_entity_ids, ARRAY[]::UUID[])) = 0
                  OR kf.entity_id IN (SELECT eid FROM anchor_neighborhood)
              )
            LIMIT p_match_count * 3
        ),
        combined AS (
            SELECT COALESCE(g.fact_id, v.fact_id, f.fact_id) AS fact_id,
                COALESCE(1.0/(p_rrf_k + g.rank_ix), 0) * p_graph_weight
              + COALESCE(1.0/(p_rrf_k + v.rank_ix), 0) * p_vector_weight
              + COALESCE(1.0/(p_rrf_k + f.rank_ix), 0) * p_fts_weight AS rrf_score,
                concat_ws('+',
                    CASE WHEN g.fact_id IS NOT NULL THEN 'graph' END,
                    CASE WHEN v.fact_id IS NOT NULL THEN 'vector' END,
                    CASE WHEN f.fact_id IS NOT NULL THEN 'fts' END) AS sources
            FROM graph_facts g
            FULL OUTER JOIN vector_facts v ON g.fact_id = v.fact_id
            FULL OUTER JOIN fts_facts f ON COALESCE(g.fact_id, v.fact_id) = f.fact_id
        )
        SELECT c.fact_id, kf.entity_id, e.name AS entity_name,
               kf.predicate, kf.object_value,
               c.rrf_score, c.sources,
               COALESCE(frag.importance, 0.5)::float AS importance,
               COALESCE(frag.fragment_type::text, 'fact') AS fragment_type,
               kf.last_accessed_at
        FROM combined c
        JOIN knowledge_facts kf ON kf.id = c.fact_id
        JOIN entities e ON e.id = kf.entity_id
        LEFT JOIN LATERAL (
            SELECT f2.importance, f2.fragment_type
            FROM fragments f2
            WHERE f2.source_fact_id = kf.id
            ORDER BY f2.created_at DESC
            LIMIT 1
        ) frag ON TRUE
        ORDER BY c.rrf_score DESC
        LIMIT p_match_count;
        $$
    """)


def downgrade() -> None:
    # Revert to v3 (11 params, same body without anchor_neighborhood).
    op.execute(
        "DROP FUNCTION IF EXISTS hybrid_graph_search("
        "UUID, TEXT, TEXT, vector, UUID[], UUID[], INT, FLOAT, FLOAT, FLOAT, INT, INT, INT)"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION hybrid_graph_search(
            p_workspace_id UUID,
            p_query_text TEXT,
            p_fts_query TEXT,
            p_query_embed vector(384),
            p_seed_ids UUID[],
            p_match_count INT DEFAULT 20,
            p_graph_weight FLOAT DEFAULT 1.0,
            p_vector_weight FLOAT DEFAULT 1.0,
            p_fts_weight FLOAT DEFAULT 0.5,
            p_max_depth INT DEFAULT 2,
            p_rrf_k INT DEFAULT 60
        ) RETURNS TABLE(
            fact_id UUID,
            entity_id UUID,
            entity_name TEXT,
            predicate TEXT,
            object_value TEXT,
            rrf_score NUMERIC,
            sources TEXT,
            importance FLOAT,
            fragment_type TEXT,
            last_accessed_at TIMESTAMPTZ
        )
        LANGUAGE sql STABLE AS $$
        WITH RECURSIVE graph_walk AS (
            SELECT r.to_entity_id AS eid, 1 AS depth,
                   ARRAY[r.from_entity_id, r.to_entity_id] AS path
            FROM entity_relations r
            WHERE r.workspace_id = p_workspace_id
              AND r.from_entity_id = ANY(p_seed_ids)
              AND (r.valid_to IS NULL OR r.valid_to > now())

            UNION ALL

            SELECT r.to_entity_id, gw.depth + 1, gw.path || r.to_entity_id
            FROM entity_relations r
            JOIN graph_walk gw ON r.from_entity_id = gw.eid
            WHERE r.workspace_id = p_workspace_id
              AND gw.depth < p_max_depth
              AND NOT (r.to_entity_id = ANY(gw.path))
              AND (r.valid_to IS NULL OR r.valid_to > now())
        ),
        graph_facts AS (
            SELECT kf.id AS fact_id, kf.entity_id,
                   ROW_NUMBER() OVER (ORDER BY MIN(gw.depth)) AS rank_ix
            FROM graph_walk gw
            JOIN knowledge_facts kf ON kf.entity_id = gw.eid
            WHERE kf.workspace_id = p_workspace_id
              AND kf.superseded_at IS NULL
            GROUP BY kf.id, kf.entity_id
        ),
        vector_facts AS (
            SELECT f.source_fact_id AS fact_id,
                   ROW_NUMBER() OVER (ORDER BY emb.vector <=> p_query_embed) AS rank_ix
            FROM embeddings emb
            JOIN fragments f ON f.id = emb.source_id
            WHERE emb.workspace_id = p_workspace_id
              AND emb.source_type = 'fragment'
              AND f.source_fact_id IS NOT NULL
            ORDER BY emb.vector <=> p_query_embed
            LIMIT p_match_count * 3
        ),
        fts_facts AS (
            SELECT kf.id AS fact_id,
                   ROW_NUMBER() OVER (ORDER BY pgroonga_score(kf.tableoid, kf.ctid) DESC) AS rank_ix
            FROM knowledge_facts kf
            JOIN entities e ON e.id = kf.entity_id
            WHERE kf.workspace_id = p_workspace_id
              AND kf.superseded_at IS NULL
              AND (kf.object_value &@~ p_fts_query
                   OR kf.source_quote &@~ p_fts_query
                   OR e.name &@~ p_fts_query)
            LIMIT p_match_count * 3
        ),
        combined AS (
            SELECT COALESCE(g.fact_id, v.fact_id, f.fact_id) AS fact_id,
                COALESCE(1.0/(p_rrf_k + g.rank_ix), 0) * p_graph_weight
              + COALESCE(1.0/(p_rrf_k + v.rank_ix), 0) * p_vector_weight
              + COALESCE(1.0/(p_rrf_k + f.rank_ix), 0) * p_fts_weight AS rrf_score,
                concat_ws('+',
                    CASE WHEN g.fact_id IS NOT NULL THEN 'graph' END,
                    CASE WHEN v.fact_id IS NOT NULL THEN 'vector' END,
                    CASE WHEN f.fact_id IS NOT NULL THEN 'fts' END) AS sources
            FROM graph_facts g
            FULL OUTER JOIN vector_facts v ON g.fact_id = v.fact_id
            FULL OUTER JOIN fts_facts f ON COALESCE(g.fact_id, v.fact_id) = f.fact_id
        )
        SELECT c.fact_id, kf.entity_id, e.name AS entity_name,
               kf.predicate, kf.object_value,
               c.rrf_score, c.sources,
               COALESCE(frag.importance, 0.5)::float AS importance,
               COALESCE(frag.fragment_type::text, 'fact') AS fragment_type,
               kf.last_accessed_at
        FROM combined c
        JOIN knowledge_facts kf ON kf.id = c.fact_id
        JOIN entities e ON e.id = kf.entity_id
        LEFT JOIN LATERAL (
            SELECT f2.importance, f2.fragment_type
            FROM fragments f2
            WHERE f2.source_fact_id = kf.id
            ORDER BY f2.created_at DESC
            LIMIT 1
        ) frag ON TRUE
        ORDER BY c.rrf_score DESC
        LIMIT p_match_count;
        $$
    """)
