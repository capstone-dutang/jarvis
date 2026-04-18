"""LLM-free gap detection pipeline for the 보완 (complement) system.

Detects which parts of a conversation transcript were NOT covered by
store_memory calls, using a 4-stage progressive filter:

Stage 1: 5-layer mechanical filter → pair-level extraction units
Stage 2: Keyword/entity density filtering
Stage 3: Semantic coverage verification
Stage 4: Priority scoring with signal boosters

No LLM calls. Based on:
- Research: 2026-04-17-assistant-turn-extraction-filter.md (5-layer filter)
- Graphiti's speaker-symmetric episode model (not Mem0's user-only)
- Pair-level extraction with 2-pair lookback context
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ── Data classes ──


@dataclass
class ConversationTurn:
    """A single turn from a conversation transcript."""

    turn_index: int
    role: str  # "user" or "assistant"
    text: str
    timestamp: str = ""
    token_estimate: int = 0


@dataclass
class ConversationPair:
    """User+assistant adjacent pair — primary extraction unit.

    Based on research: pair-level is the sweet spot for JARVIS.
    Two messages resolve ~95% of coreference cases.
    """

    user_turn: ConversationTurn
    assistant_turn: ConversationTurn
    pair_text: str  # cleaned combined text (extraction target)
    context_text: str = ""  # prior N pairs for disambiguation (not extracted from)
    signal_boost: float = 0.0  # Layer 3 boost score


@dataclass
class GapCandidate:
    """A pair identified as a potential gap in extraction coverage."""

    pair: ConversationPair
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


# ── Transcript parsing ──


def parse_transcript_turns(transcript: str) -> list[ConversationTurn]:
    """Parse a conversation transcript into individual turns.

    Handles common formats:
    - "User: ...\\nAssistant: ..."
    - "사용자: ...\\nAI: ..."
    """
    turns: list[ConversationTurn] = []
    current_role = ""
    current_text: list[str] = []
    turn_index = 0

    for line in transcript.split("\n"):
        line_stripped = line.strip()

        # Detect role switch
        role_match = re.match(
            r"^(User|Assistant|사용자|AI|Human|System)\s*:\s*(.*)",
            line_stripped,
            re.IGNORECASE,
        )
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


# ── Layer 1: Block-level filtering (adapted for preprocessed text) ──
# Research Layer 1 operates on raw JSONL content blocks. Preprocessed transcripts
# have text markers instead, so we filter on those markers.


def _strip_thinking_blocks(text: str) -> str:
    """Strip [thinking] blocks from preprocessed text.

    Handles both single-line and multi-line thinking blocks.
    Thinking blocks start with [thinking] at line beginning and continue
    as contiguous non-empty lines (including numbered lists, sub-paragraphs).
    After a blank line gap, content that looks like substantive prose
    (not a bracket marker or numbered continuation) is kept.
    """
    lines = text.split("\n")
    result: list[str] = []
    in_thinking = False
    saw_blank = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("[thinking]"):
            in_thinking = True
            saw_blank = False
            continue

        if in_thinking:
            if not stripped:
                saw_blank = True
                continue
            # After blank line, check if this looks like post-thinking content
            if saw_blank and stripped and not stripped.startswith(("[", "#")):
                # Numbered list inside thinking? Keep dropping.
                if re.match(r"^\d+\.", stripped):
                    continue
                # Looks like real content — exit thinking mode
                in_thinking = False
                result.append(line)
            else:
                # Still inside thinking block (or continuation without blank)
                saw_blank = False
                continue
        else:
            result.append(line)

    return "\n".join(result)


# Tool breadcrumbs — preprocessor already compressed. Strip for extraction.
TOOL_BREADCRUMB = re.compile(
    r"^\[(?:Read|Search|Grep|Glob|Bash|Edit|Write|Agent|Tool sequence|"
    r"CODE BLOCK|Output|Bash result)[^\]]*\].*$",
    re.MULTILINE,
)


def filter_assistant_blocks(text: str) -> str:
    """Layer 1: Strip thinking blocks and tool breadcrumbs from preprocessed text.

    Research basis: DROP_BLOCK_TYPES = {"thinking", "redacted_thinking", "tool_result"}
    Preprocessed text uses text markers instead of raw blocks.
    """
    text = _strip_thinking_blocks(text)
    text = TOOL_BREADCRUMB.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── Layer 2: Sentence-level scaffolding strip ──
# Research #1 Prompt A patterns — verbatim from research document.

# Rule 1: Sycophancy openers — the #1 Claude tic. Strip entirely.
SYCOPHANCY = re.compile(
    r"^\s*(you['\u2019]re\s+(absolutely\s+)?(right|correct)[!.]?|"
    r"(great|excellent|perfect|good)\s+(question|point|catch|idea)[!.]?|"
    r"(perfect|great|excellent|absolutely|exactly|wonderful|amazing|brilliant)[!.]?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Rule 2: Action-announcing preambles. Strip the whole sentence.
PREAMBLE = re.compile(
    r"^\s*(now\s+)?(first[,]?\s+|next[,]?\s+|then[,]?\s+)?"
    r"(i['\u2019]ll|i\s+will|i['\u2019]m\s+going\s+to|let\s+me|let['\u2019]s)"
    r"\s+(now\s+)?"
    r"(check|look|search|examine|analyze|start|begin|try|use|run|create|implement|"
    r"fix|update|add|write|read|find|explore|investigate|understand|think|verify|"
    r"test|help\s+you|walk\s+you|take\s+a\s+look|do\s+this|approach|get|see)"
    r"\b[^.\n]*[.\n]?",
    re.IGNORECASE | re.MULTILINE,
)

# Rule 3: Short filler acknowledgments.
FILLER_ACK = re.compile(
    r"^\s*(i\s+see[.!]?|i\s+understand[.!]?|got\s+it[.!]?|makes\s+sense[.!]?|"
    r"sure[,!.]?( i\s+can\s+help( with\s+that)?[.!]?)?|of\s+course[.!]?|"
    r"sounds\s+good[.!]?|interesting[.!]?|noted[.!]?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Rule 4: Hedge bridges that lead into substance — strip only the bridge.
HEDGE_BRIDGE = re.compile(
    r"^\s*(looking\s+at\s+(this|the)[^,.\n]*[,.]?\s+|"
    r"based\s+on\s+(this|the|what|my)[^,.\n]*[,.]?\s+|"
    r"from\s+what\s+i\s+can\s+see[,.]?\s+)",
    re.IGNORECASE | re.MULTILINE,
)

# Rule 5: Meta/process commentary — strip.
META_TODO = re.compile(
    r"^\s*(i['\u2019]ll\s+use\s+the\s+todowrite[^.\n]*[.]?|"
    r"let\s+me\s+update\s+the\s+todo[^.\n]*[.]?|"
    r"let\s+me\s+think\s+(about\s+this\s+)?step\s+by\s+step[.]?|"
    r"let\s+me\s+(try\s+)?(a\s+different|another)\s+approach[.]?)\s*",
    re.IGNORECASE | re.MULTILINE,
)


def strip_scaffolding(text: str) -> str:
    """Layer 2: Remove Claude's scaffolding phrases from text blocks."""
    for pattern in (SYCOPHANCY, FILLER_ACK, META_TODO, PREAMBLE, HEDGE_BRIDGE):
        text = pattern.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Layer 3: Signal boosters (priority scoring, not filtering) ──

# Decision/rationale markers — facts the assistant committed to
DECISION_MARKERS = re.compile(
    r"\b(because|since|the reason|the issue is|the problem is|the bug is|"
    r"the fix is|the root cause|turns out|it turns out|this is why|"
    r"instead of|rather than|we should|we need to|the correct|"
    r"actually,?|in fact,?|specifically,?)\b",
    re.IGNORECASE,
)

# Discovery markers — the assistant found something
DISCOVERY_MARKERS = re.compile(
    r"\b(found|discovered|noticed|realized|identified|confirmed|"
    r"turned out|the test (failed|passed) because|this file|this function|"
    r"contains|defines|implements|references|depends on|uses)\b",
    re.IGNORECASE,
)

# Negative/correction markers — contradicting prior state
CORRECTION_MARKERS = re.compile(
    r"\b(doesn['\u2019]t|does not|won['\u2019]t|will not|can['\u2019]t|cannot|"
    r"didn['\u2019]t|failed|broken|incorrect|wrong|missing|"
    r"instead|however|but\s+actually|on second thought)\b",
    re.IGNORECASE,
)

SIGNAL_BOOST_WEIGHT = 2.0  # multiplier in priority scorer


def compute_signal_boost(text: str) -> float:
    """Count how many signal categories match."""
    boost = 0.0
    if DECISION_MARKERS.search(text):
        boost += 1.0
    if DISCOVERY_MARKERS.search(text):
        boost += 1.0
    if CORRECTION_MARKERS.search(text):
        boost += 1.0
    return boost


# ── Layer 4 & 5: Pair assembly + decision tree ──

MIN_CLEANED_ASSISTANT_CHARS = 40  # drop empty/scaffolding-only assistant turns
MIN_PAIR_CHARS = 60  # user + assistant combined floor


def _empty_turn(turn_index: int = -1) -> ConversationTurn:
    """Create an empty placeholder turn."""
    return ConversationTurn(turn_index=turn_index, role="assistant", text="", token_estimate=0)


def assemble_pairs(
    turns: list[ConversationTurn],
    covered_indices: set[int],
    context_window: int = 2,
) -> list[ConversationPair]:
    """Layer 4+5: Assemble user+assistant pairs with 5-layer filtering.

    Iterates turns to find adjacent user→assistant sequences.
    Applies Layer 1 (block filter) and Layer 2 (scaffolding strip) to assistant text.
    Applies length gates (Layer 5 steps 6-7).
    Computes signal boost (Layer 3).

    Orphan handling:
    - User without assistant response: create pair with empty assistant
    - Assistant without preceding user: drop (no context)
    - Consecutive assistant turns: merge into single pair with preceding user
    """
    pairs: list[ConversationPair] = []

    i = 0
    while i < len(turns):
        turn = turns[i]

        # Skip covered turns
        if turn.turn_index in covered_indices:
            i += 1
            continue

        # We need a user turn to start a pair
        if turn.role != "user":
            i += 1
            continue

        user_turn = turn

        # Collect consecutive assistant turns after this user turn
        assistant_parts: list[str] = []
        j = i + 1
        while j < len(turns) and turns[j].role == "assistant":
            if turns[j].turn_index not in covered_indices:
                assistant_parts.append(turns[j].text)
            j += 1

        # Create assistant turn (merged or empty)
        if assistant_parts:
            raw_assistant_text = "\n\n".join(assistant_parts)
            assistant_turn = ConversationTurn(
                turn_index=turns[i + 1].turn_index,
                role="assistant",
                text=raw_assistant_text,
                token_estimate=_estimate_tokens(raw_assistant_text),
            )
        else:
            # User-only pair (no assistant response)
            assistant_turn = _empty_turn(user_turn.turn_index)
            raw_assistant_text = ""

        # Layer 1: Block-level filtering on assistant text
        cleaned_assistant = filter_assistant_blocks(raw_assistant_text) if raw_assistant_text else ""

        # Layer 2: Scaffolding strip on assistant text
        cleaned_assistant = strip_scaffolding(cleaned_assistant) if cleaned_assistant else ""

        # Layer 5, step 6: MIN_CLEANED_ASSISTANT_CHARS gate
        # (user-only pairs bypass this — user text has intrinsic value)
        if raw_assistant_text and len(cleaned_assistant) < MIN_CLEANED_ASSISTANT_CHARS:
            i = j
            continue

        # Build pair text
        pair_text = f"User: {user_turn.text}"
        if cleaned_assistant:
            pair_text += f"\n\nAssistant: {cleaned_assistant}"

        # Layer 5, step 7: MIN_PAIR_CHARS gate
        if len(pair_text) < MIN_PAIR_CHARS:
            i = j
            continue

        # Layer 3: Signal boost (on cleaned assistant text)
        boost = compute_signal_boost(cleaned_assistant) if cleaned_assistant else 0.0

        # Build context from previous pairs (Layer 4 context_window)
        context_parts = [p.pair_text for p in pairs[-context_window:]]
        context_text = "\n---\n".join(context_parts) if context_parts else ""

        pairs.append(ConversationPair(
            user_turn=user_turn,
            assistant_turn=assistant_turn,
            pair_text=pair_text,
            context_text=context_text,
            signal_boost=boost,
        ))

        i = j

    return pairs


# ── Stage 1: 5-layer mechanical filter ──


def stage1_coverage_mapping(
    all_turns: list[ConversationTurn],
    covered_turn_indices: set[int],
) -> list[ConversationPair]:
    """Stage 1: 5-layer mechanical filter → pair-level extraction units.

    Replaces the old user-only filter with Graphiti-style speaker-symmetric
    pair assembly. Research basis: 2026-04-17-assistant-turn-extraction-filter.md
    """
    return assemble_pairs(all_turns, covered_turn_indices)


# ── Stage 2: Keyword density ──


def stage2_keyword_filter(
    uncovered_pairs: list[ConversationPair],
    existing_keywords: set[str],
) -> list[GapCandidate]:
    """Filter pairs by keyword/entity density.

    Pairs with zero known keywords AND very short content are likely
    not worth extracting (greetings, "ok", "sounds good").
    """
    candidates: list[GapCandidate] = []

    for pair in uncovered_pairs:
        words = set(pair.pair_text.lower().split())
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
            pair=pair,
            keyword_density=density,
        ))

    return candidates


# ── Stage 3: Semantic similarity ──


def stage3_semantic_filter(
    candidates: list[GapCandidate],
    fact_embeddings: list[list[float]],
    threshold: float = 0.60,
) -> list[GapCandidate]:
    """Filter candidates by semantic similarity to already-extracted facts.

    Pairs with high similarity to existing facts are already covered.
    Pairs below threshold are confirmed gaps.
    """
    if not fact_embeddings or not candidates:
        return candidates  # No embeddings = can't filter, keep all

    try:
        import numpy as np

        from jarvis.core.embedding import embed_text

        fact_matrix = np.array(fact_embeddings)

        confirmed: list[GapCandidate] = []
        for candidate in candidates:
            turn_vec = np.array(embed_text(candidate.pair.pair_text[:500]))
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


# ── Stage 4: Priority scoring ──


def stage4_priority_scoring(candidates: list[GapCandidate]) -> list[GapCandidate]:
    """Score and rank gap candidates by extraction value.

    Incorporates Layer 3 signal boosters via SIGNAL_BOOST_WEIGHT multiplier.
    """
    for c in candidates:
        word_count = len(c.pair.pair_text.split())
        base_priority = (
            0.30 * c.novelty_score
            + 0.25 * c.keyword_density
            + 0.15 * min(c.entity_count / 5.0, 1.0)
            + 0.10 * min(word_count / 50.0, 1.0)
            + 0.20 * min(c.pair.signal_boost / 3.0, 1.0)
        )
        # Apply signal boost as multiplier (research: SIGNAL_BOOST_WEIGHT = 2.0)
        if c.pair.signal_boost > 0:
            c.priority = base_priority * (1.0 + c.pair.signal_boost * (SIGNAL_BOOST_WEIGHT - 1.0) / 3.0)
        else:
            c.priority = base_priority

    candidates.sort(key=lambda c: c.priority, reverse=True)
    return candidates


# ── Main pipeline ──


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
    # Count all turns with enough content (both user and assistant)
    substantive = [t for t in all_turns if t.token_estimate >= 5]

    # Stage 1: 5-layer filter → pairs
    uncovered_pairs = stage1_coverage_mapping(all_turns, covered_turn_indices)

    # Stage 2: Keyword filter
    candidates = stage2_keyword_filter(uncovered_pairs, existing_keywords)

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
        uncovered_turns=len(uncovered_pairs),
        gaps=ranked,
        coverage_ratio=coverage_ratio,
        recommendation=recommendation,
    )
