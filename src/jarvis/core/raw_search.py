"""Raw FTS search over episodes.content / episodes.summary / fragments.content.

Phase 1 of plan sequential-munching-dove.md (A 결함 해소).

PGroonga indexes created by migrations a1b2c3d4e5f6 (idx_episodes_pgroonga_content)
and b2c3d4e5f6a7 (idx_fragments_pgroonga_content) were unused by
hybrid_graph_search and the recall fallback path — raw bodies were effectively
not searchable. This module exposes the PGroonga `&@~` matcher as a direct
escape hatch so any keyword that lives in raw transcript text can still be
recalled, even when AI extraction missed it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class RawEpisodeMatch:
    episode_id: uuid.UUID
    summary: str
    snippet: str
    score: float
    created_at: datetime
    matched_field: str  # "content" | "summary"
    # First-turn timestamp = the episode's actual WORK date. created_at is the
    # row's ingestion time, which for migrated/diary episodes differs from when
    # the work happened — UI must jump to day_ts, not created_at.
    day_ts: datetime | None = None
    # Snippet built off episodes.cleaned_content when that column exists and is
    # populated. NULL otherwise — UI / AI falls back to ``snippet``.
    cleaned_snippet: str | None = None


@dataclass
class RawFragmentMatch:
    fragment_id: uuid.UUID
    content: str
    score: float
    episode_id: uuid.UUID
    fact_id: uuid.UUID | None
    created_at: datetime
    # NULL until fragments.cleaned_content is populated by R1.
    cleaned_content: str | None = None


_SNIPPET_LEN = 400


async def search_episode_content(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    fts_query: str,
    limit: int = 10,
) -> list[RawEpisodeMatch]:
    """PGroonga `&@~` search over episodes.content + episodes.summary.

    Uses idx_episodes_pgroonga_content (migration a1b2c3d4e5f6). Returns the
    matched episodes ranked by pgroonga_score. The `snippet` is a short window
    around the first match — caller uses it to decide whether to drill in via
    get_episode_excerpt.
    """
    if not fts_query or not fts_query.strip():
        return []

    # soft-delete filter (4-04): episodes with metadata.deleted='true' are hidden
    # from search. Real row stays for forensic reference but never surfaces.
    # to_jsonb(ep)->>'cleaned_content' returns NULL when the column hasn't been
    # added yet (R1's migration), so this works pre- and post-migration.
    rows = await db.execute(
        text("""
            (
                SELECT id, summary, content, created_at,
                       pgroonga_score(tableoid, ctid) AS score,
                       'content' AS matched_field,
                       to_jsonb(ep) ->> 'cleaned_content' AS cleaned_content,
                       (SELECT MIN(timestamp) FROM turns t WHERE t.episode_id = ep.id) AS first_ts
                FROM episodes ep
                WHERE workspace_id = :ws
                  AND content &@~ :q
                  AND (metadata->>'deleted' IS DISTINCT FROM 'true')
                ORDER BY pgroonga_score(tableoid, ctid) DESC
                LIMIT :lim
            )
            UNION ALL
            (
                SELECT id, summary, content, created_at,
                       pgroonga_score(tableoid, ctid) AS score,
                       'summary' AS matched_field,
                       to_jsonb(ep) ->> 'cleaned_content' AS cleaned_content,
                       (SELECT MIN(timestamp) FROM turns t WHERE t.episode_id = ep.id) AS first_ts
                FROM episodes ep
                WHERE workspace_id = :ws
                  AND summary &@~ :q
                  AND (metadata->>'deleted' IS DISTINCT FROM 'true')
                ORDER BY pgroonga_score(tableoid, ctid) DESC
                LIMIT :lim
            )
        """),
        {"ws": str(workspace_id), "q": fts_query, "lim": limit},
    )
    # An episode may match both content and summary; keep the higher-scoring row.
    seen: dict[uuid.UUID, RawEpisodeMatch] = {}
    for r in rows.fetchall():
        ep_id = r[0]
        summary = r[1] or ""
        content = r[2] or ""
        created_at = r[3]
        score = float(r[4]) if r[4] is not None else 0.0
        matched_field = r[5]
        cleaned_content_full = r[6]
        first_ts = r[7]
        body_for_snippet = content if matched_field == "content" else summary
        snippet = _make_snippet(body_for_snippet, fts_query)
        # Build cleaned snippet off the same window logic when cleaned body
        # exists. Only meaningful for content-matched rows; for summary-matched
        # we'd need a separate cleaned_summary column (not in scope here).
        cleaned_snippet: str | None = None
        if cleaned_content_full and matched_field == "content":
            cleaned_snippet = _make_snippet(cleaned_content_full, fts_query)
        prior = seen.get(ep_id)
        if prior is None or score > prior.score:
            seen[ep_id] = RawEpisodeMatch(
                episode_id=ep_id,
                summary=summary[:300],
                snippet=snippet,
                cleaned_snippet=cleaned_snippet,
                score=score,
                created_at=created_at,
                matched_field=matched_field,
                day_ts=first_ts,
            )

    return sorted(seen.values(), key=lambda m: -m.score)[:limit]


async def search_fragment_content(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    fts_query: str,
    limit: int = 10,
) -> list[RawFragmentMatch]:
    """PGroonga `&@~` search over fragments.content.

    Uses idx_fragments_pgroonga_content (migration b2c3d4e5f6a7). Complements
    search_passages (vector-only) by catching keyword matches the embedding
    couldn't surface.
    """
    if not fts_query or not fts_query.strip():
        return []

    rows = await db.execute(
        text("""
            SELECT id, content, source_episode_id, source_fact_id, created_at,
                   pgroonga_score(tableoid, ctid) AS score,
                   to_jsonb(f) ->> 'cleaned_content' AS cleaned_content
            FROM fragments f
            WHERE workspace_id = :ws
              AND content &@~ :q
            ORDER BY pgroonga_score(tableoid, ctid) DESC
            LIMIT :lim
        """),
        {"ws": str(workspace_id), "q": fts_query, "lim": limit},
    )
    return [
        RawFragmentMatch(
            fragment_id=r[0],
            content=r[1],
            episode_id=r[2],
            fact_id=r[3],
            created_at=r[4],
            score=float(r[5]) if r[5] is not None else 0.0,
            cleaned_content=r[6],
        )
        for r in rows.fetchall()
    ]


def _make_snippet(body: str, fts_query: str) -> str:
    """Return ~_SNIPPET_LEN chars of `body` around the first matching token.

    PGroonga OR-form is `term1 OR term2 OR ...` (built by preprocess_query).
    Plain substring scan against split terms is enough for a preview snippet —
    not perfect (no morphological match) but cheap and deterministic.
    """
    if not body:
        return ""
    if len(body) <= _SNIPPET_LEN:
        return body
    candidates = [t.strip() for t in fts_query.replace(" OR ", "|").split("|") if t.strip()]
    body_lower = body.lower()
    best_idx = -1
    for term in candidates:
        idx = body_lower.find(term.lower())
        if idx >= 0 and (best_idx < 0 or idx < best_idx):
            best_idx = idx
    if best_idx < 0:
        return body[:_SNIPPET_LEN]
    start = max(0, best_idx - _SNIPPET_LEN // 4)
    end = min(len(body), start + _SNIPPET_LEN)
    prefix = "" if start == 0 else "…"
    suffix = "" if end == len(body) else "…"
    return f"{prefix}{body[start:end]}{suffix}"
