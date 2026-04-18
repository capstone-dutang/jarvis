"""add entity_aliases table + seed CROSS_LINGUAL_ALIASES

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-18

Phase 1 entity-anchored retrieval Sub-Phase A. Adds:
- entity_aliases(workspace_id, entity_id, alias, lang, created_at)
- Unique (workspace_id, alias) so an alias maps to exactly one entity per workspace
- Seed: CROSS_LINGUAL_ALIASES 4 pairs (JARVIS/자비스, SecondBrain/세컨드브레인,
  Argos/아르고스, fundmessenger/펀드메신저) matched against existing entities.

Aho-Corasick automaton (core/anchor_matching.py) builds from
entities.name UNION entity_aliases.alias at recall time.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entity_aliases",
        sa.Column(
            "id", sa.UUID(), primary_key=True,
            server_default=sa.text("gen_random_uuid()"), nullable=False,
        ),
        sa.Column(
            "workspace_id", sa.UUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "entity_id", sa.UUID(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("lang", sa.String(length=10), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.UniqueConstraint("workspace_id", "alias", name="uq_entity_aliases_ws_alias"),
    )
    op.create_index("ix_entity_aliases_workspace", "entity_aliases", ["workspace_id"])
    op.create_index("ix_entity_aliases_entity", "entity_aliases", ["entity_id"])

    # Seed CROSS_LINGUAL_ALIASES (4 pairs). JOIN matches entity.name exactly.
    # ON CONFLICT (workspace_id, alias) DO NOTHING prevents duplicate insert if
    # this migration is ever rerun on top of a partially-seeded state.
    op.execute("""
        INSERT INTO entity_aliases (id, workspace_id, entity_id, alias, lang)
        SELECT gen_random_uuid(), e.workspace_id, e.id, a.alias_val, 'ko'
        FROM entities e
        JOIN (VALUES
            ('JARVIS', '자비스'),
            ('SecondBrain', '세컨드브레인'),
            ('Argos', '아르고스'),
            ('fundmessenger', '펀드메신저')
        ) AS a(name_en, alias_val) ON e.name = a.name_en
        ON CONFLICT ON CONSTRAINT uq_entity_aliases_ws_alias DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index("ix_entity_aliases_entity", table_name="entity_aliases")
    op.drop_index("ix_entity_aliases_workspace", table_name="entity_aliases")
    op.drop_table("entity_aliases")
