"""add ingest_ledger table for per-file ingest tracking

Revision ID: n4i5d6e7f8g9
Revises: m3h4c5d6e7f8
Create Date: 2026-05-29

Phase P4 of jarvis vision finalization — "본대화 N개 중 무엇이 올라갔나"

Background:
  Until now, episodes only carried `metadata.external_session_id` and
  `metadata.source_path` as freeform JSON, which made it impossible to ask
  "is this jsonl file on disk already in the cloud?" without scanning every
  episode. The Home dashboard needs that answer at-a-glance, so we add a
  dedicated ledger that records every ingest as its own row.

  One ingest = one row. Re-ingesting the same external_session_id appends
  another row with dedup_decision='append_v2' (user-confirmed policy:
  "Append v1·v2 묶음 — keep both, group in UI"). The row that won
  content_hash dedup keeps episode_id pointing at the same episode; the
  second arrival just records that the AI tried to push it again.

  source_file_path_normalized is the lowercased forward-slash form so
  case-insensitive Windows paths still join on equality. The active
  pipeline writes this via core.path_normalize.normalize_jsonl_path; the
  legacy backfill below derives it by SQL LOWER/REPLACE.

Backfill:
  All existing episodes in *active* workspaces get one row each, ordered by
  created_at within (workspace_id, external_session_id). The earliest entry
  per session is dedup_decision='new'; subsequent ones (re-ingested chunks)
  become 'append_v2'. Hidden workspaces (vision-test, reseed-test, etc.) are
  skipped on purpose — P2 already excluded them from operational views.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "n4i5d6e7f8g9"
down_revision: Union[str, Sequence[str], None] = "m3h4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_ledger (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            source_file_path TEXT NOT NULL,
            source_file_path_normalized TEXT NOT NULL,
            source_file_sha256 VARCHAR(64),
            external_session_id VARCHAR(64),
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ingested_via VARCHAR(40) NOT NULL,
            pipeline_version VARCHAR(20),
            episode_id UUID REFERENCES episodes(id) ON DELETE SET NULL,
            turn_count INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'ingested',
            dedup_decision VARCHAR(20),
            notes TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ledger_ws_ingested "
        "ON ingest_ledger(workspace_id, ingested_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ledger_sid "
        "ON ingest_ledger(external_session_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ledger_normalized "
        "ON ingest_ledger(source_file_path_normalized)"
    )

    # ── Backfill from existing active-workspace episodes ──
    # Step 1: insert every active-workspace episode as 'new'.
    op.execute(
        """
        INSERT INTO ingest_ledger (
            workspace_id, source_file_path, source_file_path_normalized,
            external_session_id, ingested_at, ingested_via,
            episode_id, turn_count, status, dedup_decision, notes
        )
        SELECT
            e.workspace_id,
            COALESCE(e.metadata->>'source_path', ''),
            LOWER(REPLACE(COALESCE(e.metadata->>'source_path', ''), '\\', '/')),
            e.metadata->>'external_session_id',
            e.created_at,
            COALESCE(e.metadata->>'ingested_via', 'legacy'),
            e.id,
            COALESCE((SELECT COUNT(*) FROM turns t WHERE t.episode_id = e.id), 0),
            'ingested',
            'new',
            'backfill from episodes.metadata (P4)'
        FROM episodes e
        JOIN workspaces ws ON ws.id = e.workspace_id
        WHERE ws.status = 'active'
        """
    )

    # Step 2: for every (workspace, external_session_id) that has more than
    # one ledger row, mark all rows after the earliest as 'append_v2'. We
    # cannot reuse a CTE in an UPDATE on the same table directly with FROM,
    # so a subselect ROW_NUMBER works fine.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY workspace_id, external_session_id
                       ORDER BY ingested_at ASC, id ASC
                   ) AS rn
            FROM ingest_ledger
            WHERE external_session_id IS NOT NULL
              AND notes = 'backfill from episodes.metadata (P4)'
        )
        UPDATE ingest_ledger l
        SET dedup_decision = 'append_v2'
        FROM ranked r
        WHERE l.id = r.id AND r.rn > 1
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ledger_normalized")
    op.execute("DROP INDEX IF EXISTS ix_ledger_sid")
    op.execute("DROP INDEX IF EXISTS ix_ledger_ws_ingested")
    op.execute("DROP TABLE IF EXISTS ingest_ledger")
