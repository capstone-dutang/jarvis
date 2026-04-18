"""hybrid_search v3: p_fts_query param + fragment-based vector_facts + importance/type return

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-18

Three changes:
1. Add p_fts_query TEXT parameter (decoupled from natural-language p_query_text).
   Caller passes PGroonga OR query like "자비스 OR JARVIS".
2. vector_facts CTE uses fragment embeddings (source_type='fragment') and joins
   fragments.source_fact_id → fact_id. Section 8 design: dual store.
3. RETURNS TABLE gains importance, fragment_type, last_accessed_at columns.
   last_accessed_at is NULL::timestamptz here (column doesn't exist until f6a7b8c9d0e1).

Signature changed from 10 to 11 params so CREATE OR REPLACE alone won't work —
must DROP the old function first.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old 10-param signature — CREATE OR REPLACE can't change param list.
    op.execute(
        "DROP FUNCTION IF EXISTS hybrid_graph_search("
        "UUID, TEXT, vector, UUID[], INT, FLOAT, FLOAT, FLOAT, INT, INT)"
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
               NULL::timestamptz AS last_accessed_at
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
    # Remove new 11-param version.
    op.execute(
        "DROP FUNCTION IF EXISTS hybrid_graph_search("
        "UUID, TEXT, TEXT, vector, UUID[], INT, FLOAT, FLOAT, FLOAT, INT, INT)"
    )
    # Restore prior 10-param version from c3d4e5f6a7b8.
    op.execute("""
        CREATE OR REPLACE FUNCTION hybrid_graph_search(
            p_workspace_id UUID,
            p_query_text TEXT,
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
            sources TEXT
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
            SELECT emb.source_id AS fact_id,
                   ROW_NUMBER() OVER (ORDER BY emb.vector <=> p_query_embed) AS rank_ix
            FROM embeddings emb
            WHERE emb.workspace_id = p_workspace_id
              AND emb.source_type = 'fact'
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
              AND (kf.object_value &@~ p_query_text
                   OR kf.source_quote &@~ p_query_text
                   OR e.name &@~ p_query_text)
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
               c.rrf_score, c.sources
        FROM combined c
        JOIN knowledge_facts kf ON kf.id = c.fact_id
        JOIN entities e ON e.id = kf.entity_id
        ORDER BY c.rrf_score DESC
        LIMIT p_match_count;
        $$
    """)
