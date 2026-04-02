"""Source quote verification against episode transcript.

Verifies that AI-extracted entities and facts are grounded in the actual
conversation, without requiring a server-side LLM. Uses substring match
with fuzzy fallback.
"""

from rapidfuzz import fuzz


def verify_quote(source_quote: str, transcript: str, threshold: float = 75.0) -> bool:
    """Check if source_quote is grounded in the transcript.

    Strategy:
    1. Exact substring match (fast path)
    2. Fuzzy partial ratio against sliding window (fallback)

    Returns True if the quote is found or closely matches part of the transcript.
    """
    if not source_quote or not transcript:
        return False

    quote_clean = source_quote.strip().lower()
    transcript_clean = transcript.strip().lower()

    # Fast path: exact substring
    if quote_clean in transcript_clean:
        return True

    # Fuzzy fallback: partial_ratio checks best substring match
    ratio = fuzz.partial_ratio(quote_clean, transcript_clean)
    return ratio >= threshold
