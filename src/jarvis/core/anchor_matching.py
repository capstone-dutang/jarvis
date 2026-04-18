"""Aho-Corasick anchor matching — query → entity IDs.

Workspace-scoped automaton, lazily built on first use and cached in memory.
Invalidated when entities are created or renamed (see store.py hooks).

Pattern source: entities.name UNION entity_aliases.alias. Both are lowercased
before building the automaton and queries are lowercased before matching.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from ahocorasick_rs import AhoCorasick, MatchKind
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# workspace_id → (automaton, patterns_aligned, entity_ids_aligned).
# patterns_aligned[i] and entity_ids_aligned[i] correspond to the i-th pattern
# fed to AhoCorasick, so find_matches_as_indexes pattern_index maps to entity_ids_aligned[i].
_CACHE: dict[uuid.UUID, tuple[AhoCorasick, list[str], list[uuid.UUID]]] = {}
_CACHE_LOCK = asyncio.Lock()

# Drop 1-character patterns: too noisy, collide with every query.
MIN_PATTERN_LEN = 2


async def _build(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Build automaton from entities.name + entity_aliases.alias."""
    result = await db.execute(
        text("""
            SELECT id AS entity_id, name AS pattern
            FROM entities
            WHERE workspace_id = :ws
            UNION ALL
            SELECT entity_id, alias AS pattern
            FROM entity_aliases
            WHERE workspace_id = :ws
        """),
        {"ws": str(workspace_id)},
    )
    rows = result.fetchall()

    patterns: list[str] = []
    entity_ids: list[uuid.UUID] = []
    for eid, pattern in rows:
        if pattern and len(pattern.strip()) >= MIN_PATTERN_LEN:
            patterns.append(pattern.lower())
            entity_ids.append(eid)

    if not patterns:
        _CACHE.pop(workspace_id, None)
        logger.info("Anchor automaton empty: workspace=%s (no patterns)", workspace_id)
        return

    # LeftmostLongest: overlapping matches collapse to the longest prefix
    # starting earliest. Avoids "자비스" + "자비" double-firing on Korean alias.
    aho = AhoCorasick(patterns, matchkind=MatchKind.LeftmostLongest)
    _CACHE[workspace_id] = (aho, patterns, entity_ids)
    logger.info(
        "Anchor automaton built: workspace=%s, patterns=%d",
        workspace_id, len(patterns),
    )


def invalidate(workspace_id: uuid.UUID) -> None:
    """Drop cached automaton. Next extract_anchor_entity_ids triggers rebuild."""
    _CACHE.pop(workspace_id, None)


async def extract_anchor_entity_ids(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    query: str,
) -> list[uuid.UUID]:
    """Match query against the automaton, return unique matched entity IDs in order."""
    async with _CACHE_LOCK:
        if workspace_id not in _CACHE:
            await _build(db, workspace_id)
        cached = _CACHE.get(workspace_id)
    if cached is None:
        return []
    aho, _, entity_ids = cached
    # ahocorasick_rs.find_matches_as_indexes → list[tuple[pattern_index, start, end]]
    matches = aho.find_matches_as_indexes(query.lower())
    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []
    for pattern_ix, _start, _end in matches:
        eid = entity_ids[pattern_ix]
        if eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out
