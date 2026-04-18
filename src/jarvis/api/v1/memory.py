"""Memory endpoints: store, recall, initialize, upload-transcript, gap analysis."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.passage_search import search_passages
from jarvis.core.recall import recall_memory
from jarvis.core.store import create_episode, get_or_create_session, store_memory
from jarvis.core.topic_map import build_topic_map
from jarvis.db import get_session
from jarvis.models.tables import Entity, Episode, KnowledgeFact, Workspace
from jarvis.schemas import (
    AnalyzeGapsRequest,
    AnalyzeGapsResponse,
    ExploreTopicRequest,
    InitializeMemoryRequest,
    InitializeMemoryResponse,
    PassageHitResponse,
    RecallMemoryRequest,
    RecallMemoryResponse,
    SearchPassagesRequest,
    SearchPassagesResponse,
    StoreMemoryRequest,
    StoreMemoryResponse,
    TopicMapResponse,
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
