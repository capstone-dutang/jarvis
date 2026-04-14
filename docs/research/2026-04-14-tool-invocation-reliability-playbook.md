# Making AI agents reliably persist memory via MCP

> 연구 일자: 2026-04-14
> 성격: 딥리서치 #2 — 도구 호출 신뢰성 + 구현 플레이북
> 상태: 활성 (절대문서 반영 필요)

**LLMs will not autonomously call memory tools without explicit, layered instruction** — this is the single most important finding from every production MCP memory system. Claude Code, Claude Desktop, and ChatGPT all exhibit the same behavior: they acknowledge information conversationally ("Got it, I'll remember that") but never call `store_memory` unless prompted. The solution is a defense-in-depth strategy combining tool descriptions, MCP instructions, CLAUDE.md directives, and Claude Code hooks. No single layer achieves 70-80% capture alone, but stacking them does. This playbook provides copy-paste configurations for each layer, a decision tree by client, and concrete schemas for your JARVIS server.

---

## Section 1: The instructions field and what Claude actually honors

### MCP `instructions` field specification

The `instructions` field is a **top-level string in `InitializeResult`**, not nested inside `capabilities`. It's returned during the MCP initialization handshake:

```python
# FastAPI/FastMCP server
from fastmcp import FastMCP

mcp = FastMCP(
    name="jarvis-memory",
    version="1.0.0",
    instructions="""JARVIS Memory Server — persistent cross-session memory for conversations.

WORKFLOW:
1. On session start: call recall_memory with context about the current task to load relevant prior knowledge.
2. During conversation: call store_memory whenever you learn preferences, make decisions, encounter corrections, or identify important facts. Err on the side of storing — future sessions benefit from over-remembering.
3. Before session end: call store_memory with a session summary capturing key outcomes and open questions.

STORAGE TRIGGERS — call store_memory when ANY of these occur:
- User states a preference, constraint, or requirement
- A technical decision is made (include reasoning and rejected alternatives)
- User corrects you or provides clarifying information
- You discover project architecture, conventions, or patterns
- A topic shift occurs (consolidate the previous topic first)

RETRIEVAL — call recall_memory BEFORE:
- Answering questions about prior context
- Making recommendations that should account for user preferences
- Starting work on a topic that may have prior history"""
)
```

**Measured impact**: The GitHub MCP server team ran 40 controlled sessions in VS Code. **Instructions improved correct tool usage from 60% to 85% overall** — a 25-percentage-point lift. The improvement was model-dependent: GPT-5-Mini jumped from 20% to 80% (+60pp), while Claude Sonnet 4 already hit 90-100% without instructions. Instructions matter most for cross-model consistency and for weaker models.

**Client support**: Claude Code injects instructions into the system prompt. VS Code Copilot does the same. **Claude Desktop has been reported to ignore the instructions field entirely** — the Blockscout team documented this when their agent started calling tools without reading operational rules. Cursor (v1.6+) supports instructions. ChatGPT does not consume MCP instructions at all (it only supports MCP tools, not the full protocol).

### Tool description patterns that maximize autonomous invocation

After analyzing tool descriptions from 12 production MCP memory servers, three patterns emerged in order of effectiveness:

**Pattern 1 — Imperative with triggers (most effective):**
```
"Save information to persistent memory. You MUST call this tool when: (1) the user states a preference or constraint, (2) a decision is made, (3) the user corrects you, (4) you discover architecture or conventions. When in doubt, store it — future sessions benefit from over-remembering."
```
Used by: mem0-selfhosted, Memento (scrypster fork), MemPalace. This pattern explicitly enumerates triggers, reducing the model's decision burden.

**Pattern 2 — Conditional with frequency guidance (moderately effective):**
```
"Search memories using semantic search. Always search your memories before making decisions to ensure you leverage your existing knowledge. Call this at the start of each session and when encountering new topics."
```
Used by: coleam00/mcp-mem0, OpenMemory. Provides "when" guidance without imperative force.

**Pattern 3 — Neutral/descriptive (least effective for autonomous use):**
```
"Stores a piece of text content as a memory associated with a specific userId."
```
Used by: pinkpixel-dev/mem0-mcp. Works when users explicitly ask to save, fails for autonomous invocation.

### The bootstrap tool pattern

The most reliable technique for ensuring instructions are read is the **"unlock" bootstrap tool** — a dedicated initialization tool whose response contains behavioral instructions. The Blockscout MCP server provides the canonical case study:

- First attempt: `__get_instructions__` tool → **Failed.** Models treated it as optional reading material.
- Second attempt: `__unlock_blockchain_analysis__` → **Succeeded.** The verb "unlock" creates a semantic signal aligning with "setup → execute" patterns. Most models now consistently call it before any analytical tools.

### CLAUDE.md is still the most reliable channel

Despite all the MCP-native options, **CLAUDE.md remains the most consistently effective location for memory instructions** in Claude Code. One developer reported: "I built a 200-line CLAUDE.md. Claude Code follows it 60% of the time. Here's what fixed that: combining CLAUDE.md with hooks for enforcement." The consensus from mem0-selfhosted, Memento, and Basic Memory is identical: CLAUDE.md alone is probabilistic but persistent; hooks add deterministic reinforcement.

**Priority hierarchy** (what Claude Code attends to most → least):
1. **Hooks** — deterministic, always fire, not subject to model attention
2. **CLAUDE.md** near CWD — loaded later, model pays more attention to recent context
3. **Tool response content** — bootstrap tool instructions in hot context
4. **MCP instructions field** — injected into system prompt, may be distant
5. **Tool descriptions** — may be deferred by Tool Search (enabled by default since v2.1.9+)
6. **`.claude/rules/*.md`** — path-scoped variant, useful for domain-specific rules

With Tool Search enabled, MCP tool descriptions are **not loaded into context until Claude decides to search for tools**. This means tool descriptions alone cannot drive proactive behavior — the model must already have the intent to use a tool before it sees the description. CLAUDE.md and instructions field fill this gap by creating that intent.

---

## Section 2: Hook-based reinforcement for Claude Code

### Complete hook event reference

Every hook receives these base fields via stdin JSON: `session_id`, `transcript_path` (path to JSONL file), `cwd`, `hook_event_name`. The `transcript_path` is the key to accessing conversation content — hooks can read this JSONL file directly.

| Event | Extra input fields | Key outputs | Conversation access |
|-------|-------------------|-------------|-------------------|
| **SessionStart** | `source` (startup/resume/clear/compact), `model` | `additionalContext` | Via `transcript_path` file |
| **UserPromptSubmit** | `prompt` (user's text) | `additionalContext`, `decision:block` | User prompt inline + `transcript_path` |
| **PreToolUse** | `tool_name`, `tool_input`, `tool_use_id` | `additionalContext`, `permissionDecision`, `updatedInput` | Via `transcript_path` |
| **PostToolUse** | `tool_name`, `tool_input`, `tool_response`, `tool_use_id` | `additionalContext`, `updatedMCPToolOutput` | Tool I/O inline + `transcript_path` |
| **Stop** | `stop_hook_active`, `last_assistant_message` | `decision:block` + `reason` | Last message inline + `transcript_path` |
| **PreCompact** | `trigger` (manual/auto), `custom_instructions` | `decision:block` | Via `transcript_path` (not inline) |
| **PostCompact** | `trigger`, `compact_summary` | Side-effects only | Summary inline |

**Critical detail**: `additionalContext` output is capped at **10,000 characters**.

### The Stop hook for forcing memory persistence

The Stop hook's `decision: "block"` mechanism is the strongest enforcement tool available. When a Stop hook returns `{"decision": "block", "reason": "..."}`, Claude Code **prevents Claude from stopping and feeds the reason back to Claude as context**. Claude must then continue working, sees the reason, and can act on it.

**The infinite loop danger is real.** The `stop_hook_active` check is **mandatory**. When true, Claude is already continuing from a prior stop hook — exit immediately.

**The softer "nudge every N stops" pattern** avoids loop risk entirely. Only outputting a reminder every 3rd Stop event via counter file.

### PreCompact: the last chance to save context

PreCompact fires before context compaction. `transcript_path` points to the JSONL file containing the full transcript. This is your last chance to extract memories before context is lost.

---

## Section 3: MCP sampling is not viable today

MCP sampling allows servers to request LLM completions from clients via `sampling/createMessage`. **This is a dead end for 2025-2026**.

**Client support status**: Claude Desktop ❌, Claude Code ❌, ChatGPT ❌, Codex CLI ❌, VS Code Copilot ✅, Cursor ✅. Only ~12% of clients support it. Additionally, sampling cannot be server-initiated unprompted — per the spec, it must occur within the scope of a client request. Don't architect around sampling.

---

## Section 4: What production systems actually report about reliability

**Universal finding**: "Without explicit instructions, Claude will acknowledge information but not actually save it. You MUST instruct Claude to use the tools."

"Claude's tool-calling training optimizes for fulfilling user requests, not for proactive self-directed tool use." — Memory storage is a self-directed action with no immediate user benefit, so it falls outside the model's default tool-calling heuristic.

### Expected reliability by technique stack

| Technique | Capture rate alone | Cumulative (stacked) |
|-----------|-------------------|---------------------|
| Tool descriptions only | ~10-15% | 10-15% |
| + MCP instructions field | ~20-30% | 25-35% |
| + CLAUDE.md instructions | ~50-60% | 55-65% |
| + Bootstrap tool (initialize_session) | ~60-70% | 65-75% |
| + Stop hook (block + nudge) | N/A (end-of-session) | 70-80% |
| + PreCompact hook (server-side extraction) | N/A (fallback) | 75-85% |

---

## Section 5: Anti-patterns to avoid

1. **Missing infinite-loop guards on Stop hooks.** Always check `stop_hook_active`.
2. **Generic tool names when multiple MCP servers are loaded.** Namespace: `jarvis_store_memory`.
3. **Over-relying on tool descriptions with Tool Search enabled.** Tool descriptions may not be in context until AI searches for tools. CLAUDE.md creates the intent.
4. **Storing full conversation transcripts as single memories.** Store atomic facts with entity links.
5. **Blocking compaction in PreCompact.** Use for extraction (async), not prevention.
6. **Putting ALL instructions in MCP instructions field alone.** Claude Desktop may ignore it. Always duplicate in tool descriptions and CLAUDE.md.
