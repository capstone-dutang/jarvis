"""add fragment table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-15

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add fragments table for semantic search."""
    op.create_table(
        "fragments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.String(length=500), nullable=False),
        sa.Column(
            "fragment_type",
            sa.Enum("fact", "decision", "error", "preference", "procedure", "relation", name="fragmenttype"),
            nullable=False,
            server_default="fact",
        ),
        sa.Column("keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("source_episode_id", sa.UUID(), nullable=False),
        sa.Column("source_fact_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_fact_id"], ["knowledge_facts.id"], ondelete="SET NULL"),
    )

    # PGroonga index for FTS on fragment content
    op.execute("""
        CREATE INDEX idx_fragments_pgroonga_content
        ON fragments USING pgroonga (content)
    """)

    # Regular indexes
    op.create_index("ix_fragments_workspace_id", "fragments", ["workspace_id"])
    op.create_index("ix_fragments_source_episode_id", "fragments", ["source_episode_id"])


def downgrade() -> None:
    """Remove fragments table."""
    op.drop_index("ix_fragments_source_episode_id", table_name="fragments")
    op.drop_index("ix_fragments_workspace_id", table_name="fragments")
    op.execute("DROP INDEX IF EXISTS idx_fragments_pgroonga_content")
    op.drop_table("fragments")
    op.execute("DROP TYPE IF EXISTS fragmenttype")
