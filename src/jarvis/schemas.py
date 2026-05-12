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
    episode_count: int = 1  # number of episodes asserting this fact (fact_episodes count)


class FactHistoryEntry(BaseModel):
    object_value: str
    valid_from: datetime
    superseded_at: datetime | None = None


class RelatedEntity(BaseModel):
    """Fact's related entity with navigation hints."""

    entity_id: uuid.UUID
    name: str
    relation_type: str
    fact_count: int  # workspace-wide active fact count — "worth paging in?"


class RecallFactResponse(BaseModel):
    entity: str
    predicate: str
    object_value: str
    grounded: bool
    valid_from: datetime
    evidence: EvidenceResponse
    related_entities: list[RelatedEntity] = Field(default_factory=list)
    history: list[FactHistoryEntry] = Field(default_factory=list)
    score: float


class CoverageMetadata(BaseModel):
    """Coverage metadata for recall context assembly."""

    total_candidates: int
    selected_count: int
    communities_represented: int
    workspace_communities: int


class RecallMemoryResponse(BaseModel):
    results: list[RecallFactResponse]
    coverage: CoverageMetadata | None = None
    structural_summary: str = ""
    pagination_token: str | None = None
    anchor_matched: bool = False  # Phase 1: did Aho-Corasick find any anchor?


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


# ── Explore Topic (topic map) ──


class ExploreTopicRequest(BaseModel):
    workspace_id: uuid.UUID
    query: str = Field(..., min_length=1)


class TopicEntity(BaseModel):
    name: str
    entity_type: str
    fact_count_in_pool: int
    workspace_fact_count: int
    out_degree: int
    community_id: int | None = None


class TopicMapResponse(BaseModel):
    query: str
    expanded_terms: list[str]
    total_candidates: int
    total_fact_count: int
    entities: list[TopicEntity]
    distinct_communities: int
    top_predicates: list[tuple[str, int]]
    edge_count: int
    isolated_entity_count: int
    time_range_start: datetime | None = None
    time_range_end: datetime | None = None


# ── Search Passages (narrative/episodic layer) ──


class SearchPassagesRequest(BaseModel):
    workspace_id: uuid.UUID
    query: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class PassageHitResponse(BaseModel):
    fragment_id: uuid.UUID
    content: str
    similarity: float
    episode_id: uuid.UUID
    fact_id: uuid.UUID | None = None
    entity_name: str | None = None
    predicate: str | None = None


class SearchPassagesResponse(BaseModel):
    query: str
    results: list[PassageHitResponse]


# ── Get Episode Excerpt (drill into one episode) ──


class EpisodeExcerptRequest(BaseModel):
    workspace_id: uuid.UUID
    episode_id: uuid.UUID
    query: str = Field(..., min_length=1)
    max_chars: int = Field(default=2000, ge=200, le=10000)
    mode: str = Field(default="relevant", pattern="^(relevant|full|head)$")


class EpisodeExcerptResponse(BaseModel):
    episode_id: uuid.UUID
    excerpt: str
    total_length: int
    mode: str
    passage_count: int
    matched_keywords: list[str] = Field(default_factory=list)
    created_at: datetime
    summary: str | None = None


# ── Follow Relation (graph navigation) ──


class FollowRelationRequest(BaseModel):
    workspace_id: uuid.UUID
    entity: str = Field(..., min_length=1)  # UUID or exact entity name
    direction: str = Field(default="both", pattern="^(out|in|both)$")
    relation_type: str | None = None
    limit: int = Field(default=10, ge=1, le=50)


class FactBriefResponse(BaseModel):
    predicate: str
    object_value: str
    grounded: bool
    valid_from: datetime


class RelatedNodeResponse(BaseModel):
    entity_id: uuid.UUID
    entity_name: str
    entity_type: str | None = None
    relation_type: str
    direction: str  # "out" | "in"
    fact_count: int
    top_facts: list[FactBriefResponse] = Field(default_factory=list)


class FollowRelationResponse(BaseModel):
    anchor_entity_id: uuid.UUID
    anchor_entity_name: str
    total_neighbors: int
    neighbors: list[RelatedNodeResponse]
    relation_type_counts: dict[str, int] = Field(default_factory=dict)


# ── Raw transcript ingest (2026-05-07 비전) ──


class TurnInput(BaseModel):
    sequence: int = Field(..., ge=0)
    role: str = Field(..., pattern="^(user|assistant|system|tool)$")
    text: str
    timestamp: datetime


class IngestTranscriptRequest(BaseModel):
    workspace_id: uuid.UUID
    session_id: uuid.UUID | None = None  # if None, create new session
    provider: str = Field(default="unknown")
    source_session_id: str = Field(default="", description="External session id (Claude Code etc.) for traceability")
    source_path: str = Field(default="", description="Source file path for traceability")
    title: str = Field(default="")
    turns: list[TurnInput] = Field(..., min_length=1)
    metadata: dict | None = None


class IngestTranscriptResponse(BaseModel):
    episode_id: uuid.UUID
    session_id: uuid.UUID
    turn_count: int
    is_duplicate: bool = False  # True if content_hash matched existing episode


class UploadStatusRequest(BaseModel):
    workspace_id: uuid.UUID


class UploadStatusResponse(BaseModel):
    workspace_id: uuid.UUID
    total_episodes: int
    total_turns: int
    earliest_episode_at: datetime | None = None
    latest_episode_at: datetime | None = None
    distinct_subjects: int = 0  # top-level subjects (parent_id IS NULL)


# ── Subject classification (proposal + confirm) ──


class SubjectProposal(BaseModel):
    turn_id: uuid.UUID
    existing_subject_ids: list[uuid.UUID] = Field(default_factory=list)
    new_subject_names: list[str] = Field(default_factory=list, description="New top-level subjects to create")
    new_sub_subjects: list[dict] = Field(
        default_factory=list,
        description="New sub-subjects: [{'name': str, 'parent_id': UUID}, ...]",
    )


class ConfirmSubjectsRequest(BaseModel):
    workspace_id: uuid.UUID
    proposals: list[SubjectProposal]


class ConfirmSubjectsResponse(BaseModel):
    created_subjects: int
    linked_turns: int


class SubjectBrief(BaseModel):
    subject_id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None = None
    parent_name: str | None = None
    turn_count: int = 0


class ListSubjectsRequest(BaseModel):
    workspace_id: uuid.UUID
    top_level_only: bool = True  # if True, only parent_id IS NULL


class ListSubjectsResponse(BaseModel):
    subjects: list[SubjectBrief]
    total: int


class ClassifyTurnsRequest(BaseModel):
    """Single-shot turn → subject classification, with optional new subject creation.

    AI client builds this after consulting /subjects and confirming with user.
    Each item links a list of turn_ids to either an existing subject_id OR a
    new subject (name + optional parent_id). The server creates new subjects
    as needed and writes turn_subjects rows.
    """
    workspace_id: uuid.UUID
    # Existing-subject links
    existing_links: list[dict] = Field(
        default_factory=list,
        description="[{'subject_id': UUID, 'turn_ids': [UUID, ...]}, ...]",
    )
    # New-subject creates + links in one shot
    new_subjects: list[dict] = Field(
        default_factory=list,
        description="[{'name': str, 'parent_id': UUID | None, 'turn_ids': [UUID, ...]}, ...]",
    )


class ClassifyTurnsResponse(BaseModel):
    created_subjects: int
    linked_turns: int
    skipped_duplicate_links: int = 0
