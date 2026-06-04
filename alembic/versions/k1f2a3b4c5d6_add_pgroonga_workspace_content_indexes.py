"""add pgroonga multi-column indexes (workspace_id, content)

Revision ID: k1f2a3b4c5d6
Revises: j0e1f2a3b4c5
Create Date: 2026-05-13

Phase 1 of plan sequential-munching-dove.md (A 결함 보강).

The single-column PGroonga indexes idx_episodes_pgroonga_content and
idx_fragments_pgroonga_content were created in earlier revisions
(a1b2c3d4e5f6, b2c3d4e5f6a7). When recall queries combine
`WHERE workspace_id = :ws AND content &@~ :q`, the planner picks
uq_episode_workspace_content (workspace_id + content_hash UNIQUE) first
and applies `content &@~` as a sequential Filter. PGroonga's
pgroonga_score then returns 0 by spec — "always returns 0.0 when full
text search isn't performed by index" — so ranking degenerates to ties.

A multi-column PGroonga index on (workspace_id, content) lets a single
index scan cover both predicates and yields real scores.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "k1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "j0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX idx_episodes_pgroonga_ws_content
        ON episodes
        USING pgroonga (workspace_id, content)
    """)
    op.execute("""
        CREATE INDEX idx_fragments_pgroonga_ws_content
        ON fragments
        USING pgroonga (workspace_id, content)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_fragments_pgroonga_ws_content")
    op.execute("DROP INDEX IF EXISTS idx_episodes_pgroonga_ws_content")
