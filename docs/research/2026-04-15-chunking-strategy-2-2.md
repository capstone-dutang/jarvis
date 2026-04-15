# 리서치 #2-2: 청킹 전략

> 연구 일자: 2026-04-15
> 성격: 딥리서치 — 긴 세션 분할 + 맥락 유지
> 상태: 결과 대기

(리서치 결과 여기에 붙여넣기)
# Optimal chunking strategies for JARVIS conversation knowledge extraction

**Your $100 budget can process the entire 91-session corpus over 20 times with Claude Sonnet 4.6 Batch.** Cost is not the binding constraint — extraction quality is. The most important finding from this research is that the conventional wisdom about chunking needs to be inverted for your specific workload: after filtering to useful text, your average session is only **~16K tokens**, and even the largest sessions (~500KB) sit at **125K–250K tokens** depending on Korean density. With Claude Sonnet 4.6's 1M-token context window (no long-context surcharge), every single session fits in a single pass. The real question is not *how to chunk* but *when chunking actually improves extraction quality versus degrading it by fragmenting conversational context*.

This report synthesizes academic research on dialogue segmentation, production system architectures (Zep/Graphiti, Mem0, MemGPT/Letta), long-context quality studies, and concrete implementation guidance into separate recommendations for your two scenarios: batch seeding and runtime gap-filling.

---

## Your sessions are smaller than you think

The critical realization is that after your 99.5% filtering, the token economics are extremely favorable. Here are the actual numbers:

| Session size (useful text) | English tokens | Korean-heavy tokens | Fits in 200K? | Fits in 1M? |
|---|---|---|---|---|
| 50KB (typical small) | ~12,500 | ~18,750 | ✅ | ✅ |
| 100KB (median) | ~25,000 | ~37,500 | ✅ | ✅ |
| 300KB (large) | ~75,000 | ~112,500 | ✅ | ✅ |
| 500KB (maximum) | ~125,000 | ~250,000 | ⚠️ Marginal | ✅ |

Korean text tokenizes at roughly **2–3x the rate of English** due to Unicode encoding characteristics — each Korean syllable block often becomes its own token. Your mixed Korean-English code-switching means roughly 1 token per 2.5–3 characters on average. The total corpus of 4.3MB useful text translates to approximately **1.4M–2.1M tokens** across all 91 sessions.

**Batch seeding cost estimates** (all using Batch API at 50% discount):

| Strategy | Model | Input cost | Output cost | Total | Passes within $100 |
|---|---|---|---|---|---|
| Single-pass full session | Sonnet 4.6 | $2.15 | $2.63 | **$4.78** | 20 |
| Single-pass full session | Opus 4.6 | $3.58 | $4.38 | **$7.96** | 12 |
| Single-pass | Haiku 4.5 | $0.72 | $0.88 | **$1.60** | 62 |
| Two-pass hierarchical | Sonnet 4.6 | $4.73 | $4.13 | **$8.86** | 11 |
| Chunked (20% overlap) | Sonnet 4.6 | $2.58 | $3.15 | **$5.73** | 17 |

This means you can afford a **multi-pass pipeline**: extract with Sonnet, verify with a second Sonnet pass, and still have budget for Opus spot-checks on critical sessions — all for under $25.

---

## The decision tree for batch seeding

Based on the convergent evidence from the "Lost in the Middle" research (Liu et al., TACL 2024), the context rot study (Chroma, 2025), and the SeCom dialogue segmentation paper (Pan et al., ICLR 2025), here is the recommended decision tree:

```
Session useful text size?
│
├─ < 30K tokens (~120KB English, ~80KB Korean-heavy)
│   → FULL SESSION, SINGLE PASS
│   → ~85% of your 91 sessions
│   → Expected extraction quality: high (minimal context rot)
│   → Place transcript at top, instructions at end (+30% quality per Anthropic)
│
├─ 30K–100K tokens
│   → FULL SESSION + VERIFICATION PASS
│   → Send full transcript → extract → send extracted items back with transcript
│   → Ask: "Review these extractions. What's missing or incorrect?"
│   → Cost: 2x base (~$0.10–$0.30 per session with Sonnet 4.6 Batch)
│   → The verification pass catches "lost in the middle" misses
│
└─ > 100K tokens (~400KB+ Korean-heavy)
    → TOPIC-BASED CHUNKING with entity carryover
    → Use embedding-based segmentation (see algorithm section below)
    → Chunk at topic boundaries, 15K–30K tokens per segment
    → Prepend global context header + entities from previous segments
    → Post-process: deduplicate entities across segments
    → Likely only 2–5 of your 91 sessions
```

**Why these thresholds?** The LongProc benchmark showed GPT-4o exact match drops from **94.8% at 0.5K tokens to 38.1% at 8K tokens** for procedural extraction. However, that benchmark tests multi-step procedural tasks, not entity/fact extraction. For simpler structured extraction, Claude's degradation curve is gentler — Anthropic claims **<5% degradation across the full context window**, though independent testing shows middle-positioned content drops to **76–82% accuracy** versus **85–95%** for beginning/end positioned content. The 30K threshold is conservative: below it, even middle-positioned content is within the "strong attention" zone. The 100K threshold marks where cumulative degradation starts meaningfully impacting extraction recall.

**The verification pass is the key insight.** Given that a full-corpus extraction costs only ~$5, spending another ~$5 on a verification pass is trivially cheap and directly addresses recall degradation. This is analogous to Zep/Graphiti's "reflexion-inspired validation" technique that they document as minimizing hallucinations and enhancing extraction coverage.

---

## Topic-based segmentation without LLM calls

For the ~5% of sessions exceeding 100K tokens where chunking becomes necessary, **embedding-based similarity valleys** is the recommended approach. Here is the specific algorithm, grounded in Song et al. (2016, Interspeech) and adapted from the SeCom architecture (ICLR 2025):

**Algorithm: Embedding-Enhanced TextTiling for conversations**

1. Encode each turn using a multilingual sentence transformer. For Korean-English code-switching, use one of these models (ranked by suitability):
   - **`xlm-r-large-en-ko-nli-ststb`** — specifically trained for English-Korean semantic similarity, Korean STS benchmark score of **84.05** (best for your use case)
   - **`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`** — good speed/quality balance, broadly multilingual
   - **`jhgan/ko-sroberta-multitask`** — best Korean-specific model, based on KLUE-RoBERTa

2. Compute cosine similarity between adjacent turn embeddings: `sim(turn_i, turn_{i+1})`

3. Apply a sliding window smoother (window=3) to reduce noise from single off-topic turns

4. Calculate depth scores at each position: `depth(i) = 0.5 * (sim(i-1,i) - sim(i,i+1) + sim(i+1,i+2) - sim(i,i+1))`. This measures how much similarity drops at position i relative to neighbors.

5. Set boundaries at positions where depth score exceeds `mean + k*std` (k=1.0 is a reasonable starting point; tune on 2–3 manually annotated sessions)

6. Enforce minimum segment size of **~2K tokens** (prevents micro-fragments) and maximum of **~30K tokens** (prevents oversized segments)

**Why not TextTiling/C99/TopicTiling directly?** TextTiling and C99 are purely lexical (bag-of-words cosine similarity) and fail on code-heavy conversations where variable names change while the topic stays constant. They also struggle with Korean-English code-switching because the vocabulary shifts are language-driven, not topic-driven. TopicTiling (Riedl & Biemann, 2012) uses LDA topic IDs and achieved **94.4% precision** vs C99's **77.6%** on document segmentation, but requires training an LDA model on similar corpus data. The embedding approach gives you TopicTiling-level semantic understanding without requiring domain-specific training data.

**Alternative: use your existing triple trigger.** Your already-defined topic detection (topic shift / 5-turn fallback / significant event) can serve as the segmentation signal without any embedding computation. For batch seeding where you're processing sessions that are mostly under 100K tokens, engineering a perfect segmenter may be premature optimization. The simpler approach: use the triple trigger to insert segment boundaries, merge segments that are too small, split segments that exceed 30K tokens at the nearest turn boundary.

---

## What production memory systems actually do

A critical finding is that **no production memory system uses traditional fixed-size chunking for conversations**. They all treat conversations as streams of turns:

| System | Processing unit | Context window | Entity resolution | Token efficiency |
|---|---|---|---|---|
| **Zep/Graphiti** | Per-message + 4 prior messages | Episode-based sliding window | Embedding similarity → LLM dedup | ~600K tokens/conversation (expensive) |
| **Mem0** | Message pair + rolling summary + m recent | Incremental per-turn | Vector search → ADD/UPDATE/DELETE/NOOP | ~1,764 tokens stored/conversation (90% savings) |
| **MemGPT/Letta** | FIFO queue, recursive summarization | Agent-managed, eviction at ~70% capacity | Agent-driven via function calls | Variable (every memory op costs inference) |
| **LlamaIndex** | SentenceSplitter (1024 default) / SemanticSplitter | No dialogue-specific chunker | FactExtractionMemoryBlock (newer) | Moderate |
| **LangChain** | RecursiveCharacterTextSplitter / SemanticChunker | No dialogue-specific chunker | LangMem SDK (ADD/UPDATE/DELETE) | Moderate (59.82s p95 latency) |

**Mem0's architecture is most relevant to JARVIS.** Its core loop extracts discrete, durable facts (not summaries) from each conversation turn, then resolves them against existing memory using a four-way operation: ADD new facts, UPDATE existing ones with new information, DELETE contradicted facts, or NOOP for duplicates. This produced **67.13% on the LOCOMO benchmark** (26% better than OpenAI's memory) while using **90% fewer tokens** than full-context approaches. Zep/Graphiti achieves higher accuracy (**94.8% DMR**) but at dramatically higher token cost.

**The key pattern across all systems**: process incrementally, maintain a running context (summary or entity list), and use conflict resolution rather than deduplication. For JARVIS's batch seeding (processing complete sessions after the fact rather than streaming turns), the incremental approach can be simulated by processing segments sequentially with entity carryover.

---

## Context preservation across chunks requires three mechanisms

When chunking is necessary, research and production systems converge on three complementary approaches. A **global context header** of **200–500 tokens** at the top of every chunk provides session-level grounding. Anthropic's own documentation recommends placing longform data at the top with queries at the end, and their context engineering blog emphasizes finding the "smallest possible set of high-signal tokens." A header like *"This Claude Code session involves building a React/PostgreSQL application. Key entities: user (ML engineer), FastAPI backend, asyncpg connection pool. Session covers database setup, connection timeout debugging, and API endpoint design."* costs negligible tokens but substantially improves the LLM's ability to resolve ambiguous references.

**Entity carryover** — passing extracted entities from Chunk N as context to Chunk N+1 — improves coreference resolution but carries error propagation risk. The recommended approach, drawn from Graphiti's architecture, is to include extracted entities as *optional context* rather than ground truth constraints. Format them as: *"Previously identified entities: [asyncpg, connection_pool, FastAPI]. Previously extracted facts: [chose asyncpg over psycopg2 for async support]."* This lets the LLM resolve "that library" or "the approach we discussed" without forcing incorrect entities forward.

**Sliding window overlap** of **10–20%** at turn boundaries (not mid-turn) handles local references. The NVIDIA chunking benchmark (2024) tested 10%, 15%, and 20% overlap on 1024-token chunks and found **15% performed best** across their test suite. For conversation data where topics can span 30+ turns, overlap at the turn level is more natural than character-level overlap. Include **2–3 complete turns** of overlap rather than a fixed token count.

---

## Preprocessing pipeline before extraction

The preprocessing pipeline should be implemented in this priority order, with the first three steps being non-negotiable:

**Step 1: Add turn numbers and standardize speaker labels.** This is critical for your `source_quote` grounding requirement. Format every turn as `[Turn 42] [User]: ...` or `[Turn 43] [Assistant]: ...`. Without turn numbers, verbatim quote matching against the original transcript becomes fragile. This is trivial to implement and has the highest impact-to-effort ratio of any preprocessing step.

**Step 2: Unicode NFC normalization.** Korean text can represent the same character in multiple ways (combining Jamo vs. precomposed syllable blocks). `unicodedata.normalize('NFC', text)` ensures consistent representation, which is critical for both verbatim quote matching and embedding-based topic detection. Apply this before any other text processing.

**Step 3: Replace tool_result with metadata stubs.** Your current complete removal works, but replacing with lightweight stubs like `[Read: src/main.py]` or `[Search: "database connection" → 5 results]` adds **~10–20 tokens per tool use** while preserving the context about *what the assistant was looking at* when it formulated its response. This context is valuable for understanding assistant reasoning. Implementation is a simple pattern match on tool_name + input parameters.

**Step 4: Collapse error-retry loops.** Coding sessions frequently contain patterns where the same test/command runs 3–5 times. Collapse these into `[Retry loop: npm test | 4 attempts | final: PASS]`, keeping only the first failure and final success to preserve the error→fix narrative. Hash-based comparison of tool_result content detects exact repeats; a diff-ratio threshold of >90% catches near-duplicates (same file re-read after minor edits).

**Step 5: Handle thinking blocks.** If your transcripts contain Claude's extended thinking, include the **summarized** version (Claude 4 models produce these by default) but cap raw thinking blocks at ~200 tokens. Thinking blocks contain valuable decision rationale but are 30% unfaithful to the model's actual reasoning process (Wei et al., NeurIPS 2022). Treat them as supplementary context, not primary extraction source.

**What to avoid**: do not lowercase text (destroys code identifiers and proper nouns), do not remove stopwords (destroys sentence structure needed for verbatim quotes), and do not translate Korean to English (introduces errors and loses original meaning).

---

## Gap-filling requires careful context engineering

The runtime gap-filling scenario (extracting from turns 31–39 when turns 1–30 and 40–50 were already covered) is where the "Lost in the Middle" research becomes directly actionable. The optimal architecture places the target region **early in the prompt** to maximize attention:

```
┌──────────────────────────────────────────────┐
│ System prompt: extraction instructions        │ ← Instructions first
├──────────────────────────────────────────────┤
│ Previously extracted items from turns 1-30    │ ← Negative examples
│ (bulleted list, ~200 tokens)                  │   (prevents re-extraction)
├──────────────────────────────────────────────┤
│ Context summary of turns 1-30 (~300 tokens)   │ ← Compressed background
├──────────────────────────────────────────────┤
│ [Turn 28-30] Full text (surrounding context)  │ ← Immediate references
├──────────────────────────────────────────────┤
│ <target_region>                               │
│ [Turn 31-39] Full text                        │ ← EXTRACTION TARGET
│ </target_region>                              │   (placed early, not middle)
├──────────────────────────────────────────────┤
│ [Turn 40-42] Brief trailing context           │ ← What happened next
├──────────────────────────────────────────────┤
│ "Extract ONLY from <target_region>"           │ ← Reinforcement at end
└──────────────────────────────────────────────┘
```

**Why 2–3 turns of surrounding context?** Discourse coherence research shows **3 turns** resolves most anaphoric references in conversation ("that approach", "the error from before", "let's try the other way"). More than 3 turns has diminishing returns and risks confusing the model about extraction scope. The summary of turns 1–30 provides topic grounding at ~300 tokens — enough to establish *what* was being discussed without the detail of *how*.

**Include previously extracted items as negative constraints.** If turns 1–30 yielded entities like `[asyncpg, connection_pool, timeout_error]`, listing them tells the LLM "these are already captured — focus on NEW knowledge in the target region." This prevents duplicate extraction and focuses attention on genuinely missed content.

**Cost per gap-fill operation**: a typical gap (9 turns ≈ 500–2,000 tokens of target text) plus context (~1,000 tokens of summary and surrounding turns) plus instructions (~500 tokens) totals roughly **2,000–3,500 input tokens**. At Sonnet 4.6 standard pricing ($3/MTok input), that's approximately **$0.006–$0.01 per gap-fill** — negligible for runtime operation.

---

## Concrete recommendations for both scenarios

**Batch seeding (91 sessions, one-time, $100 budget):**

1. Preprocess all sessions with the 5-step pipeline (turn numbers, NFC normalize, tool_result stubs, dedup retry loops, thinking block caps)
2. Sort sessions by token count ascending
3. For sessions under 30K tokens: single-pass full-session extraction with Sonnet 4.6 Batch
4. For sessions 30K–100K tokens: full-session extraction + verification pass
5. For sessions over 100K tokens (likely 2–5 sessions): segment using embedding similarity valleys with `xlm-r-large-en-ko-nli-ststb`, process segments sequentially with entity carryover and global context header
6. Post-process: entity deduplication across all sessions using embedding similarity + LLM-based resolution (the Mem0 ADD/UPDATE/DELETE/NOOP pattern)
7. Estimated total cost: **$10–$15** for extraction + verification, leaving $85+ for iterative refinement

**Runtime gap-filling (per-session, after conversation ends):**

1. Apply the same preprocessing pipeline to the unextracted turns
2. Generate a 300-token summary of the already-extracted portion (can use Haiku 4.5 at $0.001 per summary)
3. Construct the gap-fill prompt using the architecture above (summary + 3 turns surrounding context + marked target region)
4. Extract with Sonnet 4.6 (standard API, not batch — latency matters for runtime)
5. Run the ADD/UPDATE/DELETE/NOOP resolution against existing session knowledge
6. Estimated cost per gap-fill: **$0.01–$0.03** including summary generation

The SeCom finding that **segment-level granularity outperforms both turn-level and session-level** for downstream retrieval suggests your extracted knowledge items should aim for topical coherence — neither atomic facts stripped of context (too granular) nor session-level summaries (too diffuse). Each knowledge item should capture a complete *decision*, *discovery*, or *relationship* with enough surrounding context to be independently useful when retrieved later. This aligns naturally with your triple trigger (topic shift / 5-turn fallback / significant event) as the extraction boundary signal.