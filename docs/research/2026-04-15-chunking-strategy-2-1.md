# 리서치 #2-1: 청킹 전략

> 연구 일자: 2026-04-15
> 성격: 딥리서치 — 긴 세션 분할 + 맥락 유지
> 상태: 결과 대기

(리서치 결과 여기에 붙여넣기)
# Chunking strategies for structured extraction from AI conversation transcripts

**You probably don't need to chunk at all.** Claude Sonnet 4.6's 1M-token context window comfortably handles your largest session (~150K tokens), and batch-processing all 91 sessions costs roughly **$5.51** — leaving 94% of your $100 budget for multi-pass refinement, validation, and runtime reserves. The real optimization isn't chunking strategy; it's **preprocessing** (compressing tool_results yields 95%+ token reduction) and **mitigating the lost-in-the-middle effect** through prompt structure. That said, chunking becomes essential if you switch to cheaper models with smaller context windows, or if extraction quality degrades on your longest sessions — and the research provides clear guidance on exactly how to chunk when needed.

---

## Your sessions fit inside modern context windows — but length still hurts quality

The most consequential finding across this research is the tension between context window capacity and extraction accuracy. All 91 sessions (max ~150K tokens after preprocessing) fit within Claude Sonnet 4.6 (1M tokens), Claude Haiku 4.5 (200K tokens), and most fit within GPT-4o/4o-mini (128K tokens). Single-pass processing is architecturally feasible for your entire dataset.

However, **fitting inside the window does not guarantee high-quality extraction**. Du et al. (EMNLP 2025, arXiv:2510.05381) demonstrated that even with perfect retrieval — where models can recite all relevant tokens with 100% exact match — performance degrades **13.9%–85%** as input length increases. Below **15K tokens**, accuracy drops remain under 8.2%; beyond that threshold, degradation accelerates sharply. The original "Lost in the Middle" study (Liu et al., TACL 2024) found a **U-shaped attention curve**: accuracy is highest when relevant information appears at position 1 (~75%) or position 20 (~72%), dropping to ~55% at position 10 — a **20+ percentage point gap from position alone**.

Critically, this effect applies specifically to extraction tasks, not just QA. A 2024 study on information extraction with GPT-4-turbo (128K context) confirmed that "in the case of long documents whose extraction consumes almost the whole context window, LLMs give more inconsistent results" with a confirmed presence of lost-in-the-middle effects on entity extraction (arXiv:2404.04068). LongICLBench (2024) measured **20–22% F1 score declines** on entity recognition and relationship extraction as context grew. Chroma's "Context Rot" study (July 2025) tested 18 frontier models including Claude and GPT-4.1, finding **all 18 still exhibit context rot** with 20–50% accuracy drops from 10K to 100K tokens. Claude models decay the slowest but are not immune.

**The practical implication**: for sessions under ~50KB (~15K tokens), single-pass extraction will work excellently. For sessions approaching 500KB (~150K tokens), expect measurable quality degradation in the middle of the transcript. The decision to chunk should be driven by observed extraction quality, not context window limits.

---

## The preprocessing pipeline matters more than chunking

Since your raw transcripts are 99.5% tool_result/system metadata, the first and highest-impact optimization is **rule-based preprocessing** — no LLM calls needed, effectively free, and it transforms your extraction problem entirely.

A tiered tool_result compression strategy works best. For Read/Write file tools, replace content with the filename plus the first 3 lines showing structure. For Bash/command tools, keep the command itself, the first 5 lines of output, any stderr in full (error outputs drive decisions), and the exit status. For grep/search tools, reduce to `[Search: "pattern" in dir] → N matches in: file1.py, file2.py`. For file listings, use tool name only. This rule-based compression alone achieves **90–95% token reduction** on the tool_result portion.

The second preprocessing step is **exact deduplication of repeated file reads**. In Claude Code sessions, the same file is often read 3–5 times across a session as it's edited. Hash each tool_result content block; when duplicates appear, keep only the latest version with an annotation like `[file.py read 5 times, showing final version]`. This typically cuts another **10–30% of remaining tokens**. Together, these two steps — tool compression and dedup — reduce total tokens by roughly **95–97%** with zero information loss on the knowledge that matters.

**Thinking blocks** deserve special treatment. They contain unique deliberation content not always present in the final response — rejected approaches, decision rationale, uncertainty reasoning. For batch seeding, summarize them to 1–2 sentences per turn. For runtime gap-filling, remove them entirely to conserve tokens (the decisions they inform appear in the assistant's response). Never keep them in full; they are extremely verbose.

**Code blocks over ~20 lines** should be replaced with a summary: file path, language, first 3 lines, a 1-sentence description, and the last line. Short code blocks under 20 lines should stay inline — they often *are* the knowledge (config snippets, API calls, key algorithm choices). Code diffs and patches should always be kept in full, as they represent the actual changes being discussed. Long code blocks dilute attention and inflate token counts (a 200-line file consumes ~2,000 tokens — equivalent to 10–15 conversation turns) without proportionally increasing extractable knowledge density.

---

## Production memory systems don't use traditional chunking

A striking finding: **none of the major production AI memory systems use fixed-size text chunking** for conversation processing. They've converged on fundamentally different paradigms.

**Zep/Graphiti** processes conversations as discrete episodes — each message is an atomic unit. Per the Graphiti paper (arXiv:2501.13956), "conversational data streams are parsed and timestamped as episodes. No lossy transformation is performed at this layer, preserving the ground truth." Each episode triggers multiple LLM calls for entity extraction, relationship extraction, and a three-tier deduplication strategy (exact match → fuzzy similarity → LLM reasoning). The temporal knowledge graph uses a bi-temporal model tracking both when events occurred and when they were ingested. Ingestion is expensive (multiple LLM calls per episode); retrieval is cheap (no LLM calls, **~300ms P95 latency**). Graphiti uses GPT-4o-mini for graph construction and BGE-m3 for embeddings.

**Mem0** takes a fact-level extraction approach. It processes the latest user-assistant exchange combined with a rolling conversation summary and the **10 most recent messages** (their recommended M=10), extracting "a concise set of candidate memories." Each candidate is compared against the top 10 similar entries in the vector database, and the LLM performs one of four operations: ADD, UPDATE, DELETE, or NOOP. Token usage drops to **~1,800 tokens per conversation** versus 26,000 for full-context approaches — a 90% reduction. On the LOCOMO benchmark, Mem0 achieves 66.9% accuracy (26% relative improvement over OpenAI's 52.9%) with P95 total latency of 1.44s versus 17s for full-context.

**Letta/MemGPT** takes the most radical approach: the agent manages its own memory through an OS-inspired three-tier hierarchy. Core memory (always in context, **2,000 character limit** per block) holds current working knowledge. Archival memory (vector DB, unbounded) stores long-running facts. Recall memory (FIFO queue) preserves conversation history. The agent decides what to archive, what to keep, and what to forget via tool calls.

**LangMem** uses schema-based extraction via Pydantic models with parallel tool calling. It supports custom schemas (e.g., Triple with subject/predicate/object/context) and three operations: insert, update, delete. Conversation processing happens either inline during conversation or asynchronously after completion. Summarization is progressive — running summaries update incrementally, never re-summarizing already-summarized content.

The common thread: all systems preserve raw conversation data as ground truth and derive structured representations on top, using per-message or per-turn processing rather than arbitrary text splits.

---

## Topic-based segmentation algorithms and tools

For cases where chunking is needed — sessions exceeding quality thresholds, runtime processing with cheaper models, or gap-filling context assembly — topic-based segmentation significantly outperforms fixed-size splitting. A clinical decision support study (MDPI Bioengineering, November 2025) found adaptive chunking aligned to topic boundaries achieved **87% accuracy versus 50% for fixed-size** chunking.

The most practical approach for your use case is **embedding-based adjacent-turn similarity with valley detection** — essentially neural TextTiling. Embed each turn with a sentence transformer, compute cosine similarity between consecutive turn windows, smooth the curve, and detect valleys (local minima) as topic boundaries. This directly implements your existing triple-trigger concept (topic shift / 5-turn fallback / significant event) with a quantitative backbone.

For multilingual Korean/English content, use `paraphrase-multilingual-MiniLM-L12-v2` — it supports Korean natively, runs locally in under 10ms per embedding on CPU, and is free. The DriftOS project demonstrates practical thresholds: similarity > 0.38 indicates the same topic, < 0.15 indicates a new cluster, and values between suggest a subtopic shift. Cost for embedding all 91 sessions: effectively **$0**.

The key libraries available are:

- **Dialogue-Topic-Segmenter** (github.com/lxing532/Dialogue-Topic-Segmenter) — BERT-based coherence scoring between adjacent utterance pairs, supports pluggable encoders including RoBERTa and Sentence-BERT. Based on Xing & Carenini (2021).
- **DeepTiling** (github.com/Ighina/DeepTiling) — neural extension of TextTiling replacing word-count similarity with transformer embeddings. Supports BERT, Sentence-BERT, and Universal Sentence Encoder.
- **Unsupervised Topic Segmentation** (github.com/gdamaskinos/unsupervised_topic_segmentation) — RoBERTa-based, inspired by Facebook's meeting segmentation research (arXiv:2106.12978).
- **BERTopic** (github.com/MaartenGr/BERTopic) — topic modeling that can identify topic clusters across turns, useful for the hierarchical two-pass approach.

Classic algorithms (TextTiling via NLTK, C99, TopicTiling) exist but are **not recommended** for your use case. TextTiling relies on lexical repetition and strips non-Latin characters — it breaks entirely on Korean text. C99 considers global similarity structure but still uses TF vectors. TopicTiling requires training an LDA model on domain-appropriate data. All three are outperformed by embedding-based methods, and the modern UR-DTS method (Hou et al., 2024) achieves state-of-the-art results by combining utterance rewriting with unsupervised learning, improving Pk scores by ~6% absolute on DialSeg711.

No existing method specifically handles code-aware dialogue segmentation — this is a gap in the literature. The practical workaround: detect code blocks via regex, treat them as atomic units that don't break across segments, and apply embedding similarity only on natural language turns. Code blocks naturally create strong semantic boundaries due to their distinct token distribution.

---

## Cost analysis reveals massive budget surplus

Your $100 budget is **18–400× larger than needed**, fundamentally changing the optimization calculus from cost minimization to quality maximization.

| Strategy | Model | Total cost (91 sessions) | Quality |
|---|---|---|---|
| Single-pass, no chunking | GPT-4o-mini batch | **$0.25** | Baseline |
| Single-pass, no chunking | Claude Haiku 4.5 batch | **$1.84** | Good |
| Single-pass, no chunking | Claude Sonnet 4.6 batch | **$5.51** | High |
| Hierarchical two-pass | Haiku outline → Sonnet extraction | **$6.00** | Higher |
| Multi-pass with validation | Haiku + Sonnet + GPT-4o-mini cross-check | **$12.60** | Highest |

Both Anthropic and OpenAI offer **50% batch API discounts** for async processing within 24-hour windows. Anthropic's prompt caching charges 1.25× on first write but **0.1× (90% off)** on cache hits — useful when processing sessions sequentially with the same system prompt.

If chunking is used, the overhead is quantifiable. For **8K-token chunks with 15% overlap** (the research-backed optimal overlap from NVIDIA's 2024 benchmark), overlap adds ~17.6% token overhead. Adding per-chunk system prompt (~1K tokens) and entity carryover context (~500–2K tokens) inflates this to roughly **40–65% total overhead**. For your dataset, this means chunked processing costs roughly $8–9 with Sonnet batch instead of $5.51 — still trivially within budget.

The recommended allocation of the $100 budget is roughly $5.51 for primary extraction with Claude Sonnet 4.6 batch, $1.84 for a preliminary entity/topic scan with Haiku, $0.25 for cross-validation with GPT-4o-mini, $5.00 reserved for runtime gap-filling, and the remaining ~$87 for prompt iteration and quality improvement passes.

For the **runtime gap-filling scenario**, extracting from a typical turn range (9 target turns + context buffer of ~15K tokens total) costs **$0.001–$0.03 per extraction** depending on model choice. At the high end, the $5 reserve supports 150+ gap-fill operations.

---

## The decision tree for your specific system

```
BATCH SEEDING (processing full sessions):
┌─ After preprocessing, session ≤ 15K tokens (~50KB)?
│  └─ YES → Single-pass, any model. Quality ceiling is high.
│           Use Claude Sonnet 4.6 batch ($0.05/session).
│
├─ Session 15K–128K tokens (~50KB–425KB)?
│  └─ Single-pass with Claude Sonnet 4.6 batch.
│     Place extraction instructions at BOTH start and end of prompt
│     to mitigate lost-in-the-middle effect.
│     Monitor extraction density in middle third of transcript.
│     Cost: $0.10–$0.30/session.
│
├─ Session 128K–200K tokens (~425KB–660KB)?
│  └─ Single-pass with Claude Sonnet 4.6 (1M context, flat rate)
│     or Claude Haiku 4.5 (200K context).
│     Consider two-pass: Haiku for topic/entity scan, Sonnet for
│     detailed extraction per topic segment.
│     Cost: $0.30–$0.60/session.
│
└─ Session > 200K tokens?
   └─ Topic-based segmentation (paraphrase-multilingual-MiniLM-L12-v2).
      Chunk at topic boundaries, max 100K tokens per chunk.
      Carry entity registry + running summary between chunks.
      Use Claude Sonnet 4.6 batch.
      Cost: varies by session size.

RUNTIME GAP-FILLING (specific turn range):
┌─ Assemble context:
│  1. Entity registry from prior extraction (~500–2K tokens)
│  2. 2-sentence session summary (~100 tokens)
│  3. 5 preceding turns, preprocessed (~2–5K tokens)
│  4. Target turns, lightly preprocessed (~3–10K tokens)
│  5. 2 following turns (~1–2K tokens)
│
├─ Prompt structure (exploit positional bias):
│  [ENTITY REGISTRY] → placed FIRST (primacy bias)
│  [SESSION SUMMARY]
│  [PRECEDING CONTEXT]
│  [TARGET TURNS] → placed LAST (recency bias)
│  [FOLLOWING CONTEXT]
│
└─ Model choice:
   Speed-optimized: GPT-4o-mini ($0.001–$0.005/extraction, ~1–2s)
   Quality-optimized: Claude Haiku 4.5 ($0.01–$0.03/extraction, ~2–3s)
```

---

## Concrete recommendations for JARVIS

**For batch seeding**, the optimal pipeline is: (1) parse JSONL and separate by message type, (2) compress tool_results with rule-based handlers per tool type, (3) deduplicate repeated file reads via content hashing, (4) remove system messages and initialization boilerplate, (5) compress code blocks over 20 lines to summaries, (6) summarize thinking blocks to 1–2 sentences. Then process each session single-pass with Claude Sonnet 4.6 batch. Run a preliminary Haiku pass to extract entity lists and topic outlines, then feed these as context headers into the Sonnet extraction pass. Total cost: roughly **$7–8** for the complete two-pass pipeline across all 91 sessions.

**For runtime gap-filling**, the key insight from coreference resolution research is that **5–10 preceding turns** resolve most pronoun and reference chains, but technical conversations where variable names and concepts were introduced 20+ turns ago require an **entity registry** as substitute context. Stanford CoreNLP defaults to 50 mentions back for antecedent resolution; discourse model research shows entity caches below 10 items are "too restrictive." Your existing extracted entities from prior processing serve this role perfectly — inject them as a structured preamble. Semantic anchoring research (arXiv:2508.12630) shows enriching retrieval context with entity IDs and discourse tags improves factual recall by **up to 18%** over baselines.

**For topic-based segmentation** when needed, implement the embedding valley detection approach using `paraphrase-multilingual-MiniLM-L12-v2` with your existing triple trigger. Compute cosine similarity between sliding windows of turns (window size 3–5 turns for smoothing), detect valleys below a threshold (~0.35–0.40), enforce a minimum segment size of 5 turns, and apply the 5-turn fallback when no topic shift is detected. This aligns with your existing design and adds a quantitative signal at zero marginal cost.

## What the hierarchical two-pass approach looks like in practice

The strongest architecture combining research findings with production system patterns follows Graphiti's philosophy (episode-level processing with temporal context) but adapted for batch extraction. **Pass 1** uses Claude Haiku 4.5 to scan each preprocessed session and output a structured manifest: topic segments with turn ranges, an entity registry with types and first-mention locations, key decisions and their turn numbers, and a session-level summary. Cost: ~$1.84 for all 91 sessions. **Pass 2** uses Claude Sonnet 4.6 to extract detailed knowledge from each session, receiving the Pass 1 manifest as a context header. The manifest serves dual purposes: it provides the entity registry for consistent extraction, and it acts as a "roadmap" that guides the model's attention toward important segments — directly counteracting the lost-in-the-middle effect by telling the model *where* to look.

The LLM×MapReduce framework (Zhou et al., ACL 2025) provides a relevant pattern for cases requiring chunking: each chunk produces structured output including extracted information, rationale, answer, and a **confidence score**. The confidence scores enable intelligent conflict resolution during the reduce phase — when two chunks extract contradictory facts about the same entity, the higher-confidence extraction wins. Their research showed that a 4B parameter model using this framework outperformed 70B-scale models on long-context tasks, suggesting that structured decomposition can substitute for raw model capability.

## Conclusion

The central insight is that your problem is not a chunking problem — it's a **preprocessing and prompt engineering problem**. With modern context windows accommodating your entire dataset and costs under $10, the optimization frontier has shifted from "how to split text efficiently" to "how to maximize extraction quality within a single pass." The 95%+ token reduction from rule-based preprocessing, combined with positional-bias-aware prompt structure and a two-pass entity-guided extraction pipeline, should deliver higher quality knowledge extraction than any chunking strategy while remaining simpler to implement. Reserve chunking as a fallback for the rare session that demonstrates quality degradation in middle-section extraction — you'll know it when you see sparse or missing entities from the transcript's middle third. The $87+ budget surplus gives you ample room for iterative prompt refinement, which research consistently shows yields larger quality gains than architectural complexity.