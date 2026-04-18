"""Server-side gap extraction using Claude CLI pipe mode.

When gap_detection.py identifies uncovered turns, this module extracts
knowledge from those gaps using `claude -p` subprocess.

Two-step process (Haiku extraction → Sonnet reconciliation):
1. Extract entities/facts/relations from gap turns (Haiku, blind)
2. Reconcile facts against existing memories (Sonnet, ADD/UPDATE/NOOP)

Based on:
- Research #1 Prompt A (extraction-prompt-design.md Section 7)
- Research #4 Prompt A (gap-filling-pipeline.md Section 3)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import platform
import subprocess
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

CLAUDE_CMD = "claude.cmd" if platform.system() == "Windows" else "claude"


@dataclass
class ExtractedEntity:
    """An entity extracted from gap turns."""

    name: str
    entity_type: str
    source_quote: str


@dataclass
class ExtractedFact:
    """A fact extracted from gap turns."""

    subject: str
    predicate: str
    object_value: str
    source_quote: str


@dataclass
class ExtractedRelation:
    """A relation extracted from gap turns."""

    from_entity: str
    to_entity: str
    relation_type: str
    source_quote: str


@dataclass
class ExtractionResult:
    """Full extraction result from gap turns."""

    entities: list[ExtractedEntity] = field(default_factory=list)
    facts: list[ExtractedFact] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)


@dataclass
class ReconciliationAction:
    """Action to take for an extracted fact after reconciliation."""

    fact: ExtractedFact
    action: str  # "ADD" | "UPDATE" | "NOOP"
    reasoning: str


# Research #1 Prompt A adapted for gap extraction context.
# System prompt is passed via --system-prompt flag.
EXTRACTION_SYSTEM_PROMPT = (
    "You are a precise knowledge extraction system for a personal memory server. "
    "Extract structured entities, facts, and relations from conversation transcripts. "
    "Output only valid JSON."
)

EXTRACTION_PROMPT = """Extract all entities, facts, and relations from the conversation segment below.
Follow the schema and rules exactly. Every extraction must include a verbatim source_quote.

<schema>
You MUST output valid JSON matching this exact schema:
{{
  "entities": [
    {{
      "name": "canonical name of the entity",
      "entity_type": "person|project|technology|concept|preference|organization|location|event|resource",
      "source_quote": "exact verbatim substring from the GAP section"
    }}
  ],
  "facts": [
    {{
      "subject": "entity name (must match an entity in the entities list)",
      "predicate": "snake_case_verb_phrase describing the relationship",
      "object": "the value, target, or description",
      "source_quote": "exact verbatim substring from the GAP section"
    }}
  ],
  "relations": [
    {{
      "from": "source entity name (must match an entity in the entities list)",
      "to": "target entity name (must match an entity in the entities list)",
      "relation_type": "snake_case relationship type",
      "source_quote": "exact verbatim substring from the GAP section"
    }}
  ]
}}
</schema>

<rules>
1. SOURCE QUOTE GROUNDING (MANDATORY): Every extracted item MUST include a source_quote that is an EXACT verbatim substring from the GAP section. The quote must be findable via exact string match. If you cannot identify a verbatim quote, do NOT extract it.

2. ENTITY TYPES: Use ONLY these values: person, project, technology, concept, preference, organization, location, event, resource.

3. PREDICATES: Use free-form snake_case for STATE facts (uses_for_database, has_profit_margin_rate, works_on). For DECISIONS/JUDGMENTS, use these UPPERCASE_SNAKE predicates so recall can find the "why":
   - CHOSEN_OVER(selected, rejected), REJECTED(agent, option), DECIDED_FOR(agent, X), PREFERRED_OVER(A, B)
   - JUSTIFIED_BY(X, reason), MOTIVATED_BY(action, factor)
   - COMPARED_WITH(A, B), CONSIDERED(agent, X)
   - DEPRECATED(X), REPLACED_BY(old, new), INVALIDATED_BY(fact, reason)
   Example: if the user picks SecondBrain over Argos because of margins, emit BOTH
   (SecondBrain, CHOSEN_OVER, Argos) AND (SecondBrain, JUSTIFIED_BY, "86% margin + B2B market clarity"). This dual emission makes the decision findable and its reason searchable separately.

4. WHAT TO EXTRACT — substantive knowledge only:
   - Decisions made and their rationale
   - Technology choices, architecture decisions, tool selections
   - Personal preferences, opinions, sentiments (positive and negative)
   - Biographical and professional facts about the user
   - Project status, goals, plans
   - Problems identified and solutions chosen
   - Corrections and updates to previously stated facts
   - Relationships between entities (uses, depends_on, chosen_over, etc.)

5. WHAT TO SKIP:
   - AI assistant meta-commentary ("Let me search for that", "Based on the results")
   - Greetings, pleasantries, conversational scaffolding
   - Code syntax, variable names, import statements, stack traces (extract decisions ABOUT code, not code itself)
   - AI capabilities or limitations statements

6. COREFERENCE: Resolve pronouns to full entity names. Use "user" for the user themselves.

7. CORRECTIONS: When the user corrects info, extract the corrected version with predicate "corrects_to" or "changed_decision_to".

8. LANGUAGE: Preserve source_quote in original language. Entity names use canonical form (e.g., "PostgreSQL" not "포스트그레스").

9. IMPLICIT KNOWLEDGE: Extract decisions implied by choosing one option over alternatives. Extract sentiments implied by emotional language.
</rules>

{existing_entities_block}

<conversation>
{context_before}
--- GAP START ---
{gap_turns}
--- GAP END ---
{context_after}
</conversation>

Output only the JSON object. No explanation or commentary."""

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


def _call_claude(prompt: str, model: str = "sonnet", system_prompt: str = "", timeout: int = 120) -> str | None:
    """Call claude -p and return the result text."""
    cmd = [
        CLAUDE_CMD,
        "-p",
        "--output-format", "json",
        "--model", model,
        "--tools", "",
        "--no-session-persistence",
        "--system-prompt", system_prompt or "You are a precise knowledge extraction system. Output only valid JSON.",
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
        logger.warning("Claude CLI timed out after %ds (model=%s)", timeout, model)
        return None

    if result.returncode != 0:
        logger.warning("Claude CLI error (model=%s): exit %d, %s", model, result.returncode, result.stderr[:200])
        return None

    try:
        response_data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("Claude CLI returned non-JSON (model=%s)", model)
        return None

    # Try structured_output first, then result
    structured = response_data.get("structured_output")
    if structured:
        return json.dumps(structured)

    raw = response_data.get("result", "")
    return raw if raw else None


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Try to parse JSON from text, with fallback to extract embedded JSON."""
    try:
        return json.loads(text)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            start = text.index("{")
            end = text.rindex("}") + 1
            return json.loads(text[start:end])  # type: ignore[no-any-return]
        return None


async def extract_from_gaps(
    gap_turns: list[dict[str, Any]],
    transcript: str,
    context_window: int = 500,
    existing_entities: list[dict[str, str]] | None = None,
    max_chars_per_chunk: int = 30000,
) -> ExtractionResult:
    """Step 1 — Extract entities/facts/relations from gap turns using Haiku.

    Splits gap turns into chunks to stay within context window limits.
    Each chunk is processed independently, results are merged.

    Args:
        gap_turns: List of {"index": int, "text": str}
        transcript: Full transcript for context
        context_window: Chars of context before/after gap
        existing_entities: Canonical entity list [{"name": ..., "type": ...}] for consistency
        max_chars_per_chunk: Max chars of gap text per extraction call

    Returns:
        ExtractionResult with entities, facts, and relations
    """
    if not gap_turns:
        return ExtractionResult()

    # Build existing entities block (shared across all chunks)
    existing_entities_block = ""
    if existing_entities:
        entity_lines = "\n".join(f"- {e['name']} ({e.get('type', 'other')})" for e in existing_entities)
        existing_entities_block = (
            "<existing_entities>\n"
            f"{entity_lines}\n"
            "</existing_entities>\n"
            "When referring to entities that match existing entities above, "
            "use the exact canonical name from the list."
        )

    # Split gap turns into chunks by character count
    chunks: list[list[dict[str, Any]]] = []
    current_chunk: list[dict[str, Any]] = []
    current_chars = 0
    for turn in gap_turns:
        turn_chars = len(turn["text"])
        if current_chars + turn_chars > max_chars_per_chunk and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_chars = 0
        current_chunk.append(turn)
        current_chars += turn_chars
    if current_chunk:
        chunks.append(current_chunk)

    logger.info("Gap extraction: %d turns split into %d chunks", len(gap_turns), len(chunks))

    # Process each chunk
    all_entities: list[ExtractedEntity] = []
    all_facts: list[ExtractedFact] = []
    all_relations: list[ExtractedRelation] = []

    for i, chunk in enumerate(chunks):
        gap_text = "\n".join(t["text"] for t in chunk)

        # Get context before/after this chunk
        first_idx = chunk[0].get("index", 0)
        context_before = transcript[max(0, first_idx - context_window):first_idx] if first_idx > 0 else ""
        last_text = chunk[-1]["text"]
        last_pos = transcript.find(last_text)
        context_after = ""
        if last_pos >= 0:
            end = last_pos + len(last_text)
            context_after = transcript[end:end + context_window]

        prompt = EXTRACTION_PROMPT.format(
            context_before=context_before,
            gap_turns=gap_text,
            context_after=context_after,
            existing_entities_block=existing_entities_block,
        )

        response_text = await asyncio.to_thread(
            _call_claude, prompt, model="haiku", system_prompt=EXTRACTION_SYSTEM_PROMPT,
        )
        if not response_text:
            logger.warning("Gap extraction chunk %d/%d returned nothing", i + 1, len(chunks))
            continue

        data = _try_parse_json(response_text)
        if data is None:
            logger.warning("Gap extraction chunk %d/%d returned non-JSON", i + 1, len(chunks))
            continue

        chunk_result = _parse_extraction(data)
        all_entities.extend(chunk_result.entities)
        all_facts.extend(chunk_result.facts)
        all_relations.extend(chunk_result.relations)

        logger.info(
            "Gap extraction chunk %d/%d: entities=%d, facts=%d, relations=%d",
            i + 1, len(chunks), len(chunk_result.entities), len(chunk_result.facts), len(chunk_result.relations),
        )

    return ExtractionResult(entities=all_entities, facts=all_facts, relations=all_relations)


def _parse_extraction(data: dict[str, Any]) -> ExtractionResult:
    """Parse full extraction result (entities + facts + relations) from JSON."""
    entities = [
        ExtractedEntity(
            name=e.get("name", ""),
            entity_type=e.get("entity_type", "other"),
            source_quote=e.get("source_quote", ""),
        )
        for e in data.get("entities", [])
        if e.get("name")
    ]
    facts = [
        ExtractedFact(
            subject=f.get("subject", ""),
            predicate=f.get("predicate", ""),
            object_value=f.get("object", ""),
            source_quote=f.get("source_quote", ""),
        )
        for f in data.get("facts", [])
        if f.get("subject") and f.get("predicate")
    ]
    relations = [
        ExtractedRelation(
            from_entity=r.get("from", ""),
            to_entity=r.get("to", ""),
            relation_type=r.get("relation_type", "related_to"),
            source_quote=r.get("source_quote", ""),
        )
        for r in data.get("relations", [])
        if r.get("from") and r.get("to")
    ]
    return ExtractionResult(entities=entities, facts=facts, relations=relations)


async def reconcile_facts(
    new_facts: list[ExtractedFact],
    existing_facts: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Step 2 — Reconcile extracted facts against existing memories using Sonnet.

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

    response_text = await asyncio.to_thread(_call_claude, prompt, model="sonnet")
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

    data = _try_parse_json(response_text)
    if data is None:
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
