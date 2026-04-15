"""LLM-free gap detection pipeline for the 보완 (complement) system.

Detects which parts of a conversation transcript were NOT covered by
store_memory calls, using a 4-stage progressive filter:

Stage 1: Mechanical coverage mapping (which turns were in store_memory calls?)
Stage 2: Keyword/entity density filtering (is the uncovered turn substantive?)
Stage 3: Semantic coverage verification (is the content already captured?)
Stage 4: Priority scoring (which gaps are most valuable to fill?)

No LLM calls. Runs in ~120-450ms per 50-turn conversation.
This is JARVIS's novel contribution — no existing system does explicit gap detection.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ConversationTurn:
    """A single turn from a conversation transcript."""

    turn_index: int
    role: str  # "user" or "assistant"
    text: str
    timestamp: str = ""
    token_estimate: int = 0


@dataclass
class GapCandidate:
    """A turn identified as a potential gap in extraction coverage."""

    turn: ConversationTurn
    novelty_score: float = 0.0  # 1.0 - max_similarity to extracted facts
    keyword_density: float = 0.0
    entity_count: int = 0
    priority: float = 0.0


@dataclass
class GapDetectionResult:
    """Result of the gap detection pipeline."""

    total_turns: int
    substantive_turns: int  # turns with enough content to matter
    covered_turns: int  # turns included in at least one store_memory call
    uncovered_turns: int
    gaps: list[GapCandidate]
    coverage_ratio: float
    recommendation: str  # "skip" | "gap_fill" | "full_extract"


def parse_transcript_turns(transcript: str) -> list[ConversationTurn]:
    """Parse a conversation transcript into individual turns.

    Handles common formats:
    - "User: ...\nAssistant: ..."
    - "사용자: ...\nAI: ..."
    """
    turns: list[ConversationTurn] = []
    current_role = ""
    current_text: list[str] = []
    turn_index = 0

    for line in transcript.split("\n"):
        line_stripped = line.strip()

        # Detect role switch
        role_match = re.match(r"^(User|Assistant|사용자|AI|Human|System)\s*:\s*(.*)", line_stripped, re.IGNORECASE)
        if role_match:
            # Flush previous turn
            if current_role and current_text:
                text = "\n".join(current_text).strip()
                if text:
                    turns.append(ConversationTurn(
                        turn_index=turn_index,
                        role="user" if current_role.lower() in ("user", "사용자", "human") else "assistant",
                        text=text,
                        token_estimate=_estimate_tokens(text),
                    ))
                    turn_index += 1

            current_role = role_match.group(1)
            current_text = [role_match.group(2)] if role_match.group(2) else []
        else:
            current_text.append(line)

    # Flush last turn
    if current_role and current_text:
        text = "\n".join(current_text).strip()
        if text:
            turns.append(ConversationTurn(
                turn_index=turn_index,
                role="user" if current_role.lower() in ("user", "사용자", "human") else "assistant",
                text=text,
                token_estimate=_estimate_tokens(text),
            ))

    return turns


def _estimate_tokens(text: str) -> int:
    """Rough token estimate."""
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii = len(text) - ascii_chars
    return (ascii_chars // 4) + (non_ascii // 2)


# --- Stage 1: Mechanical coverage ---


def stage1_coverage_mapping(
    all_turns: list[ConversationTurn],
    covered_turn_indices: set[int],
) -> list[ConversationTurn]:
    """Identify turns that were never included in any store_memory call.

    Only keeps user turns with ≥5 tokens (skip assistant meta-commentary and tiny acks).
    """
    uncovered: list[ConversationTurn] = []
    for turn in all_turns:
        if turn.turn_index in covered_turn_indices:
            continue
        if turn.role != "user":
            continue
        if turn.token_estimate < 5:
            continue
        uncovered.append(turn)
    return uncovered


# --- Stage 2: Keyword density ---


def stage2_keyword_filter(
    uncovered_turns: list[ConversationTurn],
    existing_keywords: set[str],
) -> list[GapCandidate]:
    """Filter uncovered turns by keyword/entity density.

    Turns with zero known keywords AND very short content are likely
    not worth extracting (greetings, "ok", "sounds good").
    """
    candidates: list[GapCandidate] = []

    for turn in uncovered_turns:
        words = set(turn.text.lower().split())
        # Count how many known keywords appear
        keyword_hits = len(words & existing_keywords)
        word_count = len(words)

        if word_count == 0:
            continue

        density = keyword_hits / word_count

        # Keep if: has new keywords (density=0 means novel content)
        # OR has enough substance (>15 words)
        if density == 0 and word_count < 15:
            continue

        candidates.append(GapCandidate(
            turn=turn,
            keyword_density=density,
        ))

    return candidates


# --- Stage 3: Semantic similarity ---


def stage3_semantic_filter(
    candidates: list[GapCandidate],
    fact_embeddings: list[list[float]],
    threshold: float = 0.60,
) -> list[GapCandidate]:
    """Filter candidates by semantic similarity to already-extracted facts.

    Turns with high similarity to existing facts are already covered.
    Turns below threshold are confirmed gaps.
    """
    if not fact_embeddings or not candidates:
        return candidates  # No embeddings = can't filter, keep all

    try:
        import numpy as np

        from jarvis.core.embedding import embed_text

        fact_matrix = np.array(fact_embeddings)

        confirmed: list[GapCandidate] = []
        for candidate in candidates:
            turn_vec = np.array(embed_text(candidate.turn.text))
            similarities = fact_matrix @ turn_vec
            max_sim = float(np.max(similarities)) if len(similarities) > 0 else 0.0

            candidate.novelty_score = 1.0 - max_sim

            if max_sim < threshold:
                confirmed.append(candidate)

        return confirmed
    except ImportError:
        logger.debug("Embedding not available, keeping all candidates")
        return candidates
    except Exception:
        logger.exception("Semantic filter failed")
        return candidates


# --- Stage 4: Priority scoring ---


def stage4_priority_scoring(candidates: list[GapCandidate]) -> list[GapCandidate]:
    """Score and rank gap candidates by extraction value."""
    for c in candidates:
        word_count = len(c.turn.text.split())
        c.priority = (
            0.35 * c.novelty_score
            + 0.30 * c.keyword_density
            + 0.20 * min(c.entity_count / 5.0, 1.0)
            + 0.15 * min(word_count / 50.0, 1.0)
        )

    candidates.sort(key=lambda c: c.priority, reverse=True)
    return candidates


# --- Main pipeline ---


def detect_gaps(
    transcript: str,
    covered_turn_indices: set[int],
    existing_keywords: set[str],
    fact_embeddings: list[list[float]] | None = None,
    semantic_threshold: float = 0.60,
) -> GapDetectionResult:
    """Run the full 4-stage gap detection pipeline.

    Args:
        transcript: Full conversation transcript text
        covered_turn_indices: Set of turn indices that were included in store_memory calls
        existing_keywords: Set of keywords from already-extracted facts
        fact_embeddings: Embeddings of already-extracted facts (for semantic filter)
        semantic_threshold: Similarity threshold for stage 3 (lower = more gaps detected)

    Returns:
        GapDetectionResult with ranked gap candidates and recommendation
    """
    # Parse turns
    all_turns = parse_transcript_turns(transcript)
    substantive = [t for t in all_turns if t.role == "user" and t.token_estimate >= 5]

    # Stage 1: Coverage mapping
    uncovered = stage1_coverage_mapping(all_turns, covered_turn_indices)

    # Stage 2: Keyword filter
    candidates = stage2_keyword_filter(uncovered, existing_keywords)

    # Stage 3: Semantic filter
    confirmed = stage3_semantic_filter(
        candidates,
        fact_embeddings or [],
        threshold=semantic_threshold,
    )

    # Stage 4: Priority scoring
    ranked = stage4_priority_scoring(confirmed)

    # Coverage stats
    covered_count = len(covered_turn_indices & {t.turn_index for t in substantive})
    coverage_ratio = covered_count / len(substantive) if substantive else 1.0

    # Recommendation based on coverage
    if len(all_turns) < 10 or coverage_ratio >= 0.95:
        recommendation = "skip"
    elif coverage_ratio < 0.50:
        recommendation = "full_extract"
    else:
        recommendation = "gap_fill"

    return GapDetectionResult(
        total_turns=len(all_turns),
        substantive_turns=len(substantive),
        covered_turns=covered_count,
        uncovered_turns=len(uncovered),
        gaps=ranked,
        coverage_ratio=coverage_ratio,
        recommendation=recommendation,
    )
