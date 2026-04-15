"""Server-side gap extraction using Claude CLI pipe mode.

When gap_detection.py identifies uncovered turns, this module extracts
knowledge from those gaps using `claude -p` subprocess.

Two-step process:
1. Extract facts from gap turns (blind, no existing facts context)
2. Reconcile against existing facts (ADD/UPDATE/NOOP)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import platform
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

CLAUDE_CMD = "claude.cmd" if platform.system() == "Windows" else "claude"


@dataclass
class ExtractedFact:
    """A fact extracted from gap turns."""

    subject: str
    predicate: str
    object_value: str
    source_quote: str


@dataclass
class ReconciliationAction:
    """Action to take for an extracted fact after reconciliation."""

    fact: ExtractedFact
    action: str  # "ADD" | "UPDATE" | "NOOP"
    reasoning: str


EXTRACTION_PROMPT = """Extract all factual knowledge from the conversation segment below.

Focus on:
1. Personal preferences (likes, dislikes, choices made)
2. Decisions and their reasoning ("chose X because Y")
3. Negative knowledge ("decided NOT to use X", "rejected Y")
4. Technical details (tools, configurations, architectures)
5. Temporal facts (deadlines, schedules, durations)
6. Relationships between entities
7. Implicit preferences revealed through behavior

<CONVERSATION>
{context_before}
--- GAP START ---
{gap_turns}
--- GAP END ---
{context_after}
</CONVERSATION>

Extract facts as atomic statements. Each fact should be self-contained.
Include a source_quote that is an EXACT verbatim substring from the GAP section.

Return JSON: {{"facts": [{{"subject": "entity name", "predicate": "snake_case_verb", "object": "value", "source_quote": "exact quote from gap"}}]}}
Only return JSON, no explanation."""

RECONCILIATION_PROMPT = """Compare newly extracted facts against existing memories and decide what action to take.

<EXISTING_MEMORIES>
{existing_facts}
</EXISTING_MEMORIES>

<NEW_FACTS>
{new_facts}
</NEW_FACTS>

For each new fact, return exactly one action:
- ADD: genuinely new information absent from existing memories
- UPDATE: corrects, refines, or supersedes an existing memory
- NOOP: already captured in existing memories

Err on the side of ADD for ambiguous cases.

Return JSON: {{"actions": [{{"fact_index": 0, "action": "ADD|UPDATE|NOOP", "reasoning": "brief explanation"}}]}}
Only return JSON, no explanation."""


def _call_claude(prompt: str, timeout: int = 120) -> str | None:
    """Call claude -p and return the result text."""
    cmd = [
        CLAUDE_CMD,
        "-p",
        "--output-format", "json",
        "--model", "sonnet",
        "--tools", "",
        "--no-session-persistence",
        "--system-prompt", "You are a precise knowledge extraction system. Output only valid JSON.",
    ]

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Claude CLI timed out after %ds", timeout)
        return None

    if result.returncode != 0:
        logger.warning("Claude CLI error: exit %d, %s", result.returncode, result.stderr[:200])
        return None

    try:
        response_data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("Claude CLI returned non-JSON")
        return None

    # Try structured_output first, then result
    structured = response_data.get("structured_output")
    if structured:
        return json.dumps(structured)

    raw = response_data.get("result", "")
    return raw if raw else None


async def extract_from_gaps(
    gap_turns: list[dict[str, Any]],
    transcript: str,
    context_window: int = 500,
) -> list[ExtractedFact]:
    """Extract facts from gap turns using claude -p.

    Args:
        gap_turns: List of {"index": int, "text": str}
        transcript: Full transcript for context
        context_window: Chars of context before/after gap

    Returns:
        List of extracted facts
    """
    if not gap_turns:
        return []

    gap_text = "\n".join(t["text"] for t in gap_turns)

    # Get context before/after first gap
    first_idx = gap_turns[0].get("index", 0)
    context_before = transcript[max(0, first_idx - context_window):first_idx] if first_idx > 0 else ""
    last_text = gap_turns[-1]["text"]
    last_pos = transcript.find(last_text)
    context_after = ""
    if last_pos >= 0:
        end = last_pos + len(last_text)
        context_after = transcript[end:end + context_window]

    prompt = EXTRACTION_PROMPT.format(
        context_before=context_before,
        gap_turns=gap_text,
        context_after=context_after,
    )

    response_text = await asyncio.to_thread(_call_claude, prompt)
    if not response_text:
        return []

    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        # Try to extract JSON from response
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            start = response_text.index("{")
            end = response_text.rindex("}") + 1
            data = json.loads(response_text[start:end])
            return _parse_facts(data)
        logger.warning("Gap extraction returned non-JSON response")
        return []

    return _parse_facts(data)


def _parse_facts(data: dict[str, Any]) -> list[ExtractedFact]:
    """Parse facts from JSON response."""
    facts: list[ExtractedFact] = []
    for f in data.get("facts", []):
        facts.append(
            ExtractedFact(
                subject=f.get("subject", ""),
                predicate=f.get("predicate", ""),
                object_value=f.get("object", ""),
                source_quote=f.get("source_quote", ""),
            )
        )
    return facts


async def reconcile_facts(
    new_facts: list[ExtractedFact],
    existing_facts: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Reconcile extracted facts against existing memories using claude -p.

    Returns list of dicts with keys: entity, predicate, object, source_quote, action, reasoning
    """
    if not new_facts:
        return []

    # If no existing facts, all are ADD
    if not existing_facts:
        return [
            {
                "entity": f.subject,
                "predicate": f.predicate,
                "object": f.object_value,
                "source_quote": f.source_quote,
                "action": "ADD",
                "reasoning": "No existing facts",
            }
            for f in new_facts
        ]

    existing_text = "\n".join(
        f"- {ef['entity']} {ef['predicate']} {ef['object']}" for ef in existing_facts
    )
    new_text = "\n".join(
        f"[{i}] {f.subject} {f.predicate} {f.object_value}" for i, f in enumerate(new_facts)
    )

    prompt = RECONCILIATION_PROMPT.format(
        existing_facts=existing_text,
        new_facts=new_text,
    )

    response_text = await asyncio.to_thread(_call_claude, prompt)
    if not response_text:
        # Fallback: ADD everything
        return [
            {
                "entity": f.subject,
                "predicate": f.predicate,
                "object": f.object_value,
                "source_quote": f.source_quote,
                "action": "ADD",
                "reasoning": "Claude unavailable, defaulting to ADD",
            }
            for f in new_facts
        ]

    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            start = response_text.index("{")
            end = response_text.rindex("}") + 1
            data = json.loads(response_text[start:end])
            return _parse_actions(data, new_facts)
        # Fallback
        return [
            {
                "entity": f.subject,
                "predicate": f.predicate,
                "object": f.object_value,
                "source_quote": f.source_quote,
                "action": "ADD",
                "reasoning": "Parse error fallback",
            }
            for f in new_facts
        ]

    return _parse_actions(data, new_facts)


def _parse_actions(data: dict[str, Any], new_facts: list[ExtractedFact]) -> list[dict[str, Any]]:
    """Parse reconciliation actions from JSON response."""
    results: list[dict[str, Any]] = []
    for a in data.get("actions", []):
        fact_idx = a.get("fact_index", 0)
        if 0 <= fact_idx < len(new_facts):
            f = new_facts[fact_idx]
            results.append({
                "entity": f.subject,
                "predicate": f.predicate,
                "object": f.object_value,
                "source_quote": f.source_quote,
                "action": a.get("action", "ADD"),
                "reasoning": a.get("reasoning", ""),
            })
    return results
