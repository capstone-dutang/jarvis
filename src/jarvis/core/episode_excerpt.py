"""get_episode_excerpt: pull a query-relevant passage out of a single episode.

When recall_memory surfaces a fact with an episode_id but the AI client needs
the surrounding reasoning (why/decision/comparison/tradeoff), this tool
returns ~1-5 KB of the most relevant portion of that episode's raw transcript.

Complements search_passages:
- search_passages: pool-wide fragment semantic search (no episode scope)
- get_episode_excerpt: drill into ONE episode, return query-relevant span

Chunking is done at request time (no new table). Episode content is split on
paragraph boundaries, each chunk is scored by keyword hits + position, and the
top-scoring chunks are concatenated in original order up to max_chars.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.models.tables import Episode

logger = logging.getLogger(__name__)


@dataclass
class EpisodeExcerptResult:
    episode_id: uuid.UUID
    excerpt: str
    total_length: int
    mode: str
    passage_count: int
    matched_keywords: list[str]
    created_at: datetime
    summary: str | None


# Episode content has JSON-escaped newlines (`\n\n` as literal 2-char sequence)
# from the transcript upload path, plus real newlines for recent episodes.
# Split on either form.
_PARAGRAPH_SPLIT = re.compile(r"(?:\\n\\n|\n\n|\r\n\r\n)+")


def _split_paragraphs(content: str) -> list[str]:
    parts = _PARAGRAPH_SPLIT.split(content)
    return [p.strip() for p in parts if p.strip()]


def _score_paragraph(paragraph: str, keywords: list[str]) -> tuple[float, list[str]]:
    """Count keyword occurrences. Longer keywords weighted higher to prefer
    specific phrases over common tokens."""
    p_lower = paragraph.lower()
    score = 0.0
    matched: list[str] = []
    for kw in keywords:
        if not kw or len(kw) < 2:
            continue
        kw_lower = kw.lower()
        count = p_lower.count(kw_lower)
        if count > 0:
            score += count * max(1.0, len(kw_lower) / 3.0)
            matched.append(kw)
    return score, matched


async def get_episode_excerpt(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    episode_id: uuid.UUID,
    query: str,
    max_chars: int = 2000,
    mode: str = "relevant",
) -> EpisodeExcerptResult | None:
    """Return query-relevant span from one episode.

    mode:
      - "relevant": score paragraphs by keyword hits, concat top-K in original order
      - "full":     return content[:max_chars]
      - "head":     alias of full (future: may differ if we add summary-first)
    """
    result = await db.execute(
        select(Episode).where(
            Episode.id == episode_id,
            Episode.workspace_id == workspace_id,
        )
    )
    episode = result.scalar_one_or_none()
    if episode is None:
        return None

    content = episode.content
    total_length = len(content)

    # Short episode or non-relevant mode: return head
    if mode in ("full", "head") or total_length <= max_chars:
        excerpt = content[:max_chars]
        if total_length > max_chars and mode != "full":
            excerpt = excerpt + "\n\n[...truncated — use mode='relevant' to drill in...]"
        return EpisodeExcerptResult(
            episode_id=episode.id,
            excerpt=excerpt,
            total_length=total_length,
            mode=mode,
            passage_count=1,
            matched_keywords=[],
            created_at=episode.created_at,
            summary=episode.summary,
        )

    # mode="relevant": keyword scoring over paragraphs
    from jarvis.core.query_preprocessing import preprocess_query
    pq = preprocess_query(query)
    # Combine keywords + expanded terms + original query tokens (deduped, ≥2 chars)
    raw_keywords = list(pq.keywords) + list(pq.expanded_terms) + query.split()
    seen_kw: set[str] = set()
    keywords: list[str] = []
    for kw in raw_keywords:
        kw_stripped = kw.strip()
        k_lower = kw_stripped.lower()
        if len(kw_stripped) >= 2 and k_lower not in seen_kw:
            seen_kw.add(k_lower)
            keywords.append(kw_stripped)

    paragraphs = _split_paragraphs(content)
    if not paragraphs:
        # Shouldn't happen, but fall back to head
        return EpisodeExcerptResult(
            episode_id=episode.id,
            excerpt=content[:max_chars],
            total_length=total_length,
            mode="head",
            passage_count=1,
            matched_keywords=[],
            created_at=episode.created_at,
            summary=episode.summary,
        )

    scored: list[tuple[int, str, float, list[str]]] = []
    all_matched: set[str] = set()
    for i, p in enumerate(paragraphs):
        score, matched = _score_paragraph(p, keywords)
        scored.append((i, p, score, matched))
        all_matched.update(matched)

    matched_only = [s for s in scored if s[2] > 0]
    matched_only.sort(key=lambda x: x[2], reverse=True)

    # Greedily pick high-scoring paragraphs until max_chars (leave budget for separators)
    SEP = "\n\n[...]\n\n"
    budget = max_chars
    picked_indices: set[int] = set()
    for idx, para, score, _ in matched_only:
        cost = len(para) + len(SEP)
        if cost > budget:
            # Try truncating last paragraph if we haven't picked anything yet
            if not picked_indices and len(para) > 200:
                picked_indices.add(idx)
                break
            continue
        picked_indices.add(idx)
        budget -= cost

    if not picked_indices:
        # Nothing matched — fall back to head with a note
        return EpisodeExcerptResult(
            episode_id=episode.id,
            excerpt=content[:max_chars] + "\n\n[no keyword match — showing head]",
            total_length=total_length,
            mode="relevant_fallback_head",
            passage_count=0,
            matched_keywords=[],
            created_at=episode.created_at,
            summary=episode.summary,
        )

    # Reassemble in original order, separator between non-adjacent picks
    picked_sorted = sorted(
        [(idx, p) for idx, p, _, _ in scored if idx in picked_indices]
    )
    parts: list[str] = []
    last_idx = -2
    for idx, para in picked_sorted:
        if last_idx >= 0 and idx - last_idx > 1:
            parts.append("[...]")
        parts.append(para)
        last_idx = idx
    excerpt = "\n\n".join(parts)

    # Enforce max_chars hard cap (defensive, after budgeting)
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars - 20] + "\n\n[...truncated]"

    return EpisodeExcerptResult(
        episode_id=episode.id,
        excerpt=excerpt,
        total_length=total_length,
        mode="relevant",
        passage_count=len(picked_indices),
        matched_keywords=sorted(all_matched),
        created_at=episode.created_at,
        summary=episode.summary,
    )
