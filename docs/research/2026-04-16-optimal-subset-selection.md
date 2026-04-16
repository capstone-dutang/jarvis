# Optimal subset selection for knowledge graph memory retrieval

> 연구 일자: 2026-04-16
> 성격: 딥리서치 — recall "맥락 조립" 알고리즘
> 상태: 활성 (구현 전 참고용, 절대문서 미반영)

**The JARVIS context assembly problem — selecting the best *combination* of knowledge graph fragments to answer a query — is a well-characterized submodular optimization problem with a practical, near-optimal solution.** The core insight is that scoring individual fragments independently and returning a ranked list discards the combinatorial structure of good answers: coverage of distinct aspects, coherence between fragments, and minimality. Formally, this maps to maximizing a monotone submodular set function under cardinality or knapsack constraints, for which the greedy algorithm provides a **provable (1−1/e) ≈ 63% approximation guarantee** and runs in O(N×K) time. A two-stage architecture — pgvector HNSW retrieval of 100 candidates in ~3ms followed by graph-aware MMR subset optimization in <1ms — achieves the required <100ms latency with N > 10,000 fragments while delivering dramatically better context than flat ranked lists.

The problem sits at a rich intersection of submodular optimization, diversified information retrieval, extractive summarization, knowledge graph reasoning, and recommender system slate optimization. Each of these fields contributes specific algorithmic tools. What follows is a comprehensive analysis of the formal foundations, practical algorithms, existing systems, and a concrete implementation path for JARVIS.

---

## 1. The problem has deep roots in submodular optimization

### Formal characterization

The JARVIS context assembly problem can be stated precisely. Given a knowledge graph G = (V, E) with N fragments (facts, relations, episode segments), a query Q, and a budget K, find:

S* = argmax_{S ⊆ F, |S| ≤ K} f(S, Q)

where f(S, Q) scores the *utility of the entire subset* S for answering Q. The critical distinction from standard retrieval is that **f operates on sets, not individual items** — the value of adding a fragment depends on what's already selected.

This problem appears under different names across several fields, each contributing distinct insights:

- **Submodular maximization** (combinatorial optimization): When f exhibits *diminishing returns* — formally, f(A ∪ {x}) − f(A) ≥ f(B ∪ {x}) − f(B) for A ⊆ B — the function is submodular, and greedy selection achieves a (1−1/e) approximation guarantee (Nemhauser, Wolsey & Fisher, 1978). This is the primary theoretical framework.

- **Facility location** (operations research): The function f(S) = Σᵢ maxⱼ∈S sim(i, j) — "how well does S cover all information needs?" — is a canonical submodular function. Each query aspect is "served" by its nearest selected fragment, exactly like customers served by their nearest facility.

- **Extractive summarization** (NLP): Selecting sentences that maximize coverage of a document's content. Lin & Bilmes (2011) showed that a class of functions combining coverage and diversity for document summarization are submodular, enabling greedy selection with guarantees. Their formulation — maximizing coverage of bigrams/concepts across selected sentences while penalizing redundancy — maps directly to selecting KG fragments that cover query aspects.

- **Set cover** (combinatorial optimization): Each fragment "covers" certain query aspects. The weighted maximum coverage variant asks: select K sets to maximize total weight of covered elements. This is submodular and captures the coverage requirement precisely.

- **Diversified retrieval** (IR): MMR (Carbonell & Goldstein, 1998) and Determinantal Point Processes (Kulesza & Taskar, 2012) both formalize the relevance-diversity tradeoff. MMR uses a greedy λ-weighted combination; DPPs use a probabilistic model where P(S) ∝ det(L_S), with the kernel L encoding both item quality and pairwise diversity.

- **Slate/bundle recommendation** (RecSys): YouTube's SlateQ (Ie et al., 2019) decomposes set-level Q-values into per-item components under a conditional choice model. Netflix optimizes entire homepage layouts jointly. Amazon's MUSS system uses multilevel submodular selection in production with 20–80× speedup over naive greedy.

### Why the power set is tractable despite 2^N size

The submodularity of f is what makes this tractable. Without structural assumptions, optimizing over 2^N subsets is indeed intractable. But **submodularity provides the "greedily solvable" structure** — the diminishing returns property means that greedily adding the best marginal item at each step cannot perform worse than 63% of optimal. For monotone submodular functions (where adding items never hurts), this guarantee is tight and information-theoretically optimal for polynomial-time algorithms unless P=NP.

---

## 2. Designing the scoring function to capture all four requirements

The scoring function f(S, Q) must capture four properties: relevance, coverage, coherence, and minimality. The key question is whether these can be decomposed or require holistic evaluation.

### The MMR decomposition works for three of four requirements

**Maximal Marginal Relevance** provides the simplest decomposition:

MMR = argmax_{d_i ∈ R \ S} [ λ · sim₁(d_i, Q) - (1-λ) · max_{d_j ∈ S} sim₂(d_i, d_j) ]

This naturally captures **relevance** (first term), **coverage/diversity** (penalty for similarity to already-selected items), and **minimality** (redundant items get penalized). The parameter λ controls the tradeoff, with **λ = 0.5–0.7** being standard in production RAG systems (LangChain defaults to 0.5; relevance-biased systems use 0.6–0.7).

**Coherence is the missing piece.** MMR's diversity term actively pushes *against* coherence — it penalizes items similar to the selected set, but coherent context requires fragments that are *mutually relevant* to a common narrative. Three approaches address this:

- **Graph-structural coherence**: Replace sim₂ with graph-aware similarity. Two fragments from the same graph community sharing relational connections are coherent but potentially redundant; fragments from entirely disconnected graph regions may be diverse but incoherent. Using **community co-membership** as a soft constraint ensures selected fragments share relational context while covering distinct aspects within that context.

- **Facility location with aspect anchoring**: f(S) = Σₐ∈aspects(Q) maxⱼ∈S sim(a, j) scores how well S covers each *query aspect* rather than all possible information needs. This constrains diversity to query-relevant dimensions, preventing incoherent fragment selection.

- **Prize-Collecting Steiner Tree (PCST)**: After initial subset selection, run PCST on the knowledge graph to find the minimal connected subgraph spanning selected entities. This adds "bridge" entities that provide relational coherence — the connective tissue between diverse facts. G-Retriever (He et al., NeurIPS 2024) demonstrated this approach reduces hallucinations by **54%** compared to baselines.

### A composite submodular scoring function for JARVIS

The recommended formulation combines three submodular components:

f(S, Q) = Σ_{s∈S} rel(s, Q) + α · |{c(s) : s ∈ S}| - β · Σ_{s_i,s_j∈S} sim(s_i, s_j)

where rel(s, Q) is fragment-query relevance (embedding similarity or PPR score), c(s) is the pre-computed community membership, and sim(sᵢ, sⱼ) is pairwise similarity. The first two terms are monotone submodular; the third is supermodular but the overall function remains approximately submodular for reasonable β values.

### DPP provides an alternative probabilistic formulation

Determinantal Point Processes model subset probability as P(S) ∝ det(L_S), where the kernel matrix L = B^T B with B encoding both quality (diagonal) and diversity (off-diagonal repulsion). However, exact DPP sampling requires **O(N³)** eigendecomposition, making it impractical at N = 10,000 in real-time. Approximate DPP sampling via MCMC or greedy MAP inference reduces to O(N·K²), which is feasible but slower than MMR's O(N·K).

---

## 3. Algorithms ranked by feasibility under 100ms latency

### The two-stage architecture is the practical foundation

Every feasible approach shares a common structure: **coarse retrieval** (high recall, fast) followed by **subset optimization** (high quality, on small candidate set).

| Algorithm | Complexity | Latency (n=100, k=15) | Quality | Implementation |
|-----------|-----------|----------------------|---------|---------------|
| **Greedy MMR** | O(n·k) | **<1ms** | Good (≈63% optimal if submodular) | Trivial in Python/NumPy |
| **Lazy greedy** | O(n·k) worst, ~O(n) avg | **<1ms** | Same as greedy, 10–100× faster in practice | Simple priority queue |
| **Community-aware MMR** | O(n·k) | **<1ms** | Better diversity guarantees | Needs pre-computed communities |
| **Beam search** (width w) | O(w·n·k) | **~5ms** (w=3) | Slightly better than greedy | More complex, marginal gains |
| **PCST post-processing** | O(n² log n) | **10–50ms** | Adds coherence/connectivity | Requires graph solver |
| **DPP MAP inference** | O(n·k²) | **~2ms** | Theoretically elegant | More complex kernel design |
| **Cross-encoder reranking** | O(n) per model call | **50–200ms** | Best per-item accuracy | **Too slow** for real-time |
| **ILP relaxation** | O(n³) | **>100ms** | Exact for LP relaxation | Impractical for real-time |

### Lazy greedy deserves special attention

The accelerated greedy algorithm (Minoux, 1978) exploits submodularity by maintaining a priority queue of marginal gains. Because marginal gains can only decrease as items are added (diminishing returns), if an item's cached marginal gain exceeds all other items' cached gains, it must still be the best choice without re-evaluation. In practice, this provides **10–100× speedup** over standard greedy while producing identical results.

### Pre-computed clusters enable graph-aware diversity at zero query-time cost

Running the **Leiden algorithm** offline on the JARVIS knowledge graph produces hierarchical community assignments stored as integer columns. At query time, community membership lookup is O(1) per fragment. Microsoft's GraphRAG validated this approach at scale. The Leiden algorithm guarantees **all produced communities are internally connected** (unlike Louvain, which can produce disconnected communities in up to 25% of cases).

---

## 4. What existing production systems actually do

### Most systems still return ranked lists — true subset optimization is rare

**Systems with true subset optimization:**
- **FlashRank** greedily selects documents maximizing α·relevance + β·novelty + γ·brevity + δ·cross_encoder_score under an explicit token budget constraint.
- **DynamicRAG** uses RL-trained reranking that simultaneously determines both ranking and optimal K (subset size) per query.
- **CRAG's decompose-then-recompose** segments retrieved documents into fine-grained knowledge strips, scores each strip's relevance, filters irrelevant strips, and recomposes.
- **Amazon MUSS** deploys multilevel submodular subset selection in production, achieving 4 percentage points higher precision with 20–80× speedup over naive greedy MMR.

**Systems with structural but not optimization-based assembly:**
- **Microsoft GraphRAG** uses hierarchical Leiden community summaries.
- **RAPTOR's tree traversal** mode prunes irrelevant branches via relevance-based descent.
- **HippoRAG** runs Personalized PageRank from query entities.

**Systems that remain ranked-list retrieval:**
- LangChain and LlamaIndex provide MMR as a post-retrieval option but default to top-K.
- Cohere Rerank and Pinecone's built-in reranking improve per-item scoring but don't optimize set-level objectives.

### The MCP protocol has no built-in context optimization

MCP tool responses directly consume model context window tokens with no native compression or filtering layer. For JARVIS, **subset optimization must happen inside the MCP tool**, before returning results.

---

## 5. Graph structure provides five orthogonal advantages

**Connectivity via Prize-Collecting Steiner Tree.** PCST finds the minimal-cost connected subgraph that maximizes the sum of node prizes minus edge costs. G-Retriever demonstrated 54% hallucination reduction. The MST-based 2-approximation runs in O(|L|² log |V|) on the candidate subgraph.

**Diversity via community detection.** Pre-computed Leiden communities serve as aspect proxies — selecting from K distinct communities guarantees K distinct topical areas. O(1) at query time.

**Relevance propagation via Personalized PageRank.** PPR from query-relevant seed entities captures **multi-hop relevance**. Approximate PPR via Monte Carlo (~1,000 walks) runs in **2–5ms**.

**Structural scoring via directional distance encoding.** SubgraphRAG (Li et al., ICLR 2025) encodes structural distance and direction to topic entities, scores with lightweight MLP.

**Hierarchical abstraction via multi-level communities.** Different query types need different granularity levels. Hierarchical Leiden at 2–3 levels enables query-dependent granularity selection.

---

## 6. The greedy anchor algorithm is theoretically sound

### Validation: this is standard greedy submodular maximization

The proposed algorithm — (1) select highest-scoring fragment as anchor, (2) iteratively add the fragment maximizing marginal set score — **is exactly the standard greedy algorithm for monotone submodular maximization**. For monotone functions, the highest individual score IS the highest marginal gain from the empty set.

**The (1−1/e) ≈ 63% approximation guarantee applies** under these conditions:
- f is **monotone**: adding items never hurts
- f is **submodular**: diminishing returns
- The constraint is a **cardinality constraint**: |S| ≤ K

### Starting from the top-1 item does not bias results for monotone functions

The standard proof (Nemhauser, Wolsey & Fisher, 1978) shows the guarantee holds regardless of how S₀ was chosen.

### Multiple anchors provide meaningful improvement

Running the greedy algorithm from the **top-3 anchors in parallel** and selecting the best result adds only 3× computation (~3ms total) while providing robustness against edge cases.

### Detecting when to stop: diminishing returns threshold

Recommended: **marginal gain ratio** with τ = 0.1 (stop when next item's gain < 10% of first item's gain), hard maximum K = 20, hard minimum K = 3. This adapts subset size to query complexity — narrow queries saturate at K ≈ 3–5, broad queries at K ≈ 12–18.

---

## 7. Concrete implementation path for JARVIS

### The 80% solution: two-stage MMR with community awareness (~5ms)

**Stage 1 — pgvector HNSW retrieval (~3ms):**
```sql
SELECT id, name, embedding, community_id, metadata,
       1 - (embedding <=> $query_vec::vector) AS relevance
FROM fragments
ORDER BY embedding <=> $query_vec::vector
LIMIT 100;
```

**Stage 2 — Community-aware MMR in application code (~1ms):**
λ = 0.6, community bonus +0.05 for unrepresented communities. Pre-compute community assignments offline via Leiden.

### The research-grade solution: full graph-aware optimization (~25ms)

1. **PPR-augmented scoring** (~3ms): 30% PPR / 70% embedding blend
2. **PCST coherence post-processing** (~15ms): bridge entities for narrative coherence
3. **Adaptive K with marginal gain stopping** (~0ms additional)

### MCP tool response design

- **Selected fragments** (10–20): optimized subset with relevance scores
- **Structural summary** (2–3 sentences): what was found and how fragments connect
- **Coverage metadata**: communities represented, total available, confidence
- **Pagination token**: for follow-up depth requests

Target: **2,000–8,000 tokens**. Never return unbounded lists. "More available" indicator.

### Handling narrow vs. broad queries with the same mechanism

Marginal-gain stopping naturally adapts. Narrow queries → K ≈ 1–3. Broad queries → K ≈ 10–18. No separate query classification needed.

### Learning the scoring function from feedback

(1) Start with hand-tuned MMR parameters → (2) log query-subset-outcome triples → (3) after ~1,000 examples, train lightweight set scoring model → (4) adaptive parameters per query.

---

## Conclusion

The greedy anchor-based algorithm is theoretically sound, achieving **63% of optimal** with O(N×K) complexity. The 80% solution (two-stage MMR with community awareness, ~5ms) captures most of the value and is implementable in days. The research-grade additions (PPR, PCST, learned parameters) provide incremental improvement on complex multi-hop queries.

Three findings particularly matter: (1) **coherence through graph structure** distinguishes this from generic retrieval, (2) **adaptive subset sizing via marginal gain stopping** solves narrow-vs-broad without routing logic, (3) **MCP response design** with structural summaries prevents AI clients from treating incomplete results as ground truth.
