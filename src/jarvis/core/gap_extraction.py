"""Server-side gap extraction using Claude API.

When gap_detection.py identifies uncovered turns, this module extracts
knowledge from those gaps using the Anthropic API.

Two-step process (from research #4):
1. Haiku extracts facts from gap turns (blind, no existing facts context)
2. Sonnet reconciles against existing facts (ADD/UPDATE/NOOP)

Cost: ~$0.01-0.02 per session via Batch API.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ExtractedFact:
    """A fact extracted from gap turns."""

    subject: str
    predicate: str
    object_value: str
    source_quote: str
    confidence: str  # "high" | "medium" | "low"


@dataclass
class ReconciliationAction:
    """Action to take for an extracted fact after reconciliation."""

    fact: ExtractedFact
    action: str  # "ADD" | "UPDATE" | "NOOP"
    target_fact_id: str | None  # ID of existing fact to update (if UPDATE)
    reasoning: str


EXTRACTION_PROMPT = """You are a knowledge extraction system. Extract all factual knowledge from the conversation segment below.

Focus on:
1. Personal preferences (likes, dislikes, choices made)
2. Decisions and their reasoning ("chose X because Y")
3. Negative knowledge ("decided NOT to use X", "rejected Y")
4. Technical details (tools, configurations, architectures)
5. Temporal facts (deadlines, schedules, durations)
6. Relationships between entities
7. Implicit preferences revealed through behavior
8. Constraints and requirements mentioned in passing

<CONVERSATION>
{context_before}
--- GAP START ---
{gap_turns}
--- GAP END ---
{context_after}
</CONVERSATION>

Extract facts as atomic statements. Each fact should be self-contained (no pronouns — resolve all references using context).
Include a source_quote that is an EXACT verbatim substring from the GAP section.

Return JSON: {{"facts": [{{"subject": "entity name", "predicate": "snake_case_verb", "object": "value", "source_quote": "exact quote from gap", "confidence": "high|medium|low"}}]}}
Only return JSON, no explanation."""

RECONCILIATION_PROMPT = """You are a memory deduplication system. Compare newly extracted facts against existing memories and decide what action to take for each.

<EXISTING_MEMORIES>
{existing_facts}
</EXISTING_MEMORIES>

<NEW_FACTS>
{new_facts}
</NEW_FACTS>

For each new fact, return exactly one action:
- ADD: genuinely new information absent from existing memories
- UPDATE: corrects, refines, or supersedes an existing memory (specify which memory ID)
- NOOP: already captured in existing memories (specify which memory ID)

Err on the side of ADD for ambiguous cases.

Return JSON: {{"actions": [{{"fact_index": 0, "action": "ADD|UPDATE|NOOP", "target_memory_id": null|"id", "reasoning": "brief explanation"}}]}}
Only return JSON, no explanation."""


def _get_client():  # type: ignore[no-untyped-def]
    """Get Anthropic client. Returns None if API key not set."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set. Gap extraction disabled.")
        return None
    try:
        import anthropic

        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        logger.warning("anthropic package not installed. Gap extraction disabled.")
        return None


def extract_from_gaps(
    gap_turns: list[str],
    context_before: list[str] | None = None,
    context_after: list[str] | None = None,
    model: str = "claude-haiku-4-5-20251001",
) -> list[ExtractedFact]:
    """Extract facts from gap turns using Claude API.

    Args:
        gap_turns: List of turn texts from the gap
        context_before: 3-5 turns before the gap (for reference resolution)
        context_after: 2-3 turns after the gap
        model: Claude model to use (Haiku for cost efficiency)

    Returns:
        List of extracted facts
    """
    client = _get_client()
    if client is None:
        return []

    prompt = EXTRACTION_PROMPT.format(
        context_before="\n".join(context_before or []),
        gap_turns="\n".join(gap_turns),
        context_after="\n".join(context_after or []),
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        data = json.loads(text)

        facts: list[ExtractedFact] = []
        for f in data.get("facts", []):
            facts.append(
                ExtractedFact(
                    subject=f.get("subject", ""),
                    predicate=f.get("predicate", ""),
                    object_value=f.get("object", ""),
                    source_quote=f.get("source_quote", ""),
                    confidence=f.get("confidence", "medium"),
                )
            )
        return facts

    except json.JSONDecodeError:
        logger.warning("Gap extraction returned non-JSON response")
        return []
    except Exception:
        logger.exception("Gap extraction failed")
        return []


def reconcile_facts(
    new_facts: list[ExtractedFact],
    existing_facts: list[dict[str, str]],
    model: str = "claude-sonnet-4-20250514",
) -> list[ReconciliationAction]:
    """Reconcile extracted facts against existing memories.

    Args:
        new_facts: Facts extracted from gap turns
        existing_facts: Existing facts as dicts with 'id', 'text' keys
        model: Claude model (Sonnet for better reasoning)

    Returns:
        List of reconciliation actions (ADD/UPDATE/NOOP)
    """
    if not new_facts:
        return []

    client = _get_client()
    if client is None:
        # Without API, default to ADD everything
        return [
            ReconciliationAction(
                fact=f,
                action="ADD",
                target_fact_id=None,
                reasoning="No API available, defaulting to ADD",
            )
            for f in new_facts
        ]

    existing_text = "\n".join(
        f"[{ef['id']}] {ef['text']}" for ef in existing_facts
    )
    new_text = "\n".join(
        f"[{i}] {f.subject} {f.predicate} {f.object_value}" for i, f in enumerate(new_facts)
    )

    prompt = RECONCILIATION_PROMPT.format(
        existing_facts=existing_text,
        new_facts=new_text,
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        data = json.loads(text)

        actions: list[ReconciliationAction] = []
        for a in data.get("actions", []):
            fact_idx = a.get("fact_index", 0)
            if 0 <= fact_idx < len(new_facts):
                actions.append(
                    ReconciliationAction(
                        fact=new_facts[fact_idx],
                        action=a.get("action", "ADD"),
                        target_fact_id=a.get("target_memory_id"),
                        reasoning=a.get("reasoning", ""),
                    )
                )
        return actions

    except json.JSONDecodeError:
        logger.warning("Reconciliation returned non-JSON response")
        return [
            ReconciliationAction(fact=f, action="ADD", target_fact_id=None, reasoning="Parse error fallback")
            for f in new_facts
        ]
    except Exception:
        logger.exception("Reconciliation failed")
        return [
            ReconciliationAction(fact=f, action="ADD", target_fact_id=None, reasoning="Error fallback")
            for f in new_facts
        ]
