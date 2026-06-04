"""repair entity graph — episode_topic promotion + parent_id + aliases

Revision ID: q7l8m9n0o1p2
Revises: p6k7l8m9n0o1
Create Date: 2026-05-29

Phase P6 part 2 of jarvis vision finalization — "위키 그래프 정상화".

Background:
  In ai-clean-test (71a0ddee), 36 entities have names that are session-labels
  (contain '>', '+', or 'YYYY-MM-DD'). These are episode-level topic anchors,
  NOT concept entities — they bloat the wiki index and pollute graph queries.

  Solution: promote them to entity_type='episode_topic' (added in the previous
  revision p6k7l8m9n0o1). Retrieval/dashboard top-entities filters now skip
  this type. The episodes still link to these via turn_subjects (date-bucket /
  subject-feed UI still works), but they no longer appear in the wiki index.

  In ai-clean-test, 57/96 entities are isolated (parent_id IS NULL). Many are
  obvious children of JARVIS or Argos by name prefix. We repair parent_id via
  case-insensitive prefix match (excluding episode_topic rows).

  In ai-argos (95782196), the canonical Argos entity is missing aliases — we
  add '아르고스' / 'argos' so anchor-resolution catches Korean and lowercase
  variants.

Strategy:
  1) UPDATE entities SET entity_type='episode_topic' WHERE name matches
     session-label patterns. Hand-vetted exception: 'MCP OAuth 2.1 PKCE+DCR'
     (id=0899eae2-…) is a real concept, not a session label.
  2) UPDATE entities SET parent_id=<JARVIS|Argos> WHERE name prefix matches.
  3) UPDATE entities SET aliases for ai-argos Argos canonical row.

  All steps are idempotent.
  Downgrade is a no-op — we can't reconstruct previous values.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "q7l8m9n0o1p2"
down_revision: Union[str, Sequence[str], None] = "p6k7l8m9n0o1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


WS_AI_CLEAN = "71a0ddee-a88c-4ca3-978a-ee5c61e5ed63"
WS_AI_ARGOS = "95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd"

# Parent IDs in ai-clean-test
JARVIS_IN_AI_CLEAN = "b62e97d4-2335-49fc-bf12-0c3fe034deed"
ARGOS_IN_AI_CLEAN = "48166f4f-a87c-4513-8607-e41b83d5005b"

# Parent ID in ai-argos
ARGOS_IN_AI_ARGOS = "42984701-51f2-4636-8d18-bf01c8ab7cc5"


def upgrade() -> None:
    # ── 1) Promote session-label entities to entity_type='episode_topic' ──
    op.execute(
        f"""
        UPDATE entities
           SET entity_type = 'episode_topic'
         WHERE workspace_id IN ('{WS_AI_CLEAN}', '{WS_AI_ARGOS}')
           AND entity_type != 'episode_topic'
           AND (name LIKE '%>%' OR name LIKE '%+%' OR name ~ '\\d{{4}}-\\d{{2}}-\\d{{2}}')
           AND id != '0899eae2-c3a8-4006-9404-6dd090436882'
        """
    )

    # ── 2a) Restore parent_id for JARVIS-prefixed entities ──
    op.execute(
        f"""
        UPDATE entities
           SET parent_id = '{JARVIS_IN_AI_CLEAN}'
         WHERE workspace_id = '{WS_AI_CLEAN}'
           AND parent_id IS NULL
           AND entity_type != 'episode_topic'
           AND id != '{JARVIS_IN_AI_CLEAN}'
           AND (LOWER(name) LIKE 'jarvis %'
             OR LOWER(name) LIKE 'jarvis_%'
             OR LOWER(name) LIKE 'jarvis-%'
             OR LOWER(name) LIKE 'jarvis.%')
        """
    )

    # ── 2b) Restore parent_id for Argos-prefixed entities ──
    op.execute(
        f"""
        UPDATE entities
           SET parent_id = '{ARGOS_IN_AI_CLEAN}'
         WHERE workspace_id = '{WS_AI_CLEAN}'
           AND parent_id IS NULL
           AND entity_type != 'episode_topic'
           AND id != '{ARGOS_IN_AI_CLEAN}'
           AND (LOWER(name) LIKE 'argos %'
             OR LOWER(name) LIKE 'argos_%'
             OR LOWER(name) LIKE 'argos-%'
             OR LOWER(name) LIKE 'argos.%')
        """
    )

    # ── 3) Add aliases to ai-argos canonical Argos entity ──
    # Merges with existing aliases instead of overwriting.
    op.execute(
        f"""
        UPDATE entities
           SET aliases = COALESCE(aliases, '[]'::jsonb) || '["아르고스","argos"]'::jsonb
         WHERE id = '{ARGOS_IN_AI_ARGOS}'
           AND workspace_id = '{WS_AI_ARGOS}'
           AND NOT (COALESCE(aliases, '[]'::jsonb) @> '["아르고스"]'::jsonb)
        """
    )


def downgrade() -> None:
    pass
