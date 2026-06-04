"""add workspace status column

Revision ID: l2g3b4c5d6e7
Revises: k1f2a3b4c5d6
Create Date: 2026-05-29

Phase P2 of jarvis vision finalization.

Adds a status column to workspaces so that non-operational workspaces
(vision-test, reseed-test, bundle-verify, personal sandbox, etc.) can be
hidden from the UI / search / recall while preserving their data.

Values:
- active   : visible everywhere (default)
- hidden   : excluded from /workspaces by default; data preserved
- archived : reserved for future use (read-only legacy)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "l2g3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "k1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workspaces "
        "ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active'"
    )
    op.execute(
        "ALTER TABLE workspaces "
        "DROP CONSTRAINT IF EXISTS ck_ws_status"
    )
    op.execute(
        "ALTER TABLE workspaces "
        "ADD CONSTRAINT ck_ws_status "
        "CHECK (status IN ('active','hidden','archived'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE workspaces DROP CONSTRAINT IF EXISTS ck_ws_status")
    op.execute("ALTER TABLE workspaces DROP COLUMN IF EXISTS status")
