"""add turns + turn_subjects + daily_subject_summaries + entities.parent_id

Revision ID: j0e1f2a3b4c5
Revises: i9d0e1f2a3b4
Create Date: 2026-05-07

비전 재정의 (자비스 = AI 대화의 git + 노션 스타일 위키) 우선순위 1.
데이터 모델 확장:
- entities.parent_id: 주제 계층 (NULL=최상위). UI는 평면이지만 데이터로 보존.
- turns: 턴 단위 저장. raw 트랜스크립트의 message 단위.
- turn_subjects: 턴 ↔ 주제 M:N. 한 턴이 여러 주제에 속할 수 있음.
- daily_subject_summaries: (workspace, subject, date) 단위 요약.
  일/주/월 zoom 뷰는 이 요약을 조합해서 만듦.

마이그레이션 후 기존 episode 데이터는 그대로 살아있되, raw 트랜스크립트에서
새로 ingest 시 turn 단위로 들어감 (legacy episode는 후속 작업에서 처리).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "i9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. entities.parent_id — 주제 계층 (NULL = 최상위 주제)
    op.add_column(
        "entities",
        sa.Column(
            "parent_id", sa.UUID(),
            sa.ForeignKey("entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_entities_parent_id", "entities", ["parent_id"])

    # 2. turns — 턴 단위 저장. 트랜스크립트의 한 message
    op.create_table(
        "turns",
        sa.Column(
            "id", sa.UUID(), primary_key=True,
            server_default=sa.text("gen_random_uuid()"), nullable=False,
        ),
        sa.Column(
            "workspace_id", sa.UUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "episode_id", sa.UUID(),
            sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')",
            name="ck_turns_role",
        ),
        sa.UniqueConstraint("episode_id", "sequence", name="uq_turns_episode_seq"),
    )
    op.create_index(
        "ix_turns_workspace_time", "turns",
        ["workspace_id", sa.text("timestamp DESC")],
    )
    op.create_index("ix_turns_episode", "turns", ["episode_id"])

    # 3. turn_subjects — M:N (한 턴이 여러 주제에 동시 소속 가능)
    op.create_table(
        "turn_subjects",
        sa.Column(
            "turn_id", sa.UUID(),
            sa.ForeignKey("turns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id", sa.UUID(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id", sa.UUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("turn_id", "subject_id"),
    )
    op.create_index("ix_turn_subjects_subject", "turn_subjects", ["subject_id"])
    op.create_index(
        "ix_turn_subjects_workspace_subject",
        "turn_subjects", ["workspace_id", "subject_id"],
    )

    # 4. daily_subject_summaries — (workspace, subject, date) 단위 요약
    op.create_table(
        "daily_subject_summaries",
        sa.Column(
            "id", sa.UUID(), primary_key=True,
            server_default=sa.text("gen_random_uuid()"), nullable=False,
        ),
        sa.Column(
            "workspace_id", sa.UUID(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id", sa.UUID(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id", "subject_id", "date",
            name="uq_dss_workspace_subject_date",
        ),
    )
    op.create_index(
        "ix_dss_workspace_date",
        "daily_subject_summaries",
        ["workspace_id", sa.text("date DESC")],
    )
    op.create_index("ix_dss_subject", "daily_subject_summaries", ["subject_id"])


def downgrade() -> None:
    op.drop_index("ix_dss_subject", table_name="daily_subject_summaries")
    op.drop_index("ix_dss_workspace_date", table_name="daily_subject_summaries")
    op.drop_table("daily_subject_summaries")

    op.drop_index("ix_turn_subjects_workspace_subject", table_name="turn_subjects")
    op.drop_index("ix_turn_subjects_subject", table_name="turn_subjects")
    op.drop_table("turn_subjects")

    op.drop_index("ix_turns_episode", table_name="turns")
    op.drop_index("ix_turns_workspace_time", table_name="turns")
    op.drop_table("turns")

    op.drop_index("ix_entities_parent_id", table_name="entities")
    op.drop_column("entities", "parent_id")
