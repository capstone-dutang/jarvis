# JARVIS: building a cross-provider AI memory server on MCP

> 연구 일자: 2026-04-14
> 성격: 딥리서치 #1 — 멀티 클라이언트 아키텍처 + 구현 플레이북
> 상태: 활성 (절대문서 반영 필요)

**The core thesis holds—with a ceiling.** Instruction design is the primary lever for making AI clients voluntarily call `store_memory`, but the evidence shows autonomous tool invocation tops out around **30–60% reliability** without hooks. The realistic architecture is a dual-path system: hook-reinforced capture for coding agents (~85–95% reliability) and instruction-optimized voluntary capture for hookless clients (~40–70% with careful design). Cross-provider memory sharing—GPT's decisions available to Claude next session—is fully achievable at the MCP protocol level since tools are universally supported.

---

## Multi-Client Target Matrix

| Client | Transport | Hooks | Expected reliability |
|---|---|---|---|
| Claude Code | Streamable HTTP / stdio | 27 events, 4 handler types | **85–95%** |
| Claude Desktop | Streamable HTTP / stdio | None | **40–60%** |
| Claude.ai Web | OAuth remote MCP | None | **40–60%** |
| ChatGPT Desktop | OAuth remote MCP | None | **30–50%** |
| Codex CLI | Streamable HTTP / stdio | 5 events (experimental) | **75–90%** |
| Cursor | Streamable HTTP | 6 events (beta) | **65–80%** |
| Windsurf | Streamable HTTP | 12 events (no injection) | **55–75%** (passive 90%+) |

---

## Hook Capabilities Comparison

| Capability | Claude Code | Codex CLI | Cursor | Windsurf |
|---|---|---|---|---|
| Session context injection | additionalContext | additionalContext | ❌ | ❌ |
| Stop-hook continuation | decision: "block" | decision: "block" | followup_message | ❌ |
| Full transcript access | transcript_path | transcript_path | ❌ | JSONL file |
| MCP tool interception | matcher | ❌ | beforeMCPExecution | pre/post_mcp_tool_use |
| Pre-compaction capture | PreCompact | ❌ | ❌ | ❌ |
| HTTP handler type | ✅ | ❌ | ❌ | ❌ |
| Total events | ~27 | 5 | 6 | 12 |

---

## Lessons from Existing Memory Servers

**Zep/Graphiti**: Temporal knowledge graph with bi-temporal fact management. DMR benchmark 94.8%. They explicitly argue tool-call-based memory is limited by "unknown unknowns" and remove agent decision entirely via pre-assembled context injection.

**Letta/MemGPT**: Self-editing memory with editable blocks pinned to system prompt. OS-inspired three-tier model (core/recall/archival). "Sleep-time compute" for async consolidation.

**Mem0**: Four-operation update cycle: ADD, UPDATE, DELETE, NOOP. LongMemEval score: 49.0%.

**Hindsight**: retain/recall/reflect operations. Entity resolution + cross-encoder reranking. PostgreSQL + pgvector single Docker.

**Key lessons**: Keep tool set minimal (3-5). Use hybrid retrieval (vector + graph). Store raw episodes as ground truth. Extract structured facts async. Adopt bi-temporal model. Don't rely solely on agent judgment.

---

## Storage Architecture: Progressive Recall

Five depth layers:
1. Community summaries (highest abstraction)
2. Entity summaries (project/concept level)
3. Atomic facts (structured propositions)
4. Episode summaries (interaction-level)
5. Raw episodes (full verbatim content)

Storage cadence: topic-boundary driven, not turn-count. Store on every decision, correction, failed approach. "Decided PostgreSQL because MongoDB lacked ACID for financial data, discovered after initial prototype failed" — not just "decided PostgreSQL".

---

## Implementation Checklist (Priority Order)

1. Deploy JARVIS MCP server — Streamable HTTP, 4 tools
2. Storage backend — PostgreSQL + pgvector
3. MCP instructions field template
4. Bootstrap response — initialize_memory returns memories + behavioral priming
5. Claude Code hooks — SessionStart + Stop + PreCompact + PostCompact
6. CLAUDE.md template
7. Measure baseline capture rate (1 week)
8. Add hookless client support — Claude.ai Custom Connector (OAuth)
9. Tune instructions based on metrics
10. Add Codex CLI and Cursor hooks
11. Add Windsurf passive capture
12. Async extraction pipeline — raw episodes → entities + facts + relations
13. Progressive recall with depth parameter
14. ChatGPT Desktop support
15. Cross-provider verification — store in Claude, recall in ChatGPT

---

## The Non-Coding-Agent Path

Claude Desktop, Claude.ai Web, ChatGPT Desktop: no hooks, no CLAUDE.md, limited injection.

Realistic ceiling: **40-70%** with good instruction design. Bootstrap pattern (initialize_memory response with behavioral priming) is strongest mechanism. User-initiated storage ("remember this") closes the gap — document as expected interaction pattern, not failure mode.

Available injection per client:
- Claude Desktop/Web: MCP instructions + Project instructions (Team/Enterprise)
- ChatGPT Desktop: Custom Instructions (4,500 chars) — MCP instructions NOT supported
- Gemini: MCP via CLI only, not desktop/web
