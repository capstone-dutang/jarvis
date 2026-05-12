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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.models.tables import Episode, Session, Turn

logger = logging.getLogger(__name__)


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


async def ingest_transcript(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    turns: list[dict],
    session_id: uuid.UUID | None = None,
    provider: str = "unknown",
    title: str = "",
    metadata: dict[str, Any] | None = None,
) -> tuple[Episode, int, bool]:
    """Ingest one transcript as Episode + Turn rows.

    `turns` is a list of dicts: {sequence, role, text, timestamp}.
    Returns (episode, turn_count, is_duplicate).
    is_duplicate=True if content_hash matched existing → no new rows created.
    """
    if not turns:
        raise ValueError("turns must be non-empty")

    content = _build_episode_content(turns)
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
        return dup, len(turns), True

    sess = await get_or_create_session(db, workspace_id, session_id, provider)

    episode = Episode(
        session_id=sess.id,
        workspace_id=workspace_id,
        content=content,
        content_hash=content_hash,
        summary=title or content[:300],
        metadata_=metadata or {},
    )
    db.add(episode)
    await db.flush()

    for t in turns:
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

    await db.flush()
    logger.info(
        "Ingested episode %s with %d turns (workspace=%s)",
        episode.id, len(turns), workspace_id,
    )
    return episode, len(turns), False


async def ingest_preprocessed_file(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    file_path: Path,
) -> tuple[Episode, int, bool]:
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
