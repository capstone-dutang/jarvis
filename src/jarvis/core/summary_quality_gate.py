"""Episode summary quality gate.

Catches low-signal summaries that slipped through the ingest path:
  - empty / whitespace-only
  - too short (<50 chars) to convey what the conversation was about
  - jsonl agent identifiers ("jsonl-agent-ab")
  - bare integers ("18", "42") — telltale slicing bug

Episodes flagged here are marked `processing_status='needs_resummarize'`
so a later batch job (or the user) can regenerate a real summary without
losing the episode itself.
"""

from __future__ import annotations

import re

NEEDS_RESUMMARIZE = "needs_resummarize"

_JSONL_AGENT_RE = re.compile(r"^jsonl-agent-[a-z0-9]{2}\s*$")
_BARE_INT_RE = re.compile(r"^\d+\s*$")
_MIN_LEN = 50


def is_low_quality_summary(s: str | None) -> bool:
    """True if `s` should not be trusted as an episode summary.

    Trigger conditions (any one):
      - None / empty / whitespace-only
      - len(s.strip()) < 50
      - matches ^jsonl-agent-XX\\s*$  (e.g. "jsonl-agent-7c")
      - matches ^\\d+\\s*$            (e.g. "18", "42 ")
    """
    if s is None:
        return True
    stripped = s.strip()
    if not stripped:
        return True
    if len(stripped) < _MIN_LEN:
        return True
    if _JSONL_AGENT_RE.match(stripped):
        return True
    if _BARE_INT_RE.match(stripped):
        return True
    return False
