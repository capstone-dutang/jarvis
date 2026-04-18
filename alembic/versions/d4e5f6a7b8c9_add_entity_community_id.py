"""add entity community_id for Leiden clustering

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-18

Adds community_id column to entities table for pre-computed Leiden community
assignments. Used by recall's context assembly (MMR) to boost diversity across
communities. See: docs/research/2026-04-16-optimal-subset-selection.md
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add community_id column + index to entities."""
    op.add_column("entities", sa.Column("community_id", sa.Integer(), nullable=True))
    op.create_index("ix_entities_community_id", "entities", ["community_id"])


def downgrade() -> None:
    """Remove community_id column + index."""
    op.drop_index("ix_entities_community_id", table_name="entities")
    op.drop_column("entities", "community_id")
