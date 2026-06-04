"""Raw transcript ingest pipeline — Episode + Turn level storage.

Replaces the old "AI extracts entities/facts/relations" flow at the data layer.
The new vision (2026-05-07): user explicitly pushes transcripts; subject
classification happens after, as a separate AI-proposes/user-confirms flow.

This module handles only the mechanical ingest (no extraction, no subject
classification). Inputs come from:
  - preprocessed/sessions/*.json (turns array already parsed)
  - ~/.claude/projects/**/*.jsonl (raw Claude Code, parsing needed)
  - Inline data via API
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.path_normalize import normalize_jsonl_path
from jarvis.core.sanitizer import sanitize_turn_text
from jarvis.core.summary_quality_gate import NEEDS_RESUMMARIZE, is_low_quality_summary
from jarvis.models.tables import Episode, Session, Turn

# Bumped when the ingest pipeline's canonical-form rules change in a way
# downstream tools care about (e.g., new sanitizer rule). Recorded on each
# ingest_ledger row so we can spot rows produced by an older pipeline.
PIPELINE_VERSION = "p4-ledger"

logger = logging.getLogger(__name__)


def _dedupe_turns(turns: list[dict]) -> tuple[list[dict], int]:
    """Drop duplicate turns sharing the same (role, text) — keep the first by sequence.

    Mirrors the DB-side dedupe migration so the ingest pipeline cannot
    re-introduce duplicates from a noisy input chunk. Returns the deduped
    list and the count of dropped turns.
    """
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    dropped = 0
    # Sort by sequence to make "first" deterministic regardless of input order.
    for t in sorted(turns, key=lambda x: x.get("sequence", 0)):
        key = (t.get("role", ""), t.get("text", ""))
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        deduped.append(t)
    return deduped, dropped


def _sanitize_turns(turns: list[dict]) -> tuple[list[dict], list[dict]]:
    """Strip tool-use lines from each turn.text.

    Returns (sanitized_turns, tool_payload_records).
      - sanitized_turns: turns with cleaned text (tool prefix lines removed).
      - tool_payload_records: per-turn sidecar [{"sequence", "role", "removed": [...]}]
        for turns that had any removed lines. Stored in
        episode.metadata.tool_payload for lossless audit.

    Turns whose entire text is tool output collapse to text='' — they are
    kept (not dropped) so sequence/timing stays intact, but their text is
    empty. Callers can later filter empty turns from display if desired.
    """
    sanitized_turns: list[dict] = []
    sidecar: list[dict] = []
    for t in turns:
        original = t.get("text", "")
        cleaned, removed = sanitize_turn_text(original, mode="cleaned")
        new_turn = {**t, "text": cleaned}
        sanitized_turns.append(new_turn)
        if removed:
            sidecar.append({
                "sequence": t.get("sequence"),
                "role": t.get("role"),
                "removed": removed,
            })
    return sanitized_turns, sidecar


def _build_episode_content(turns: list[dict]) -> str:
    """Concatenate turns into a single content blob for Episode.content.

    Preserves backward compat with existing search_passages / get_episode_excerpt
    which read episode.content. Turns are also stored in `turns` table for
    structured access.
    """
    parts = []
    for t in turns:
        role = t.get("role", "?")
        text = t.get("text", "")
        parts.append(f"[{role}] {text}")
    return "\n\n".join(parts)


def _compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def _write_ingest_ledger(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    metadata: dict[str, Any] | None,
    episode_id: uuid.UUID,
    turn_count: int,
    is_duplicate: bool,
) -> None:
    """Append one row to ingest_ledger for the just-completed ingest call.

    Reads source_path / external_session_id / ingested_via out of `metadata`
    so the caller doesn't need a new positional argument. If the same
    external_session_id already has a ledger entry in this workspace, the
    new row is marked dedup_decision='append_v2' (user policy: keep both,
    group in UI). On content_hash dedup hits, status='duplicate' and
    dedup_decision='replaced' — the episode already exists but the AI
    tried to push it again, which is itself information worth keeping.
    """
    md = metadata or {}
    source_path = md.get("source_path") or md.get("source_file") or ""
    ext_sid = md.get("external_session_id") or None
    ingested_via = md.get("ingested_via") or "unknown"
    sha = md.get("source_file_sha256") or None
    normalized = normalize_jsonl_path(source_path) if source_path else ""

    if is_duplicate:
        dedup_decision = "replaced"
        status = "duplicate"
    elif ext_sid:
        # Has there already been an entry for this sid in this workspace?
        existing = await db.execute(
            sql_text(
                "SELECT 1 FROM ingest_ledger "
                "WHERE workspace_id = :ws AND external_session_id = :sid LIMIT 1"
            ),
            {"ws": str(workspace_id), "sid": ext_sid},
        )
        dedup_decision = "append_v2" if existing.first() else "new"
        status = "ingested"
    else:
        dedup_decision = "new"
        status = "ingested"

    await db.execute(
        sql_text(
            """
            INSERT INTO ingest_ledger (
                workspace_id, source_file_path, source_file_path_normalized,
                source_file_sha256, external_session_id, ingested_via,
                pipeline_version, episode_id, turn_count, status, dedup_decision
            ) VALUES (
                :ws, :src, :norm, :sha, :sid, :via,
                :pv, :eid, :tc, :status, :dedup
            )
            """
        ),
        {
            "ws": str(workspace_id),
            "src": source_path,
            "norm": normalized,
            "sha": sha,
            "sid": ext_sid,
            "via": ingested_via,
            "pv": PIPELINE_VERSION,
            "eid": str(episode_id),
            "tc": int(turn_count or 0),
            "status": status,
            "dedup": dedup_decision,
        },
    )


def resolve_turn_sequences(spec: dict, seq_to_id: dict[int, uuid.UUID]) -> dict:
    """Translate spec['turn_sequences'] → spec['turn_ids'] in a copy of spec.

    Callers of ingest_transcript() can address turns by their input
    sequence number (caller-supplied int) instead of the server-assigned
    turn UUID. If turn_ids is already populated, the spec is returned as
    is. Sequences that miss in the map are silently dropped. Added in
    plan sequential-munching-dove.md (phase 2, B1 해소).
    """
    if spec.get("turn_ids"):
        return spec
    seqs = spec.get("turn_sequences")
    if not seqs:
        return spec
    return {**spec, "turn_ids": [seq_to_id[s] for s in seqs if s in seq_to_id]}


async def get_or_create_session(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID | None,
    provider: str,
) -> Session:
    if session_id:
        result = await db.execute(select(Session).where(Session.id == session_id))
        existing = result.scalar_one_or_none()
        if existing:
            existing.last_active_at = func.now()
            return existing
    sess = Session(
        workspace_id=workspace_id,
        provider=provider,
        client_type=provider,
    )
    db.add(sess)
    await db.flush()
    return sess


def _is_meta_episode(turns: list[dict]) -> bool:
    """DEPRECATED — kept for backward compat. Always returns False.

    Reason: heuristic filtering creates false positives (short user messages,
    valid one-line requests like "도커 먼저 해줘"). Model judgment via claude -p
    handles meta/irrelevant episodes correctly via empty subjects array.
    """
    return False


async def ingest_transcript(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    turns: list[dict],
    session_id: uuid.UUID | None = None,
    provider: str = "unknown",
    title: str = "",
    summary: str = "",
    diary_entry: str = "",
    human_summary: str = "",
    metadata: dict[str, Any] | None = None,
    skip_meta: bool = True,
    raw_content: str | None = None,
) -> tuple[Episode, int, bool, dict[int, uuid.UUID]]:
    """Ingest one transcript as Episode + Turn rows.

    `turns` is a list of dicts: {sequence, role, text, timestamp} — typically cleaned.
    `raw_content` (optional): full untruncated original (jsonl raw or paste). Stored
        in episodes.content for deep-recall escape hatch. Self-contained cloud
        backup — no dependency on user disk.

    Returns (episode, turn_count, is_duplicate, sequence_to_turn_id).
    sequence_to_turn_id is a {turn.sequence: turn.id} map for the just-inserted
    (or deduplicated) episode — callers translate caller-supplied sequence
    numbers into turn UUIDs for turn-level subject classification. Added in
    plan sequential-munching-dove.md (phase 2, B1 해소).
    """
    if not turns:
        raise ValueError("turns must be non-empty")

    # P5: dedupe + sanitize turns at the gate so the canonical stored form is
    # clean. raw_content (if provided) is preserved verbatim — that is the
    # untruncated escape hatch.
    turns, dropped_dup = _dedupe_turns(turns)
    if dropped_dup:
        logger.info("Dropped %d duplicate input turns (provider=%s)", dropped_dup, provider)
    if not turns:
        raise ValueError("turns must be non-empty (after dedupe)")
    turns, tool_payload = _sanitize_turns(turns)

    is_meta = bool(skip_meta and _is_meta_episode(turns))
    if is_meta:
        logger.info("Marking meta episode (provider=%s, turn_count=%d)", provider, len(turns))
        metadata = dict(metadata or {})
        metadata["is_meta"] = True

    # Stash removed tool-use lines as a sidecar so nothing is lost.
    if tool_payload:
        metadata = dict(metadata or {})
        metadata["tool_payload"] = tool_payload

    # Episode.content: prefer raw (full untruncated). If absent, fall back to cleaned turns.
    content = raw_content if raw_content else _build_episode_content(turns)
    content_hash = _compute_content_hash(content)

    # Dedup by content_hash
    existing_q = await db.execute(
        select(Episode).where(
            Episode.workspace_id == workspace_id,
            Episode.content_hash == content_hash,
        )
    )
    dup = existing_q.scalar_one_or_none()
    if dup:
        logger.info("Transcript already ingested (hash=%s) — skipping", content_hash[:12])
        # Rebuild the sequence → turn_id map from the existing turns so the
        # caller can still address turns by sequence even on a duplicate.
        dup_turns_q = await db.execute(
            select(Turn.sequence, Turn.id).where(Turn.episode_id == dup.id)
        )
        seq_to_id = {row[0]: row[1] for row in dup_turns_q.fetchall()}
        # P4: even on dedup, record the attempt — "본대화 N개 중 무엇이
        # 올라갔나" must include "tried to push again, was deduped".
        await _write_ingest_ledger(
            db,
            workspace_id=workspace_id,
            metadata=metadata,
            episode_id=dup.id,
            turn_count=len(turns),
            is_duplicate=True,
        )
        return dup, len(turns), True, seq_to_id

    sess = await get_or_create_session(db, workspace_id, session_id, provider)

    candidate_summary = summary or title or content[:300]
    # P5 quality gate: bare integers, jsonl-agent ids, sub-50-char snippets get
    # parked for later resummarization instead of polluting timeline UI.
    if is_low_quality_summary(candidate_summary):
        logger.info(
            "Episode summary failed quality gate (provider=%s, summary=%r) → %s",
            provider, candidate_summary[:60], NEEDS_RESUMMARIZE,
        )
        initial_status = NEEDS_RESUMMARIZE
    else:
        initial_status = "done"

    episode = Episode(
        session_id=sess.id,
        workspace_id=workspace_id,
        content=content,
        content_hash=content_hash,
        summary=candidate_summary,
        diary_entry=diary_entry or None,
        human_summary=human_summary or None,
        metadata_=metadata or {},
        processing_status=initial_status,
    )
    db.add(episode)
    await db.flush()

    chunk_size = 500
    for i, t in enumerate(turns):
        ts = t.get("timestamp")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        turn = Turn(
            workspace_id=workspace_id,
            episode_id=episode.id,
            sequence=t["sequence"],
            role=t["role"],
            text=t.get("text", ""),
            timestamp=ts,
            summary=t.get("summary"),
        )
        db.add(turn)
        if (i + 1) % chunk_size == 0:
            await db.flush()

    await db.flush()
    seq_to_id_q = await db.execute(
        select(Turn.sequence, Turn.id).where(Turn.episode_id == episode.id)
    )
    seq_to_id = {row[0]: row[1] for row in seq_to_id_q.fetchall()}
    logger.info(
        "Ingested episode %s with %d turns (workspace=%s)",
        episode.id, len(turns), workspace_id,
    )
    # P4: record success in ingest_ledger so the Home dashboard widget can
    # answer "본대화 N개 중 무엇이 올라갔나" without scanning episodes JSONB.
    await _write_ingest_ledger(
        db,
        workspace_id=workspace_id,
        metadata=metadata,
        episode_id=episode.id,
        turn_count=len(turns),
        is_duplicate=False,
    )
    return episode, len(turns), False, seq_to_id


async def ingest_preprocessed_file(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    file_path: Path,
) -> tuple[Episode, int, bool, dict[int, uuid.UUID]]:
    """Ingest one preprocessed session JSON from preprocessed/sessions/."""
    import json

    with open(file_path, encoding="utf-8") as fp:
        data = json.load(fp)

    raw_turns = data.get("turns", [])
    turns = []
    for i, t in enumerate(raw_turns):
        turns.append({
            "sequence": t.get("turn_id", i + 1),
            "role": t.get("role", "user"),
            "text": t.get("text", ""),
            "timestamp": t.get("timestamp", data.get("date_range", [None])[0]),
        })

    return await ingest_transcript(
        db, workspace_id, turns,
        provider=data.get("entrypoint", "preprocessed"),
        title=data.get("ai_title", "") or f"Session {data.get('session_id', '?')[:8]}",
        metadata={
            "source_file": str(file_path),
            "external_session_id": data.get("session_id"),
            "project": data.get("project"),
            "git_branch": data.get("git_branch"),
            "cwd": data.get("cwd"),
            "date_range": data.get("date_range"),
            "entrypoint": data.get("entrypoint"),
        },
    )


async def get_upload_status(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """Return upload progress summary — 'how far up are we?' meta query."""
    from sqlalchemy import text as sql_text

    result = await db.execute(
        sql_text("""
            SELECT
                COUNT(DISTINCT e.id) AS total_episodes,
                COALESCE(SUM(turn_counts.cnt), 0) AS total_turns,
                MIN(e.created_at) AS earliest,
                MAX(e.created_at) AS latest
            FROM episodes e
            LEFT JOIN (
                SELECT episode_id, COUNT(*) AS cnt
                FROM turns
                WHERE workspace_id = :ws
                GROUP BY episode_id
            ) turn_counts ON turn_counts.episode_id = e.id
            WHERE e.workspace_id = :ws
              AND (e.metadata->>'deleted' IS DISTINCT FROM 'true')
        """),
        {"ws": str(workspace_id)},
    )
    row = result.fetchone()

    # Distinct top-level subjects (entities with parent_id IS NULL that have at least one turn link)
    subj_result = await db.execute(
        sql_text("""
            SELECT COUNT(DISTINCT e.id)
            FROM entities e
            JOIN turn_subjects ts ON ts.subject_id = e.id
            WHERE e.workspace_id = :ws AND e.parent_id IS NULL
        """),
        {"ws": str(workspace_id)},
    )
    distinct_subjects = int(subj_result.scalar() or 0)

    return {
        "total_episodes": int(row[0] or 0),
        "total_turns": int(row[1] or 0),
        "earliest_episode_at": row[2],
        "latest_episode_at": row[3],
        "distinct_subjects": distinct_subjects,
    }
