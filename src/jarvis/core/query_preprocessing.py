"""Query preprocessing for recall hybrid search.

Normalizes queries for embedding, builds FTS-specific OR query,
and expands Korean/English cross-lingual aliases.
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.entity_resolution import ALIAS_DICT, CROSS_LINGUAL_ALIASES

# Korean particles + English stopwords. Drop for FTS keyword extraction.
KOREAN_PARTICLES = {
    "의", "는", "이", "가", "을", "를", "에", "에서", "에게", "에서부터",
    "부터", "까지", "으로", "로", "와", "과", "도", "만", "뿐", "이나",
    "나", "든지", "라도", "마저", "조차", "뿐만", "처럼", "같이", "보다",
    "하고", "하고는",
}
ENGLISH_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with",
    "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "do", "does", "did", "will", "would",
    "what", "which", "who", "when", "where", "why", "how",
}


@dataclass
class PreprocessedQuery:
    original: str
    normalized: str          # NFKC + lowercase — embedding input
    fts_query: str           # PGroonga OR query — "JARVIS OR 구현"
    keywords: list[str]      # extracted tokens
    expanded_terms: list[str]  # alias-expanded terms
    anchor_entity_ids: list[uuid.UUID] = field(default_factory=list)


def _strip_particle(token: str) -> str:
    """Strip trailing Korean particle (simple heuristic)."""
    for p in sorted(KOREAN_PARTICLES, key=len, reverse=True):
        if token.endswith(p) and len(token) > len(p) + 1:
            return token[: -len(p)]
    return token


def extract_keywords(text: str) -> list[str]:
    """Split on whitespace + punctuation, drop particles/stopwords/short tokens.

    Public — reused by store.py to build Fragment.keywords.
    """
    # Replace punctuation with spaces (keep Korean + ASCII alnum)
    cleaned = re.sub(r"[^\w\s가-힣]", " ", text)
    tokens = cleaned.split()
    keywords: list[str] = []
    for tok in tokens:
        lower = tok.lower()
        if lower in ENGLISH_STOPWORDS:
            continue
        stripped = _strip_particle(tok)
        if len(stripped) < 2:
            continue
        if stripped.lower() in ENGLISH_STOPWORDS:
            continue
        keywords.append(stripped)
    return keywords


def _expand_aliases(keywords: list[str]) -> list[str]:
    """Expand each keyword with its alias if present (keep BOTH original + alias)."""
    expanded: list[str] = []
    for kw in keywords:
        expanded.append(kw)
        nfkc = unicodedata.normalize("NFKC", kw)
        if nfkc in CROSS_LINGUAL_ALIASES:
            expanded.append(CROSS_LINGUAL_ALIASES[nfkc])
        lower = nfkc.lower()
        if lower in ALIAS_DICT and ALIAS_DICT[lower] != lower:
            expanded.append(ALIAS_DICT[lower])
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for term in expanded:
        if term.lower() not in seen:
            seen.add(term.lower())
            result.append(term)
    return result


def preprocess_query(query: str) -> PreprocessedQuery:
    """Preprocess user query for 3-way hybrid search (sync, no anchor lookup)."""
    normalized = unicodedata.normalize("NFKC", query.strip()).lower()
    keywords = extract_keywords(query)
    expanded = _expand_aliases(keywords)
    # PGroonga OR query — any keyword matches (broader recall)
    fts_query = " OR ".join(expanded) if expanded else query
    return PreprocessedQuery(
        original=query,
        normalized=normalized,
        fts_query=fts_query,
        keywords=keywords,
        expanded_terms=expanded,
    )


async def preprocess_query_with_anchors(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    query: str,
) -> PreprocessedQuery:
    """Sync preprocess + async Aho-Corasick anchor extraction.

    Used by recall_memory. topic_map keeps the sync preprocess_query since its
    Stage 1 already scans the whole workspace.
    """
    from jarvis.core.anchor_matching import extract_anchor_entity_ids
    pq = preprocess_query(query)
    pq.anchor_entity_ids = await extract_anchor_entity_ids(db, workspace_id, query)
    return pq
