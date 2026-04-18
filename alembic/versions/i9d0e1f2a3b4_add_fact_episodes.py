"""add fact_episodes M:N table + backfill + source_episode_id nullable

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-04-19

Phase 2 episodic+semantic hybrid. Adds fact_episodes(fact_id, episode_id, role)
connection table so one fact can link to many episodes (confidence accumulation
when the same fact is re-asserted across sessions).

- PK (fact_id, episode_id), reverse index on episode_id.
- role CHECK constraint covers research vocab (source/supporting/contradicting/reinforcing);
  backfill writes 'source' for every existing knowledge_facts.source_episode_id.
- knowledge_facts.source_episode_id kept but relaxed to NULLABLE. New writes can
  leave it NULL and rely solely on fact_episodes; legacy readers still work.
  A follow-up migration will drop the column once all reads are migrated.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "h8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fact_episodes",
        sa.Column(
            "fact_id", sa.UUID(),
            sa.ForeignKey("knowledge_facts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "episode_id", sa.UUID(),
            sa.ForeignKey("episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role", sa.String(length=20),
            server_default=sa.text("'source'"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("fact_id", "episode_id"),
        sa.CheckConstraint(
            "role IN ('source', 'supporting', 'contradicting', 'reinforcing')",
            name="ck_fact_episodes_role",
        ),
    )
    op.create_index("ix_fact_episodes_episode", "fact_episodes", ["episode_id"])

    op.execute("""
        INSERT INTO fact_episodes (fact_id, episode_id, role, created_at)
        SELECT id, source_episode_id, 'source', recorded_at
        FROM knowledge_facts
        WHERE source_episode_id IS NOT NULL
        ON CONFLICT (fact_id, episode_id) DO NOTHING
    """)

    op.alter_column(
        "knowledge_facts", "source_episode_id",
        existing_type=sa.UUID(), nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "knowledge_facts", "source_episode_id",
        existing_type=sa.UUID(), nullable=False,
    )
    op.drop_index("ix_fact_episodes_episode", table_name="fact_episodes")
    op.drop_table("fact_episodes")
