"""Entity resolution: 3-stage pipeline.

Based on: research/2026-03-31-multilingual-kg-postgresql-research.md lines 48-93

Stage 1: Normalize + alias lookup (<1ms)
Stage 2: Embedding candidate retrieval via pgvector (15-35ms)
Stage 3: Hybrid scoring — RapidFuzz + cosine (< 1ms)
"""

import unicodedata

from rapidfuzz import fuzz

# Alias dictionary for common variations
ALIAS_DICT: dict[str, str] = {
    "포스트그레스": "postgresql",
    "postgres": "postgresql",
    "포스트그레sql": "postgresql",
    "리액트": "react",
    "k8s": "kubernetes",
    "쿠버네티스": "kubernetes",
    "도커": "docker",
    "파이썬": "python",
    "자바스크립트": "javascript",
    "js": "javascript",
    "ts": "typescript",
    "타입스크립트": "typescript",
    "노드": "node.js",
    "node": "node.js",
    "깃": "git",
    "깃허브": "github",
    "aws": "amazon web services",
    "gcp": "google cloud platform",
    "oci": "oracle cloud infrastructure",
}


def normalize_name(name: str) -> str:
    """Stage 1: Unicode NFKC normalization + lowercase + alias lookup."""
    normalized = unicodedata.normalize("NFKC", name.strip()).lower()
    return ALIAS_DICT.get(normalized, normalized)


def is_cross_lingual(name_a: str, name_b: str) -> bool:
    """Check if two names are in different scripts (Korean vs Latin)."""

    def has_hangul(s: str) -> bool:
        return any("\uac00" <= c <= "\ud7a3" or "\u3131" <= c <= "\u3163" for c in s)

    def has_latin(s: str) -> bool:
        return any(c.isascii() and c.isalpha() for c in s)

    a_hangul, a_latin = has_hangul(name_a), has_latin(name_a)
    b_hangul, b_latin = has_hangul(name_b), has_latin(name_b)

    return (a_hangul and b_latin and not a_latin) or (b_hangul and a_latin and not b_latin)


def compute_fuzzy_ratio(name_a: str, name_b: str) -> float:
    """Compute max of 3 RapidFuzz strategies.

    Based on: research/multilingual-kg lines 80-84
    Uses max(ratio, token_sort_ratio, partial_ratio) for best match.
    """
    return max(
        fuzz.ratio(name_a, name_b),
        fuzz.token_sort_ratio(name_a, name_b),
        fuzz.partial_ratio(name_a, name_b),
    )


def hybrid_score(
    fuzzy_ratio: float,
    cosine_sim: float,
    cross_lingual: bool,
) -> float:
    """Stage 3: Weighted combination of string similarity and embedding cosine.

    Based on: research/multilingual-kg lines 85-86
    Cross-lingual: 5% string + 95% embedding
    Same language: 40% string + 60% embedding
    """
    fuzzy_norm = fuzzy_ratio / 100.0  # RapidFuzz returns 0-100

    if cross_lingual:
        return 0.05 * fuzzy_norm + 0.95 * cosine_sim
    return 0.40 * fuzzy_norm + 0.60 * cosine_sim
