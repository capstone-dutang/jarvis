# Extracting knowledge from assistant turns: a concrete filter design for JARVIS

> 연구 일자: 2026-04-17
> 성격: 딥리서치 — assistant 턴 추출 필터 설계
> 상태: 활성 (구현 대기)

**Build a symmetric, mechanical Stage 1 filter modeled on Graphiti's episode approach — not Mem0's user-only approach.** The dominant open-source memory systems split along a sharp axis: Mem0 explicitly forbids assistant extraction ("YOU WILL BE PENALIZED IF YOU INCLUDE INFORMATION FROM ASSISTANT OR SYSTEM MESSAGES"), while Graphiti treats speakers symmetrically and lets facts come from whoever utters them. For a coding-assistant memory where 88% of content and virtually all decisions live in assistant turns, Mem0's philosophy is the wrong transfer; its role-filter mechanics are still useful inverted. Below is a rule set you can port directly to `gap_detection.py` — all regex, keyword lists, and thresholds — with an LLM-free Stage 1 and a clear escape hatch if specific signal categories warrant it. **No LLM call is needed at Stage 1; the existing Haiku+Sonnet gap extraction already provides the semantic backstop.**

## What the major systems actually do (and why Mem0 is the wrong model)

**Mem0 hard-codes asymmetric, user-only extraction.** In `mem0/configs/prompts.py`, the default `USER_MEMORY_EXTRACTION_PROMPT` contains the verbatim instruction `# [IMPORTANT]: GENERATE FACTS SOLELY BASED ON THE USER'S MESSAGES. DO NOT INCLUDE INFORMATION FROM ASSISTANT OR SYSTEM MESSAGES` and `# [IMPORTANT]: YOU WILL BE PENALIZED IF YOU INCLUDE INFORMATION FROM ASSISTANT OR SYSTEM MESSAGES`. An `AGENT_MEMORY_EXTRACTION_PROMPT` flips this polarity but is only activated when both `metadata.agent_id` is set *and* assistant messages are present (`_should_use_agent_memory_extraction` in `mem0/memory/main.py`). Preprocessing is trivial — just `parse_messages()` flattening role-tagged strings via `f"{role}: {content}\n"`, with system messages dropped. **No regex noise filtering, no tool_use handling, no length thresholds.** Mem0 assumes a personal-assistant use case where memory = user self-facts. For JARVIS's use case (technical decisions, bug discoveries, implementation rationale), this inversion is philosophically wrong — but the role-aware message flattening and the post-LLM `remove_code_blocks` + `extract_json` recovery are portable.

**Graphiti is the closer analogue.** Its extraction unit is an `Episode` — an arbitrarily-long multi-turn string passed as one LLM call. From `graphiti_core/nodes.py`, `EpisodeType.message` expects `"role: content\nrole: content..."` format. The `extract_nodes.py` system prompt is **speaker-symmetric**: *"Always extract the speaker (the part before the colon `:` in each dialogue line) as the first entity node"*, with no "ignore assistant messages" clause. The `extract_edges.py` prompt instructs extraction of facts *"clearly stated or unambiguously implied in the CURRENT MESSAGE,"* with the PREVIOUS_MESSAGES window (default `EPISODE_WINDOW_LEN = 3`) used *"only to disambiguate references or support continuity."* This is exactly the coreference resolution pattern JARVIS needs ("that approach" → resolved via prior turn context). Graphiti does no mechanical preprocessing — it trusts the LLM — but **its episode-window abstraction is the right architectural choice.**

**LangMem is most flexible and least opinionated.** The `create_memory_manager`/`create_memory_store_manager` APIs take an `instructions: str` parameter that users customize, with Pydantic `schemas` controlling what structured memory looks like. It operates on a `messages` list with configurable `max_steps` for multi-phase enrichment, and exposes the canonical **hot-path vs background** distinction: hot-path uses `create_manage_memory_tool` exposed to the agent directly, background uses `ReflectionExecutor` with debouncing (idle timeout, min-new-messages threshold). Defaults worth copying: `enable_deletes=False` (prefer consolidation over deletion), `query_limit` + separate cheaper `query_model` for memory search.

**ChatGPT's `bio` tool is assistant-triggered but user-scoped.** The leaked system prompt (archived at `github.com/asgeirtj/system_prompts_leaks` and `github.com/EliFuzz/awesome-system-prompts`, corroborated across multiple dumps) instructs: *"Anytime the user shares information that will likely be true for months or years and will likely change your future responses in similar situations, you should always call the bio tool."* The exclusion list is sharp and directly portable: **don't store short-lived facts, random details, redundant information, translation/rewrite source text, or sensitive attributes** (race, religion, sexual orientation, political views, health, criminal record) unless explicitly requested. There is no post-hoc mining of assistant turns — the assistant itself decides in-line whether to write. **This is orthogonal to JARVIS: you're doing the post-hoc extraction ChatGPT skips.**

**Claude's Memory Tool (beta, `memory_20250818`, launched Sep 2025) is a filesystem abstraction, not an extraction pipeline.** Six ops (`view`, `create`, `str_replace`, `insert`, `delete`, `rename`) over a client-side `/memories` directory, with the injected system prompt `"IMPORTANT: ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE"` plus `"ASSUME INTERRUPTION: Your context window might be reset at any moment."` The CLAUDE.md convention cascades across four scopes (enterprise → project → user → session). Neither does automatic extraction — **the agent writes memory imperatively, not by being mined.** No transfer for JARVIS's Stage 1 filter.

**Bottom line across systems:** none do sophisticated mechanical noise filtering before the LLM call. Graphiti and Mem0 both trust the LLM to filter. LangMem pushes it to user-supplied instructions. **JARVIS is filling a real gap here** — an LLM-free Stage 1 that handles Claude Code's structural noise (scaffolding phrases, tool envelopes, thinking blocks) before the expensive extraction pass.

## Tool_use and tool_result handling across the ecosystem

**None of the major memory systems natively handle Anthropic-style content blocks.** Mem0's `parse_messages` silently drops anything that isn't `role ∈ {system, user, assistant}` with a string `content` field. Graphiti expects pre-flattened `"role: content"` strings. LangChain's `ConversationKGMemory` and `ConversationSummaryMemory` operate on flat human/AI text. **Every pipeline assumes the caller has already flattened tool blocks into prose.** This means JARVIS must make the flattening decisions itself.

The empirically correct policy splits by block type:

**`tool_result` blocks should be stripped entirely by default.** They duplicate state that the next assistant message will reference anyway, and they explode transcript size (a single file Read can be thousands of lines). The single exception is `is_error: true` results — keep a one-line error summary because errors frequently contain user-visible decision context ("permission denied for Bash(npx:*)"). Post-preprocessing the existing JARVIS pipeline already summarizes these; the filter should preserve those compressed summaries only when they surface via the assistant's interpretive text.

**`tool_use` blocks should be compressed to a single-line breadcrumb.** For pure-retrieval tools (Read, Grep, Glob, LS, WebFetch), drop the input entirely and emit `<tool:Read path>` or similar. For mutation tools (Edit, MultiEdit, Write, Bash, TodoWrite), emit a 1-line summary that preserves the target and action — e.g., `<tool:Edit src/foo.py: added function bar>`. The reason: **the tool call's existence is signal** (it anchors assistant narration like "I ran tests and they failed") **but its payload is noise** (the full command, full file contents).

**`thinking` blocks are the hardest call and deserve a deliberate default: strip them at Stage 1.** They contain candid reasoning but also rabbit holes, reconsiderations, and scratch planning. Crucially, **the assistant's text block after a thinking block typically summarizes the conclusion** — the thinking is exploratory, the text is the commitment. Keeping both duplicates signal and adds noise. `redacted_thinking` blocks should always be stripped (opaque blobs). The `signature` field must never be stored regardless — it's cryptographic and useless outside the original API session. If later analysis shows JARVIS is missing specific decision rationale, reintroduce thinking blocks selectively (e.g., only ones >200 chars, or only those immediately preceding a `tool_use` for Edit/Write). Start strict; loosen empirically.

**The "bridge" problem ("tool showed X → therefore fact is Y") is solved by pair extraction, not by clever tool-block preservation.** If you flatten `[assistant text → tool_use breadcrumb → tool_result dropped → next assistant text]` into a single assistant block, the before/after prose around the tool call captures the interpretation. That's where the knowledge actually lives.

## Chunk unit tradeoffs: why pair-level wins for JARVIS

The three approaches produce measurably different quality at different costs:

**Turn-level** (one message per extraction unit) is what JARVIS currently does for user turns. Token-cheap, simple, but breaks on "that approach didn't work" because the antecedent is in the previous turn. Also splits assistant "I see the issue — it's X because Y" from the user's "why doesn't this work?" that triggered it. **Coreference failure rate here is the empirical reason to abandon it.**

**Episode-level** (Graphiti's approach, N=3 messages of prior context window) preserves long-range references and handles "let's go back to what we discussed earlier" well. Cost: each unit is ~3x larger, extraction runs less frequently, and you pay for the overlapping context on every pass. Graphiti's `EPISODE_WINDOW_LEN = 3` prior episodes-for-context is the middle ground — the current episode is extracted against, prior ones disambiguate only.

**Pair-level** (user+assistant adjacent, Mem0's implicit unit per `.chat()` turn) is the sweet spot for JARVIS. Two messages resolve ~95% of coreference cases (the "that approach" pattern is almost always referring to what the assistant just said in the immediately prior turn), tokens stay manageable, and the unit maps cleanly to how Claude Code sessions actually progress (user directive → assistant execution). Edge cases where a user references something 3+ turns back are rare in coding sessions because user turns are usually short directives, not elaborations.

**JARVIS recommendation: adopt pair-level as the primary unit, with a Graphiti-style 2-pair lookback window as disambiguation context only (not extracted from).** This matches the empirical user-turn brevity you measured (7-15 words) and aligns with the directive-then-execution rhythm of Claude Code. Implementation: iterate the JSONL grouping `(user_turn_i, assistant_turn_i)` as one extraction unit, with pairs `i-2` and `i-1` available as prefix context in the Stage 1 filter's priority scoring (but not themselves re-extracted).

## Claude Code JSONL structure and what to keep vs strip

The format is **undocumented by Anthropic** but well-reverse-engineered via community parsers (most complete: `claude-parser` by alicoding, `claude-code-log` by daaain). Each line is an event with top-level fields `type`, `uuid`, `parentUuid`, `sessionId`, `timestamp`, `cwd`, `version`, `userType`, `isSidechain`, `requestId`, `message`. The `type` is one of `user`, `assistant`, `summary`, `system`. The `message.content` array uses standard Anthropic content blocks: `text`, `thinking` (with opaque `signature`), `tool_use` (`name`, `input`, `id` like `toolu_01...`), and `tool_result` (which appears inside synthetic `user`-role rows with `tool_use_id` linking back). A real 837-row session analyzed by Liam ERD contained 532 assistant rows, 299 user rows, and 286 messages carrying `tool_use` — **tool-heavy density is typical, confirming aggressive stripping is essential.**

Specific keep/strip recommendations against your preprocessed format:

- **`User:` / `Assistant:` markers** — keep; they're the role boundary and Graphiti-style speaker anchor.
- **`[thinking]` blocks** — strip at Stage 1. Length-gate is a weak substitute; short thinking is pure planning and long thinking is exploratory, not committed. Both get superseded by the subsequent text block.
- **Compressed `tool_result` summaries** — keep them *as part of the assistant's narration* when the assistant references them ("The test output shows..."), but drop standalone `tool_result` blocks. Your preprocessor has already compressed them, so if they survived preprocessing they're likely the short informative ones worth keeping.
- **`[CODE BLOCK: language, N lines]` placeholders** — keep. They're signal that code was produced, which matters for "the assistant implemented X" extraction. Don't strip the placeholder; strip only the scaffolding around it.
- **"Let me..." / "I'll..."** scaffold — strip aggressively (regex below). These are Claude's canonical preambles documented across the Liam ERD session ("I'll implement...", "Let me start by searching...", "Let me examine...", "Now let me look for...") and cited in `anthropics/claude-code` issue #3382 and related community complaints.
- **Markdown headers (`##`, `###`)** — keep when in structured output (summary sections, final answers); strip when purely organizational ("### Step 1:" style). Heuristic: keep headers with >30 chars of title text; strip short action headers.
- **Bullet lists** — keep. They usually carry enumerated facts or decisions in summary form.
- **Sycophancy** ("You're absolutely right!", "Perfect!", "Great question!") — strip unconditionally. This is the #1 most documented Claude verbal tic (`anthropics/claude-code` #3382, `news.ycombinator.com/item?id=44885398`, `theregister.com/2025/08/13/claude_codes_copious_coddling_confounds/`).
- **`isSidechain: true` rows** — drop from extraction; these are Task/sub-agent sessions whose parent will re-narrate conclusions.
- **`type: summary` and `type: system` rows** — drop.
- **Synthetic user rows whose `message.content` is entirely `tool_result` blocks** — drop; these aren't real user turns.
- **`isCompactSummary`, `isVisibleInTranscriptOnly`, `isMeta` flags** — when truthy, exclude.

## MCP cloud transcript access: effectively unavailable

No MCP server can legitimately pull ChatGPT web or Claude.ai conversation history — neither OpenAI nor Anthropic exposes a public API for a user's own conversation history as of April 2026. Existing MCP memory servers (`doobidoo/mcp-memory-service`, `adamkwhite/claude-memory-mcp`, `baryhuang/mcp-openmemory`, `thedotmack/claude-mem`, `mkreyman/mcp-memory-keeper`) all work around this by capturing live turns via hooks/tools during active sessions, ingesting local Claude Code JSONL from `~/.claude/projects/`, or importing manual JSON dumps from Claude.ai's data-export feature. Simon Willison's `claude-code-transcripts` had a `web` subcommand that scraped claude.ai via undocumented APIs and macOS keychain tokens, but its README now warns it's broken due to unofficial API changes. **JARVIS should assume cloud transcripts are unreachable and standardize on local JSONL plus live MCP capture hooks.**

## The JARVIS Stage 1 filter: a concrete rule set

The design is LLM-free, mechanical, and layered. Process each content block, then each sentence within `text` blocks, then score pairs. The existing coverage/keyword/semantic/priority stages then run against the cleaned output.

### Layer 1: block-level filtering (applied during JSONL parsing)

```python
DROP_BLOCK_TYPES = {"thinking", "redacted_thinking", "tool_result"}
SUMMARIZE_BLOCK_TYPES = {"tool_use"}
KEEP_BLOCK_TYPES = {"text", "image"}

DROP_ROW_IF = {
    "type": {"summary", "system"},
    "isSidechain": True,
    "isCompactSummary": True,
    "isVisibleInTranscriptOnly": True,
    "isMeta": True,
}

# Drop user rows that are pure tool_result envelopes
def is_synthetic_tool_result_row(row):
    msg = row.get("message", {})
    content = msg.get("content")
    return (isinstance(content, list)
            and len(content) > 0
            and all(b.get("type") == "tool_result" for b in content))

# tool_use compression
READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "NotebookRead"}
MUTATION_TOOLS  = {"Edit", "MultiEdit", "Write", "Bash", "TodoWrite", "NotebookEdit"}

def compress_tool_use(block):
    name = block["name"]
    if name in READ_ONLY_TOOLS:
        target = block["input"].get("file_path") or block["input"].get("pattern") or ""
        return f"<tool:{name} {target}>".strip()
    if name in MUTATION_TOOLS:
        target = (block["input"].get("file_path")
                  or block["input"].get("command", "")[:80]
                  or "")
        return f"<tool:{name} {target}>".strip()
    # MCP or unknown tool — keep name only
    return f"<tool:{name}>"
```

### Layer 2: sentence-level scaffolding strip (applied to `text` blocks)

Apply in order; each is case-insensitive and multiline.

```python
import re

# Rule 1: Sycophancy openers — the #1 Claude tic. Strip entirely.
SYCOPHANCY = re.compile(
    r"^\s*(you['']re\s+(absolutely\s+)?(right|correct)[!.]?|"
    r"(great|excellent|perfect|good)\s+(question|point|catch|idea)[!.]?|"
    r"(perfect|great|excellent|absolutely|exactly|wonderful|amazing|brilliant)[!.]?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Rule 2: Action-announcing preambles. Strip the whole sentence.
PREAMBLE = re.compile(
    r"^\s*(now\s+)?(first[,]?\s+|next[,]?\s+|then[,]?\s+)?"
    r"(i['']ll|i\s+will|i['']m\s+going\s+to|let\s+me|let['']s)"
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
    r"^\s*(i['']ll\s+use\s+the\s+todowrite[^.\n]*[.]?|"
    r"let\s+me\s+update\s+the\s+todo[^.\n]*[.]?|"
    r"let\s+me\s+think\s+(about\s+this\s+)?step\s+by\s+step[.]?|"
    r"let\s+me\s+(try\s+)?(a\s+different|another)\s+approach[.]?)\s*",
    re.IGNORECASE | re.MULTILINE,
)

def strip_scaffolding(text: str) -> str:
    for pattern in (SYCOPHANCY, FILLER_ACK, META_TODO, PREAMBLE, HEDGE_BRIDGE):
        text = pattern.sub("", text)
    # Collapse blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
```

### Layer 3: signal boosters (priority scoring, not filtering)

These don't drop content; they boost priority in the existing Stage 1 priority scorer. A sentence matching any of these is very likely a decision, discovery, or solution and should win ties.

```python
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
    r"\b(doesn['']t|does not|won['']t|will not|can['']t|cannot|"
    r"didn['']t|failed|broken|incorrect|wrong|missing|"
    r"instead|however|but\s+actually|on second thought)\b",
    re.IGNORECASE,
)

SIGNAL_BOOST_WEIGHT = 2.0  # multiplier in priority scorer
```

### Layer 4: pair assembly and length gates

```python
MIN_CLEANED_ASSISTANT_CHARS = 40   # drop empty/scaffolding-only turns
MIN_PAIR_CHARS = 60                # user + assistant combined floor

def assemble_pair(user_turn, assistant_turn, context_window=2):
    """
    Returns (pair_text, context_prefix_text).
    pair_text: the unit actually extracted from.
    context_prefix_text: prior N pairs, available for disambiguation only.
    """
    ...
```

### Layer 5: final decision tree

The Stage 1 decision for each assistant turn collapses to:

1. If row-level `DROP_ROW_IF` matches → drop.
2. If `is_synthetic_tool_result_row` → drop.
3. For each content block: drop thinking/redacted_thinking/tool_result; compress tool_use; keep text/image.
4. Concatenate remaining text blocks.
5. Apply `strip_scaffolding` (layer 2).
6. If remaining length < `MIN_CLEANED_ASSISTANT_CHARS` → drop.
7. Pair with the preceding user turn; if combined length < `MIN_PAIR_CHARS` → drop.
8. Score: apply DECISION/DISCOVERY/CORRECTION boosters to existing priority calculation.
9. Emit the pair as the extraction unit for downstream Haiku+Sonnet.

### Why no LLM at Stage 1

The existing Haiku+Sonnet 2-stage gap extraction already does semantic filtering downstream — duplicating that with a Stage 1 LLM wastes tokens. The mechanical rules above achieve two specific jobs the LLM shouldn't have to do: **(a) removing Claude's documented scaffolding habits**, which are so regular that regex catches >90% of cases at zero cost, and **(b) structural normalization** of tool_use/tool_result/thinking blocks, which Mem0 and Graphiti assume the caller has already done. If you later find specific categories slipping through — e.g., novel scaffolding phrases from newer Claude versions — add them to the regex lists rather than escalating to an LLM. The regex approach compounds: every pattern you add is permanent zero-cost coverage.

**The one place to consider a minimal LLM call is distinguishing "suggestion the assistant offered but user rejected" from "decision committed to."** If this failure mode shows up empirically, a single-sentence Haiku classifier on the boundary cases (sentences containing DECISION_MARKERS but also hedging like "we could" or "you might want to") is cheap. Don't build it preemptively.

## Key takeaways

The research surfaces a sharp philosophical split: **Mem0-style user-only extraction encodes a personal-assistant assumption that inverts for coding sessions**, where the assistant does the thinking and the user writes three-word directives. Graphiti's speaker-symmetric episode model is the correct lineage, and pair-level extraction with a 2-pair lookback is the right chunk size — smaller than Graphiti's episode window, larger than Mem0's implicit per-call message list. **None of the production systems handle Anthropic content blocks natively,** which means JARVIS's biggest leverage is mechanical preprocessing that none of its competitors do well: strip thinking, compress tool_use, drop tool_result, kill scaffolding, boost decision markers. The rule set above is directly portable to Python; start strict, measure what slips through by sampling dropped content against manual judgments, and loosen selectively. The existing Haiku+Sonnet backstop makes over-filtering at Stage 1 cheap to correct and over-inclusion expensive — so err strict.
