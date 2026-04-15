"""add pgroonga indexes and hybrid_graph_search function

Revision ID: a1b2c3d4e5f6
Revises: bdeaa9ae2598
Create Date: 2026-04-15

"""

from typing import Sequence, Union

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "bdeaa9ae2598"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add PGroonga indexes and hybrid_graph_search SQL function."""

    # PGroonga indexes for Korean + English FTS
    op.execute("""
        CREATE INDEX idx_knowledge_facts_pgroonga_fts
        ON knowledge_facts
        USING pgroonga (object_value, source_quote)
    """)

    op.execute("""
        CREATE INDEX idx_entities_pgroonga_name
        ON entities
        USING pgroonga (name)
    """)

    op.execute("""
        CREATE INDEX idx_episodes_pgroonga_content
        ON episodes
        USING pgroonga (content)
    """)

    # hybrid_graph_search() function — 3-way RRF (graph + vector + FTS)
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
            WHERE kf.workspace_id = p_workspace_id
              AND kf.superseded_at IS NULL
              AND (kf.object_value &@~ p_query_text OR kf.source_quote &@~ p_query_text)
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


def downgrade() -> None:
    """Remove PGroonga indexes and hybrid_graph_search function."""
    op.execute("DROP FUNCTION IF EXISTS hybrid_graph_search(UUID, TEXT, vector, UUID[], INT, FLOAT, FLOAT, FLOAT, INT, INT)")
    op.execute("DROP INDEX IF EXISTS idx_episodes_pgroonga_content")
    op.execute("DROP INDEX IF EXISTS idx_entities_pgroonga_name")
    op.execute("DROP INDEX IF EXISTS idx_knowledge_facts_pgroonga_fts")
