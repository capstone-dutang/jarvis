"""add episode_topic to entitytype enum

Revision ID: p6k7l8m9n0o1
Revises: o5j6e7f8g9h0
Create Date: 2026-05-29

Phase P6 part 1 of jarvis vision finalization — "위키 그래프 정상화".

Background:
  PostgreSQL requires `ALTER TYPE ... ADD VALUE` to be COMMITTED before the new
  enum value can be referenced in subsequent SQL (UnsafeNewEnumValueUsageError).
  Split P6 into 2 alembic revisions so the enum-add commits before the data
  updates that use it (in q7l8m9n0o1p2_repair_entity_graph.py).

  Idempotent (ADD VALUE IF NOT EXISTS).
  Downgrade is a no-op — Postgres does not support DROP VALUE on enums.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "p6k7l8m9n0o1"
down_revision: Union[str, Sequence[str], None] = "o5j6e7f8g9h0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Must run outside a transaction in some DBs. asyncpg / Postgres tolerates
    # ADD VALUE inside a tx; the constraint is just that the value can't be
    # used in the same tx. We rely on alembic committing this revision
    # before the next one runs.
    op.execute("ALTER TYPE entitytype ADD VALUE IF NOT EXISTS 'episode_topic'")


def downgrade() -> None:
    # Postgres does not support removing enum values safely.
    pass
