"""All database table definitions."""

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


# ── Enums ──


class WorkspaceRole(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    contributor = "contributor"
    viewer = "viewer"


class EntityType(enum.StrEnum):
    person = "person"
    organization = "organization"
    location = "location"
    event = "event"
    concept = "concept"
    product = "product"
    preference = "preference"
    procedure = "procedure"
    other = "other"
    # P6: episode-level topic anchors (session labels like "JARVIS > X (date)").
    # These are excluded from wiki index / dashboard top entities / topic_map.
    episode_topic = "episode_topic"
    # "다룬 주제" work-themes (e.g. "Recall 품질 수복", "워크스페이스 UX 설계").
    # Added to the DB enum earlier; the Python member must stay in sync or the
    # ORM raises LookupError loading these rows (broke recall — 2026-06-04).
    theme = "theme"


class RelationType(enum.StrEnum):
    supports = "supports"
    contradicts = "contradicts"
    depends_on = "depends_on"
    part_of = "part_of"
    related_to = "related_to"


class TrustLevel(enum.StrEnum):
    grounded = "grounded"
    low_trust = "low_trust"


class FragmentType(enum.StrEnum):
    fact = "fact"
    decision = "decision"
    error = "error"
    preference = "preference"
    procedure = "procedure"
    relation = "relation"


# ── Users & Workspaces ──


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memberships: Mapped[list["WorkspaceMember"]] = relationship(back_populates="user")


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cumulative narrative summary — "지금까지 흐름" 누적. description 과 분리:
    # description 은 정체성/목적 (수동, 잘 안 변함), cumulative_summary 는 매
    # 일기마다 AI 가 갱신하는 흐름 요약. brief 의 sub line / recall 의 컨텍스트
    # 가 이 컬럼을 읽어 다른 세션 AI 에게 "지난 작업" 을 전달한다.
    cumulative_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["WorkspaceMember"]] = relationship(back_populates="workspace")
    sessions: Mapped[list["Session"]] = relationship(back_populates="workspace")
    entities: Mapped[list["Entity"]] = relationship(back_populates="workspace")


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_user"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[WorkspaceRole] = mapped_column(Enum(WorkspaceRole), nullable=False, default=WorkspaceRole.contributor)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")


# ── Sessions & Episodes ──


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    client_type: Mapped[str] = mapped_column(String(50), nullable=False)  # chatgpt, claude, cli, web
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # openai, anthropic, google, manual
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="sessions")
    episodes: Mapped[list["Episode"]] = relationship(back_populates="session")


class Episode(Base):
    """Immutable conversation transcript chunk. Never updated or deleted."""

    __tablename__ = "episodes"
    __table_args__ = (
        UniqueConstraint("workspace_id", "content_hash", name="uq_episode_workspace_content"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # raw transcript
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    diary_entry: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, object] | None] = mapped_column("metadata", JSONB, nullable=True)
    processing_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending/processing/done/failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["Session"] = relationship(back_populates="episodes")
    facts: Mapped[list["KnowledgeFact"]] = relationship(back_populates="source_episode")
    fact_links: Mapped[list["FactEpisode"]] = relationship(
        back_populates="episode", cascade="all, delete-orphan",
    )


# ── Knowledge Graph ──


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name_normalized", name="uq_workspace_entity_name"),
        Index("ix_entity_workspace_type", "workspace_id", "entity_type"),
        # HNSW index for entity name embedding (Stage 2 entity resolution)
        # Based on: research/multilingual-kg line 200, ef_construction=200
        Index(
            "ix_entity_name_embedding_hnsw",
            "name_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 200},
            postgresql_ops={"name_embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[EntityType] = mapped_column(Enum(EntityType), nullable=False)
    aliases: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True, default=list)
    # Direct embedding on entity table for fast Stage 2 vector lookup
    # Based on: research/multilingual-kg line 72
    name_embedding = mapped_column(Vector(384), nullable=True)
    # Pre-computed Leiden community assignment. Used by recall MMR for diversity.
    # Recomputed offline by worker after batch processing.
    community_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    # Subject hierarchy: NULL = top-level subject, else child of parent_id.
    # UI shows top-level subjects as flat horizontal list; hierarchy is data-side.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    # AI-written wiki-style article for this entity. Long-form narrative that
    # explains what this subject is, history, current state — shown at the
    # top of the entity modal. None ⇒ falls back to facts/relations only.
    wiki_article: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="entities")


class EntityAlias(Base):
    __tablename__ = "entity_aliases"
    __table_args__ = (
        UniqueConstraint("workspace_id", "alias", name="uq_entity_aliases_ws_alias"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    lang: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeFact(Base):
    """Bitemporal fact: 4 timestamps managed by server."""

    __tablename__ = "knowledge_facts"
    __table_args__ = (
        Index("ix_fact_entity_predicate", "entity_id", "predicate"),
        Index("ix_fact_workspace_current", "workspace_id", "superseded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    predicate: Mapped[str] = mapped_column(String(255), nullable=False)
    object_value: Mapped[str] = mapped_column(Text, nullable=False)
    source_episode_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), nullable=True)
    source_quote: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trust_level: Mapped[TrustLevel] = mapped_column(Enum(TrustLevel), nullable=False, default=TrustLevel.grounded)

    # Bitemporal timestamps — all server-managed
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    source_episode: Mapped["Episode | None"] = relationship(back_populates="facts")
    episode_links: Mapped[list["FactEpisode"]] = relationship(
        back_populates="fact", cascade="all, delete-orphan", lazy="selectin",
    )


class FactEpisode(Base):
    """M:N link between KnowledgeFact and Episode.

    One fact may be asserted across many episodes (confidence accumulates);
    one episode may produce many facts. The `role` distinguishes the kind of
    support the episode provides for the fact.
    """

    __tablename__ = "fact_episodes"

    fact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_facts.id", ondelete="CASCADE"), primary_key=True,
    )
    episode_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="source")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )

    fact: Mapped["KnowledgeFact"] = relationship(back_populates="episode_links")
    episode: Mapped["Episode"] = relationship(back_populates="fact_links")


class EntityRelation(Base):
    __tablename__ = "entity_relations"
    __table_args__ = (
        Index("ix_relation_from_type", "from_entity_id", "relation_type"),
        Index("ix_relation_active", "from_entity_id", "valid_to"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    from_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    to_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    relation_type: Mapped[RelationType] = mapped_column(Enum(RelationType), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source_episode_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArtifactLink(Base):
    __tablename__ = "artifact_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=True)
    fact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_facts.id", ondelete="CASCADE"), nullable=True)
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)  # file, commit, url
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Fragment(Base):
    """Natural language text fragment (≤300 chars) for semantic search.

    Every new memory is stored as BOTH KnowledgeFact (structured query)
    AND Fragment (semantic search). This dual-store design ensures both
    exact predicate queries and fuzzy semantic recall work.
    """

    __tablename__ = "fragments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(String(500), nullable=False)  # ≤300 target, 500 hard limit
    fragment_type: Mapped[FragmentType] = mapped_column(Enum(FragmentType), nullable=False, default=FragmentType.fact)
    keywords: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    source_episode_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False
    )
    source_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_facts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Turn-level + Subject Tree + Daily Summary (2026-05-07 비전 재정의) ──


class Turn(Base):
    """A single message in a conversation transcript.

    Raw transcripts arrive as ordered turn arrays. Each Turn is one message
    (user/assistant/system/tool). Turns belong to an Episode (the session)
    and can be linked to multiple Subjects via TurnSubject (M:N).
    """

    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint("episode_id", "sequence", name="uq_turns_episode_seq"),
        Index("ix_turns_workspace_time", "workspace_id", "timestamp"),
        Index("ix_turns_episode", "episode_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    episode_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False,
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user/assistant/system/tool
    text: Mapped[str] = mapped_column(Text, nullable=False)
    cleaned_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    subject_links: Mapped[list["TurnSubject"]] = relationship(
        back_populates="turn", cascade="all, delete-orphan",
    )


class TurnSubject(Base):
    """M:N link between Turn and Subject (Entity).

    One turn can belong to multiple subjects (e.g. a turn discussing
    "JARVIS의 OAuth 흐름" belongs to both [자비스] and [자비스-인증]).
    No priority field — UI shows turn under any subject it's linked to.
    """

    __tablename__ = "turn_subjects"
    __table_args__ = (
        Index("ix_turn_subjects_subject", "subject_id"),
        Index("ix_turn_subjects_workspace_subject", "workspace_id", "subject_id"),
    )

    turn_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("turns.id", ondelete="CASCADE"), primary_key=True,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    turn: Mapped["Turn"] = relationship(back_populates="subject_links")


class DailySubjectSummary(Base):
    """Summary of all turns linked to a Subject on a specific date.

    Generated when user runs reflect ("오늘 정리해"). Day/week/month
    zoom views are built by combining these (date, subject) summaries.
    """

    __tablename__ = "daily_subject_summaries"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "subject_id", "date",
            name="uq_dss_workspace_subject_date",
        ),
        Index("ix_dss_workspace_date", "workspace_id", "date"),
        Index("ix_dss_subject", "subject_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False,
    )
    date: Mapped[datetime] = mapped_column(Date, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    turn_count: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        Index(
            "ix_embedding_vector_hnsw",
            "vector",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 200},
            postgresql_ops={"vector": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)  # entity, fact, episode
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    vector = mapped_column(Vector(384), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── OAuth ──


class OAuthClient(Base):
    """Dynamic Client Registration (RFC 7591)."""

    __tablename__ = "oauth_clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    client_secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    redirect_uris: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    grant_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
