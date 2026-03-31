# Building JARVIS: a practical blueprint for multilingual knowledge graphs on PostgreSQL

> 연구 일자: 2026-03-31
> 성격: 구현 단계 리서치 — 엔티티 해소, 그래프 탐색, 한국어 FTS
> 상태: 활성 (절대문서에 반영됨)

**A lightweight, LLM-free entity resolution pipeline combined with recursive-CTE graph traversal and Korean full-text search can deliver sub-50ms hybrid retrieval on Oracle Cloud ARM — without exotic extensions.** The core stack is PostgreSQL 16 + pgvector + PGroonga (or textsearch_ko) + sentence-transformers + RapidFuzz, all confirmed to run on aarch64.

---

## AREA 1: Entity resolution without LLM inference

### How Graphiti actually works — and why you should diverge

Zep's Graphiti engine uses a three-tier deduplication strategy: exact name match → embedding-based candidate retrieval → LLM final judgment. Graphiti is LLM-dependent for entity resolution — each `add_episode()` triggers 4–6 LLM calls across 13 specialized prompts. This is expensive and slow.

For JARVIS on ARM, a lightweight pipeline using embeddings + string matching + alias dictionaries achieves comparable results at 50–100× lower latency.

### The recommended three-stage pipeline

**Stage 1 — Normalize and alias lookup (< 1ms)**
- Unicode NFKC normalization
- Static alias dictionary: "포스트그레스"→"postgresql", "k8s"→"kubernetes"
- Pattern-based abbreviation detection

**Stage 2 — Embedding generation and candidate retrieval (15–35ms)**
- Generate vector embedding for incoming entity
- Query pgvector for top-10 nearest existing entities above cosine 0.75

**Stage 3 — Hybrid scoring and decision (< 1ms)**
- Weighted combination of RapidFuzz + embedding cosine
- Cross-lingual dynamic weighting:
  - Korean↔English: 0.05 string + 0.95 embedding
  - Same language: 0.4 string + 0.6 embedding

### Thresholds
- Auto-merge: ≥ 0.92
- High-confidence merge: ≥ 0.85
- Candidate review: ≥ 0.78
- Distinct entity: < 0.78

### Recommended embedding model
**dragonkue/multilingual-e5-small-ko** — 118M params, 384 dims, Korean-optimized, ~500MB RAM, 15-30ms per entity on ARM CPU.

### Complete entity resolver (Python)

```python
from sentence_transformers import SentenceTransformer
from rapidfuzz import fuzz
import numpy as np, unicodedata

class LightweightEntityResolver:
    def __init__(self, db_pool, model_name='dragonkue/multilingual-e5-small-ko'):
        self.model = SentenceTransformer(model_name)
        self.db = db_pool
        self.alias_map = {}

    def _normalize(self, name: str) -> str:
        return unicodedata.normalize('NFKC', name.strip())

    def _is_cross_lingual(self, a: str, b: str) -> bool:
        has_ko_a = any('\uac00' <= c <= '\ud7a3' for c in a)
        has_ko_b = any('\uac00' <= c <= '\ud7a3' for c in b)
        return has_ko_a != has_ko_b

    async def resolve(self, entity_name: str, group_id: str):
        normalized = self._normalize(entity_name)
        canonical = self.alias_map.get(normalized.lower(), normalized)
        embedding = self.model.encode(f"query: {canonical}").tolist()

        candidates = await self.db.fetch("""
            SELECT uuid, name, 1 - (name_embedding <=> $1::vector) AS cos_sim
            FROM entity_nodes WHERE group_id = $2
              AND 1 - (name_embedding <=> $1::vector) > 0.75
            ORDER BY name_embedding <=> $1::vector LIMIT 10
        """, embedding, group_id)

        best_match, best_score = None, 0
        for c in candidates:
            str_sim = max(
                fuzz.ratio(canonical.lower(), c['name'].lower()),
                fuzz.token_sort_ratio(canonical.lower(), c['name'].lower()),
                fuzz.partial_ratio(canonical.lower(), c['name'].lower()),
            ) / 100.0
            w_str = 0.05 if self._is_cross_lingual(canonical, c['name']) else 0.4
            score = w_str * str_sim + (1 - w_str) * c['cos_sim']
            if score > best_score:
                best_score, best_match = score, c

        if best_score >= 0.85:
            return best_match['uuid'], False
        return None, True
```

---

## AREA 2: Graph traversal search with recursive CTEs and RRF fusion

### Production BFS with depth limiting and cycle detection

```sql
WITH RECURSIVE graph_bfs AS (
    SELECT e.id AS entity_id, e.name, e.entity_type,
        r.relation_type, r.weight, 1 AS depth, ARRAY[seed.id, e.id] AS path
    FROM entities seed
    JOIN entity_relations r ON r.from_entity_id = seed.id
    JOIN entities e ON e.id = r.to_entity_id
    WHERE seed.id = $1
      AND r.relation_type = ANY($2)
      AND (r.valid_to IS NULL OR r.valid_to > now())
    UNION ALL
    SELECT e.id, e.name, e.entity_type,
        r.relation_type, r.weight, gb.depth + 1, gb.path || e.id
    FROM graph_bfs gb
    JOIN entity_relations r ON r.from_entity_id = gb.entity_id
    JOIN entities e ON e.id = r.to_entity_id
    WHERE gb.depth < $3
      AND NOT (e.id = ANY(gb.path))
      AND r.relation_type = ANY($2)
      AND (r.valid_to IS NULL OR r.valid_to > now())
)
SEARCH BREADTH FIRST BY entity_id SET ordercol
SELECT DISTINCT ON (entity_id)
    entity_id, name, entity_type, relation_type, depth, path
FROM graph_bfs ORDER BY entity_id, depth LIMIT 100;
```

### Performance at target scale

| Scale | Depth | Latency (indexed) |
|-------|-------|--------------------|
| 10K nodes, 50K edges | 3 hops | 1–5ms |
| 100K nodes, 500K edges | 3 hops | 5–20ms |
| 1M nodes, 5M edges | 3 hops | 10–50ms |

### Apache AGE: skip it
Recursive CTEs are 1.5–40× faster for simple traversals.

### Three-way RRF function (graph + vector + FTS)

```sql
CREATE OR REPLACE FUNCTION hybrid_graph_search(
    p_query_text TEXT, p_query_embed vector(384), p_seed_id UUID,
    p_match_count INT DEFAULT 20, p_graph_weight FLOAT DEFAULT 1.0,
    p_vector_weight FLOAT DEFAULT 1.0, p_fts_weight FLOAT DEFAULT 0.5,
    p_max_depth INT DEFAULT 2, p_rrf_k INT DEFAULT 60
) RETURNS TABLE(entity_id UUID, name TEXT, entity_type TEXT, rrf_score NUMERIC, sources TEXT)
LANGUAGE sql STABLE AS $$
WITH RECURSIVE graph_walk AS (
    SELECT r.to_entity_id AS eid, 1 AS depth,
           ARRAY[r.from_entity_id, r.to_entity_id] AS path
    FROM entity_relations r WHERE r.from_entity_id = p_seed_id
      AND (r.valid_to IS NULL OR r.valid_to > now())
    UNION ALL
    SELECT r.to_entity_id, gw.depth + 1, gw.path || r.to_entity_id
    FROM entity_relations r JOIN graph_walk gw ON r.from_entity_id = gw.eid
    WHERE gw.depth < p_max_depth AND NOT (r.to_entity_id = ANY(gw.path))
      AND (r.valid_to IS NULL OR r.valid_to > now())
),
graph_ranked AS (
    SELECT eid AS entity_id, ROW_NUMBER() OVER (ORDER BY MIN(depth)) AS rank_ix
    FROM graph_walk GROUP BY eid
),
vector_ranked AS (
    SELECT id AS entity_id, ROW_NUMBER() OVER (ORDER BY embedding <=> p_query_embed) AS rank_ix
    FROM entities ORDER BY embedding <=> p_query_embed LIMIT p_match_count * 2
),
fts_ranked AS (
    SELECT id AS entity_id,
           ROW_NUMBER() OVER (ORDER BY ts_rank_cd(content_tsv,
               websearch_to_tsquery('english', p_query_text)) DESC) AS rank_ix
    FROM entities WHERE content_tsv @@ websearch_to_tsquery('english', p_query_text)
    LIMIT p_match_count * 2
),
combined AS (
    SELECT COALESCE(g.entity_id, v.entity_id, f.entity_id) AS entity_id,
        COALESCE(1.0/(p_rrf_k + g.rank_ix), 0) * p_graph_weight
      + COALESCE(1.0/(p_rrf_k + v.rank_ix), 0) * p_vector_weight
      + COALESCE(1.0/(p_rrf_k + f.rank_ix), 0) * p_fts_weight AS rrf_score,
        concat_ws('+',
            CASE WHEN g.entity_id IS NOT NULL THEN 'graph' END,
            CASE WHEN v.entity_id IS NOT NULL THEN 'vector' END,
            CASE WHEN f.entity_id IS NOT NULL THEN 'fts' END) AS sources
    FROM graph_ranked g
    FULL OUTER JOIN vector_ranked v ON g.entity_id = v.entity_id
    FULL OUTER JOIN fts_ranked f ON COALESCE(g.entity_id, v.entity_id) = f.entity_id
)
SELECT c.entity_id, e.name, e.entity_type, c.rrf_score, c.sources
FROM combined c JOIN entities e ON e.id = c.entity_id
ORDER BY c.rrf_score DESC LIMIT p_match_count;
$$;
```

### Critical indexes

```sql
CREATE INDEX idx_rel_from_type ON entity_relations (from_entity_id, relation_type);
CREATE INDEX idx_rel_to_type ON entity_relations (to_entity_id, relation_type);
CREATE INDEX idx_rel_from_active ON entity_relations (from_entity_id, relation_type) WHERE valid_to IS NULL;
CREATE INDEX idx_entities_embed ON entities USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=200);
CREATE INDEX idx_entities_fts ON entities USING GIN (content_tsv);
CREATE INDEX idx_entities_name_trgm ON entities USING GIN (name gin_trgm_ops);
```

### PostgreSQL configuration for 24GB ARM

```ini
shared_buffers = '6GB'
effective_cache_size = '18GB'
work_mem = '256MB'
maintenance_work_mem = '1GB'
max_parallel_workers_per_gather = 2
statement_timeout = '10s'
```

---

## AREA 3: Korean full-text search on ARM

### pg_trgm is useless for Korean
3-byte trigrams = 1 Korean character. Confirmed by PostgreSQL core developer.

### Comparison

| Feature | PGroonga | textsearch_ko (mecab) | pg_bigm |
|---------|----------|----------------------|---------|
| Speed | ~50× faster than pg_bigm | Comparable to native FTS | Slowest |
| Korean+English | Automatic | Via 'korean' config | Bigram for everything |
| ts_rank integration | No (own pgroonga_score()) | Yes — full ts_rank | No |
| ARM64 | ✅ apt packages | ✅ with --build flag | ✅ from source |

### Recommendation
Start with **PGroonga** (TokenBigram, zero config). Upgrade to textsearch_ko for ts_rank if needed.

```bash
sudo apt install -y postgresql-16-pgdg-pgroonga
```

```sql
CREATE EXTENSION pgroonga;
CREATE INDEX idx_docs_pgroonga ON documents USING pgroonga (content);
SELECT id, content, pgroonga_score(tableoid, ctid) AS score
FROM documents WHERE content &@~ '한국어 검색' ORDER BY score DESC;
```
