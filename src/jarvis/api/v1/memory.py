"""Memory endpoints: store, recall, initialize, upload-transcript, gap analysis."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.episode_excerpt import get_episode_excerpt
from jarvis.core.follow_relation import follow_relation
from jarvis.core.passage_search import search_passages
from jarvis.core.raw_search import (
    search_episode_content,
    search_fragment_content,
)
from jarvis.core.recall import recall_memory
from jarvis.core.store import create_episode, get_or_create_session, store_memory
from jarvis.core.topic_map import build_topic_map
from jarvis.db import get_session
from jarvis.models.tables import Entity, Episode, KnowledgeFact, Workspace
from jarvis.schemas import (
    AnalyzeGapsRequest,
    AnalyzeGapsResponse,
    BoostIndexHintsRequest,
    BoostIndexHintsResponse,
    BriefActiveWorkspace,
    BriefDataQuality,
    BriefLastEpisode,
    BriefOpenItem,
    BriefRecentThread,
    BriefRecommendation,
    BriefRequest,
    BriefResponse,
    ClassifyTurnsRequest,
    ClassifyTurnsResponse,
    DashboardOnThisDay,
    DashboardRecentSummary,
    DashboardRequest,
    DashboardResponse,
    DashboardStats,
    DashboardTopEntity,
    DateBucket,
    DateBucketsRequest,
    DateBucketsResponse,
    EntityHint,
    EntityIndexEntry,
    EntityIndexGroup,
    EntityIndexRequest,
    EntityIndexResponse,
    EntityPageRequest,
    EntityPageResponse,
    EntitySummary,
    EpisodeExcerptRequest,
    EpisodeExcerptResponse,
    EpisodeRef,
    ExploreTopicRequest,
    FactBriefResponse,
    FactEntry,
    FollowRelationRequest,
    FollowRelationResponse,
    GetSummariesRequest,
    GetSummariesResponse,
    GraphEdge,
    GraphNode,
    GraphRequest,
    GraphResponse,
    IndexEpisodeRequest,
    IndexEpisodeResponse,
    IngestAndIndexRequest,
    IngestAndIndexResponse,
    IngestLedgerEntry,
    IngestLedgerLocalDiffEntry,
    IngestLedgerRequest,
    IngestLedgerResponse,
    IngestTranscriptRequest,
    IngestTranscriptResponse,
    InitializeMemoryRequest,
    InitializeMemoryResponse,
    ListSubjectsRequest,
    ListSubjectsResponse,
    OnThisDayMatch,
    OnThisDayRequest,
    OnThisDayResponse,
    PassageHitResponse,
    PendingReflectItem,
    PendingReflectsRequest,
    PendingReflectsResponse,
    RawEpisodeHit,
    RawFragmentHit,
    RecallMemoryRequest,
    RecallMemoryResponse,
    RelatedNodeResponse,
    RelationEntry,
    SaveSummariesRequest,
    SaveSummariesResponse,
    SearchEpisodesRequest,
    SearchEpisodesResponse,
    SearchFragmentsFtsRequest,
    SearchFragmentsFtsResponse,
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


@router.post("/store", response_model=StoreMemoryResponse, deprecated=True)
async def api_store_memory(
    request: StoreMemoryRequest,
    db: AsyncSession = Depends(get_session),
) -> StoreMemoryResponse:
    """DEPRECATED — legacy KG-only ingest. Use /ingest-and-index instead.

    Diary-mode vision §3.6 expects body + summary + keywords + subject
    classification + entity/fact/relation in one call. This endpoint only
    handles entity/fact/relation and predates the unified flow. Kept for
    backwards compatibility with the legacy `jarvis_store_memory` MCP tool.
    """
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
                cleaned_content=h.cleaned_content,
                similarity=h.similarity,
                episode_id=h.episode_id,
                fact_id=h.fact_id,
                entity_name=h.entity_name,
                predicate=h.predicate,
            )
            for h in hits
        ],
    )


@router.post("/search-episodes", response_model=SearchEpisodesResponse)
async def api_search_episodes(
    request: SearchEpisodesRequest,
    db: AsyncSession = Depends(get_session),
) -> SearchEpisodesResponse:
    """PGroonga `&@~` over episodes.content + episodes.summary.

    Phase 1 raw FTS endpoint (plan sequential-munching-dove.md, A 결함 해소).
    Surfaces any keyword that lives in raw transcript bodies even when AI
    extraction missed it as an entity/fact.
    """
    from jarvis.core.query_preprocessing import preprocess_query

    pq = preprocess_query(request.query)
    hits = await search_episode_content(
        db, request.workspace_id, pq.fts_query, request.limit,
    )
    return SearchEpisodesResponse(
        query=request.query,
        fts_query=pq.fts_query,
        results=[
            RawEpisodeHit(
                episode_id=h.episode_id,
                summary=h.summary,
                snippet=h.snippet,
                cleaned_snippet=h.cleaned_snippet,
                score=h.score,
                created_at=h.created_at,
                matched_field=h.matched_field,
            )
            for h in hits
        ],
    )


@router.post("/search-fragments-fts", response_model=SearchFragmentsFtsResponse)
async def api_search_fragments_fts(
    request: SearchFragmentsFtsRequest,
    db: AsyncSession = Depends(get_session),
) -> SearchFragmentsFtsResponse:
    """PGroonga `&@~` over fragments.content.

    Phase 1 raw FTS endpoint — complements vector-only /search-passages so
    keywords the embedding can't surface still come through.
    """
    from jarvis.core.query_preprocessing import preprocess_query

    pq = preprocess_query(request.query)
    hits = await search_fragment_content(
        db, request.workspace_id, pq.fts_query, request.limit,
    )
    return SearchFragmentsFtsResponse(
        query=request.query,
        fts_query=pq.fts_query,
        results=[
            RawFragmentHit(
                fragment_id=h.fragment_id,
                content=h.content,
                cleaned_content=h.cleaned_content,
                score=h.score,
                episode_id=h.episode_id,
                fact_id=h.fact_id,
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


@router.post("/upload-transcript", response_model=UploadTranscriptResponse, deprecated=True)
async def api_upload_transcript(
    request: UploadTranscriptRequest,
    db: AsyncSession = Depends(get_session),
) -> UploadTranscriptResponse:
    """DEPRECATED — Path B (async gap-extraction worker) is retired.

    ACTIVE_ROADMAP.md 2026-05-13: worker.py / gap_extraction.py removed.
    Use /ingest-and-index instead — the AI client writes the diary entry
    (turns + summary + keywords + subjects + entity/fact/relation) directly.
    """
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


@router.post("/ingest-transcript", response_model=IngestTranscriptResponse, deprecated=True)
async def api_ingest_transcript(
    request: IngestTranscriptRequest,
    db: AsyncSession = Depends(get_session),
) -> IngestTranscriptResponse:
    """DEPRECATED — turns-only ingest with no subject classification.

    Use /ingest-and-index instead, which accepts the same turns plus the
    AI-written summary, keywords, subject mapping, and optional
    entity/fact/relation index in one call (vision §3.6).
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

    episode, turn_count, is_dup, _ = await _ingest(
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


@router.patch("/episodes/{episode_id}/index-hints", response_model=BoostIndexHintsResponse)
async def api_boost_index_hints(
    episode_id: uuid.UUID,
    request: BoostIndexHintsRequest,
    db: AsyncSession = Depends(get_session),
) -> BoostIndexHintsResponse:
    """Backfill index hints (keywords + subject mapping) on an existing episode.

    Diary-mode vision §3.6 makes summary + keywords + subject mapping mandatory
    on `/ingest-and-index`, but the ~2,200 episodes ingested before this rule
    have very sparse hints (keywords ~0.5%, subject mapping ~6%). This endpoint
    lets the AI client re-read an old episode and PATCH the missing index
    pieces so recall can actually find it.

    Body: workspace_id + at least one of keywords / existing_links / new_subjects.
    Keywords union with whatever is already in metadata. Subject links go through
    the same classify_turns logic as /classify-turns.
    """
    from jarvis.core.subjects import classify_turns as _classify_turns

    result = await db.execute(
        select(Episode).where(
            Episode.id == episode_id,
            Episode.workspace_id == request.workspace_id,
        )
    )
    episode = result.scalar_one_or_none()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found in workspace")

    keywords_count = 0
    if request.keywords:
        meta = dict(episode.metadata_ or {})
        existing_kw = list(meta.get("keywords") or [])
        merged = list(dict.fromkeys(existing_kw + request.keywords))
        meta["keywords"] = merged
        episode.metadata_ = meta
        keywords_count = len(merged)
    elif episode.metadata_ and isinstance(episode.metadata_, dict):
        keywords_count = len(episode.metadata_.get("keywords") or [])

    created_subjects = 0
    linked_turns = 0
    if request.existing_links or request.new_subjects:
        info = await _classify_turns(
            db, request.workspace_id,
            existing_links=request.existing_links,
            new_subjects=request.new_subjects,
        )
        created_subjects = info.get("created_subjects", 0)
        linked_turns = info.get("linked_turns", 0)

    await db.commit()
    return BoostIndexHintsResponse(
        episode_id=episode_id,
        keywords_count=keywords_count,
        created_subjects=created_subjects,
        linked_turns=linked_turns,
    )


@router.post("/ingest-and-index", response_model=IngestAndIndexResponse)
async def api_ingest_and_index(
    request: IngestAndIndexRequest,
    db: AsyncSession = Depends(get_session),
) -> IngestAndIndexResponse:
    """Diary-mode single-call ingest + classify + summaries + entity/fact/relation index.

    Vision §3.6: AI writes its diary entry — turns, raw, summary, keywords,
    subject classification, daily summary, and entity/fact/relation index —
    all in one call.

    Internally calls (in order):
      1. ingest_transcript()  — Episode + Turn rows (content_hash dedup)
      2. classify_turns()     — subject links (skipped if no link payload)
      3. save_summaries()     — daily_subject_summaries upsert (skipped if empty)
      3b. subject_summaries   — cumulative narrative upsert (subject_name → id,
                                skipped if empty; misses logged & dropped)
      3c. workspaces.cumulative_summary UPDATE — None ⇒ untouched, else replace
      4. store_memory()       — entity/fact/relation index (skipped if empty)

    Each stage is independently optional; the API is a convenience wrapper
    that lets the caller pass everything in one HTTP request and avoids
    forcing the AI to orchestrate four separate calls.
    """
    from jarvis.core.reflect import save_summaries as _save_summaries
    from jarvis.core.subjects import classify_turns as _classify_turns
    from jarvis.core.turn_ingest import (
        ingest_transcript as _ingest,
    )
    from jarvis.core.turn_ingest import (
        resolve_turn_sequences as _resolve_seqs,
    )

    # --- Stage 1: ingest_transcript ---
    turn_dicts = [
        {"sequence": t.sequence, "role": t.role, "text": t.text, "timestamp": t.timestamp}
        for t in request.turns
    ]
    metadata = dict(request.metadata or {})
    if request.source_session_id:
        metadata["external_session_id"] = request.source_session_id
    if request.source_path:
        metadata["source_path"] = request.source_path
    if request.summary:
        metadata["summary"] = request.summary
    if request.keywords:
        metadata["keywords"] = request.keywords

    episode, turn_count, is_dup, seq_to_id = await _ingest(
        db, request.workspace_id, turn_dicts,
        session_id=request.session_id,
        provider=request.provider,
        title=request.title or request.summary[:200],
        summary=request.summary,
        diary_entry=request.diary_entry,
        human_summary=request.human_summary,
        metadata=metadata,
        raw_content=request.raw_content,
    )

    # --- Stage 2: classify_turns (optional) ---
    # Translate turn_sequences → turn_ids using the map from ingest_transcript
    # so the same call can do turn-level subject classification without two
    # round-trips. plan sequential-munching-dove.md (phase 2, B1 해소).
    created_subjects = 0
    linked_turns = 0
    if request.existing_links or request.new_subjects:
        existing_links = [_resolve_seqs(s, seq_to_id) for s in request.existing_links]
        new_subjects = [_resolve_seqs(s, seq_to_id) for s in request.new_subjects]
        info = await _classify_turns(
            db, request.workspace_id,
            existing_links=existing_links,
            new_subjects=new_subjects,
        )
        created_subjects = info.get("created_subjects", 0)
        linked_turns = info.get("linked_turns", 0)

    # --- Stage 3: save_summaries (optional) ---
    summaries_upserted = 0
    if request.daily_summaries:
        items = [
            {
                "subject_id": s.subject_id,
                "date": s.date,
                "summary": s.summary,
                "turn_count": s.turn_count,
                "unique_turn_count": s.unique_turn_count,
            }
            for s in request.daily_summaries
        ]
        summaries_upserted = await _save_summaries(db, request.workspace_id, items)

    # --- Stage 3b: subject_summaries cumulative upsert (optional) ---
    # SubjectSummary speaks subject_name (AI-facing); resolve → subject_id by
    # looking up entities in this workspace. Misses are skipped + logged so
    # one typo doesn't fail the whole diary call.
    subject_summaries_upserted = 0
    if request.subject_summaries:
        from datetime import date as _date_cls

        # Pull a {normalized_name: id} map for the ws — single query, no N+1.
        name_rows = await db.execute(
            select(Entity.id, Entity.name, Entity.name_normalized)
            .where(Entity.workspace_id == request.workspace_id),
        )
        # Prefer exact name match; fall back to normalized for robustness.
        name_to_id: dict[str, uuid.UUID] = {}
        norm_to_id: dict[str, uuid.UUID] = {}
        for eid, ename, enorm in name_rows.all():
            name_to_id[ename] = eid
            if enorm:
                norm_to_id[enorm] = eid

        today_str = _date_cls.today().isoformat()
        cum_items: list[dict[str, object]] = []
        for ss in request.subject_summaries:
            sid = name_to_id.get(ss.subject_name)
            if sid is None:
                sid = norm_to_id.get(ss.subject_name.strip().lower())
            if sid is None:
                logger.warning(
                    "subject_summaries: unknown subject_name=%r in ws=%s — skipped",
                    ss.subject_name, request.workspace_id,
                )
                continue
            cum_items.append({
                "subject_id": sid,
                "date": ss.date or today_str,
                "summary": ss.cumulative_summary,
                "turn_count": int(ss.turn_count_today or 0),
                "unique_turn_count": int(ss.turn_count_today or 0),
            })
        if cum_items:
            subject_summaries_upserted = await _save_summaries(
                db, request.workspace_id, cum_items,
            )

    # --- Stage 3c: workspaces.cumulative_summary UPDATE (optional) ---
    # `None` ⇒ caller did not opt in, leave column alone. Any string (incl.
    # empty) ⇒ explicit replace. Lets the AI clear a stale narrative by
    # passing "" without breaking the partial-update model.
    workspace_summary_updated = False
    if request.workspace_summary is not None:
        from sqlalchemy import text as _sql_text

        await db.execute(
            _sql_text(
                "UPDATE workspaces SET cumulative_summary = :cs WHERE id = :wid"
            ),
            {"cs": request.workspace_summary, "wid": str(request.workspace_id)},
        )
        workspace_summary_updated = True

    # --- Stage 4: store_memory (optional) ---
    entities_resolved = 0
    entities_created = 0
    facts_stored = []
    relations_stored = 0
    if request.entities or request.facts or request.relations:
        # store_memory rebuilds Episode by content_hash dedup → returns same
        # episode created in Stage 1, then adds entity/fact/relation to it.
        transcript_for_store = request.raw_content or "\n\n".join(
            f"[{t.role}] {t.text}" for t in request.turns
        )
        sm_req = StoreMemoryRequest(
            workspace_id=request.workspace_id,
            session_id=episode.session_id,
            provider=request.provider,
            conversation_transcript=transcript_for_store,
            entities=request.entities,
            facts=request.facts,
            relations=request.relations,
            conversation_summary=request.summary,
        )
        sm_resp = await store_memory(db, sm_req)
        entities_resolved = sm_resp.entities_resolved
        entities_created = sm_resp.entities_created
        facts_stored = sm_resp.facts_stored
        relations_stored = len(request.relations)

    await db.commit()
    return IngestAndIndexResponse(
        episode_id=episode.id,
        session_id=episode.session_id,
        turn_count=turn_count,
        is_duplicate=is_dup,
        created_subjects=created_subjects,
        linked_turns=linked_turns,
        summaries_upserted=summaries_upserted,
        subject_summaries_upserted=subject_summaries_upserted,
        workspace_summary_updated=workspace_summary_updated,
        entities_resolved=entities_resolved,
        entities_created=entities_created,
        facts_stored=facts_stored,
        relations_stored=relations_stored,
    )


@router.post("/index-episode", response_model=IndexEpisodeResponse)
async def api_index_episode(
    request: IndexEpisodeRequest,
    db: AsyncSession = Depends(get_session),
) -> IndexEpisodeResponse:
    """Backfill a knowledge index (entities/facts/relations + embeddings) onto an
    EXISTING episode.

    Unlike /ingest-and-index Stage 4, this targets the episode by id and never
    calls create_episode — so re-indexing the diary-migration episodes (which
    lack any fact/fragment/embedding) cannot spawn duplicate episodes from a
    content_hash mismatch. Embeddings are generated synchronously, so when this
    returns the episode is recall-ready.
    """
    from jarvis.core.store import (
        _generate_embeddings,
        _store_relation,
        resolve_entity,
        store_fact,
    )

    ep = await db.get(Episode, request.episode_id)
    if ep is None or str(ep.workspace_id) != str(request.workspace_id):
        raise HTTPException(status_code=404, detail="Episode not found in workspace")
    # Grounding source for verify_quote — the stored joined-turn content.
    transcript = ep.content or ep.summary or ""

    entity_map: dict[str, Entity] = {}
    entities_created = 0
    for hint in request.entities:
        entity, is_new = await resolve_entity(db, request.workspace_id, hint)
        entity_map[hint.name] = entity
        if is_new:
            entities_created += 1

    stored_facts = []
    for fh in request.facts:
        if fh.subject not in entity_map:
            entity, is_new = await resolve_entity(
                db,
                request.workspace_id,
                EntityHint(name=fh.subject, entity_type="other", source_quote=fh.source_quote),
            )
            entity_map[fh.subject] = entity
            if is_new:
                entities_created += 1
        resp = await store_fact(
            db, request.workspace_id, entity_map[fh.subject], fh, ep, transcript,
        )
        stored_facts.append(resp)

    for rh in request.relations:
        await _store_relation(db, request.workspace_id, rh, entity_map, ep)

    await db.commit()

    # Synchronous embeddings (await, not fire-and-forget) so the caller can
    # verify recall is ready before moving to the next episode.
    embeddings_ok = True
    try:
        await _generate_embeddings(
            request.workspace_id,
            ep,
            list(entity_map.values()),
            [f.fact_id for f in stored_facts],
        )
    except Exception:
        logger.exception("index-episode: embedding generation failed for %s", ep.id)
        embeddings_ok = False

    return IndexEpisodeResponse(
        episode_id=ep.id,
        entities_resolved=len(entity_map) - entities_created,
        entities_created=entities_created,
        facts_stored=len(stored_facts),
        relations_stored=len(request.relations),
        embeddings_generated=embeddings_ok,
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


@router.post("/diaries-by-date")
async def api_diaries_by_date(
    request: dict,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Diary-first main view payload.

    Returns one record per episode (with diary_entry) within the date range.
    Episodes whose diary_entry is null fall back to summary so the UI never
    shows a blank card.
    """
    from sqlalchemy import text as sql_text
    workspace_id = request.get("workspace_id")
    date_from = request.get("date_from")  # ISO string or None
    date_to = request.get("date_to")
    limit = int(request.get("limit", 200))
    order = "DESC" if request.get("descending", True) else "ASC"

    from datetime import datetime as _dt

    def _parse_dt(v):
        if not v:
            return None
        if isinstance(v, _dt):
            return v
        return _dt.fromisoformat(str(v).replace("Z", "+00:00"))

    params: dict[str, Any] = {"ws": workspace_id, "lim": limit}
    where = ["e.workspace_id = :ws",
             "(e.metadata->>'deleted' IS NULL OR e.metadata->>'deleted' != 'true')"]
    df = _parse_dt(date_from)
    dt_ = _parse_dt(date_to)
    if df is not None:
        where.append("first_turn.first_ts >= :date_from")
        params["date_from"] = df
    if dt_ is not None:
        where.append("first_turn.first_ts < :date_to")
        params["date_to"] = dt_

    where_sql = " AND ".join(where)
    rows = await db.execute(
        sql_text(f"""
            WITH first_turn AS (
                SELECT episode_id, MIN(timestamp) AS first_ts, COUNT(*) AS turn_count
                FROM turns
                GROUP BY episode_id
            )
            SELECT
                e.id, e.summary, e.diary_entry, e.metadata->>'title' AS title,
                first_turn.first_ts AS day_ts,
                first_turn.turn_count,
                e.human_summary
            FROM episodes e
            JOIN first_turn ON first_turn.episode_id = e.id
            WHERE {where_sql}
            ORDER BY first_turn.first_ts {order}
            LIMIT :lim
        """),
        params,
    )
    items = []
    for r in rows.fetchall():
        items.append({
            "episode_id": str(r[0]),
            "summary": r[1] or "",
            "diary_entry": r[2],
            "title": r[3] or "",
            "day_ts": r[4].isoformat() if r[4] else None,
            "turn_count": int(r[5] or 0),
            "human_summary": r[6],
        })
    return {"items": items, "total": len(items)}


@router.post("/date-buckets", response_model=DateBucketsResponse)
async def api_date_buckets(
    request: DateBucketsRequest,
    db: AsyncSession = Depends(get_session),
) -> DateBucketsResponse:
    """Per-day turn count for full workspace — light, used by sidebar date tree."""
    from jarvis.core.retrieval import get_date_buckets

    buckets, total = await get_date_buckets(db, request.workspace_id, request.subject_id)
    return DateBucketsResponse(
        buckets=[DateBucket(**b) for b in buckets],
        total_turns=total,
    )


@router.post("/entity-page", response_model=EntityPageResponse)
async def api_entity_page(
    request: EntityPageRequest,
    db: AsyncSession = Depends(get_session),
) -> EntityPageResponse:
    """Wiki-style entity page — entity + facts + bidirectional relations + recent episodes."""
    from fastapi import HTTPException

    from jarvis.core.retrieval import get_entity_page

    payload = await get_entity_page(
        db, request.workspace_id, request.entity_id,
        fact_limit=request.fact_limit,
        relation_limit=request.relation_limit,
        episode_limit=request.episode_limit,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="entity not found")
    return EntityPageResponse(
        entity_id=payload["entity_id"],
        name=payload["name"],
        entity_type=payload["entity_type"],
        parent=EntitySummary(**payload["parent"]) if payload["parent"] else None,
        children=[EntitySummary(**c) for c in payload["children"]],
        aliases=payload["aliases"],
        wiki_article=payload.get("wiki_article"),
        facts=[FactEntry(**f) for f in payload["facts"]],
        relations=[RelationEntry(**r) for r in payload["relations"]],
        recent_episodes=[EpisodeRef(**e) for e in payload["recent_episodes"]],
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
        entity_type=node_dict.get("entity_type"),
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


@router.post("/brief", response_model=BriefResponse)
async def api_brief(
    request: BriefRequest,
    db: AsyncSession = Depends(get_session),
) -> BriefResponse:
    """JARVIS Today's Brief — '지금 뭐 해야 하지?' single payload.

    Shared by MCP tool jarvis_brief_me and the UI Today's Brief card.
    workspace_id (or workspace_name) absent → cross-ws mode.
    workspace_id/name present → deep mode.
    """
    return await _brief_response(
        db,
        workspace_id=request.workspace_id,
        workspace_name=request.workspace_name,
        detail=request.detail,
        include_hidden=request.include_hidden,
    )


@router.get("/brief", response_model=BriefResponse)
async def api_brief_get(
    workspace_id: uuid.UUID | None = Query(default=None),
    workspace_name: str | None = Query(default=None),
    detail: str = Query(default="brief"),
    include_hidden: bool = Query(default=False),
    db: AsyncSession = Depends(get_session),
) -> BriefResponse:
    """GET form for easy curl / browser testing — same payload as POST."""
    detail_norm = detail.strip().lower() if detail else "brief"
    if detail_norm not in ("brief", "deep"):
        raise HTTPException(
            status_code=400,
            detail=f"detail must be 'brief' or 'deep', got '{detail}'",
        )
    return await _brief_response(
        db,
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        detail=detail_norm,  # type: ignore[arg-type]
        include_hidden=include_hidden,
    )


async def _brief_response(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID | None,
    workspace_name: str | None,
    detail: str,
    include_hidden: bool,
) -> BriefResponse:
    from jarvis.core.brief_engine import compute_brief

    payload = await compute_brief(
        db,
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        detail=detail,  # type: ignore[arg-type]
        include_hidden=include_hidden,
    )
    today_val = payload["today"]
    today_str = today_val.isoformat() if hasattr(today_val, "isoformat") else str(today_val)

    target_ws = payload.get("target_workspace")
    if target_ws is not None:
        # Stringify uuid for JSON friendliness on the optional dict field.
        target_ws = {"id": str(target_ws["id"]), "name": target_ws["name"]}

    return BriefResponse(
        mode=payload["mode"],
        generated_at=payload["generated_at"],
        today=today_str,
        target_workspace=target_ws,
        active_workspaces=[BriefActiveWorkspace(**w) for w in payload["active_workspaces"]],
        recent_threads=[BriefRecentThread(**t) for t in payload["recent_threads"]],
        open_questions=[BriefOpenItem(**o) for o in payload["open_questions"]],
        next_recommendations=[
            BriefRecommendation(**r) for r in payload["next_recommendations"]
        ],
        data_quality=BriefDataQuality(**payload["data_quality"]),
        last_episodes=[BriefLastEpisode(**e) for e in payload.get("last_episodes", [])],
        ascii_text=payload["ascii_text"],
    )


@router.post("/dashboard", response_model=DashboardResponse)
async def api_dashboard(
    request: DashboardRequest,
    db: AsyncSession = Depends(get_session),
) -> DashboardResponse:
    """JARVIS Home — 4-block dashboard payload.

    Stats pill (header), recent daily summaries (last 7d), "On This Day"
    history, and top entities by active fact count. Powers the first-screen
    "alive · today's topics · neat" feel without requiring the user to
    pick a date or topic first.
    """
    from jarvis.core.retrieval import get_dashboard

    # P8 — 14d × ≤2 top-level subjects per day = ≤28 rows max.
    payload = await get_dashboard(
        db, request.workspace_id, recent_days=14, recent_limit=28
    )
    return DashboardResponse(
        stats=DashboardStats(**payload["stats"]),
        recent_summaries=[DashboardRecentSummary(**s) for s in payload["recent_summaries"]],
        on_this_day=[DashboardOnThisDay(**o) for o in payload["on_this_day"]],
        top_entities=[DashboardTopEntity(**e) for e in payload["top_entities"]],
    )


@router.post("/entity-index", response_model=EntityIndexResponse)
async def api_entity_index(
    request: EntityIndexRequest,
    db: AsyncSession = Depends(get_session),
) -> EntityIndexResponse:
    """Wiki index — every (non-session-label) entity grouped by entity_type.

    Powers the new header '위키' tab so the user can scan, at a glance,
    what concepts/products/people/etc. the workspace has accumulated.
    Within each group, entries are sorted fact_count desc.
    """
    from jarvis.core.retrieval import get_entity_index

    groups = await get_entity_index(db, request.workspace_id)
    return EntityIndexResponse(
        groups=[
            EntityIndexGroup(
                entity_type=g["entity_type"],
                entities=[EntityIndexEntry(**e) for e in g["entities"]],
            )
            for g in groups
        ],
    )


@router.post("/graph", response_model=GraphResponse)
async def api_graph(
    request: GraphRequest,
    db: AsyncSession = Depends(get_session),
) -> GraphResponse:
    """Stage 2C — D3 force-directed entity graph for the wiki tab.

    Returns hub-cut nodes (top-N by relation_count) plus every edge whose
    endpoints both survive the cut — no orphan edges. `episode_topic`
    entities are always excluded. Inactive (archived/hidden) workspaces
    return 404 — the graph view is for live workspaces only.
    """
    from jarvis.core.retrieval import get_entity_graph

    # Active-ws gate — same contract as the rest of the read-side endpoints.
    # 404 (not 403) because the UI treats an inactive ws as "doesn't exist
    # for this view"; permission-style errors are reserved for membership.
    ws_row = await db.execute(
        select(Workspace.status).where(Workspace.id == request.workspace_id)
    )
    ws_status = ws_row.scalar_one_or_none()
    if ws_status is None or ws_status != "active":
        raise HTTPException(status_code=404, detail="workspace not found or inactive")

    data = await get_entity_graph(
        db,
        request.workspace_id,
        limit=request.limit,
        min_rel_cnt=request.min_rel_cnt,
        entity_types=request.entity_types,
        include_isolates=request.include_isolates,
    )
    return GraphResponse(
        workspace_id=data["workspace_id"],
        total_entities=data["total_entities"],
        returned_nodes=data["returned_nodes"],
        nodes=[GraphNode(**n) for n in data["nodes"]],
        edges=[GraphEdge(**e) for e in data["edges"]],
    )


@router.post("/on-this-day", response_model=OnThisDayResponse)
async def api_on_this_day(
    request: OnThisDayRequest,
    db: AsyncSession = Depends(get_session),
) -> OnThisDayResponse:
    """Episodes that landed on (month, day) in any past year.

    Defaults to today when month/day are omitted. Powers the home
    'On This Day' card and lets the AI surface 작년 같은 날 conversations
    without needing the calendar widget.
    """
    from jarvis.core.retrieval import get_on_this_day

    resolved_m, resolved_d, matches = await get_on_this_day(
        db, request.workspace_id,
        month=request.month, day=request.day, limit=request.limit,
    )
    return OnThisDayResponse(
        month=resolved_m,
        day=resolved_d,
        matches=[OnThisDayMatch(**m) for m in matches],
    )


@router.post("/ingest-ledger", response_model=IngestLedgerResponse)
async def api_ingest_ledger(
    request: IngestLedgerRequest,
    db: AsyncSession = Depends(get_session),
) -> IngestLedgerResponse:
    """P4 — "본대화 N개 중 무엇이 올라갔나" ledger view.

    Returns recent ingest rows for the workspace (newest first). When
    `include_local_diff` is true, also scans
    ``C:/Users/lhhh0/.claude/projects/F--brain/*.jsonl`` and compares each
    file's basename (= external_session_id) against the ledger so the
    caller can render a "안 올라간 본대화 N건" badge without doing the
    scan client-side.
    """
    from pathlib import Path

    from sqlalchemy import text as sql_text

    from jarvis.core.path_normalize import basename_no_ext

    ws = str(request.workspace_id)

    where = ["workspace_id = :ws"]
    params: dict = {"ws": ws, "lim": request.limit}
    if request.date_from is not None:
        where.append("ingested_at >= :date_from")
        params["date_from"] = request.date_from
    if request.date_to is not None:
        where.append("ingested_at < :date_to")
        params["date_to"] = request.date_to
    where_sql = " AND ".join(where)

    total_row = await db.execute(
        sql_text(f"SELECT COUNT(*) FROM ingest_ledger WHERE {where_sql}"),
        params,
    )
    total = int(total_row.scalar() or 0)

    rows = await db.execute(
        sql_text(
            f"""
            SELECT id, ingested_at, source_file_path, source_file_path_normalized,
                   external_session_id, episode_id, turn_count, ingested_via,
                   pipeline_version, status, dedup_decision, notes
            FROM ingest_ledger
            WHERE {where_sql}
            ORDER BY ingested_at DESC, id DESC
            LIMIT :lim
            """
        ),
        params,
    )
    entries = [
        IngestLedgerEntry(
            ledger_id=r[0],
            ingested_at=r[1],
            source_file_path=r[2] or "",
            source_file_path_normalized=r[3] or "",
            external_session_id=r[4],
            episode_id=r[5],
            turn_count=int(r[6] or 0),
            ingested_via=r[7] or "unknown",
            pipeline_version=r[8],
            status=r[9] or "ingested",
            dedup_decision=r[10],
            notes=r[11],
        )
        for r in rows.fetchall()
    ]

    local_diff: list[IngestLedgerLocalDiffEntry] = []
    summary = {"matched": 0, "not_ingested": 0}

    if request.include_local_diff:
        # Pull all sids ever ingested in this workspace (any version) for
        # match lookup. Workspace-scoped on purpose: a file lives "in" one
        # workspace from the operator's mental model.
        sid_rows = await db.execute(
            sql_text(
                """
                SELECT external_session_id, COUNT(*) AS c
                FROM ingest_ledger
                WHERE workspace_id = :ws AND external_session_id IS NOT NULL
                GROUP BY external_session_id
                """
            ),
            {"ws": ws},
        )
        ingested_sids: dict[str, int] = {r[0]: int(r[1] or 0) for r in sid_rows.fetchall()}

        # Scan the on-disk folder. The directory is configured via
        # JARVIS_CLAUDE_PROJECTS_DIR (set in docker-compose to the
        # bind-mounted host path) — operator UX, not a general-purpose API.
        # ≥100KB filter drops empty/aborted sessions the user does not
        # care about.
        import os
        scan_dir_path = os.environ.get(
            "JARVIS_CLAUDE_PROJECTS_DIR",
            "C:/Users/lhhh0/.claude/projects/F--brain",
        )
        scan_dir = Path(scan_dir_path)
        MIN_BYTES = 100 * 1024  # 100KB threshold per spec
        try:
            jsonl_paths = sorted(scan_dir.glob("*.jsonl"))
        except OSError:
            jsonl_paths = []

        for p in jsonl_paths:
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size < MIN_BYTES:
                continue
            sid = basename_no_ext(str(p))
            matched_count = ingested_sids.get(sid, 0)
            status = "matched" if matched_count > 0 else "not_ingested"
            summary[status] = summary.get(status, 0) + 1
            local_diff.append(
                IngestLedgerLocalDiffEntry(
                    local_file_path=str(p).replace("\\", "/"),
                    local_size=size,
                    external_session_id=sid,
                    status=status,
                    ingested_count=matched_count,
                )
            )

    return IngestLedgerResponse(
        entries=entries,
        total=total,
        local_diff=local_diff,
        local_diff_summary=summary,
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
