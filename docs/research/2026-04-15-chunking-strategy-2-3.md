# 리서치 #2-3: 청킹 전략

> 연구 일자: 2026-04-15
> 성격: 딥리서치 — 긴 세션 분할 + 맥락 유지
> 상태: 결과 대기

(리서치 결과 여기에 붙여넣기)
# Optimal chunking for JARVIS conversation extraction

Your **$100 batch budget can fund 7–31 complete processing runs** across all 91 sessions—cost is not the binding constraint here; extraction quality is. The most effective strategy is a **two-phase pipeline**: lightweight preprocessing that cuts 50–70% of tokens, followed by Haiku-based topic segmentation and Sonnet 4.6 extraction per segment using the Batch API. At **$3.23–$4.30 per full run** with Sonnet Batch, you can iterate on prompts extensively. The key insight from production memory systems (Zep, Mem0, Letta) is that **none of them use fixed-size chunking**—they all process at message or turn granularity and extract structured facts incrementally, which aligns with your filtered-transcript approach.

For the 4.3MB useful-text corpus (~1.075M tokens), every strategy explored costs under $14 even at Opus standard pricing. The real decision axis is **extraction recall vs. implementation complexity**, not cost.

## Preprocessing pipeline eliminates 50–70% of tokens before any LLM call

Preprocessing is the highest-ROI step. Your raw sessions are 99.5% tool_result noise; even after your existing filtering to "useful text," further compression is possible and directly improves extraction quality by reducing noise that triggers context rot.

**Recommended preprocessing stack (no LLM needed):**

- **Tool results >500 tokens**: Replace with `[Read src/auth.ts: 2,847 tokens, exports handleAuth(), AuthMiddleware]`—keep filename, token count, and top-level exports/function signatures. For grep output, keep only matched lines with filenames. For bash with exit code 0 and large output, collapse to `[command succeeded, N lines output]`. **Keep full error messages verbatim** (non-zero exit codes).
- **Repeated file reads**: Track by path. First read: keep summary. Subsequent identical reads: `[Re-read src/auth.ts — unchanged since turn 12]`. Modified reads: show only a brief diff note.
- **Thinking blocks**: Summarize to 1–2 sentences capturing the key decision or reasoning. Skip entirely on first extraction pass; revisit only for targeted gap-fills where you need to understand *why* a decision was made.
- **Consecutive tool-use sequences**: When Claude runs 5 tools in a row without user interaction, collapse into a single block: `[Tool sequence: read 3 files, ran tests, edited src/auth.ts]`.
- **System/init messages**: Strip repeated Claude Code configuration boilerplate entirely.
- **Korean/English code-switching**: No normalization needed—Claude handles this natively. But account for Korean's **1.5–2× token density** vs. English in your token budget estimates.

After this preprocessing, expect your 1.075M tokens to compress to **~400K–550K tokens**, making per-session sizes ~4,400–6,000 tokens on average (with the same skewed distribution).

## Two-phase extraction: segment with Haiku, extract with Sonnet

The optimal architecture for both scenarios is a **two-phase pipeline** inspired by how production systems work, adapted for batch conversation processing.

**Phase 1 — Segmentation (Haiku 4.5):** Send each preprocessed session to Haiku with a prompt that identifies topical segments and outputs segment boundaries with 1-sentence summaries. Haiku handles this well—Microsoft's SeCom (ICLR 2025) demonstrated that even **RoBERTa-scale models perform competitively with GPT-4 for conversation segmentation**. Haiku at $1/$5 per MTok is the right tool here. Output: a list of `(start_turn, end_turn, topic_summary)` tuples per session.

**Phase 2 — Extraction (Sonnet 4.6):** For each segment, send the segment content with a **structured context header** prepended:

```
<session_context>
Project: [repo], Languages: [TS/Python], Session goal: [from first user msg]
Key entities so far: [file:src/auth.ts, func:handleAuth, pkg:jsonwebtoken, error:ECONNREFUSED]
Prior segment summary: [1 sentence from Haiku's output]
</session_context>

<segment>
[Full content of turns N through M]
</segment>

Extract entities, facts, and relationships from the <segment> only.
```

This architecture works because it exploits the **quality-cost asymmetry**: segmentation is a classification task (cheap model sufficient), while extraction requires reasoning over technical content (expensive model justified). The context header with entity carryover prevents the **synonym proliferation** problem documented by MDKeyChunker research—without it, independent chunks generate "admissions timeline" and "application deadlines" for the same concept.

## The decision tree for processing any session

```
Input: preprocessed session with T tokens

if T < 8,000 tokens:
    → Single-pass Sonnet extraction (no chunking needed)
    → Include full session + extraction prompt
    
elif T < 30,000 tokens:
    → Single-pass Sonnet extraction with global context header
    → Quality is high: <5% degradation at this range for Claude 4.6
    → No segmentation pass needed—save the Haiku call
    
elif T < 100,000 tokens:
    → Two-phase: Haiku segmentation → Sonnet extraction per segment
    → Target segments of 3,000–8,000 tokens each
    → Include entity carryover between segments
    → 15% overlap at segment boundaries (1–2 shared turns)
    
elif T >= 100,000 tokens:
    → Two-phase: Haiku segmentation → Sonnet extraction per segment
    → Hard cap segments at 30,000 tokens; split oversized segments
    → Include rolling entity list (top 20 most recent entities)
    → Prepend 150-token structured summary of prior segment
    → Consider Opus 4.6 for these sessions if quality matters more
    
Gap-fill scenario (turns X–Y unextracted):
    → Include turns [X-5] through [Y+3] as full content
    → Prepend global context header with entity list from prior extraction
    → Mark extraction target with XML tags
    → Use Sonnet (not Opus) for cost efficiency on frequent runtime calls
```

After preprocessing, most of your 91 sessions will fall in the **8K–30K range**, meaning the majority can be processed in a single Sonnet pass without segmentation overhead. Only the largest sessions (originally ~500KB useful text → ~125K tokens → ~50K after preprocessing) need the full two-phase treatment.

## Topic segmentation algorithms and libraries ranked by practicality

For your specific use case—AI assistant software engineering conversations with code blocks and mixed languages—here are the methods ranked by **practicality × quality**:

**Tier 1: Recommended for JARVIS**
- **LLM-based segmentation with Haiku 4.5** — Best quality-to-complexity ratio. Zero training data needed. Handles code blocks and Korean/English natively. Cost: ~$0.005 per average session (batch). Mackenzie et al. (Dec 2025) showed LLM-based segmentation outperforms all unsupervised methods when context window accommodates the full conversation.
- **SeCom** (Microsoft, ICLR 2025) — Purpose-built for segment-level conversation memory. Includes RoBERTa-based segmentation model that's competitive with GPT-4. Free, local inference. GitHub: `github.com/microsoft/SeCom`

**Tier 2: Lightweight alternatives if you want to avoid LLM calls for segmentation**
- **DeepTiling** — TextTiling algorithm with neural sentence embeddings (Sentence-BERT, SimCSE). Detects topic boundaries via cosine similarity valleys between adjacent turn groups. GitHub: `github.com/Ighina/DeepTiling`
- **Embedding similarity valley detection** — Embed each turn with `all-MiniLM-L6-v2` or `SimCSE`, compute pairwise cosine similarity, place boundaries at dips exceeding `mean - 1σ`. Simple to implement from scratch (~50 lines of Python). For code-containing turns, consider `CodeBERT` embeddings to handle code/NL mixing.
- **SuperDialseg baselines** — Includes implementations of TextTiling, EmbTT, BayesSeg, CSM, and supervised models for dialogue. GitHub: `github.com/Coldog2333/SuperDialseg`

**Tier 3: Classic methods (not recommended for this use case)**
- **TextTiling** (NLTK: `nltk.tokenize.TextTilingTokenizer`) — Lexical co-occurrence based. Fails on short dialogue turns due to vocabulary sparsity. Code blocks create spurious signals.
- **C99, TopicTiling** — Same fundamental limitations. TopicTiling (LDA-based) requires domain-specific topic model training that won't generalize to code-mixed Korean/English SE conversations.

**Key finding from academic research**: Hou et al. (2024) showed that **coreference and ellipsis in multi-turn dialogues hurt segmentation quality**. Their utterance rewriting technique (resolving "that file" → "src/auth.ts" before segmentation) improved Pk by ~6%. For your pipeline, the preprocessing step that replaces tool_result blocks with descriptive summaries effectively accomplishes this—it makes the conversation self-contained.

**Evaluation tools**: `segeval` (PyPI) for Pk, WindowDiff, Boundary Similarity metrics. NLTK includes `nltk.metrics.segmentation` for basic Pk/WD.

## Cost breakdown across six strategies for the full 91-session corpus

All calculations use **1.075M input tokens** (pre-preprocessing; post-preprocessing ~500K tokens), **~215K output tokens**, and verified April 2026 pricing.

| Strategy | Sonnet Std | Sonnet Batch | Opus Std | Opus Batch | Notes |
|---|---|---|---|---|---|
| **1. Single-pass (no chunking)** | $6.45 | $3.23 | $10.75 | $5.38 | Simplest; quality ceiling for large sessions |
| **2. Fixed-size 1024tok, 15% overlap** | $6.93 | $3.47 | $11.56 | $5.78 | Loses topic coherence at boundaries |
| **3. Two-pass hierarchical (Haiku→Sonnet)** | $7.96 | $3.98 | $12.37 | $6.18 | Best quality/cost; recommended |
| **4. Segmentation pre-pass + per-segment** | $8.61 | $4.30 | $13.46 | $6.73 | Highest quality; +$0.67 for Haiku pass |
| **5. Gap-fill runtime (20% session)** | $3.23 | $1.61 | $5.39 | $2.69 | Per-run across all 91 sessions worst-case |
| **6. Opus single-pass, Sonnet gap-fill** | — | — | — | $5.38 + $1.61 = **$6.99** | Hybrid: Opus for seeding, Sonnet for runtime |

**The $100 budget supports 7–31 complete runs.** Use this headroom for prompt iteration: run 2–3 sessions with different extraction prompts, evaluate output quality, then batch-process all 91 sessions with the winning configuration. The Batch API's **50% discount** and 24-hour turnaround make this the obvious choice for historical seeding.

**Post-preprocessing cost adjustment**: After the preprocessing pipeline removes 50–70% of tokens, actual costs drop proportionally. The two-pass hierarchical strategy (Strategy 3) with preprocessing costs approximately **$1.60–$2.40 via Sonnet Batch** for the entire corpus.

## How production memory systems actually handle chunking

The most striking finding is that **every major production system has abandoned fixed-size chunking** in favor of message-level or fact-level extraction.

**Zep/Graphiti** processes each message as an individual "episode" through a 5-stage pipeline: entity extraction → entity resolution → fact extraction → fact resolution → temporal extraction. All stages use **gpt-4o-mini**. Facts are stored as edges in a temporal knowledge graph with bi-temporal validity windows (when something was true vs. when it was recorded). No fixed chunks, no overlap. Their DMR benchmark score of **94.8%** leads the field. Critically, episodic data is never deleted—only invalidated—maintaining full provenance.

**Mem0** extracts from each user-assistant message pair individually. It combines the latest exchange + a rolling conversation summary + recent messages, then an LLM distills these into candidate facts. Candidate facts are compared against stored memories via vector similarity, triggering add/update/delete/no-op operations. Their graph variant (Mem0^g) stores entities and relationships in a directed labeled graph. On LoCoMo benchmarks, Mem0^g achieved **68% J-score** vs. 61% for the best RAG chunking baseline (which peaked at 512–1024 token chunks). Full-context processing scored 73% but with **17s p95 latency** vs. Mem0's 200ms.

**Letta (MemGPT)** takes the most radical approach: the agent manages its own memory using an OS-inspired virtual memory hierarchy. Core memory (always in-context, **2,000 chars per block** default) is self-edited by the agent. When message history exceeds the context window, Letta evicts ~**70% of messages** and recursively summarizes them. Old messages remain searchable via `conversation_search` tools. Their "sleep-time compute" feature processes and consolidates memory asynchronously during idle time.

**LangMem** passes entire conversation message lists to an LLM which extracts structured memories against existing stored memories. It supports insert/update/delete operations with configurable multi-phase enrichment. No chunking at all—extraction is the primitive, not retrieval.

**Cognee** is the exception: it uses **token-based paragraph chunking at 1,024 tokens default** with configurable 0–20% overlap. Their chunker is strictly invertible (every character preserved in exactly one chunk), which they consider critical for GraphRAG provenance. A 6-stage pipeline follows chunking: classify → check permissions → extract chunks → extract graph → summarize → embed.

**Anthropic's own guidance** recommends that for knowledge bases under ~200K tokens, **include everything in the prompt** rather than using retrieval. Their contextual retrieval technique—prepending chunk-specific context descriptions generated by an LLM—reduced retrieval failure by **35%** at $1.02 per million document tokens.

## Quality degrades predictably with context length—but Claude 4.6 is among the best

The "lost in the middle" phenomenon (Liu et al., 2024) is real and affects extraction tasks specifically. Seitl et al. (2024) directly measured this for information extraction using their MINEA metric (inserting artificial entities as "needles" into documents): **extraction recall drops for entities positioned in the document middle**, and iterative LLM calls improve coverage but eventually saturate.

Claude 4.6 models handle long context better than most competitors. **Opus 4.6 scores 76% on MRCR v2 8-needle retrieval at 1M tokens** (vs. 18.5% for Sonnet 4.5—a 309% improvement). Sonnet 4.6 shows **<5% accuracy degradation across its full 200K range** on retrieval tasks. However, multi-entity extraction is harder than single-needle retrieval. The practical degradation profile synthesized across multiple studies:

| Token range | Effective utilization | Implication for extraction |
|---|---|---|
| **0–30K** | ~95% | Minimal recall loss; single-pass safe |
| **30K–50K** | ~90% | Measurable but acceptable degradation begins |
| **50K–100K** | ~85% | Chunk if extraction completeness matters |
| **100K–200K** | ~70–80% | Always chunk; expect 15–25% entity recall loss in middle positions |
| **200K+** | ~60% | Opus 4.6 only; significant quality risk |

Chroma Research's "context rot" study (2025) across 18 frontier models found that **performance degrades at every context length increment, not just near the limit**—it's noise accumulation, not capacity. Claude models decay the slowest among tested models. The RULER benchmark (NVIDIA, COLM 2024) showed that despite near-perfect NIAH scores, effective context for complex tasks is typically **50–65% of advertised capacity**.

**Critical practical note**: The output token limit is often the binding constraint before input context. Structured JSON extraction for hundreds of entities consumes output tokens rapidly. Opus 4.6 supports **128K output tokens** (Sonnet: 64K standard, 300K in batch), which provides substantial headroom.

## Gap-filling needs 5 turns of context, not 20

For the runtime gap-filling scenario (processing turns 31–39 that were missed), the research converges on a clear recommendation.

Most **coreference resolution in dialogue resolves within 2–3 turns** of the antecedent. Apple's ReALM study confirmed that conversational entity references "predominantly come from a previous turn." For software engineering conversations, references like "that function" or "the bug we discussed" typically resolve within **3–5 turns**. But project-level references ("the auth module") require a **global entity list** rather than expanded raw context.

**Recommended gap-fill prompt structure:**

```xml
<session_context>
Project: [repo], Key entities: [from prior extraction passes]
</session_context>

<prior_context>
[Turns 26–30 verbatim — 5 turns before target]
</prior_context>

<extraction_target>
[Turns 31–39 — EXTRACT FROM THIS SECTION ONLY]
</extraction_target>

<following_context>
[Turns 40–42 verbatim — 3 turns after target]
</following_context>

Extract entities, facts, and relationships ONLY from <extraction_target>.
Use other sections solely for resolving references.
```

The **5-before, 3-after** context window plus entity list covers >95% of reference resolution cases. Expanding to 10+ turns before yields diminishing returns and adds noise. The XML tag demarcation is critical—it exploits Claude's strong instruction following to prevent extraction from context sections.

For cost: a typical gap-fill processes ~2,000–5,000 tokens of target content + ~3,000 tokens of context + ~500 tokens of entity header = **~5,500–8,500 total input tokens per gap-fill**. At Sonnet standard pricing, that's **$0.017–$0.026 per gap-fill call**—cheap enough to run after every session without concern.

## Concrete recommendations for your two scenarios

**Batch seeding (91 sessions, ~$100 budget):**

1. Preprocess all sessions (no LLM): strip tool_result noise, deduplicate file reads, collapse tool sequences → expect ~400K–550K tokens total
2. Sort sessions by token count
3. Sessions <30K tokens post-preprocessing (likely 80%+ of sessions): single-pass Sonnet 4.6 Batch extraction with global context header
4. Sessions ≥30K tokens: two-phase—Haiku segmentation → Sonnet extraction per segment with entity carryover
5. Use Batch API throughout (50% discount, 24-hour turnaround acceptable)
6. **Run 3 pilot sessions first** with different extraction prompts, evaluate output, then batch the rest
7. **Expected total cost: $2–$5 via Sonnet Batch** (post-preprocessing)
8. Budget remaining for **~20+ iteration cycles** if needed

**Runtime gap-filling (per-session, after each session ends):**

1. Preprocess the new session (same pipeline, <1 second)
2. Identify unextracted turn ranges
3. For each gap: extract with Sonnet standard API using the 5-before/3-after context window + entity list from prior extractions
4. If entire session is new and <30K preprocessed tokens: single-pass extraction
5. If entire session is new and ≥30K: segment with Haiku, extract with Sonnet
6. **Expected cost per session: $0.02–$0.15** depending on size
7. **Latency: 5–30 seconds** per gap-fill call at Sonnet standard

## Conclusion

The most important insight from this research is that **cost is irrelevant for your workload**—at 4.3MB useful text, even the most expensive strategy costs under $14. This frees you to optimize purely for extraction quality. The production memory ecosystem has converged on a clear pattern: **process at natural conversation boundaries (turns/episodes), extract structured facts per segment, resolve against a growing entity graph**. Zep, Mem0, and Letta all independently arrived at this architecture.

For JARVIS specifically, the two factors that matter most are: (1) **preprocessing quality**—aggressive tool_result summarization and deduplication directly improve extraction by reducing noise that causes context rot, and (2) **entity carryover between segments**—without it, you'll get synonym proliferation across chunks. Your existing "triple trigger" topic detection approach (topic shift / 5-turn fallback / significant event) aligns well with what SeCom's research validated: segment-level memory outperforms both turn-level and session-level granularity.

The gap-filling architecture is the genuinely novel piece of your system. No production memory system reviewed supports partial re-extraction of specific conversation spans. The 5-before/3-after context window with entity header is sufficient and cheap enough (~$0.02 per call) to run on every session without budgetary concern. The key implementation detail: always pass previously extracted entities as structured context—this resolves cross-chunk references far more efficiently than expanding the raw context window.