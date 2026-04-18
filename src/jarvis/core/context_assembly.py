"""Context assembly: MMR re-ranking with community awareness.

Replaces flat RRF list with "relevance × diversity" optimized subset.
Based on research: 2026-04-16-optimal-subset-selection.md ("80% solution").

Two responsibilities:
1. assemble_context() — online: MMR selection with adaptive K
2. recompute_communities() — offline: Leiden clustering on entity graph
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.models.tables import Embedding, Entity, EntityRelation
from jarvis.schemas import CoverageMetadata

logger = logging.getLogger(__name__)


# Section 8: half-life by fragment type (days).
# preference = 120, decision = 90, fact/error/relation = 60, procedure = 30.
HALF_LIFE_DAYS: dict[str, int] = {
    "preference": 120,
    "decision": 90,
    "fact": 60,
    "error": 60,
    "relation": 60,
    "procedure": 30,
}
DEFAULT_HALF_LIFE = 60


# ── Data classes ──


@dataclass
class FactCandidate:
    """Stage 1 hybrid search result + metadata for MMR."""

    fact_id: uuid.UUID
    entity_id: uuid.UUID
    entity_name: str
    predicate: str
    object_value: str
    rrf_score: float
    community_id: int | None = None
    embedding: list[float] | None = None  # fact text embedding for sim_2
    # Importance/decay metadata (Phase 3). Migration 1 returns defaults; Migration 2 returns real values.
    importance: float = 0.5
    fragment_type: str = "fact"
    last_accessed_at: datetime | None = None
    final_score: float = 0.0  # rrf_score × importance × decay (Phase 3)
    # Internal: normalized sim_1 used by MMR
    _sim_1: float = field(default=0.0, repr=False)


@dataclass
class AssemblyResult:
    """Output of assemble_context()."""

    selected: list[FactCandidate]
    coverage: CoverageMetadata
    structural_summary: str
    has_more: bool


# ── MMR assembly ──


async def _fetch_candidate_metadata(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    candidates: list[FactCandidate],
) -> None:
    """Batch-fetch community_id and fact embeddings for candidates.

    Mutates candidates in place.
    """
    if not candidates:
        return

    entity_ids = list({c.entity_id for c in candidates})
    fact_ids = list({c.fact_id for c in candidates})

    # Fetch entity.community_id
    entity_rows = await db.execute(
        select(Entity.id, Entity.community_id).where(Entity.id.in_(entity_ids))
    )
    entity_community = {row[0]: row[1] for row in entity_rows.fetchall()}

    # Fetch fact embeddings from Embedding table
    emb_rows = await db.execute(
        select(Embedding.source_id, Embedding.vector).where(
            Embedding.workspace_id == workspace_id,
            Embedding.source_type == "fact",
            Embedding.source_id.in_(fact_ids),
        )
    )
    fact_embedding = {row[0]: list(row[1]) if row[1] is not None else None for row in emb_rows.fetchall()}

    for c in candidates:
        c.community_id = entity_community.get(c.entity_id)
        c.embedding = fact_embedding.get(c.fact_id)


def _compute_final_scores(candidates: list[FactCandidate], now: datetime) -> None:
    """Compute final_score = rrf_score × importance × 2^(-days/half_life).

    Mutates each candidate's final_score. Called before MMR so diversity
    selection operates on decayed relevance, not raw RRF.
    """
    for c in candidates:
        if c.last_accessed_at is None:
            days = 0.0
        else:
            last = c.last_accessed_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            days = max(0.0, (now - last).total_seconds() / 86400.0)
        half_life = HALF_LIFE_DAYS.get(c.fragment_type, DEFAULT_HALF_LIFE)
        decay = 0.5 ** (days / half_life)
        c.final_score = c.rrf_score * c.importance * decay


def _normalize_sim_1(candidates: list[FactCandidate]) -> None:
    """Min-max normalize final_score → candidate._sim_1 in [0, 1].

    Falls back to rank-based when all scores are identical.
    """
    if not candidates:
        return
    min_s = min(c.final_score for c in candidates)
    max_s = max(c.final_score for c in candidates)
    range_s = max_s - min_s
    if range_s < 1e-9:
        # All identical — rank by position (Stage 1 already sorted desc)
        n = max(len(candidates), 1)
        for i, c in enumerate(candidates):
            c._sim_1 = 1.0 - (i / n)
    else:
        for c in candidates:
            c._sim_1 = (c.final_score - min_s) / range_s


def _build_summary(
    selected: list[FactCandidate],
    total_candidates: int,
    communities_represented: int,
    workspace_communities: int,
) -> str:
    """Build 2-3 sentence structural summary (template-based, no LLM)."""
    seen_entities: list[str] = []
    for c in selected:
        if c.entity_name and c.entity_name not in seen_entities:
            seen_entities.append(c.entity_name)
        if len(seen_entities) >= 3:
            break
    entity_sample = ", ".join(seen_entities)

    sentences: list[str] = [
        f"Selected {len(selected)} facts from {total_candidates} candidates.",
    ]
    if workspace_communities > 0:
        sentences.append(
            f"Coverage: {communities_represented} of {workspace_communities} communities represented."
        )
    if entity_sample:
        sentences.append(f"Primary entities: {entity_sample}.")
    return " ".join(sentences)


async def _count_workspace_communities(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    """Count distinct community_id values in workspace (excluding NULL)."""
    result = await db.execute(
        text("""
            SELECT COUNT(DISTINCT community_id) FROM entities
            WHERE workspace_id = :ws AND community_id IS NOT NULL
        """),
        {"ws": str(workspace_id)},
    )
    row = result.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def assemble_context(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    candidates: list[FactCandidate],
    limit: int,
    lambda_: float = 0.6,
    tau: float = 0.1,
    min_k: int = 3,
    max_k: int = 20,
    community_bonus: float = 0.05,
) -> AssemblyResult:
    """MMR + adaptive K + community awareness.

    See: docs/research/2026-04-16-optimal-subset-selection.md ("80% solution")

    Args:
        candidates: Stage 1 hybrid search results (all with rrf_score).
        limit: User-requested limit (capped at max_k).

    Returns:
        AssemblyResult with selected facts + coverage metadata + summary.
    """
    import numpy as np

    total_candidates = len(candidates)

    # Fetch community_id + embeddings for all candidates (batch)
    await _fetch_candidate_metadata(db, workspace_id, candidates)

    # Count workspace communities for coverage metadata
    workspace_communities = await _count_workspace_communities(db, workspace_id)

    # Edge case: drop candidates without embedding (can't compute sim_2)
    no_emb = [c for c in candidates if not c.embedding]
    if no_emb:
        logger.warning("Dropping %d candidates without embedding from MMR", len(no_emb))
    remaining = [c for c in candidates if c.embedding]

    if not remaining:
        return AssemblyResult(
            selected=[],
            coverage=CoverageMetadata(
                total_candidates=total_candidates,
                selected_count=0,
                communities_represented=0,
                workspace_communities=workspace_communities,
            ),
            structural_summary="No candidates with embeddings available.",
            has_more=False,
        )

    # Apply soft decay (Section 8): final_score = rrf × importance × 2^(-days/half_life)
    now = datetime.now(UTC)
    _compute_final_scores(remaining, now)
    if remaining:
        sample = remaining[0]
        logger.info(
            "Decay sample: rrf=%.4f × imp=%.2f × decay(%s) → final=%.4f",
            sample.rrf_score, sample.importance, sample.fragment_type, sample.final_score,
        )

    # Normalize sim_1 to [0, 1] using min-max on final_score
    _normalize_sim_1(remaining)

    selected: list[FactCandidate] = []
    selected_communities: set[int] = set()

    # First pick: highest sim_1 (pure relevance)
    first = max(remaining, key=lambda c: c._sim_1)
    selected.append(first)
    if first.community_id is not None:
        selected_communities.add(first.community_id)
    remaining.remove(first)

    # first_gain uses same scale as later marginal gains:
    # relevance term + community bonus, NO diversity penalty.
    first_gain = lambda_ * first._sim_1  # first pick has no bonus (nothing selected before)

    # Iterative greedy selection
    effective_cap = min(limit, max_k)
    while remaining and len(selected) < effective_cap:
        best: FactCandidate | None = None
        best_mmr_score = float("-inf")
        best_relevance_with_bonus = 0.0

        for c in remaining:
            # sim_2: max cosine similarity to any already-selected
            assert c.embedding is not None  # filtered above
            max_sim_2 = 0.0
            c_vec = np.asarray(c.embedding, dtype=np.float32)
            for s in selected:
                assert s.embedding is not None
                s_vec = np.asarray(s.embedding, dtype=np.float32)
                cos = float(np.dot(c_vec, s_vec))
                if cos > max_sim_2:
                    max_sim_2 = cos

            # Community bonus: +community_bonus for unrepresented communities
            bonus = community_bonus if (
                c.community_id is not None
                and c.community_id not in selected_communities
            ) else 0.0

            relevance_term = lambda_ * c._sim_1
            diversity_penalty = (1.0 - lambda_) * max_sim_2
            mmr_score = relevance_term - diversity_penalty + bonus

            if mmr_score > best_mmr_score:
                best_mmr_score = mmr_score
                best = c
                # relevance + bonus (no diversity penalty) — same scale as first_gain
                best_relevance_with_bonus = relevance_term + bonus

        if best is None:
            break

        # Adaptive K stopping: compare like-for-like (relevance+bonus, no penalty)
        if len(selected) >= min_k and best_relevance_with_bonus < tau * first_gain:
            break

        selected.append(best)
        if best.community_id is not None:
            selected_communities.add(best.community_id)
        remaining.remove(best)

    has_more = len(remaining) > 0 and len(selected) >= effective_cap

    communities_represented = len(selected_communities)
    coverage = CoverageMetadata(
        total_candidates=total_candidates,
        selected_count=len(selected),
        communities_represented=communities_represented,
        workspace_communities=workspace_communities,
    )
    summary = _build_summary(selected, total_candidates, communities_represented, workspace_communities)

    logger.info(
        "Context assembly: selected=%d from %d candidates, communities=%d/%d, has_more=%s",
        len(selected), total_candidates, communities_represented, workspace_communities, has_more,
    )

    return AssemblyResult(
        selected=selected,
        coverage=coverage,
        structural_summary=summary,
        has_more=has_more,
    )


# ── Leiden community detection (offline) ──


async def recompute_communities(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> int:
    """Run Leiden on workspace entity graph, update entities.community_id.

    No-op if entity count < 3 (not enough for meaningful communities).
    Returns count of communities found (0 if skipped).
    """
    try:
        import igraph as ig  # type: ignore[import-not-found]
        import leidenalg  # type: ignore[import-not-found]
    except ImportError:
        logger.exception("Leiden dependencies missing (python-igraph, leidenalg)")
        return 0

    # Fetch all entities for workspace
    entity_rows = await db.execute(
        select(Entity.id).where(Entity.workspace_id == workspace_id)
    )
    entity_ids = [row[0] for row in entity_rows.fetchall()]

    if len(entity_ids) < 3:
        logger.debug(
            "Skipping Leiden for workspace=%s — too few entities (%d)",
            workspace_id, len(entity_ids),
        )
        return 0

    # Build entity_id → graph vertex index
    entity_to_idx = {eid: i for i, eid in enumerate(entity_ids)}

    # Fetch relations (undirected for community detection)
    relation_rows = await db.execute(
        select(EntityRelation.from_entity_id, EntityRelation.to_entity_id).where(
            EntityRelation.workspace_id == workspace_id,
            EntityRelation.valid_to.is_(None),
        )
    )
    edges: list[tuple[int, int]] = []
    for from_id, to_id in relation_rows.fetchall():
        if from_id in entity_to_idx and to_id in entity_to_idx:
            edges.append((entity_to_idx[from_id], entity_to_idx[to_id]))

    # Build igraph
    graph = ig.Graph(n=len(entity_ids), edges=edges, directed=False)

    # Run Leiden with modularity (default, well-tested)
    partition = leidenalg.find_partition(graph, leidenalg.ModularityVertexPartition)

    # Assign community_id to each entity (batch UPDATE)
    community_ids = partition.membership  # list[int], one per vertex
    num_communities = len(set(community_ids))

    # Batch update via UPDATE ... CASE WHEN, or per-entity updates.
    # Per-entity is simpler and workspace has ≤ a few thousand entities.
    for eid, cid in zip(entity_ids, community_ids, strict=True):
        await db.execute(
            text("UPDATE entities SET community_id = :cid WHERE id = :eid"),
            {"cid": int(cid), "eid": str(eid)},
        )
    await db.commit()

    logger.info(
        "Leiden complete: workspace=%s, entities=%d, communities=%d, edges=%d",
        workspace_id, len(entity_ids), num_communities, len(edges),
    )
    return num_communities
