"""add unique_turn_count to daily_subject_summaries

Revision ID: o5j6e7f8g9h0
Revises: n4i5d6e7f8g9
Create Date: 2026-05-29

Phase P8 of jarvis vision finalization — "JARVIS·Argos 도메인 균형 + unique_turn_count"

Background:
  Until now `turn_count` on daily_subject_summaries counted every linked
  turn, but a parent-subject row and its child-subject row both linked to
  the same turn would each report it — inflating totals when summing
  subject + sub-subjects in UI views.

  The new column `unique_turn_count` holds the number of *distinct* turns
  linked to (date, subject_id ∪ descendants) on that date. UI sums and
  the home dashboard then have a deduplicated count to display without
  re-querying.

Backfill:
  For every existing summary row, recompute unique_turn_count via a
  recursive CTE that gathers subject_id + all descendants, then counts
  DISTINCT turn ids on that date. Rows whose subject has no descendants
  end up with the same number as `turn_count` (no dedup needed) which
  is correct.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "o5j6e7f8g9h0"
down_revision: Union[str, Sequence[str], None] = "n4i5d6e7f8g9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE daily_subject_summaries
            ADD COLUMN IF NOT EXISTS unique_turn_count INTEGER
        """
    )

    # Backfill — for every existing row, count distinct turns linked
    # to (subject_id ∪ descendants) on dss.date.
    op.execute(
        """
        UPDATE daily_subject_summaries dss
        SET unique_turn_count = (
            SELECT COUNT(DISTINCT t.id)
            FROM turns t
            JOIN turn_subjects ts ON ts.turn_id = t.id
            WHERE t.workspace_id = dss.workspace_id
              AND DATE(t.timestamp AT TIME ZONE 'UTC') = dss.date
              AND ts.subject_id IN (
                WITH RECURSIVE st AS (
                    SELECT dss.subject_id AS id
                    UNION ALL
                    SELECT e.id
                    FROM entities e
                    JOIN st ON e.parent_id = st.id
                )
                SELECT id FROM st
              )
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE daily_subject_summaries DROP COLUMN IF EXISTS unique_turn_count"
    )
