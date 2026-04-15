"""Memory endpoints: store, recall, initialize, upload-transcript, gap analysis."""

import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.recall import recall_memory
from jarvis.core.store import create_episode, get_or_create_session, store_memory
from jarvis.db import async_session_factory, get_session
from jarvis.models.tables import Entity, Episode, KnowledgeFact, Workspace
from jarvis.schemas import (
    AnalyzeGapsRequest,
    AnalyzeGapsResponse,
    InitializeMemoryRequest,
    InitializeMemoryResponse,
    RecallMemoryRequest,
    RecallMemoryResponse,
    StoreMemoryRequest,
    StoreMemoryResponse,
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
    await db.commit()

    logger.info(
        "Transcript uploaded: episode=%s, session=%s, %d chars",
        episode.id, session.id, len(request.transcript),
    )

    # Gap processing is triggered separately via analyze-gaps endpoint
    # Not auto-triggered on upload to avoid overloading server with concurrent tasks

    return UploadTranscriptResponse(
        episode_id=episode.id,
        session_id=session.id,
        status="processing",
    )


async def _process_transcript_background(
    episode_id: str,
    workspace_id: str,
) -> None:
    """Background: detect gaps → extract → store."""
    try:
        async with async_session_factory() as db:
            # Load episode
            result = await db.execute(
                select(Episode).where(Episode.id == episode_id)
            )
            episode = result.scalar_one_or_none()
            if not episode:
                logger.error("Background processing: episode %s not found", episode_id)
                return

            transcript = episode.content

            # Get existing keywords from fragments for gap detection
            from jarvis.models.tables import Fragment
            frag_result = await db.execute(
                select(Fragment.keywords).where(Fragment.workspace_id == workspace_id)
            )
            existing_keywords: set[str] = set()
            for row in frag_result.fetchall():
                if row[0]:
                    existing_keywords.update(row[0])

            # Stage 1: Detect gaps
            from jarvis.core.gap_detection import detect_gaps
            gaps = detect_gaps(
                transcript=transcript,
                covered_turn_indices=set(),  # No turns covered yet (new transcript)
                existing_keywords=existing_keywords,
            )

            logger.info(
                "Gap detection: episode=%s, recommendation=%s, coverage=%.2f, gaps=%d",
                episode_id, gaps.recommendation, gaps.coverage_ratio, len(gaps.gaps),
            )

            if gaps.recommendation == "skip":
                return

            # Stage 2: Extract from gaps using claude -p
            from jarvis.core.gap_extraction import extract_from_gaps
            gap_turns = [
                {"index": g.turn.turn_index, "text": g.turn.text}
                for g in gaps.gaps[:20]  # Limit to top 20 gaps
            ]
            extracted = await extract_from_gaps(gap_turns, transcript)

            if not extracted:
                logger.info("Gap extraction: no facts extracted for episode=%s", episode_id)
                return

            # Stage 3: Reconcile with existing facts
            existing_facts_result = await db.execute(
                select(KnowledgeFact, Entity.name)
                .join(Entity, KnowledgeFact.entity_id == Entity.id)
                .where(
                    KnowledgeFact.workspace_id == workspace_id,
                    KnowledgeFact.superseded_at.is_(None),
                )
            )
            existing_facts = [
                {"entity": name, "predicate": f.predicate, "object": f.object_value}
                for f, name in existing_facts_result.all()
            ]

            from jarvis.core.gap_extraction import reconcile_facts
            actions = await reconcile_facts(extracted, existing_facts)

            # Stage 4: Store ADD/UPDATE actions via store_memory pipeline
            import uuid as uuid_mod

            from jarvis.schemas import EntityHint, FactHint

            actionable = [a for a in actions if a.get("action") in ("ADD", "UPDATE")]
            if actionable:
                store_request = StoreMemoryRequest(
                    workspace_id=uuid_mod.UUID(workspace_id),
                    provider="manual",
                    conversation_transcript=transcript[:10000],
                    entities=[
                        EntityHint(
                            name=a["entity"],
                            entity_type="concept",
                            source_quote=a.get("source_quote", ""),
                        )
                        for a in actionable
                    ],
                    facts=[
                        FactHint(
                            subject=a["entity"],
                            predicate=a["predicate"],
                            object=a["object"],
                            source_quote=a.get("source_quote", ""),
                        )
                        for a in actionable
                    ],
                )
                store_result = await store_memory(db, store_request)
                supersede_count = sum(
                    1 for sf in store_result.facts_stored if sf.is_supersede
                )

                logger.info(
                    "Gap extraction stored: episode=%s, facts=%d, supersedes=%d",
                    episode_id, len(store_result.facts_stored), supersede_count,
                )
            else:
                logger.info("Gap reconciliation: all NOOP for episode=%s", episode_id)

    except Exception:
        logger.exception("Background transcript processing failed for episode=%s", episode_id)


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
