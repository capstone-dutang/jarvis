"""All database table definitions."""

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
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


class RelationType(enum.StrEnum):
    supports = "supports"
    contradicts = "contradicts"
    depends_on = "depends_on"
    part_of = "part_of"
    related_to = "related_to"


class TrustLevel(enum.StrEnum):
    grounded = "grounded"
    low_trust = "low_trust"


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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # raw transcript
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_: Mapped[dict[str, object] | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["Session"] = relationship(back_populates="episodes")
    facts: Mapped[list["KnowledgeFact"]] = relationship(back_populates="source_episode")


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="entities")


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
    source_episode_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False)
    source_quote: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trust_level: Mapped[TrustLevel] = mapped_column(Enum(TrustLevel), nullable=False, default=TrustLevel.grounded)

    # Bitemporal timestamps — all server-managed
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source_episode: Mapped["Episode"] = relationship(back_populates="facts")


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
