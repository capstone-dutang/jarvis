"""dedupe duplicate turns in active workspaces

Revision ID: m3h4c5d6e7f8
Revises: l2g3b4c5d6e7
Create Date: 2026-05-29

Phase P5 of jarvis vision finalization.

Background:
  Repeated re-ingest of the same Claude Code session (different chunk sizes,
  classifier reruns, append v1/v2 policy) created identical (episode_id, role,
  text) rows in `turns`. Baseline counts before dedupe:
    - ai-clean-test (71a0ddee-a88c-4ca3-978a-ee5c61e5ed63): 19,821 turns
    - ai-argos     (95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd):  1,839 turns
    - duplicates_to_delete (rn > 1): 5,793

Strategy:
  PARTITION BY (episode_id, role, text), keep the row with the lowest sequence
  (earliest insertion / canonical order). Delete the rest. Only touches the two
  active operational workspaces — vision-test and the other 5 hidden workspaces
  are left untouched (P2 already hid them).

  This migration is idempotent: re-running it after the dedupe produces a
  no-op DELETE because no further duplicates exist.

  The downgrade is intentionally a no-op — we cannot restore the deleted rows,
  and there is nothing the schema needs to revert.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "m3h4c5d6e7f8"
down_revision: Union[str, Sequence[str], None] = "l2g3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ACTIVE_WS_IDS = (
    "71a0ddee-a88c-4ca3-978a-ee5c61e5ed63",  # ai-clean-test
    "95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd",  # ai-argos
)


def upgrade() -> None:
    op.execute(
        f"""
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY episode_id, role, text
                       ORDER BY sequence ASC, id ASC
                   ) AS rn
            FROM turns
            WHERE workspace_id IN ('{ACTIVE_WS_IDS[0]}', '{ACTIVE_WS_IDS[1]}')
        )
        DELETE FROM turns
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1);
        """
    )


def downgrade() -> None:
    # Cannot restore deleted rows; no schema changes to revert.
    pass
