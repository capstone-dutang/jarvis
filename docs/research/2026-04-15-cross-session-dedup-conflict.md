# 리서치 #3: 세션 간 중복/충돌 처리

> 연구 일자: 2026-04-15
> 성격: 딥리서치 — 다중 세션 사실 병합 + 일관성
> 상태: 결과 대기

(리서치 결과 여기에 붙여넣기)
# Batch seeding a bitemporal KG from 91 conversation sessions

**Two-phase independent extraction followed by entity-blocked merge is the correct architecture for your 91-session batch seed.** Every production knowledge graph system at scale—Diffbot (10B+ entities), LinkedIn, Google—uses independent extraction with post-merge rather than sequential processing with accumulated context. Graphiti's chronological approach works for real-time streaming but introduces error propagation and context rot during batch loads. The research converges on a concrete pipeline: extract independently per session, merge via embedding similarity + NLI with entity-blocking to avoid O(n²) blowup, and use your bitemporal model to represent the full temporal evolution. Total cost for 91 sessions with GPT-4o-mini extraction is under $0.50; the dominant complexity is in the reconciliation phase, which runs on local models.

---

## 1. Processing order: independent extraction wins for batch seeding

Three strategies exist, each with distinct failure modes. The evidence strongly favors independent extraction + post-merge for your use case.

**Chronological (oldest first)** is Graphiti's approach for real-time streaming. From the Zep paper (arXiv:2501.13956): *"timeline T represents the chronological ordering of events, and timeline T' represents the transactional order of Zep's data ingestion."* Graphiti processes each episode against the existing graph, using **n=4 previous messages** (two conversation turns) as extraction context. It prioritizes new information: *"Following the transactional timeline T', Graphiti consistently prioritizes new information when determining edge invalidation."* The failure mode is **error accumulation**—early entity resolution mistakes propagate forward, and the cold-start problem means initial sessions have no context for disambiguation. For 91 sessions processed sequentially, an entity misidentified in session 3 corrupts all downstream references.

**Reverse chronological (newest first)** has no production implementation. The fundamental problem is logical incoherence: you encounter invalidations before their original facts, making temporal supersession impossible to model correctly. Skip this.

**Independent extraction + post-merge** is the dominant production pattern. ATOM (evolution of iText2KG, arXiv:2409.03284) achieves **93.8% latency reduction versus Graphiti** by parallelizing extraction and using cosine similarity for merging instead of LLM calls. KGGen (arXiv:2502.09956) describes the pattern: *"After extracting triples from each source text, we collect all the unique entities and edges across all source graphs and combine them into a single graph. All entities and edges are normalized to be in lowercase letters only. The aggregation step reduces redundancy in the KG. Note that the aggregation step does not require an LLM."* Diffbot rebuilds its entire 10B-entity graph every 4-5 days using fully independent per-page extraction followed by ML-based knowledge fusion. LinkedIn processes each profile independently then standardizes via ML taxonomy construction and entity resolution. The failure mode is **entity fragmentation**—"the project" in session 5 may not resolve to "JARVIS" in session 10 without cross-session context. This is solvable in the merge phase.

DIAL-KG (arXiv:2603.20059) offers a hybrid: it maintains a Meta-Knowledge Base (MKB) that accumulates entity profiles and schema proposals across batches. Performance *"remains within 1–2 F1 points of the batch setting"* in streaming mode—meaning batch processing is at least as good as incremental.

**Recommendation**: Use two-phase independent extraction. Process all 91 sessions in parallel (Phase 1), then merge (Phase 2). Tag each extraction with the session's timestamp for bitemporal modeling. Reserve Graphiti-style chronological processing for future real-time ingestion after the initial seed is complete.

---

## 2. The deduplication pipeline: five stages from raw extraction to clean graph

The following pipeline synthesizes approaches from Graphiti, Mem0, KGGen, and ATOM, with specific thresholds calibrated to the research literature.

**Stage 1 — Per-session extraction (parallel, no cross-session context).** Extract entities and relations from each session independently using structured LLM output (Pydantic models). Include the session timestamp as `valid_from`. Provide only the entity type schema and extraction instructions—**not** prior knowledge state. Context rot research is definitive: Chroma's 2025 study of 18 frontier models found *"every one exhibits [attention degradation] at every input length increment tested."* The NoLiMa benchmark showed **11 of 12 models dropped below 50% performance at 32K tokens**. For 91 sessions averaging ~20 facts each at ~15 tokens/fact, the accumulated knowledge state would reach ~27K tokens by session 90—deep in the degradation zone.

**Stage 2 — Normalize and embed.** Lowercase all entity names and predicate strings. Generate embeddings for every entity name and every fact text. Use your existing embedding infrastructure (sentence-transformers or equivalent). ATOM uses `ent_threshold=0.8` and `rel_threshold=0.7` for cosine similarity merging; Graphiti uses **1024-dimensional** vectors from text-embedding-3-small.

**Stage 3 — Entity resolution (your existing 3-stage pipeline).** Your alias dict → embedding candidates → hybrid scoring pipeline aligns with Graphiti's three-tier approach: exact match → fuzzy similarity (embedding + BM25) → LLM reasoning. Graphiti's key optimization: *"The hybrid search for relevant edges is constrained to edges existing between the same entity pairs as the proposed new edge. This constraint not only prevents erroneous combinations of similar edges between different entities but also significantly reduces the computational complexity."* Apply this same constraint in your merge phase—only compare facts that share resolved entity pairs.

For cross-session coreference ("the project" → "JARVIS"), your existing predicate resolution (embedding 70% + fuzzy 30%, ≥0.85 threshold) handles the predicate side. For entities, the merge phase should build a canonical entity registry incrementally: process sessions in chronological order during merge (not extraction), so that when session 10 establishes "the project" = "JARVIS", all earlier references can be retroactively resolved.

**Stage 4 — Fact deduplication via NLI + embedding similarity.** For each new fact, compare against existing facts **between the same entity pair only**. This is Graphiti's critical optimization. Run your NLI cross-encoder (nli-deberta-v3-xsmall) on candidate pairs. Classify using the decision tree in Section 4 below.

Concrete thresholds based on the literature:

- **Embedding cosine ≥ 0.92** (sentence-transformers): auto-merge as duplicate. NVIDIA's SemDeDup uses eps=0.01 (cosine ≥ 0.99) for near-identical; the MDPI paraphrase study found **0.671 optimal for MPNet on MRPC**, but for knowledge facts (shorter, more structured), empirical testing suggests 0.85-0.92 is the sweet spot.
- **NLI entailment softmax ≥ 0.70**: high-confidence duplicate. The FacTeR-Check system uses entailment probability exceeding a threshold for fact matching.
- **NLI contradiction softmax ≥ 0.70**: high-confidence contradiction → supersede.
- **Scores between 0.50-0.70**: flag for review (see Section 7 HITL thresholds).
- **Your existing ≥0.85 predicate resolution threshold** is well-calibrated against the entity resolution literature, where the ACM TKDD graph-based ER paper uses ≥0.85 average cosine for entity merge.

**Stage 5 — Temporal resolution and bitemporal assignment.** For each deduplicated fact, assign `valid_from` from the earliest session mentioning it, `recorded_at` = batch processing timestamp, `superseded_at` = NULL for active facts. For contradictions detected in Stage 4, apply your existing atomic supersede transaction. For facts mentioned across multiple sessions, boost confidence (see Section 5).

---

## 3. How Graphiti, Mem0, and Letta handle merge — verbatim strategies

### Graphiti's edge deduplication prompt (from `graphiti_core/prompts/dedupe_edges.py`)

The LLM receives structured instructions and returns a Pydantic model:

```python
class EdgeDuplicate(BaseModel):
    duplicate_facts: list[int] = Field(
        ..., description='List of idx values of any duplicate facts. '
        'If no duplicate facts are found, default to empty list.')
    contradicted_facts: list[int] = Field(
        ..., description='List of idx values of facts that should be invalidated. '
        'If no facts should be invalidated, the list should be empty.')
    fact_type: str = Field(..., description='One of the provided fact types or DEFAULT')
```

The prompt instructs:

> *"1. DUPLICATE DETECTION: If the NEW FACT represents identical factual information as any fact in EXISTING FACTS, return those idx values in duplicate_facts. Facts with similar information that contain key differences should NOT be marked as duplicates. 2. FACT TYPE CLASSIFICATION: Given the predefined FACT TYPES, determine if the NEW FACT should be classified as one of these types. 3. CONTRADICTION DETECTION: Based on FACT INVALIDATION CANDIDATES and NEW FACT, determine which facts the new fact contradicts."*

Separate guidelines emphasize: *"Some facts may be very similar but will have key differences, particularly around numeric values in the facts. Do not mark these facts as duplicates."*

For batch deduplication within a list, a different prompt applies: *"identical or near identical facts are duplicates. Facts are also duplicates if they are represented by similar sentences. Facts will often discuss the same or similar relation between identical entities. The final list should have only unique facts. If 3 facts are all duplicates of each other, only one of their facts should be in the response."*

### Graphiti's temporal invalidation (from `resolve_edge_contradictions()`, lines 469-505)

When contradictions are found, old edges get `invalid_at` set to the new edge's `valid_at`, and `expired_at` set to the current system time. **The new edge itself may also be expired** if a more recent contradicting fact already exists (lines 688-703). This bidirectional check prevents stale facts from overwriting newer ones during out-of-order ingestion—directly relevant to your batch seed scenario.

Graphiti's four timestamps map exactly to your model:

| Graphiti field | Your field | Timeline | Semantics |
|---|---|---|---|
| `valid_at` | `valid_from` | T (world) | When the fact became true |
| `invalid_at` | `valid_to` | T (world) | When the fact stopped being true |
| `created_at` | `recorded_at` | T' (system) | When the system ingested the fact |
| `expired_at` | `superseded_at` | T' (system) | When the system marked it replaced |

### Mem0's ADD/UPDATE/DELETE/NOOP pipeline

Mem0 uses a fundamentally different approach: every candidate fact goes through an LLM decision. From their paper (arXiv:2504.19413): the system retrieves the **top s=10** most similar existing memories via dense embeddings, then presents candidate + similar memories to the LLM, which selects one of four operations: **ADD** (new memory), **UPDATE** (modify existing), **DELETE** (contradicted), or **NOOP** (duplicate/irrelevant). There is **no embedding similarity threshold**—the decision is entirely LLM-driven. Old facts are destructively updated (overwritten or deleted), with an audit trail in SQLite at `~/.mem0/history.db`. This is lossy—no bitemporal tracking.

### Letta (MemGPT) — agent self-management

Letta delegates memory management to the agent itself via tool calls. The agent uses `core_memory_replace(name, old_content, new_content)` for updates—exact string replacement. There is **no automatic deduplication pipeline**. Archival memory is append-only with vector search. This approach relies entirely on the LLM's reasoning quality and provides no systematic guarantee against duplicates or contradictions.

### LangGraph memory — developer responsibility

LangGraph provides no built-in dedup. The recommended approach is **Trustcall** (github.com/hinthornw/trustcall), which uses JSON Patch operations: the LLM generates patches to fix/update existing schemas rather than regenerating full documents. This "patch-don't-post" philosophy is relevant for your merge phase—instead of replacing entire facts, generate incremental patches.

---

## 4. The refinement-contradiction-duplication decision tree

The progressive refinement problem ("need memory server" → "should use MCP" → "MCP with Streamable HTTP, OAuth 2.1" → "4 tools: initialize, store, recall, manage_workspace") requires distinguishing three fundamentally different knowledge changes. The AGM belief revision framework (Alchourrón, Gärdenfors, Makinson, 1985) provides the theoretical foundation: **revision** (new info about same situation, old beliefs may be wrong) vs **update** (world actually changed, old beliefs were correct at the time).

The Jarnac et al. survey on uncertainty management in KG construction (TGDK 2025) proposes classifying knowledge deltas by conflict type, distinguishing **differences in specificity** (refinements) from **knowledge contradictions**. Synthesizing this with production system approaches, here is the decision algorithm:

**For each new fact F_new compared against existing fact F_old (same entity pair, same predicate cluster):**

1. **Compute NLI scores**: Run nli-deberta-v3-xsmall on (F_old, F_new). Get softmax probabilities for [contradiction, entailment, neutral].

2. **If entailment ≥ 0.70**: F_new is a duplicate or refinement of F_old.
   - Compute embedding cosine similarity between F_new and F_old.
   - **If cosine ≥ 0.92**: Pure duplicate → discard F_new, increment F_old's attestation count.
   - **If cosine 0.70-0.92**: Refinement → check if F_new is more specific (longer, contains additional predicates, or adds quantitative detail). If yes, **keep both** as separate facts in the graph with a `refines` edge linking F_new → F_old, both active. If F_new strictly subsumes F_old (entailment is unidirectional: F_new entails F_old but not vice versa), supersede F_old.

3. **If contradiction ≥ 0.70**: F_new contradicts F_old.
   - Check for explicit change language in the source quote ("switched from", "no longer", "instead of", "moved to"). If present → **explicit supersede**: set F_old's `valid_to` = F_new's `valid_from`, F_old's `superseded_at` = now.
   - If no change language → **implicit supersede**: apply the same supersession but flag for review if contradiction confidence is 0.70-0.85. Auto-supersede only if contradiction ≥ 0.85.
   - **"Latest timestamp wins" is not always correct.** A user revisiting old decisions may explicitly revert: "actually, let's go back to Oracle." The temporal model handles this naturally—it's a new fact that supersedes the GCP fact, restoring the Oracle state. The key is tracking `valid_from` accurately from session timestamps.

4. **If neutral ≥ 0.70 (neither entailment nor contradiction)**: Separate facts → insert F_new as a new fact.

5. **If no class exceeds 0.70**: Ambiguous → flag for review.

**For the refinement chain specifically** ("need memory server" → "should use MCP" → "MCP with Streamable HTTP, OAuth 2.1"):
- Store each as a separate fact with increasing specificity.
- The predicate resolution system should map these to the same predicate cluster (e.g., "architecture_decision").
- Link them with `refines` edges: each new fact `refines` its predecessor.
- All remain active—the most specific is the "current understanding," but earlier facts provide context and provenance.
- An extraction prompt instruction helps: *"When a fact adds detail to a previously known general concept, mark it as REFINEMENT. When a fact changes the value of a known attribute, mark it as UPDATE."*

Academic support for this approach comes from the "Spectral Neuro-Symbolic Reasoning II" paper (arXiv:2511.10655), which demonstrates that Sentence-BERT/SimCSE embeddings successfully merge semantically redundant propositions while DeBERTa-based entailment filtering validates candidate edges, achieving **+3.8% accuracy gains** on reasoning benchmarks.

---

## 5. Scaling contradiction detection from O(n²) to practical

**Entity-blocking eliminates 99.9% of comparisons.** The Papadakis et al. survey (ACM Computing Surveys, 2021) establishes that blocking reduces quadratic complexity to near-linear: *"Without [blocking], ER suffers from a quadratic time complexity, O(n²), as every entity profile has to be compared with all others."* Block cleaning reduces comparisons from ~10⁸ to ~10⁴-10⁵ (**3-4 orders of magnitude**) while maintaining recall >0.80.

For your knowledge graph, the blocking strategy is:

1. **Block by entity pair** (Graphiti's approach): Only compare facts sharing the same subject-object entity pair. For a personal KG with ~200 entities and ~2,000 facts, this reduces from ~2M comparisons to ~200 clusters × ~45 comparisons each = **~9,000 NLI calls**.

2. **Block by predicate cluster** within entity blocks: If two facts about the same entity have different predicates (e.g., "employer" vs "hobby"), they can't contradict. Your predicate resolution (embedding 70% + fuzzy 30%) already clusters predicates—reuse this. Reduces comparisons by another **~80%** → **~1,800 NLI calls** for 2,000 facts.

3. **Embedding pre-filter**: Before running NLI, compute cosine similarity between fact embeddings. Only run NLI on pairs with cosine > 0.40 (very permissive, just eliminates clearly unrelated facts). This catches the remaining edge cases.

At **~28ms per NLI call** on CPU with your nli-deberta-v3-xsmall, 1,800 calls = **~50 seconds**. Even at 10,000 facts, entity-blocking + predicate-blocking keeps this under 10 minutes on CPU.

**Confidence from repetition** follows a natural formula: `confidence = 1 - (1 - base_confidence)^n` where n = number of independent session attestations. At base confidence 0.7: 1 session = 0.70, 2 sessions = 0.91, 3 sessions = 0.97, 10 sessions = 0.9999. This aligns with Dempster-Shafer theory for combining independent evidence, though the simplified formula avoids DST's known problems with highly conflicting evidence. The Jarnac et al. survey confirms: *"If [a new fact matches] a fact already present in the KG, we increase the confidence given to the source."*

**Explicit vs implicit supersession** requires extraction-time classification. Include in your extraction prompt:

> *"Classify each extracted fact: NEW (not previously known), UPDATE_EXPLICIT (directly states a change—look for: 'switched', 'migrated', 'no longer', 'instead of', 'replaced'), UPDATE_IMPLICIT (mentions current state without referencing change), or CONFIRMATION (restates known fact). Include the classification in structured output."*

For implicit supersession, require **contradiction confidence ≥ 0.85** before auto-superseding. For explicit supersession (change language detected), **≥ 0.70** suffices. Route the gap to human review.

---

## 6. Cross-session context and the knowledge state summary pattern

The research is unambiguous: **do not inject the full knowledge state into extraction prompts.** The lost-in-the-middle effect (Stanford/TACL 2024) showed *"accuracy dropped by more than 30% when the relevant document was placed in positions 5-15."* Multi-turn degradation research (arXiv:2506.00069) found *"performance may degrade drastically with long prior context, as high as 73% drop compared to performance when no prior context is added."*

For Phase 1 extraction, provide only:
- The session transcript
- The entity type schema (what categories of entities to extract)
- Extraction format instructions

For Phase 2 merge, a **compact entity registry** serves as the knowledge state summary:

```
Entity Registry (compact format):
JARVIS: AI memory server project, cross-provider MCP server
MCP: Model Context Protocol, used by JARVIS
PostgreSQL: database technology, used by JARVIS
OAuth 2.1: authentication standard, used by JARVIS MCP server
```

This is **~50-100 tokens** for 50 entities vs **~15,000 tokens** for the full fact list. Provide it to the merge-phase LLM when resolving ambiguous entity matches—not during extraction. Graphiti's approach of using only n=4 recent messages for extraction context supports this: even in real-time streaming, they limit context rather than providing the full graph state.

**When does context become too large?** Based on the NoLiMa benchmark data: keep any injected knowledge state **under 5K tokens** for extraction tasks, **under 10K tokens** for merge/resolution tasks. At 91 sessions with ~20 facts each at ~15 tokens/fact, the full state is ~27K tokens—clearly in the degradation zone if injected wholesale.

**Cost comparison**: Independent extraction (Phase 1 alone) costs **~$0.11** for 91 sessions with GPT-4o-mini. Adding accumulated context approximately doubles this to **~$0.21**—the cost difference is negligible, but the quality difference from context rot is significant. The merge phase uses local NLI and embedding models at zero API cost. An optional LLM-assisted merge for ambiguous cases adds ~$0.05-0.10. **Total pipeline cost: under $0.50** with efficient models, under $5 even with GPT-4o.

---

## 7. Evaluation without exhaustive annotation

**Sampled manual verification through your bitemporal model** is the most practical approach. The Nature Scientific Reports (2024) study on KG evaluation found critical sample sizes of **n=3-8** per stratum at which measured accuracy converges to true accuracy. For your 91-session seed:

1. **Stratified sampling**: Sample 5-8 facts from each category (entities, relationships, temporal assignments, supersessions). Manually verify against source sessions using `source_quote` grounding. Total verification effort: **~40-60 facts** out of perhaps 1,500-2,000 total.

2. **Deduplication quality metrics** without gold standard:
   - **LP-Measure** (ACM 2024): Uses link prediction as a proxy for KG quality—measures self-consistency without requiring a reference graph.
   - **Cluster purity**: For entity resolution, sample 20 entity clusters, manually check if all members are the same entity. Report purity (correct / total).
   - **Duplicate escape rate**: Search for near-duplicate facts (cosine 0.85-0.92) that weren't merged; sample and check if they should have been.
   - **False merge rate**: Sample merged facts and check if any distinct facts were incorrectly collapsed.

3. **Standard temporal KG benchmarks** for reference:

| Benchmark | Entities | Relations | Time Type | Use |
|---|---|---|---|---|
| ICEWS14 | 6,869 | 230 | Point | Standard TKGC evaluation |
| ICEWS05-15 | 10,488 | 251 | Point | Multi-year temporal reasoning |
| GDELT | 500 | 20 | Point | High-volume event streams |
| YAGO15k | 15,403 | 32 | Range | Validity interval modeling |

Standard TKGC metrics are **MRR** (Mean Reciprocal Rank), **Hits@1/3/10**. These evaluate temporal link prediction, not deduplication quality directly, but can serve as downstream task metrics if you build a completion model.

4. **Intrinsic metrics** from the Zaveri et al. framework (18 quality dimensions, 69 metrics): Focus on **accuracy** (sampled verification), **completeness** (coverage of known topics from sessions), **consistency** (no active contradictions in the graph), and **timeliness** (temporal assignments match session dates).

5. **Practical test**: Take 5 sessions where you know the ground truth (topics discussed, facts established, contradictions introduced). Run the full pipeline. Manually compare output graph against expected state. This gives an end-to-end quality measure without annotating the full corpus.

---

## Scalability analysis across three orders of magnitude

**At 100 facts** (~5-10 sessions): Everything works naively. Full pairwise NLI takes ~140 seconds on CPU (4,950 pairs × 28ms). No blocking needed. Full knowledge state fits in context (~1,500 tokens). Single-phase with context is viable. Entity resolution is trivial.

**At 10,000 facts** (~500 sessions): Entity-blocked NLI reduces to ~45,000 comparisons → **~21 minutes on CPU**, parallelizable across cores. Knowledge state exceeds context limits (~150K tokens). Two-phase extraction is mandatory. Entity resolution requires embedding-based blocking (not all-pairs). Graphiti-style same-entity-pair scoping becomes critical.

**At 100,000 facts** (~5,000 sessions): Entity-blocked NLI: ~450,000 comparisons → **~3.5 hours on CPU**, trivially parallelizable to under 30 minutes on 8 cores. At this scale, follow Diffbot's pattern: fully independent extraction with ML-based fusion. Embedding index (HNSW/IVF) required for candidate retrieval. Consider moving NLI to GPU (cross-encoder/nli-deberta-v3-xsmall at ~2ms/pair on GPU → 15 minutes for 450K comparisons). Knowledge state summary must be hierarchical: entity registry → topic summaries → community clusters (Graphiti's community subgraph pattern).

The bottleneck shifts across scales: at 100 facts, LLM extraction dominates. At 10,000 facts, entity resolution dominates. At 100,000 facts, embedding index maintenance and graph database writes dominate.

---

## The complete recommended pipeline for your 91-session batch seed

**Phase 1 — Independent extraction** (parallelizable, ~$0.11 with GPT-4o-mini batch API):
Process all 91 sessions in parallel. Extract (entity, predicate, object, valid_from, source_quote) tuples. Tag with session timestamp as `valid_from`. Use structured Pydantic output. Include change-type classification in the extraction prompt (NEW/UPDATE_EXPLICIT/UPDATE_IMPLICIT/CONFIRMATION).

**Phase 2a — Normalize and entity resolution** (local, minutes):
Lowercase all strings. Run your 3-stage entity resolution pipeline across all extracted entities. Build canonical entity registry. Resolve cross-session references ("the project" → "JARVIS") using embedding similarity + your alias dict.

**Phase 2b — Fact deduplication and contradiction detection** (local NLI + embeddings, minutes):
For each entity pair, collect all facts. Run the decision tree from Section 4. Auto-merge duplicates (entailment ≥ 0.70, cosine ≥ 0.92). Auto-supersede contradictions (contradiction ≥ 0.85 or ≥ 0.70 with explicit change language). Flag ambiguous cases (max class 0.50-0.70) for review. Boost confidence for facts attested across multiple sessions.

**Phase 2c — Temporal assignment** (deterministic):
For each surviving fact: `valid_from` = earliest session timestamp attesting it. `valid_to` = NULL if active, or superseding fact's `valid_from` if superseded. `recorded_at` = batch processing timestamp. `superseded_at` = NULL if active, or processing timestamp if superseded. Execute as atomic transactions per your existing model.

**Phase 3 — Quality verification** (manual, ~1 hour):
Sample 40-60 facts stratified by type. Verify against source sessions via source_quote. Check entity resolution clusters for purity. Search for duplicate escapes. Report accuracy, cluster purity, duplicate escape rate, false merge rate.

---

## Conclusion

The strongest signal from this research is that **Graphiti is the only production AI memory system implementing true bitemporal modeling**, and its approach to edge deduplication—constraining comparisons to same entity pairs, using hybrid search (embedding + BM25) before LLM reasoning, and invalidating rather than deleting contradicted facts—directly validates your architecture. However, Graphiti processes episodes chronologically for real-time use; for batch seeding, the independent extraction + post-merge pattern used by Diffbot, KGGen, and ATOM is superior because it eliminates error propagation and enables full parallelism.

The most underappreciated finding is the severity of **context rot**: injecting accumulated knowledge state into extraction prompts actively degrades quality beyond ~10K tokens, not just fails to help. This makes two-phase processing not merely more efficient but more accurate. The refinement-vs-contradiction distinction requires both NLI and embedding similarity working together—neither alone suffices, because NLI captures logical relationships while embeddings capture semantic proximity. Your existing infrastructure (nli-deberta-v3-xsmall + embedding pipeline + entity resolution) is well-suited to this task; the missing piece is the entity-blocked comparison strategy and the refinement classification in extraction prompts. With those additions, the 91-session batch seed is a tractable problem solvable in under an hour of compute time and under $1 of API cost.