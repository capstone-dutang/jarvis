# Building a Cloud Context Server: deep research across 8 critical domains

> 연구 일자: 2026-03-26
> 성격: 초기 아키텍처 리서치 — 8개 도메인 종합
> 상태: 참조 (이후 리서치로 일부 대체됨)

**A Cloud Context Server — where AI clients push conversation transcripts and structured hints via MCP to a server that stores raw text, validates hints, generates embeddings, and connects relationships — is architecturally feasible today, but no existing system does exactly this.** The closest analog is Zep's retrieval path, which operates without an LLM. Every major memory system (Zep, LangMem, Letta, OpenAI Memory) requires an LLM for memory *formation*, which is precisely what our design offloads to the AI client. This architectural inversion — making the client responsible for knowledge extraction and the server responsible only for validation, embedding, and retrieval — is the defining innovation and the source of both its advantages and its challenges.

This report synthesizes research across memory architectures, knowledge extraction, MCP protocol design, data normalization, temporal modeling, hybrid search, infrastructure costs, and failure modes to provide actionable guidance for building this system.

---

## 1. Existing memory systems all depend on LLMs — but Zep's retrieval path shows the way

Four major AI memory systems were analyzed in depth: **Zep** (cloud service + open-source Graphiti engine), **LangMem** (LangChain SDK), **Letta/MemGPT** (server platform), and **OpenAI Memory** (proprietary ChatGPT feature). The critical finding is that **none operates fully without an LLM**, but they vary dramatically in where and how much LLM involvement is required.

**Zep** uses the most sophisticated data model — a three-tier temporal knowledge graph with episodic, semantic entity, and community subgraphs. It tracks four timestamps per fact using a bitemporal model (`t_created`, `t_expired`, `t_valid`, `t_invalid`). Crucially, **Zep's retrieval path requires zero LLM calls** — it uses pre-computed embeddings (BGE-m3), BM25 full-text search, and breadth-first graph traversal, merged via Reciprocal Rank Fusion, achieving sub-**200ms P95 latency**. However, its ingestion pipeline depends heavily on an LLM (defaulting to `gpt-4o-mini`) for entity extraction, fact extraction, entity resolution, and edge deduplication.

**Letta/MemGPT** takes the most LLM-dependent approach — the LLM *is* the memory manager, deciding what to store and retrieve through tool calls at every step. Its OS-inspired two-tier memory model (core memory as "RAM," archival memory as "disk") is elegant but consumes enormous token budgets. **LangMem** is a lightweight Python SDK requiring LLM calls for every operation (extraction, consolidation, search). **OpenAI Memory** provides no developer API whatsoever — it is a closed consumer feature with poor temporal reasoning performance in benchmarks.

| System | LLM for write | LLM for read | Data model | Retrieval latency |
|--------|--------------|-------------|-----------|------------------|
| **Zep** | Yes (async) | **No** | Temporal knowledge graph | <200ms |
| **LangMem** | Yes | Yes | Flat namespaced memories | LLM-dependent |
| **Letta** | Yes (every step) | Yes (every step) | Two-tier (core + archival) | High |
| **OpenAI** | Yes | Yes | Opaque | Unknown |

### 우리 설계에 적용할 수 있는 구체적 시사점

Our design inverts the pattern: the **AI client** (which already has an LLM) performs the extraction that Zep's ingestion pipeline does, then sends structured hints to the server. This eliminates the server's need for an LLM entirely, but we must validate those hints rigorously since we can't independently verify them with our own LLM.

Adopt Zep's **retrieval architecture** as the primary blueprint: pre-computed embeddings + BM25 + graph traversal, merged via RRF. Adopt its **bitemporal data model** (4 timestamps per fact) for tracking knowledge evolution. Adopt its **episodic layer** that preserves raw conversation text alongside extracted knowledge — this is our insurance policy when structured hints are wrong. Do not adopt Letta's approach of putting memory decisions in the hot path — our server should process everything asynchronously. Store the full conversation transcript as an "episode" (ground truth), validate and store structured hints as graph edges, and serve retrieval entirely from pre-computed indexes.

---

## 2. Knowledge extraction without an LLM is feasible — using a validate-and-normalize architecture

Since our server has no LLM, it cannot independently extract knowledge from conversations. Instead, the AI client sends pre-extracted "structured hints" (entities, relationships, facts, temporal markers), and the **server validates, normalizes, deduplicates, and stores** them. Research reveals a rich ecosystem of lightweight tools for server-side validation.

**For entity validation**, GLiNER (Zaratiana et al., NAACL 2024) is the standout tool: a **~205M parameter** model (DeBERTa-v3 backbone) that performs zero-shot NER by framing it as a matching task rather than generation. It outperforms ChatGPT on zero-shot NER benchmarks while running on CPU. A multilingual version (`urchade/gliner_multi-v2.1`) and Korean model (`taeminlee/gliner_ko`) are available. For lighter-weight validation, spaCy's `en_core_web_sm` (12MB) provides adequate NER at 3–5× the speed of alternatives.

**For entity resolution without an LLM**, research recommends a funnel-shaped approach: (1) exact string matching after normalization (lowercase, strip whitespace), (2) fuzzy matching via RapidFuzz (Jaro-Winkler, token sort ratio), (3) embedding similarity using sentence-transformers with cosine threshold >0.85, and (4) connected components clustering for transitive closure. A VLDB 2023 paper demonstrated that pre-trained sentence embeddings (GTR-T5) achieve strong unsupervised entity matching without any LLM fine-tuning.

**For temporal validation**, SUTime (Stanford CoreNLP) and HeidelTime are rule-based temporal taggers with zero ML dependencies that can independently verify the AI client's temporal extractions. HeidelTime supports 200+ languages including Korean and achieves **86% F1** on temporal extraction benchmarks.

**For triple/fact validation**, spaCy dependency parsing can extract (subject, predicate, object) patterns from text as a cross-check against client-submitted triples. Research by Vossen et al. (2024) found that dependency-based triple extraction achieves **51% precision** on complete triples from dialogue — imperfect, but sufficient for flagging suspicious client submissions.

### 우리 설계에 적용할 수 있는 구체적 시사점

Design the hint validation pipeline as a **multi-layer funnel**:

1. **Schema validation** (Pydantic): Reject malformed hints immediately — enforce type constraints, allowed entity types, relationship categories, confidence score ranges
2. **Entity resolution** (RapidFuzz + embedding similarity): Deduplicate entities against existing graph nodes before creating new ones
3. **Temporal re-verification** (SUTime/HeidelTime): Independently parse temporal expressions from the raw conversation text; flag discrepancies with client-submitted temporal hints
4. **Optional cross-check** (GLiNER): Run lightweight NER on the raw conversation text to spot-check whether client-extracted entities actually appear in the source text
5. **Confidence gating**: Store hints with confidence >0.8 directly; queue 0.5–0.8 for deferred review; reject <0.5

The total server-side model footprint for this pipeline is **~400MB–1GB**, all runnable on CPU. The key architectural insight is that **the server doesn't need to extract knowledge — it needs to verify that submitted knowledge is grounded in the source text**.

---

## 3. MCP server design requires careful tool descriptions and invocation control

The Model Context Protocol (MCP) uses **JSON-RPC 2.0** for all communication, with three core primitives: **Tools** (model-invoked functions), **Resources** (application-exposed read-only data via URIs), and **Prompts** (reusable message templates). The latest spec version is **2025-11-25**, which adds elicitation as a client feature.

For our Cloud Context Server, the primary transport should be **Streamable HTTP** (introduced 2025-03-26), which replaced the deprecated SSE transport. It uses a single HTTP endpoint, supports session management via `Mcp-Session-Id` headers, enables full HTTP authentication (Bearer tokens, OAuth 2.1), and handles both synchronous responses (`application/json`) and streaming (`text/event-stream`). For local development, **stdio** transport provides microsecond latency.

**Tool description design** is critical because it directly influences when and how the AI calls memory tools. Anthropic's official guidance emphasizes: lead with the action verb, keep descriptions to 1–2 sentences, include workflow context ("Call `retrieve_memory` at the start of every new session"), and use JSON Schema `strict: true` mode for reliable structured responses. Tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`) help the AI reason about safety and side effects.

**Controlling tool invocation frequency** is the biggest practical challenge. Research reveals that LLMs in retry loops can generate **1,000+ API calls per minute**. Mitigation strategies include: (1) explicit wording in tool descriptions ("Only call when new user information is present"), (2) server-side rate limiting (token bucket per agent/tool), (3) reducing total tool count to under 10 to prevent context overload, and (4) emerging MCP gateway layers (WSO2, Lunar MCPX) that provide centralized rate limiting and audit logging.

### 우리 설계에 적용할 수 있는 구체적 시사점

Design exactly **two primary tools** — `store_memory` and `recall_memory` — plus optionally `list_entities` and `delete_memory`. Keep the tool surface minimal. The `store_memory` tool should accept both the **raw conversation transcript** (ground truth) and **structured hints** (entities, relationships, temporal markers) in a single call. This ensures we always preserve raw text even if hints are empty or wrong.

Use Streamable HTTP transport for production with Bearer token authentication. Implement server-side rate limiting: max **3 store calls** and **5 recall calls per conversation session**. Include the `instructions` field in the MCP server's initialization response to guide AI behavior globally.

---

## 4. Normalizing multi-provider data demands a typed content-parts schema

Claude, OpenAI, and Gemini structure conversations in fundamentally different ways. **Claude** uses `content: ContentBlock[]` where tool calls, text, images, and thinking blocks are all typed elements in the same array. **OpenAI** uses `content: string` with tool calls in a separate `tool_calls` field — and critically, tool arguments are **JSON strings, not objects**. **Gemini** uses `parts: Part[]` (similar to Claude), names the assistant role `"model"` instead of `"assistant"`, and uses camelCase for function calls (`functionCall`, `functionResponse`). System prompts live in three different places: Claude's top-level `system` parameter, OpenAI's `role: "system"` message, and Gemini's `systemInstruction` parameter.

Eight critical edge cases to handle: (1) always `JSON.parse()` OpenAI tool arguments, (2) map Gemini `"model"` → `"assistant"`, (3) extract Claude tool calls from content array to separate `tool_calls` field, (4) map OpenAI `role: "tool"` to `role: "tool_result"`, (5) unify system prompt location, (6) generate synthetic tool call IDs for older Gemini models, (7) map Claude `thinking` blocks and OpenAI reasoning tokens to `type: "thinking"`, and (8) always store `content` as an array of typed parts — never a flat string.

---

## 5. Temporal knowledge management requires bitemporal modeling and event sourcing

Knowledge changes over time — users change jobs, preferences evolve, relationships form and dissolve. A robust temporal system must answer two distinct questions: "What was true at time T?" (valid time) and "What did we know at time T?" (transaction time).

The recommended schema uses `tstzrange` columns with GiST indexes for efficient temporal queries. Each fact carries four timestamps: `valid_from`/`valid_to` (when true in reality) and `recorded_at`/`superseded_at` (when the system learned/corrected it).

For **contradiction detection**, the simplest approach is rule-based: same subject + same predicate + different object = potential contradiction. The **supersede function** implements "latest-wins" by setting `superseded_at = now()` on the old fact and inserting the new one — never deleting history.

**Memory decay** follows an exponential model inspired by Ebbinghaus's forgetting curve: `relevance = 0.4 × recency + 0.3 × frequency + 0.3 × confidence`, where recency decays with a configurable half-life (default 90 days). A minimum floor of 0.01 prevents complete information loss.

---

## 6. Hybrid search with pgvector and PostgreSQL FTS delivers 84% precision through RRF fusion

Combining pgvector's semantic search with PostgreSQL's FTS via RRF is the proven production pattern. Research demonstrates that hybrid search improves precision from **~62% (pure vector) to ~84%**.

**HNSW** is the recommended index for pgvector in production: **95%+ recall** with default parameters, **40.5 QPS** vs IVFFlat's 2.6 QPS on 1M-vector benchmarks. Key parameters: `m = 16`, `ef_construction = 200`, `ef_search = 100`.

---

## 7. A $0/month architecture is viable on GCP's free tier with API-based embeddings

> NOTE: 이 섹션은 Oracle Cloud Always Free로 대체됨 (2026-03-31 결정)

The cheapest viable architecture runs on GCP's always-free tier: e2-micro (0.25 vCPU, 1GB RAM) with Gemini API free tier for embeddings. Total cost: $0/month.

---

## 8. The two highest-priority failure modes are tool non-invocation and long-conversation overflow

**P0**: AI failing to call the memory tool — mitigate with explicit instructions, rate limiting, post-session sweep
**P0**: Long conversations overflowing context — mitigate with incremental storage every 3-5 turns
**P1**: Hallucinated structured hints — multi-layer validation pipeline
**P1**: Memory conflicts — bitemporal supersede
**P1**: Embedding model drift — version pinning + monthly monitoring
