"""Pydantic schemas for API request/response."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# ── Store Memory ──


class EntityHint(BaseModel):
    name: str
    entity_type: str = Field(
        ...,
        description="One of: person, organization, location, event, concept, product, preference, procedure, other",
    )
    source_quote: str = Field(..., description="Exact quote from conversation that mentions this entity")


class FactHint(BaseModel):
    subject: str
    predicate: str = Field(..., description="Relationship verb in snake_case, e.g. uses_db, works_at, prefers")
    object: str
    temporal: str = Field(default="", description="Temporal info as free-form string, e.g. 'since last week'")
    source_quote: str = Field(..., description="Exact quote from conversation supporting this fact")


class RelationHint(BaseModel):
    from_entity: str = Field(..., description="Source entity name (must match an entity)")
    to_entity: str = Field(..., description="Target entity name (must match an entity)")
    relation_type: str = Field(
        default="related_to",
        description="One of: supports, contradicts, depends_on, part_of, related_to",
    )
    source_quote: str = Field(default="", description="Exact quote from conversation")


class StoreMemoryRequest(BaseModel):
    workspace_id: uuid.UUID
    session_id: uuid.UUID | None = None
    provider: str = Field(..., description="AI provider: openai, anthropic, google, manual")
    conversation_transcript: str
    entities: list[EntityHint] = Field(default_factory=list)
    facts: list[FactHint] = Field(default_factory=list)
    relations: list[RelationHint] = Field(default_factory=list)
    conversation_summary: str = ""


class StoredFactResponse(BaseModel):
    fact_id: uuid.UUID
    entity_name: str
    predicate: str
    object_value: str
    trust_level: str
    is_supersede: bool = False


class StoreMemoryResponse(BaseModel):
    episode_id: uuid.UUID
    session_id: uuid.UUID
    facts_stored: list[StoredFactResponse]
    entities_resolved: int
    entities_created: int


# ── Recall Memory ──


class RecallMemoryRequest(BaseModel):
    workspace_id: uuid.UUID
    query: str
    limit: int = Field(default=10, ge=1, le=50)
    relation_types: list[str] | None = Field(default=None, description="Filter graph traversal by relation types")


class EvidenceResponse(BaseModel):
    excerpt: str
    episode_id: uuid.UUID
    recorded_at: datetime


class FactHistoryEntry(BaseModel):
    object_value: str
    valid_from: datetime
    superseded_at: datetime | None = None


class RecallFactResponse(BaseModel):
    entity: str
    predicate: str
    object_value: str
    grounded: bool
    valid_from: datetime
    evidence: EvidenceResponse
    related_entities: list[str] = Field(default_factory=list)
    history: list[FactHistoryEntry] = Field(default_factory=list)
    score: float


class RecallMemoryResponse(BaseModel):
    results: list[RecallFactResponse]


# ── Initialize Memory ──


class InitializeMemoryRequest(BaseModel):
    workspace_id: uuid.UUID


class InitializeMemoryResponse(BaseModel):
    workspace_name: str
    recent_summary: str
    protocol: str


# ── Workspace / User ──


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class WorkspaceResponse(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime


class UserCreate(BaseModel):
    email: str
    display_name: str
    password: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    created_at: datetime


class MemberInvite(BaseModel):
    email: str
    role: str = "contributor"


# ── Transcript Upload (Path B) ──


class UploadTranscriptRequest(BaseModel):
    workspace_id: uuid.UUID
    provider: str = Field(default="manual", description="AI provider: openai, anthropic, google, manual")
    transcript: str = Field(..., description="Raw conversation transcript")
    summary: str = ""


class UploadTranscriptResponse(BaseModel):
    episode_id: uuid.UUID
    session_id: uuid.UUID
    status: str = "processing"


# ── Gap Analysis ──


class AnalyzeGapsRequest(BaseModel):
    workspace_id: uuid.UUID
    episode_id: uuid.UUID


class GapCandidate(BaseModel):
    turn_index: int
    text: str
    priority_score: float


class AnalyzeGapsResponse(BaseModel):
    recommendation: str = Field(..., description="skip, gap_fill, or full_extract")
    coverage_ratio: float
    gap_count: int
    gaps: list[GapCandidate] = Field(default_factory=list)


class ExtractGapsRequest(BaseModel):
    workspace_id: uuid.UUID
    episode_id: uuid.UUID
    gap_turns: list[int] = Field(default_factory=list, description="Turn indices to extract from")


class ExtractGapsResponse(BaseModel):
    facts_extracted: int
    facts_stored: int
    supersedes: int
