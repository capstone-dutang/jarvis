"""Memory endpoints: store, recall, initialize, upload-transcript, gap analysis."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import HTTPException

from jarvis.core.episode_excerpt import get_episode_excerpt
from jarvis.core.follow_relation import follow_relation
from jarvis.core.passage_search import search_passages
from jarvis.core.recall import recall_memory
from jarvis.core.store import create_episode, get_or_create_session, store_memory
from jarvis.core.topic_map import build_topic_map
from jarvis.db import get_session
from jarvis.models.tables import Entity, Episode, KnowledgeFact, Workspace
from jarvis.schemas import (
    AnalyzeGapsRequest,
    AnalyzeGapsResponse,
    ClassifyTurnsRequest,
    ClassifyTurnsResponse,
    EpisodeExcerptRequest,
    EpisodeExcerptResponse,
    ExploreTopicRequest,
    FactBriefResponse,
    FollowRelationRequest,
    FollowRelationResponse,
    GetSummariesRequest,
    GetSummariesResponse,
    IngestTranscriptRequest,
    IngestTranscriptResponse,
    InitializeMemoryRequest,
    InitializeMemoryResponse,
    ListSubjectsRequest,
    ListSubjectsResponse,
    PassageHitResponse,
    PendingReflectItem,
    PendingReflectsRequest,
    PendingReflectsResponse,
    RecallMemoryRequest,
    RecallMemoryResponse,
    RelatedNodeResponse,
    SaveSummariesRequest,
    SaveSummariesResponse,
    SearchPassagesRequest,
    SearchPassagesResponse,
    StoreMemoryRequest,
    StoreMemoryResponse,
    SubjectBrief,
    SubjectFeedRequest,
    SubjectFeedResponse,
    SubjectTreeNode,
    SubjectTreeRequest,
    SubjectTreeResponse,
    SummaryBrief,
    TimelineRequest,
    TimelineResponse,
    TopicMapResponse,
    TurnView,
    UploadStatusRequest,
    UploadStatusResponse,
    UploadTranscriptRequest,
    UploadTranscriptResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])

PROTOCOL = (
    "Memory protocol:\n"
    "- Call store_memory when you learn a new fact about the user, "
    "their preferences, goals, technical stack, or decisions.\n"
    "- Call store_memory when the user corrects or updates previously known information.\n"
    "- Call store_memory if 5+ substantive exchanges have passed without storing.\n"
    "- Each memory should be a self-contained statement.\n"
    "- Do NOT store greetings, small talk, or your own responses."
)


@router.post("/store", response_model=StoreMemoryResponse)
async def api_store_memory(
    request: StoreMemoryRequest,
    db: AsyncSession = Depends(get_session),
) -> StoreMemoryResponse:
    return await store_memory(db, request)


@router.post("/recall", response_model=RecallMemoryResponse)
async def api_recall_memory(
    request: RecallMemoryRequest,
    db: AsyncSession = Depends(get_session),
) -> RecallMemoryResponse:
    return await recall_memory(db, request)


@router.post("/explore", response_model=TopicMapResponse)
async def api_explore_topic(
    request: ExploreTopicRequest,
    db: AsyncSession = Depends(get_session),
) -> TopicMapResponse:
    return await build_topic_map(db, request.workspace_id, request.query)


@router.post("/search-passages", response_model=SearchPassagesResponse)
async def api_search_passages(
    request: SearchPassagesRequest,
    db: AsyncSession = Depends(get_session),
) -> SearchPassagesResponse:
    hits = await search_passages(db, request.workspace_id, request.query, request.limit)
    return SearchPassagesResponse(
        query=request.query,
        results=[
            PassageHitResponse(
                fragment_id=h.fragment_id,
                content=h.content,
                similarity=h.similarity,
                episode_id=h.episode_id,
                fact_id=h.fact_id,
                entity_name=h.entity_name,
                predicate=h.predicate,
            )
            for h in hits
        ],
    )


@router.post("/episode-excerpt", response_model=EpisodeExcerptResponse)
async def api_episode_excerpt(
    request: EpisodeExcerptRequest,
    db: AsyncSession = Depends(get_session),
) -> EpisodeExcerptResponse:
    result = await get_episode_excerpt(
        db, request.workspace_id, request.episode_id,
        request.query, request.max_chars, request.mode,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Episode not found in workspace")
    return EpisodeExcerptResponse(
        episode_id=result.episode_id,
        excerpt=result.excerpt,
        total_length=result.total_length,
        mode=result.mode,
        passage_count=result.passage_count,
        matched_keywords=result.matched_keywords,
        created_at=result.created_at,
        summary=result.summary,
    )


@router.post("/follow-relation", response_model=FollowRelationResponse)
async def api_follow_relation(
    request: FollowRelationRequest,
    db: AsyncSession = Depends(get_session),
) -> FollowRelationResponse:
    result = await follow_relation(
        db, request.workspace_id, request.entity,
        direction=request.direction,
        relation_type=request.relation_type,
        limit=request.limit,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Entity '{request.entity}' not found in workspace",
        )
    return FollowRelationResponse(
        anchor_entity_id=result.anchor_entity_id,
        anchor_entity_name=result.anchor_entity_name,
        total_neighbors=result.total_neighbors,
        neighbors=[
            RelatedNodeResponse(
                entity_id=n.entity_id,
                entity_name=n.entity_name,
                entity_type=n.entity_type,
                relation_type=n.relation_type,
                direction=n.direction,
                fact_count=n.fact_count,
                top_facts=[
                    FactBriefResponse(
                        predicate=f.predicate,
                        object_value=f.object_value,
                        grounded=f.grounded,
                        valid_from=f.valid_from,
                    )
                    for f in n.top_facts
                ],
            )
            for n in result.neighbors
        ],
        relation_type_counts=result.relation_type_counts,
    )


@router.post("/initialize", response_model=InitializeMemoryResponse)
async def api_initialize_memory(
    request: InitializeMemoryRequest,
    db: AsyncSession = Depends(get_session),
) -> InitializeMemoryResponse:
    """Initialize memory: return workspace recent context + protocol."""
    # Get workspace name
    ws_result = await db.execute(select(Workspace).where(Workspace.id == request.workspace_id))
    ws = ws_result.scalar_one_or_none()
    workspace_name = ws.name if ws else "unknown"

    # Get recent active facts (top 10)
    fact_result = await db.execute(
        select(KnowledgeFact, Entity.name)
        .join(Entity, KnowledgeFact.entity_id == Entity.id)
        .where(
            KnowledgeFact.workspace_id == request.workspace_id,
            KnowledgeFact.superseded_at.is_(None),
        )
        .order_by(KnowledgeFact.recorded_at.desc())
        .limit(10)
    )
    rows = fact_result.all()

    recent_summary = ""
    if rows:
        lines = [f"- {name} {fact.predicate} {fact.object_value}" for fact, name in rows]
        recent_summary = "\n".join(lines)

    return InitializeMemoryResponse(
        workspace_name=workspace_name,
        recent_summary=recent_summary,
        protocol=PROTOCOL,
    )


@router.post("/upload-transcript", response_model=UploadTranscriptResponse)
async def api_upload_transcript(
    request: UploadTranscriptRequest,
    db: AsyncSession = Depends(get_session),
) -> UploadTranscriptResponse:
    """Upload a transcript for Path B: Episode auto-save + async gap processing."""
    # Create session + episode
    session = await get_or_create_session(
        db, request.workspace_id, session_id=None, provider=request.provider,
    )
    episode = await create_episode(
        db, session, request.workspace_id,
        request.transcript, request.summary, request.provider,
    )
    is_duplicate = episode.processing_status != "pending"
    await db.commit()

    if is_duplicate:
        logger.info("Duplicate transcript: episode=%s (already %s)", episode.id, episode.processing_status)
    else:
        logger.info(
            "Transcript uploaded: episode=%s, session=%s, %d chars",
            episode.id, session.id, len(request.transcript),
        )

    return UploadTranscriptResponse(
        episode_id=episode.id,
        session_id=session.id,
        status=episode.processing_status if is_duplicate else "pending",
    )


@router.post("/analyze-gaps", response_model=AnalyzeGapsResponse)
async def api_analyze_gaps(
    request: AnalyzeGapsRequest,
    db: AsyncSession = Depends(get_session),
) -> AnalyzeGapsResponse:
    """Analyze gaps in an episode's coverage."""
    # Load episode
    result = await db.execute(
        select(Episode).where(
            Episode.id == request.episode_id,
            Episode.workspace_id == request.workspace_id,
        )
    )
    episode = result.scalar_one_or_none()
    if not episode:
        return AnalyzeGapsResponse(
            recommendation="skip", coverage_ratio=0.0, gap_count=0,
        )

    # Get existing keywords
    from jarvis.models.tables import Fragment
    frag_result = await db.execute(
        select(Fragment.keywords).where(Fragment.workspace_id == request.workspace_id)
    )
    existing_keywords: set[str] = set()
    for row in frag_result.fetchall():
        if row[0]:
            existing_keywords.update(row[0])

    from jarvis.core.gap_detection import detect_gaps
    gaps = detect_gaps(
        transcript=episode.content,
        covered_turn_indices=set(),
        existing_keywords=existing_keywords,
    )

    from jarvis.schemas import GapCandidate as GapCandidateResponse
    gap_candidates = [
        GapCandidateResponse(
            turn_index=g.turn.turn_index,
            text=g.turn.text[:200],
            priority_score=g.priority,
        )
        for g in gaps.gaps[:20]
    ]

    return AnalyzeGapsResponse(
        recommendation=gaps.recommendation,
        coverage_ratio=gaps.coverage_ratio,
        gap_count=len(gaps.gaps),
        gaps=gap_candidates,
    )


# ── Raw transcript ingest (2026-05-07 비전) ──


@router.post("/ingest-transcript", response_model=IngestTranscriptResponse)
async def api_ingest_transcript(
    request: IngestTranscriptRequest,
    db: AsyncSession = Depends(get_session),
) -> IngestTranscriptResponse:
    """Ingest raw transcript as Episode + Turn rows.

    No extraction, no subject classification — pure mechanical ingest.
    Subject classification happens later via /propose-subjects + /confirm-subjects.
    """
    from jarvis.core.turn_ingest import ingest_transcript as _ingest

    turn_dicts = [
        {
            "sequence": t.sequence,
            "role": t.role,
            "text": t.text,
            "timestamp": t.timestamp,
        }
        for t in request.turns
    ]
    metadata = dict(request.metadata or {})
    if request.source_session_id:
        metadata["external_session_id"] = request.source_session_id
    if request.source_path:
        metadata["source_path"] = request.source_path

    # Merge AI-generated summary/keywords into metadata for retrievability
    if request.summary:
        metadata["summary"] = request.summary
    if request.keywords:
        metadata["keywords"] = request.keywords

    episode, turn_count, is_dup = await _ingest(
        db, request.workspace_id, turn_dicts,
        session_id=request.session_id,
        provider=request.provider,
        title=request.title or request.summary[:200],  # title fallback to summary head
        metadata=metadata,
        raw_content=request.raw_content,
    )
    await db.commit()
    return IngestTranscriptResponse(
        episode_id=episode.id,
        session_id=episode.session_id,
        turn_count=turn_count,
        is_duplicate=is_dup,
    )


@router.post("/upload-status", response_model=UploadStatusResponse)
async def api_upload_status(
    request: UploadStatusRequest,
    db: AsyncSession = Depends(get_session),
) -> UploadStatusResponse:
    """How far up are we? — meta query for 'git log -1' equivalent."""
    from jarvis.core.turn_ingest import get_upload_status

    info = await get_upload_status(db, request.workspace_id)
    return UploadStatusResponse(
        workspace_id=request.workspace_id,
        total_episodes=info["total_episodes"],
        total_turns=info["total_turns"],
        earliest_episode_at=info["earliest_episode_at"],
        latest_episode_at=info["latest_episode_at"],
        distinct_subjects=info["distinct_subjects"],
    )


@router.post("/subjects", response_model=ListSubjectsResponse)
async def api_list_subjects(
    request: ListSubjectsRequest,
    db: AsyncSession = Depends(get_session),
) -> ListSubjectsResponse:
    """List existing subjects in the workspace.

    AI calls this before proposing subject classification to user, so it knows
    what already exists (avoids creating duplicate top-level subjects).
    """
    from jarvis.core.subjects import list_subjects

    subjects = await list_subjects(db, request.workspace_id, top_level_only=request.top_level_only)
    return ListSubjectsResponse(
        subjects=[SubjectBrief(**s) for s in subjects],
        total=len(subjects),
    )


@router.post("/classify-turns", response_model=ClassifyTurnsResponse)
async def api_classify_turns(
    request: ClassifyTurnsRequest,
    db: AsyncSession = Depends(get_session),
) -> ClassifyTurnsResponse:
    """Confirmed turn → subject assignments. Creates new subjects + links."""
    from jarvis.core.subjects import classify_turns

    info = await classify_turns(
        db, request.workspace_id,
        existing_links=request.existing_links,
        new_subjects=request.new_subjects,
    )
    await db.commit()
    return ClassifyTurnsResponse(**info)


# ── Retrieval (P3 — 줄글 회수) ──


@router.post("/timeline", response_model=TimelineResponse)
async def api_timeline(
    request: TimelineRequest,
    db: AsyncSession = Depends(get_session),
) -> TimelineResponse:
    """Turns in time range, newest-first by default. Used for day/week/month views."""
    from jarvis.core.retrieval import get_timeline

    turns, total = await get_timeline(
        db, request.workspace_id,
        date_from=request.date_from, date_to=request.date_to,
        descending=request.descending,
        limit=request.limit, offset=request.offset,
    )
    return TimelineResponse(
        turns=[TurnView(**t) for t in turns],
        total_turns=total,
        has_more=(request.offset + len(turns)) < total,
    )


@router.post("/subject-feed", response_model=SubjectFeedResponse)
async def api_subject_feed(
    request: SubjectFeedRequest,
    db: AsyncSession = Depends(get_session),
) -> SubjectFeedResponse:
    """Turns linked to a subject (and descendants), ordered by time."""
    from jarvis.core.retrieval import get_subject_feed

    subject_name, turns, total = await get_subject_feed(
        db, request.workspace_id, request.subject_id,
        include_descendants=request.include_descendants,
        date_from=request.date_from, date_to=request.date_to,
        descending=request.descending,
        limit=request.limit, offset=request.offset,
    )
    return SubjectFeedResponse(
        subject_id=request.subject_id,
        subject_name=subject_name,
        turns=[TurnView(**t) for t in turns],
        total_turns=total,
        has_more=(request.offset + len(turns)) < total,
    )


def _build_tree(node_dict: dict) -> SubjectTreeNode:
    return SubjectTreeNode(
        subject_id=node_dict["subject_id"],
        name=node_dict["name"],
        turn_count=node_dict["turn_count"],
        children=[_build_tree(c) for c in node_dict.get("children", [])],
    )


@router.post("/subject-tree", response_model=SubjectTreeResponse)
async def api_subject_tree(
    request: SubjectTreeRequest,
    db: AsyncSession = Depends(get_session),
) -> SubjectTreeResponse:
    """Hierarchical subject tree for sidebar rendering."""
    from jarvis.core.retrieval import get_subject_tree

    roots, total = await get_subject_tree(db, request.workspace_id)
    return SubjectTreeResponse(
        roots=[_build_tree(r) for r in roots],
        total_subjects=total,
    )


# ── Reflect & summaries (P4) ──


@router.post("/save-summaries", response_model=SaveSummariesResponse)
async def api_save_summaries(
    request: SaveSummariesRequest,
    db: AsyncSession = Depends(get_session),
) -> SaveSummariesResponse:
    """Upsert (date × subject) summaries that the AI client generated."""
    from jarvis.core.reflect import save_summaries

    items = [
        {
            "subject_id": s.subject_id,
            "date": s.date,
            "summary": s.summary,
            "turn_count": s.turn_count,
        }
        for s in request.summaries
    ]
    count = await save_summaries(db, request.workspace_id, items)
    await db.commit()
    return SaveSummariesResponse(upserted=count)


@router.post("/summaries", response_model=GetSummariesResponse)
async def api_get_summaries(
    request: GetSummariesRequest,
    db: AsyncSession = Depends(get_session),
) -> GetSummariesResponse:
    """Fetch summaries in date range (optionally subject-filtered)."""
    from jarvis.core.reflect import _parse_date, get_summaries

    items = await get_summaries(
        db, request.workspace_id,
        date_from=_parse_date(request.date_from),
        date_to=_parse_date(request.date_to),
        subject_id=request.subject_id,
    )
    return GetSummariesResponse(
        summaries=[SummaryBrief(**i) for i in items],
        total=len(items),
    )


@router.post("/pending-reflects", response_model=PendingReflectsResponse)
async def api_pending_reflects(
    request: PendingReflectsRequest,
    db: AsyncSession = Depends(get_session),
) -> PendingReflectsResponse:
    """Find (date, subject) pairs with turns but no summary yet.

    Used by AI client when user says '오늘 정리해' to know what to summarize.
    """
    from jarvis.core.reflect import _parse_date, get_pending_reflects

    items = await get_pending_reflects(
        db, request.workspace_id,
        date_from=_parse_date(request.date_from),
        date_to=_parse_date(request.date_to),
    )
    return PendingReflectsResponse(
        pending=[PendingReflectItem(**i) for i in items],
    )
