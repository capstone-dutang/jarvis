"""NLI-based contradiction detection for knowledge facts.

Uses cross-encoder/nli-deberta-v3-xsmall (22M params, ~28ms CPU per pair)
to detect semantic contradictions between knowledge facts.

Decision tree (from JARVIS_DEFINITIVE.md):
- entailment ≥0.70 + cosine ≥0.92 → duplicate
- entailment ≥0.70 + cosine 0.70-0.92 → refinement
- contradiction ≥0.85 → auto supersede
- contradiction 0.70-0.85 → review flag
- max class <0.70 → separate facts
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_nli_model = None


class ConflictType(enum.StrEnum):
    duplicate = "duplicate"
    refinement = "refinement"
    contradiction_auto = "contradiction_auto"
    contradiction_review = "contradiction_review"
    separate = "separate"


@dataclass
class NLIResult:
    """Result of NLI comparison between two facts."""

    entailment: float
    contradiction: float
    neutral: float
    conflict_type: ConflictType
    existing_fact_text: str


def _get_nli_model():  # type: ignore[no-untyped-def]
    """Lazy-load NLI model on first use."""
    global _nli_model  # noqa: PLW0603
    if _nli_model is None:
        try:
            from sentence_transformers import CrossEncoder

            _nli_model = CrossEncoder("cross-encoder/nli-deberta-v3-xsmall")
            logger.info("NLI model loaded: cross-encoder/nli-deberta-v3-xsmall")
        except Exception:
            logger.warning("Failed to load NLI model. Contradiction detection disabled.")
    return _nli_model


def classify_conflict(
    entailment: float,
    contradiction: float,
    neutral: float,
    cosine_similarity: float,
) -> ConflictType:
    """Apply NLI decision tree from JARVIS_DEFINITIVE.md.

    Args:
        entailment: NLI entailment softmax score
        contradiction: NLI contradiction softmax score
        neutral: NLI neutral softmax score
        cosine_similarity: Embedding cosine similarity between facts
    """
    # Entailment path: duplicate or refinement
    if entailment >= 0.70:
        if cosine_similarity >= 0.92:
            return ConflictType.duplicate
        if cosine_similarity >= 0.70:
            return ConflictType.refinement
        return ConflictType.separate

    # Contradiction path: auto-supersede or review
    if contradiction >= 0.85:
        return ConflictType.contradiction_auto
    if contradiction >= 0.70:
        return ConflictType.contradiction_review

    return ConflictType.separate


def detect_contradictions(
    new_fact_text: str,
    candidate_facts: list[tuple[str, float]],
) -> list[NLIResult]:
    """Detect contradictions between a new fact and existing candidate facts.

    Args:
        new_fact_text: Text representation of the new fact (e.g., "JARVIS uses PostgreSQL")
        candidate_facts: List of (fact_text, cosine_similarity) tuples for top-k similar existing facts

    Returns:
        List of NLIResult for each candidate that is NOT classified as 'separate'
    """
    model = _get_nli_model()
    if model is None or not candidate_facts:
        return []

    results: list[NLIResult] = []

    # Build pairs for batch prediction
    pairs = [(new_fact_text, fact_text) for fact_text, _ in candidate_facts]

    try:
        # CrossEncoder.predict returns logits: [contradiction, entailment, neutral]
        # for nli-deberta-v3-xsmall
        scores = model.predict(pairs, apply_softmax=True)

        for i, (fact_text, cosine_sim) in enumerate(candidate_facts):
            # nli-deberta-v3-xsmall label order: contradiction=0, entailment=1, neutral=2
            contradiction_score = float(scores[i][0])
            entailment_score = float(scores[i][1])
            neutral_score = float(scores[i][2])

            conflict_type = classify_conflict(
                entailment=entailment_score,
                contradiction=contradiction_score,
                neutral=neutral_score,
                cosine_similarity=cosine_sim,
            )

            if conflict_type != ConflictType.separate:
                results.append(
                    NLIResult(
                        entailment=entailment_score,
                        contradiction=contradiction_score,
                        neutral=neutral_score,
                        conflict_type=conflict_type,
                        existing_fact_text=fact_text,
                    )
                )

    except Exception:
        logger.exception("NLI prediction failed")

    return results
