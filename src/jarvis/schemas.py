"""Pydantic schemas for API request/response."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

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


class IndexEpisodeRequest(BaseModel):
    """Attach an entity/fact/relation index to an EXISTING episode by id.

    Backfill path for episodes ingested without a knowledge index (the diary
    migration stored turns+summary but skipped store_memory, leaving recall
    empty). Targets the episode by id, so it never goes through create_episode's
    content_hash dedup and cannot create a duplicate episode.
    """
    workspace_id: uuid.UUID
    episode_id: uuid.UUID
    entities: list[EntityHint] = Field(default_factory=list)
    facts: list[FactHint] = Field(default_factory=list)
    relations: list[RelationHint] = Field(default_factory=list)


class IndexEpisodeResponse(BaseModel):
    episode_id: uuid.UUID
    entities_resolved: int
    entities_created: int
    facts_stored: int
    relations_stored: int
    embeddings_generated: bool


# ── Recall Memory ──


class RecallMemoryRequest(BaseModel):
    workspace_id: uuid.UUID
    query: str
    limit: int = Field(default=10, ge=1, le=50)
    relation_types: list[str] | None = Field(default=None, description="Filter graph traversal by relation types")


class EvidenceResponse(BaseModel):
    excerpt: str
    # Cleaned variant of ``excerpt`` — populated when the underlying fragment /
    # episode has a cleaned body (R1's cleaning pipeline). NULL otherwise.
    # Same key contract as PassageHitResponse.cleaned_content / RawEpisodeHit.cleaned_snippet.
    cleaned_excerpt: str | None = None
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
    # Raw FTS fallback hits (plan sequential-munching-dove.md phase 1).
    # Populated when hybrid_graph_search misses or anchor matching fails — any
    # keyword living only in raw transcripts/fragments still surfaces here.
    raw_episode_hits: list["RawEpisodeHit"] = Field(default_factory=list)
    raw_fragment_hits: list["RawFragmentHit"] = Field(default_factory=list)
    # Daily summary hits (plan sequential-munching-dove.md phase 3, B3 해소).
    # When the query's anchor entities double as subjects, surface their most
    # recent daily_subject_summaries so the caller gets a temporal overview
    # alongside the fact recall.
    daily_summary_hits: list["DailySummaryHit"] = Field(default_factory=list)


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
    status: str = "active"
    description: str | None = None
    cumulative_summary: str | None = None


class WorkspaceRich(BaseModel):
    """Workspace summary with activity stats + description + top subjects.

    Used by the rich workspace listing endpoint so the UI / AI can show
    "what is this ws and is it alive" without N+1 queries.
    """

    id: uuid.UUID
    name: str
    status: str
    description: str | None = None
    cumulative_summary: str | None = None
    created_at: datetime
    episode_count: int = 0
    turn_count: int = 0
    last_activity: datetime | None = None
    top_subjects: list[str] = Field(default_factory=list)


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
    # Cleaned variant of ``content``. NULL when R1's cleaning pipeline has not
    # populated it (column may be missing today; SELECT uses COALESCE → NULL).
    # AI / MCP / UI should display ``cleaned_content`` first and fall back to
    # ``content`` for raw mode. Key contract: same field name across
    # search-passages / recall / search-episodes so consumers use one path.
    cleaned_content: str | None = None
    similarity: float
    episode_id: uuid.UUID
    fact_id: uuid.UUID | None = None
    entity_name: str | None = None
    predicate: str | None = None


class SearchPassagesResponse(BaseModel):
    query: str
    results: list[PassageHitResponse]


# ── Raw FTS Search (episodes/fragments via PGroonga &@~) ──


class RawEpisodeHit(BaseModel):
    episode_id: uuid.UUID
    summary: str = ""
    snippet: str
    # Cleaned variant of ``snippet`` — derived from episodes.cleaned_content
    # (populated by R1) using the same window logic. NULL when no cleaned body
    # exists. Consumers display this first; raw ``snippet`` is the toggle-OFF fallback.
    cleaned_snippet: str | None = None
    score: float
    created_at: datetime
    # Episode's actual work date (first turn). UI jumps here, not created_at
    # (which is ingestion time and differs for migrated/diary episodes).
    day_ts: datetime | None = None
    matched_field: str  # "content" | "summary"


class RawFragmentHit(BaseModel):
    fragment_id: uuid.UUID
    content: str
    # Cleaned variant of ``content`` (fragments.cleaned_content when populated).
    # NULL when R1 has not run on this row yet.
    cleaned_content: str | None = None
    score: float
    episode_id: uuid.UUID
    fact_id: uuid.UUID | None = None


class SearchEpisodesRequest(BaseModel):
    workspace_id: uuid.UUID
    query: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class SearchEpisodesResponse(BaseModel):
    query: str
    fts_query: str
    results: list[RawEpisodeHit]


class SearchFragmentsFtsRequest(BaseModel):
    workspace_id: uuid.UUID
    query: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class SearchFragmentsFtsResponse(BaseModel):
    query: str
    fts_query: str
    results: list[RawFragmentHit]


# ── Daily Summary Hits (plan sequential-munching-dove.md phase 3) ──


class DailySummaryHit(BaseModel):
    subject_id: uuid.UUID
    subject_name: str
    date: str  # YYYY-MM-DD
    summary: str
    turn_count: int


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
    summary: str = Field(default="", description="AI-written episode summary (1-3 sentences). For UI overview + recall index.")
    keywords: list[str] = Field(default_factory=list, description="5-10 keywords/entities. For keyword-based search index.")
    turns: list[TurnInput] = Field(..., min_length=1, description="Cleaned turns for UI/recall")
    raw_content: str | None = Field(default=None, description="Full raw transcript. Cloud-resident backup for deep recall.")
    metadata: dict | None = None


class IngestTranscriptResponse(BaseModel):
    episode_id: uuid.UUID
    session_id: uuid.UUID
    turn_count: int
    is_duplicate: bool = False  # True if content_hash matched existing episode


class IngestAndIndexRequest(BaseModel):
    """Single-call ingest + classify + summarize + index — diary-mode flow.

    Reflects vision §3.6: "AI writes diary, all four artifacts in one call".
    Episode-level subject linking; per-turn linking still goes through
    /classify-turns when needed.
    """
    # --- Episode ingest (required) ---
    workspace_id: uuid.UUID
    session_id: uuid.UUID | None = None
    provider: str = Field(default="claude-code")
    source_session_id: str = ""
    source_path: str = ""
    title: str = ""
    summary: str = Field(default="", description="AI-written summary, length proportional to body")
    keywords: list[str] = Field(default_factory=list, description="Keywords proportional to content density")
    diary_entry: str = Field(
        default="",
        description=(
            "AI 관찰자 시점의 1000자(800~1200자) 작업 일지 노트. "
            "사용자 행동을 세세하게 관찰하듯 기록 — '사용자가 X 시각에 ...를 했다, 나는 ...로 응답' 톤. "
            "summary와 별도 — summary는 3인칭 색인용, diary_entry는 메인 뷰에 그대로 노출되는 일기 본문. "
            "비어있으면 ingest는 통과하지만 UI 일기 뷰에서는 placeholder가 보임. 신규 ingest에서는 반드시 채울 것."
        ),
    )
    human_summary: str = Field(
        default="",
        description=(
            "사람용 2~3줄(100~200자) 짧은 요약. UI 사이드바/위키 모달에 노출 — 사람이 한눈에 "
            "'이 날 뭐 했는지' 파악하는 용도. 평이한 한국어, AI 보고체/약어 금지. "
            "summary(색인용 3인칭 압축)·diary_entry(1000자 일기)와 별개의 셋째 텍스트. 신규 ingest에서 채울 것."
        ),
    )
    turns: list[TurnInput] = Field(
        ..., min_length=1,
        description=(
            "AI가 기억으로 재구성한 대화. 원본 트랜스크립트 통째 X — 사용자 발화는 "
            "verbatim, AI 발화는 함축 요약, 도구 호출/결과는 별도 turn으로 넣지 않음."
        ),
    )
    raw_content: str | None = Field(
        default=None,
        description=(
            "[폐기] 원본 트랜스크립트 통째 적재는 더 이상 쓰지 않는다(토큰 낭비 + "
            "raw 접근 없는 환경 불가). 비워 둘 것. turns가 재구성된 대화를 담는다."
        ),
    )
    metadata: dict | None = None

    # --- Subject classification (optional) ---
    # plan sequential-munching-dove.md (phase 2): each item accepts either
    # turn_ids (caller already knows the UUIDs) OR turn_sequences (caller knows
    # only the input turn.sequence numbers). The endpoint translates
    # turn_sequences → turn_ids using the map returned by ingest_transcript().
    existing_links: list[dict] = Field(
        default_factory=list,
        description=(
            "[{'subject_id': UUID, 'turn_ids': [UUID, ...]}, ...] OR "
            "[{'subject_id': UUID, 'turn_sequences': [int, ...]}, ...]"
        ),
    )
    new_subjects: list[dict] = Field(
        default_factory=list,
        description=(
            "[{'name': str, 'parent_id': UUID | None, "
            "'turn_ids' OR 'turn_sequences': [...]}, ...]"
        ),
    )

    # --- Daily summaries (optional) ---
    daily_summaries: list["DailySummaryInput"] = Field(default_factory=list)

    # --- Cumulative subject summaries (optional, "지금까지 흐름" narrative) ---
    # SubjectSummary uses subject_name (AI-facing) and is mapped to subject_id
    # inside the endpoint. Upserts daily_subject_summaries on (workspace,
    # subject, date) the same way daily_summaries does, but the AI is meant
    # to write the rolling narrative — not just "today's recap". See the
    # SubjectSummary docstring for the contract.
    subject_summaries: list["SubjectSummary"] = Field(default_factory=list)

    # --- Workspace cumulative summary (optional, "지금까지 ws 전체 흐름") ---
    # None ⇒ do not touch workspaces.cumulative_summary. Empty string ⇒
    # explicitly clear it. Any other string ⇒ replace. The brief sub-line and
    # cross-session recall read this field, so other AI sessions see "what
    # this ws has been doing" without needing to re-derive from episodes.
    workspace_summary: str | None = None

    # --- Entity/fact/relation index (optional) ---
    entities: list[EntityHint] = Field(default_factory=list)
    facts: list[FactHint] = Field(default_factory=list)
    relations: list[RelationHint] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_index_hints(self) -> "IngestAndIndexRequest":
        # Vision §3.6 — diary entry must arrive with body + index hints in one call.
        # Empty hints make the episode unfindable; reject at the boundary.
        if not self.summary.strip():
            raise ValueError(
                "summary is required (non-empty; ≥30 chars recommended). "
                "Diary entries without a summary cannot be recalled."
            )
        if len(self.keywords) < 3:
            raise ValueError(
                "keywords required (≥3). Count should be proportional to content density."
            )
        if not self.existing_links and not self.new_subjects:
            raise ValueError(
                "at least one subject mapping required "
                "(pass existing_links or new_subjects). "
                "Unclassified turns leak out of the subject feed."
            )
        return self


class BoostIndexHintsRequest(BaseModel):
    """Backfill index hints on an episode that was ingested before
    diary-mode required them. Used to bring the ~2,200 pre-vision episodes
    up to the recall index level the new flow enforces.

    Supply at least one of `keywords`, `existing_links`, `new_subjects`.
    """
    workspace_id: uuid.UUID
    keywords: list[str] = Field(
        default_factory=list,
        description="Keywords to union into episodes.metadata.keywords",
    )
    existing_links: list[dict] = Field(
        default_factory=list,
        description="[{'subject_id': UUID, 'turn_ids': [UUID, ...]}, ...]",
    )
    new_subjects: list[dict] = Field(
        default_factory=list,
        description="[{'name': str, 'parent_id': UUID | None, 'turn_ids': [UUID, ...]}, ...]",
    )

    @model_validator(mode="after")
    def require_at_least_one(self) -> "BoostIndexHintsRequest":
        if not self.keywords and not self.existing_links and not self.new_subjects:
            raise ValueError(
                "at least one of keywords / existing_links / new_subjects required"
            )
        return self


class BoostIndexHintsResponse(BaseModel):
    episode_id: uuid.UUID
    keywords_count: int = Field(default=0, description="Total keywords after union")
    created_subjects: int = 0
    linked_turns: int = 0


class IngestAndIndexResponse(BaseModel):
    episode_id: uuid.UUID
    session_id: uuid.UUID
    turn_count: int
    is_duplicate: bool = False
    # classification
    created_subjects: int = 0
    linked_turns: int = 0
    # summaries
    summaries_upserted: int = 0
    # Cumulative summaries — subject_summaries entries that resolved to a real
    # entity and upserted into daily_subject_summaries. Mismatched names are
    # skipped silently (server logs a warning) so a typo doesn't fail the diary.
    subject_summaries_upserted: int = 0
    # Whether workspaces.cumulative_summary was updated (workspace_summary was
    # not None). False = caller did not pass the field.
    workspace_summary_updated: bool = False
    # index
    entities_resolved: int = 0
    entities_created: int = 0
    facts_stored: list[StoredFactResponse] = Field(default_factory=list)
    relations_stored: int = 0


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


# ── Retrieval API (P3 — 줄글 회수) ──


class TurnView(BaseModel):
    turn_id: uuid.UUID
    episode_id: uuid.UUID
    sequence: int
    role: str
    text: str
    cleaned_text: str | None = None
    timestamp: datetime
    subjects: list[uuid.UUID] = Field(default_factory=list, description="Linked subject IDs")


class TimelineRequest(BaseModel):
    workspace_id: uuid.UUID
    date_from: datetime | None = None  # ISO datetime, inclusive
    date_to: datetime | None = None    # ISO datetime, exclusive
    descending: bool = True            # newest first by default per UI spec
    limit: int = Field(default=500, ge=1, le=5000)
    offset: int = Field(default=0, ge=0)


class TimelineResponse(BaseModel):
    turns: list[TurnView]
    total_turns: int
    has_more: bool = False


class DateBucketsRequest(BaseModel):
    """Light date histogram for sidebar date tree — no turn payload."""
    workspace_id: uuid.UUID
    subject_id: uuid.UUID | None = None  # if set, count only turns linked to subject (+descendants)


class DateBucket(BaseModel):
    date: str  # YYYY-MM-DD
    count: int


class DateBucketsResponse(BaseModel):
    buckets: list[DateBucket]
    total_turns: int


class EntityPageRequest(BaseModel):
    workspace_id: uuid.UUID
    entity_id: uuid.UUID
    fact_limit: int = Field(default=30, ge=1, le=200)
    relation_limit: int = Field(default=24, ge=1, le=100)
    episode_limit: int = Field(default=10, ge=1, le=50)


class EntitySummary(BaseModel):
    entity_id: uuid.UUID
    name: str


class FactEntry(BaseModel):
    fact_id: uuid.UUID
    predicate: str
    object_value: str
    source_episode_id: uuid.UUID | None
    source_quote: str
    valid_from: datetime
    superseded_at: datetime | None
    trust_level: str


class RelationEntry(BaseModel):
    relation_id: uuid.UUID
    relation_type: str
    other_entity_id: uuid.UUID
    other_entity_name: str
    direction: str  # 'out' | 'in'
    weight: float
    valid_from: datetime


class EpisodeRef(BaseModel):
    episode_id: uuid.UUID
    date: str
    summary: str
    turn_count: int


class EntityPageResponse(BaseModel):
    entity_id: uuid.UUID
    name: str
    entity_type: str
    parent: EntitySummary | None = None
    children: list[EntitySummary]
    aliases: list[str]
    # AI-written long-form wiki article (Entity.wiki_article). None ⇒ UI
    # falls back to facts/relations only — no synthetic summary is generated.
    wiki_article: str | None = None
    facts: list[FactEntry]
    relations: list[RelationEntry]
    recent_episodes: list[EpisodeRef]


class SubjectFeedRequest(BaseModel):
    workspace_id: uuid.UUID
    subject_id: uuid.UUID
    include_descendants: bool = True  # include sub-subjects too
    date_from: datetime | None = None
    date_to: datetime | None = None
    descending: bool = True
    limit: int = Field(default=500, ge=1, le=5000)
    offset: int = Field(default=0, ge=0)


class SubjectFeedResponse(BaseModel):
    subject_id: uuid.UUID
    subject_name: str
    turns: list[TurnView]
    total_turns: int
    has_more: bool = False


class SubjectTreeNode(BaseModel):
    subject_id: uuid.UUID
    name: str
    turn_count: int
    entity_type: str | None = None
    children: list["SubjectTreeNode"] = Field(default_factory=list)


class SubjectTreeRequest(BaseModel):
    workspace_id: uuid.UUID


class SubjectTreeResponse(BaseModel):
    roots: list[SubjectTreeNode]
    total_subjects: int


# ── Reflect & zoom summaries (P4) ──


class DailySummaryInput(BaseModel):
    subject_id: uuid.UUID
    date: str  # YYYY-MM-DD
    summary: str
    turn_count: int = 0
    # P8 — optional; when omitted, save_summaries falls back to turn_count.
    unique_turn_count: int = 0


class SubjectSummary(BaseModel):
    """Cumulative-narrative summary per subject — "지금까지 + 오늘" 누적.

    Unlike DailySummaryInput (which speaks subject_id UUID), this struct is
    AI-facing and uses subject_name. The server resolves the name → UUID via
    the workspace entities table before upserting daily_subject_summaries.

    Use this to communicate "the running story" of a subject across sessions.
    Saved into daily_subject_summaries with `date` (defaults to today on the
    server) as the row key — same row gets overwritten across the day so the
    most recent diary call always wins.
    """

    subject_name: str
    cumulative_summary: str
    # Optional — how many turns of activity today contributed to this subject.
    # Persists into daily_subject_summaries.turn_count for brief's activity
    # signal. 0 = "summary updated, no new turns" (e.g. reflection-only call).
    turn_count_today: int = 0
    # Optional — explicit date for the daily_subject_summaries row. None = today
    # on the server. Lets backfill scripts target past days with the same shape.
    date: str | None = None


class SaveSummariesRequest(BaseModel):
    """AI sends summaries after reflecting on a day's turns.

    Each summary is for (subject, date). Upsert: replace if (workspace, subject, date)
    already exists.
    """
    workspace_id: uuid.UUID
    summaries: list[DailySummaryInput]


class SaveSummariesResponse(BaseModel):
    upserted: int


class GetSummariesRequest(BaseModel):
    """Read summaries in date range. Optionally filtered by subject."""
    workspace_id: uuid.UUID
    date_from: str | None = None  # YYYY-MM-DD
    date_to: str | None = None    # YYYY-MM-DD (exclusive)
    subject_id: uuid.UUID | None = None


class SummaryBrief(BaseModel):
    summary_id: uuid.UUID
    subject_id: uuid.UUID
    subject_name: str
    date: str
    summary: str
    turn_count: int
    # P8 — distinct turns linked to (subject ∪ descendants) on this date.
    # On leaf subjects this equals turn_count; on parents it deduplicates
    # turns shared with sub-subjects so the UI total is honest.
    unique_turn_count: int = 0


class GetSummariesResponse(BaseModel):
    summaries: list[SummaryBrief]
    total: int


class PendingReflectsRequest(BaseModel):
    """Which (date, subject) pairs have turns but no summary yet?"""
    workspace_id: uuid.UUID
    date_from: str | None = None
    date_to: str | None = None


class PendingReflectItem(BaseModel):
    date: str
    subject_id: uuid.UUID
    subject_name: str
    turn_count: int


class PendingReflectsResponse(BaseModel):
    pending: list[PendingReflectItem]


# ── Home Dashboard (P3 — JARVIS Home) ──


class DashboardRequest(BaseModel):
    """Workspace home dashboard payload — single call powering the 4-block home."""
    workspace_id: uuid.UUID


class DashboardStats(BaseModel):
    episode_count: int = 0
    turn_count: int = 0
    entity_count: int = 0
    fact_count: int = 0
    first_date: str | None = None  # YYYY-MM-DD, oldest turn/episode
    last_date: str | None = None   # YYYY-MM-DD, newest turn/episode


class DashboardRecentSummary(BaseModel):
    date: str  # YYYY-MM-DD
    subject_id: uuid.UUID
    subject_name: str
    summary: str
    turn_count: int
    # P8 — distinct turns when summing parent + sub-subject rows on the same day.
    unique_turn_count: int = 0


class DashboardOnThisDay(BaseModel):
    date: str  # YYYY-MM-DD of the past episode
    episode_id: uuid.UUID
    summary: str
    days_ago: int


class DashboardTopEntity(BaseModel):
    entity_id: uuid.UUID
    name: str
    fact_count: int
    relation_count: int


class DashboardResponse(BaseModel):
    stats: DashboardStats
    recent_summaries: list[DashboardRecentSummary] = Field(default_factory=list)
    on_this_day: list[DashboardOnThisDay] = Field(default_factory=list)
    top_entities: list[DashboardTopEntity] = Field(default_factory=list)


# ── Wiki index (P7 — '어떤 대화가 들어있는지 한눈에') ──


class EntityIndexRequest(BaseModel):
    workspace_id: uuid.UUID


class EntityIndexEntry(BaseModel):
    entity_id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None = None
    parent_name: str | None = None
    fact_count: int = 0
    relation_count: int = 0
    last_seen_date: str | None = None  # YYYY-MM-DD, most-recent fact valid_from


class EntityIndexGroup(BaseModel):
    entity_type: str
    entities: list[EntityIndexEntry] = Field(default_factory=list)


class EntityIndexResponse(BaseModel):
    groups: list[EntityIndexGroup] = Field(default_factory=list)


# ── Graph view (Stage 2C — D3 force-directed entity graph) ──
#
# `/graph` returns a hub-cut view of the workspace's entity-relation network so
# the frontend can D3-render it. `episode_topic` is excluded (session-label
# anchors, same rationale as `/entity-index`). `from_id`/`to_id` use `_id`
# suffix because `from` is a Python reserved word — see survey-note.


class GraphRequest(BaseModel):
    workspace_id: uuid.UUID
    limit: int = Field(default=100, ge=1, le=500, description="top-N hub entity cutoff (rel_cnt desc)")
    min_rel_cnt: int = Field(default=1, ge=0, description="drop nodes whose rel_cnt < threshold")
    # When True, allow isolated entities (rel_cnt = 0) into the result regardless
    # of `min_rel_cnt`. Default False — wiki graph view hides orphans.
    include_isolates: bool = False
    entity_types: list[str] | None = Field(
        default=None,
        description="optional filter, e.g. ['concept', 'product']. None = all (still excludes episode_topic).",
    )


class GraphNode(BaseModel):
    id: uuid.UUID
    name: str
    entity_type: str
    rel_cnt: int = Field(ge=0)
    has_wiki: bool = False


class GraphEdge(BaseModel):
    from_id: uuid.UUID
    to_id: uuid.UUID
    relation_type: str
    weight: int = Field(default=1, ge=1)


class GraphResponse(BaseModel):
    workspace_id: uuid.UUID
    total_entities: int
    returned_nodes: int
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


# ── On This Day (P7 — 같은 날짜 과거 기록, 임의 month·day) ──


class OnThisDayRequest(BaseModel):
    workspace_id: uuid.UUID
    month: int | None = Field(default=None, ge=1, le=12, description="1-12, defaults to today")
    day: int | None = Field(default=None, ge=1, le=31, description="1-31, defaults to today")
    limit: int = Field(default=10, ge=1, le=50)


class OnThisDayMatch(BaseModel):
    episode_id: uuid.UUID
    date: str  # YYYY-MM-DD
    year: int
    days_ago: int
    summary: str
    turn_count: int


class OnThisDayResponse(BaseModel):
    month: int
    day: int
    matches: list[OnThisDayMatch] = Field(default_factory=list)


# ── Ingest Ledger (P4 — 본대화 N개 중 무엇이 올라갔나) ──


class IngestLedgerEntry(BaseModel):
    """One ledger row — "this file was ingested at this time"."""

    ledger_id: uuid.UUID
    ingested_at: datetime
    source_file_path: str
    source_file_path_normalized: str
    external_session_id: str | None = None
    episode_id: uuid.UUID | None = None
    turn_count: int = 0
    ingested_via: str
    pipeline_version: str | None = None
    status: str  # 'ingested' | 'duplicate' | 'failed'
    dedup_decision: str | None = None  # 'new' | 'append_v2' | 'replaced' | 'rejected'
    notes: str | None = None


class IngestLedgerLocalDiffEntry(BaseModel):
    """One on-disk jsonl matched (or not) against the ledger."""

    local_file_path: str
    local_size: int
    local_sha: str | None = None
    external_session_id: str | None = None  # parsed from basename
    status: str  # 'matched' | 'not_ingested'
    ingested_count: int = 0  # how many ledger rows share this sid (across all versions)


class IngestLedgerRequest(BaseModel):
    workspace_id: uuid.UUID
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=500)
    include_local_diff: bool = False


class IngestLedgerResponse(BaseModel):
    entries: list[IngestLedgerEntry]
    total: int
    local_diff: list[IngestLedgerLocalDiffEntry] = Field(default_factory=list)
    local_diff_summary: dict[str, int] = Field(
        default_factory=dict,
        description="{'matched': N, 'not_ingested': M} for quick badge rendering",
    )


# ── Today's Brief (JARVIS Brief Me) ──


class BriefRequest(BaseModel):
    """Brief payload request.

    workspace_id missing → cross-ws mode (active ws distribution + Top 3 thread).
    workspace_id present → deep mode (entity hub + open question + last eps).
    """
    workspace_id: uuid.UUID | None = None
    workspace_name: str | None = None
    detail: Literal["brief", "deep"] = "brief"
    include_hidden: bool = False


class BriefActiveWorkspace(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    description: str | None = None
    activity_tag: str
    ep_count_total: int = 0
    ep_count_7d: int = 0
    ep_count_today: int = 0
    ep_count_yesterday: int = 0
    turn_count_7d: int = 0
    last_activity: datetime | None = None
    top_subjects_14d: list[str] = Field(default_factory=list)
    signal_line: str = ""
    # Rolling "지금까지 흐름" narrative from workspaces.cumulative_summary.
    # UI brief renders this as a second-line summary under the meta signal;
    # empty string ⇒ ws has no cumulative summary yet (collapse to 1-line chip).
    cumulative_summary: str = ""


class BriefRecentThread(BaseModel):
    workspace_id: uuid.UUID
    workspace_name: str
    subject_id: uuid.UUID
    subject_name: str
    turn_count_14d: int
    last_active_date: str
    summary: str
    is_summary_missing: bool = False
    episode_ids: list[uuid.UUID] = Field(default_factory=list)
    fact_ids: list[uuid.UUID] = Field(default_factory=list)


class BriefOpenItem(BaseModel):
    fact_id: uuid.UUID
    entity_name: str
    predicate: str
    object_value: str
    recorded_at: datetime
    source_quote: str | None = None


class BriefRecommendation(BaseModel):
    rank: int
    title: str
    reason_code: str
    workspace_id: uuid.UUID
    workspace_name: str
    target_kind: Literal["workspace", "subject", "entity", "episode"]
    target_id: uuid.UUID | None = None
    target_date: str | None = None
    detail: str


class BriefDataQuality(BaseModel):
    ws_count: int
    fact_count_active: int = 0
    summary_coverage: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class BriefLastEpisode(BaseModel):
    episode_id: str
    created_at: datetime
    summary: str = ""


class BriefResponse(BaseModel):
    mode: Literal["cross", "deep"]
    generated_at: datetime
    today: str
    target_workspace: dict[str, Any] | None = None
    active_workspaces: list[BriefActiveWorkspace] = Field(default_factory=list)
    recent_threads: list[BriefRecentThread] = Field(default_factory=list)
    open_questions: list[BriefOpenItem] = Field(default_factory=list)
    next_recommendations: list[BriefRecommendation] = Field(default_factory=list)
    data_quality: BriefDataQuality
    last_episodes: list[BriefLastEpisode] = Field(default_factory=list)
    ascii_text: str
