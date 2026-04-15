# 리서치 #5: 출력 포맷 + 중간 저장소

> 연구 일자: 2026-04-15
> 성격: 딥리서치 — 자비스 서버 가동 전 추출 결과의 저장과 활용
> 상태: 결과 대기

(리서치 결과 여기에 붙여넣기)
# From 91 transcripts to a living knowledge base: the intermediate storage playbook

**Use JSON-per-session files plus a parallel JSONL replay log, committed to git, with soft-normalized entities.** This hybrid gives you the fastest path to CLAUDE.md generation (your top priority), zero-infrastructure storage during the 2-day deadline, full git diffability, and a clean replay-ready format for eventual JARVIS import. Every major KG construction pipeline — Graphiti, LangChain, LlamaIndex — separates extraction from storage with a lightweight intermediate representation; your approach should do the same, optimized for your immediate CLAUDE.md use case rather than premature database engineering.

The critical insight from production systems: **don't normalize entities fully at extraction time, but don't leave them completely raw either.** A "soft normalization" approach — canonical names plus preserved aliases — gives you clean CLAUDE.md summaries immediately while preserving the raw material JARVIS needs for proper entity resolution later.

---

## CLAUDE.md generation pipeline: the primary value path

Claude Code loads CLAUDE.md files hierarchically — user-level (`~/.claude/CLAUDE.md`), project root (`./CLAUDE.md`), and subdirectory files on-demand — concatenating them all into context. Every CLAUDE.md token loads on **every API call** and survives compaction, making this the highest-leverage file in your entire workflow. Anthropic recommends **under 200 lines** (~1,500–2,000 tokens). Community consensus caps at 300 lines. HumanLayer's analysis found that Claude Code's system prompt already contains ~50 instructions, and frontier models reliably follow only ~150–200 instructions total — overstuffed CLAUDE.md files cause uniform degradation across all instructions.

The right strategy for 91 sessions of extracted knowledge is **progressive disclosure**: a lean, hand-curated root CLAUDE.md containing only universally applicable facts, pointing to detailed reference documents that Claude reads on-demand via `@path` imports. HumanLayer strongly advises against auto-generating the root CLAUDE.md itself ("it's the highest leverage point of the harness"), but auto-generating the reference documents it points to is exactly right.

### Recommended CLAUDE.md template

**Root `CLAUDE.md`** (target: 100–150 lines, ~1,200 tokens):

```markdown
# [Project Name]

[One-sentence description. Core tech stack.]

## Architecture
- `src/server/` — API server, route handlers
- `src/models/` — PostgreSQL schema, entity types  
- `src/pipeline/` — extraction and resolution logic
[5-10 key directory entries only]

## Commands
- `npm run build`: Compile TypeScript
- `npm test`: Run full test suite  
- `npm run test:unit -- path/to/file`: Single test file

## Key conventions
- [3-5 critical rules Claude repeatedly violates]
- NEVER [high-stakes anti-pattern with alternative]
- ALWAYS [non-obvious requirement]

## Domain terminology
- **Entity**: name + type + aliases, resolved via embedding similarity
- **KnowledgeFact**: entity + predicate + object_value, bitemporally versioned
[5-10 domain terms mapped to code concepts]

## Critical warnings
- NEVER modify migration files after deployment
- Bitemporal updates require all 4 timestamps; omitting valid_to creates open intervals

## Extracted knowledge (auto-updated)
For detailed context from past sessions, read the relevant file before starting:
- Architecture decisions and rationale: @docs/decisions.md
- Lessons learned and debugging patterns: @docs/lessons.md  
- Known gotchas and workarounds: @docs/gotchas.md
- Entity and concept reference: @docs/entities.md
```

**Reference document `docs/decisions.md`** (auto-generated from extracted facts, no size limit since loaded on-demand):

```markdown
# Architecture decisions

## Embedding model selection
**Decision**: Use multilingual-e5-small-ko (384 dims, ONNX int8)
**Context**: Needed Korean full-text support with low latency
**Trade-off**: Smaller model loses nuance on complex English passages
**Sessions**: ep_012, ep_034

## Bitemporal data model
**Decision**: 4-timestamp model (valid_from, valid_to, recorded_at, superseded_at)
**Context**: Facts change over time; need to track both real-world and system time
**Sessions**: ep_007, ep_008, ep_015
```

**Reference document `docs/lessons.md`**:

```markdown
# Lessons learned

## Patterns that work
**pgvector HNSW indexing**: Build index AFTER bulk insert, not during.
  Uses ef_construction=128, m=16 for 384-dim vectors. (ep_041)

## Anti-patterns to avoid  
**Eager entity resolution**: Resolving entities during LLM extraction
  causes cascading errors. Extract raw, resolve in pipeline. (ep_056)

## Debugging insights
**PGroonga tokenizer mismatch**: Korean text must use TokenMecab,
  not TokenBigram. Symptom: zero results on Korean queries. (ep_023)
```

### Keeping CLAUDE.md fresh

**For reference documents** (`docs/decisions.md`, etc.): regenerate entirely from the current extracted knowledge base whenever you re-extract or add new sessions. Since these files are auto-generated, treat them as build artifacts — overwrite, don't append.

**For root CLAUDE.md**: manually edit only. Update when Claude repeatedly makes the same mistake (add the correction), when architecture changes, or during periodic review. Never auto-regenerate.

**Handling superseded facts** is critical. In your extraction output, each fact carries `confidence` and `evidence_type` fields. When generating reference docs, sort facts by recency (session date) and confidence, and explicitly mark superseded decisions:

```markdown
## Database choice
~~**Decision (superseded ep_003)**: Use SQLite for development~~  
**Decision (ep_019)**: PostgreSQL 16 for all environments
**Reason for change**: PGroonga and pgvector require PostgreSQL
```

### Hierarchical CLAUDE.md for multi-project knowledge

For your 91 transcripts spanning multiple projects, use this hierarchy:

- **`~/.claude/CLAUDE.md`**: Personal preferences, universal coding style
- **Per-project `CLAUDE.md`**: Project-specific conventions and commands
- **Per-project `docs/`**: Auto-generated knowledge reference files
- **`.claude/rules/`**: Path-scoped rules with YAML `paths` frontmatter (e.g., API patterns only for `src/api/**`)

The `.claude/rules/` directory is especially useful for large projects — rules load only when Claude works in matching paths, keeping context lean.

---

## The intermediate format: JSON + JSONL hybrid wins

Comparing all five options against your constraints — 2-day deadline, CLAUDE.md as immediate output, eventual PostgreSQL import, git-friendliness, and debuggability — the hybrid approach dominates.

**Raw JSON files (one per session)** score highest for CLAUDE.md generation, git-friendliness, and debuggability. Each file is self-contained, human-readable, and trivially iterable. At 91 files averaging ~47KB each, every file opens instantly in any editor, diffs cleanly in git, and can be processed with `jq`. Re-extraction means overwriting a single file with zero coordination.

**JSONL replay log** adds the import-ready dimension. Each line maps to one `store_memory` API call or one database upsert, with extraction IDs enabling idempotent replay. JSONL's streaming nature — one bad line doesn't corrupt the file — makes it the format of choice for spaCy/Prodigy and most NLP pipelines. Prodigy's documentation explains: "JSONL doesn't require parsing the entire file, which results in overall better performance."

**SQLite** offers schema validation and relational queries, but it's a binary file that can't be diffed in git, requires a query layer to build, and adds 4–6 hours of implementation time you don't have. It becomes worthwhile at 10K+ facts when cross-session queries matter — not at your current scale.

**SQLite + sqlite-vec** adds embedding-based search atop SQLite. sqlite-vec reached v0.1.7 (March 2026) as a Mozilla Builders project with 6.6K stars, but it's still alpha-quality for ANN indexes and had a maintenance hiatus in mid-2025. For a CLAUDE.md-first workflow, vector search at the intermediate stage adds complexity without proportional value — save it for pgvector in JARVIS.

**Direct-to-PostgreSQL** eliminates the intermediate step entirely. Tempting in theory, but server provisioning is the very bottleneck that created this problem. If Docker Compose can get PostgreSQL + pgvector + PGroonga running in hours and you're confident in the schema, this is defensible. But it couples extraction to infrastructure readiness — a risky bet on a 2-day deadline.

### Recommended file structure

```
knowledge-extraction/
├── prompts/
│   ├── entity_extraction_v1.md
│   ├── fact_extraction_v1.md
│   └── relation_extraction_v1.md
├── scripts/
│   ├── extract.py
│   ├── generate_claude_md.py
│   └── import_to_jarvis.py
├── extracted/
│   ├── sessions/              # 91 JSON files (git-tracked)
│   │   ├── ep_001.json
│   │   ├── ep_002.json
│   │   └── ...
│   ├── entities.jsonl         # Replay-ready (git-tracked)
│   ├── facts.jsonl
│   └── relations.jsonl
├── output/
│   ├── CLAUDE.md              # Hand-curated root
│   └── docs/                  # Auto-generated reference
│       ├── decisions.md
│       ├── lessons.md
│       ├── gotchas.md
│       └── entities.md
└── extraction_manifest.json   # Run metadata
```

### Per-session JSON schema

```json
{
  "session_id": "ep_042",
  "source_file": "transcripts/2025-11-03_jarvis-entity-resolution.md",
  "session_date": "2025-11-03",
  "project": "jarvis",
  "session_goal": "implement entity resolution pipeline",
  "extraction": {
    "model": "claude-sonnet-4-20250514",
    "prompt_version": "v1.0",
    "prompt_hash": "sha256:a1b2c3...",
    "extracted_at": "2026-04-15T09:30:00Z",
    "batch_id": "batch_2026-04-15_001"
  },
  "entities": [
    {
      "extraction_id": "ep042_ent_001",
      "canonical_name": "pgvector",
      "original_mention": "the pgvector extension",
      "entity_type": "library",
      "aliases": ["pg_vector", "pgvector extension"],
      "confidence": 0.95,
      "evidence_type": "explicit"
    }
  ],
  "facts": [
    {
      "extraction_id": "ep042_fact_001",
      "entity_name": "pgvector",
      "predicate": "requires_version",
      "object_value": "PostgreSQL 16+",
      "source_quote": "pgvector 0.7 needs PostgreSQL 16 or later",
      "source_turn_range": [15, 16],
      "confidence": 0.92,
      "evidence_type": "explicit",
      "content_hash": "md5:pgvector|requires_version|PostgreSQL 16+"
    }
  ],
  "relations": [
    {
      "extraction_id": "ep042_rel_001",
      "from_entity": "JARVIS",
      "to_entity": "pgvector",
      "relation_type": "depends_on",
      "source_quote": "JARVIS uses pgvector for similarity search",
      "confidence": 0.90
    }
  ],
  "fragments": [
    {
      "content": "HNSW index should be built after bulk insert, not during",
      "type": "lesson",
      "keywords": ["pgvector", "HNSW", "performance"],
      "importance": 8,
      "source_turn_range": [22, 23]
    }
  ],
  "conversation_summary": "Implemented entity resolution pipeline with alias dict → embedding → hybrid scoring. Discovered PGroonga requires TokenMecab for Korean."
}
```

---

## Entity resolution: soft-normalize now, fully resolve at import

Every major production KG pipeline extracts first and resolves second, but with varying degrees of in-extraction normalization. **Graphiti** extracts entities via zero-shot LLM prompts, then runs entity resolution as a separate asynchronous step using entropy-gated fuzzy matching with deterministic IR front-ends — they explicitly moved away from LLM-only resolution because it "created variance, retry loops, and token burn." **LangChain's LLMGraphTransformer** uses lightweight prompt-based coreference ("always use the most complete identifier") but performs no formal post-extraction resolution. **iText2KG/ATOM** separates extraction and resolution into distinct modules, using cosine similarity thresholds (0.8 for entities) rather than LLMs, achieving **93.8% latency reduction** versus Graphiti. **LlamaIndex PropertyGraphIndex** has no built-in entity resolution at all — users must implement custom deduplication.

The academic literature reinforces this pattern. LINK-KG (2025) showed that pre-extraction coreference resolution reduced node duplication by **45.2%**. CORE-KG (2025) demonstrated a two-stage post-extraction approach — fuzzy string matching to build clusters, then LLM-selected canonical representatives — reducing duplication from 30.4% to 20.3%.

**For your 500–2,000 facts and ~200–500 entities, soft normalization wins decisively.** Full normalization at extraction time makes prompts complex, errors costly (wrong merges are hard to undo), and cross-session resolution impossible without an entity registry the LLM can reference. Pure denormalization produces messy intermediate data that's hard to turn into clean CLAUDE.md summaries. The middle ground — instruct the LLM to use consistent canonical names within each session and capture aliases alongside — gives you readable summaries immediately while preserving the raw material JARVIS needs.

Concretely: each extracted entity should carry `canonical_name` (LLM's best guess at the standard name), `original_mention` (exact text from transcript), and `aliases[]` (other forms seen in the same session). Cross-session resolution — "the pgvector extension" in transcript 1 equaling "pgvector" in transcript 54 — should be deferred to your JARVIS pipeline's alias dict → embedding → hybrid scoring system, which has the global view that per-session extraction cannot.

---

## Metadata envelope: what to capture beyond JARVIS's schema

Production systems universally track **source provenance** (Graphiti's episode chains, LangChain's `source: Document`), **temporal metadata** (Graphiti's bi-temporal model, Diffbot's crawl timestamps), and **confidence signals** (Diffbot's Knowledge Fusion scores, NELL's per-fact confidence). The Leipzig survey on KG construction (2023) recommends "deep/statement-level provenance" — knowing which specific text paragraph produced each fact — as the key differentiator enabling fact-level corrections without re-running entire pipelines.

Your metadata envelope should have three tiers:

**Must-have fields** (Tier 1): `source_transcript_id`, `source_turn_range` (which message turns), `source_text_hash` (SHA256 for dedup), `extraction_model`, `extraction_timestamp`, `prompt_version`, `confidence` (0.0–1.0), and `evidence_type` ("explicit" | "inferred" | "implied"). These six fields enable debugging, reproducibility, quality filtering, and idempotent re-extraction.

**Recommended fields** (Tier 2): `project_name`, `session_goal`, `session_phase` (planning/implementation/debugging/review), `original_mention`, `canonical_name`, `aliases[]`, `chunk_index`/`chunk_total`, and `session_date`. These power CLAUDE.md generation (grouping by project, chronological ordering) and JARVIS import (entity resolution hints, temporal validity).

**Nice-to-have fields** (Tier 3): `extraction_batch_id`, `extraction_config_hash`, `model_temperature`, `llm_reasoning` (chain-of-thought), `conflicting_fact_ids`, `supersedes_fact_id`. These matter for iterative prompt improvement and conflict detection but aren't worth the complexity on day one.

The `extraction_manifest.json` file should track run-level metadata: total counts, prompt hashes, model versions, and processing timestamps. This is your reproducibility anchor — given the manifest and the prompts directory, anyone can verify or re-run the extraction.

---

## Import strategy: intermediate to JARVIS

When JARVIS is operational, import via **direct SQL upserts with content-hash-based idempotency**, not API replay. API replay runs each fact through your full application stack including LLM-based entity resolution, which at ~600K+ tokens per conversation (based on Graphiti benchmarks) would cost far more than the ~$100 budget for the initial extraction. Direct SQL upserts insert 2,000 rows in seconds with full control over conflict resolution.

**Idempotency uses three complementary strategies.** Extraction IDs (`{session_id}_{type}_{sequence}`) serve as the primary unique constraint — re-extracting a session with better prompts produces the same IDs, cleanly replacing old data. Content hashes (`md5(entity_name || predicate || object_value)`) catch semantic duplicates across sessions — two different transcripts producing "pgvector requires PostgreSQL 16+" generate the same hash. Bitemporal "last writer wins" via `WHERE EXCLUDED.extraction_timestamp > fact.extraction_timestamp` ensures newer extractions always supersede older ones.

**Import order matters for foreign key integrity**: entities first (upserted by `entity_type + normalized_name`), then facts (keyed by `extraction_id`, referencing resolved `entity_id`), then relations (keyed by `from_entity_id + to_entity_id + relation_type`). Run entity resolution as a second pass after initial bulk insert — first deterministic matching (normalized name), then embedding-based fuzzy matching for candidates above your similarity threshold, then optional LLM verification for ambiguous pairs.

Graphiti's `add_episode_bulk()` is instructive as a counter-example: it's explicitly designed **only for populating empty graphs**, skips edge invalidation (temporal conflict resolution), and is not idempotent — re-running creates duplicates. Multiple users report bugs with bulk ingestion (GitHub issues #223, #882). Your JSONL replay approach with content-hash dedup is more robust.

```sql
-- Entity upsert pattern
INSERT INTO entity (name, entity_type, normalized_name, aliases)
VALUES ($1, $2, lower(regexp_replace($1, '[^a-z0-9가-힣]', '', 'gi')), $3)
ON CONFLICT (entity_type, normalized_name)
DO UPDATE SET
  aliases = array_cat(entity.aliases, EXCLUDED.aliases),
  recorded_at = now()
RETURNING id;

-- Fact upsert pattern  
INSERT INTO knowledge_fact (
  extraction_id, entity_id, predicate, object_value,
  source_quote, source_episode_id, content_hash,
  valid_from, recorded_at
)
VALUES ($1, $2, $3, $4, $5, $6,
  md5($2 || '|' || $3 || '|' || $4)::uuid, $7, now())
ON CONFLICT (content_hash)
DO UPDATE SET
  source_quote = COALESCE(EXCLUDED.source_quote, knowledge_fact.source_quote),
  recorded_at = now()
WHERE EXCLUDED.extraction_id > knowledge_fact.extraction_id;
```

---

## How production pipelines validate these recommendations

**Graphiti** ingests data as episodes with bi-temporal metadata, preserves raw source text as ground truth ("non-lossy"), and uses LLM extraction with hybrid entity resolution. Its bulk ingestion bugs and high token costs (600K+ per conversation) validate the choice to separate extraction from resolution and avoid API-replay-based import.

**LangChain's LLMGraphTransformer** uses `GraphDocument` as its intermediate representation — a Pydantic model containing `nodes[]`, `relationships[]`, and `source: Document` — then converts to Cypher MERGE statements. Its node ID–based dedup (same `id` = same entity) maps directly to the content-hash approach recommended here.

**spaCy/Prodigy** uses JSONL specifically because "it can be read line by line without parsing the entire file" and supports complex nested structures with per-record error isolation. Their annotation format includes `_input_hash` and `_task_hash` for deduplication, `score` for confidence, and `source` for model provenance — the same metadata fields recommended in the envelope specification above.

**Mem0** automatically splits conversations into atomic facts and uses an ADD/UPDATE/DELETE determination against existing memories via similarity search. Its `memory_export` feature uses customizable JSON schemas, validating the approach of schema-flexible JSON extraction with structured JSONL for replay.

**cole-medin/claude-memory-compiler** is the most directly relevant project: it hooks into Claude Code sessions, extracts decisions and patterns into daily logs, then compiles them into cross-referenced knowledge articles. At personal scale (50–500 articles), its author found that **structured index files outperform vector similarity search** — reinforcing the CLAUDE.md-first approach over sqlite-vec.

---

## Version control and re-extraction strategy

At **4.3MB input and ~2–5MB extracted output**, plain git handles everything. No need for DVC, Git LFS, or dedicated versioning tools. Commit extraction scripts, prompts, per-session JSON files, JSONL replay logs, and the extraction manifest. DVC becomes worthwhile only if extraction becomes iterative over weeks with large binary artifacts — revisit at that point.

When re-extracting with improved prompts, the `extraction_id` scheme (`{session_id}_{type}_{sequence}`) ensures old facts for that session are cleanly replaced via upsert. The `content_hash` catches semantic duplicates across sessions. Version your prompts in the `prompts/` directory with explicit version numbers; the `extraction_manifest.json` records which prompt version produced each batch. This gives you full traceability: given any fact in the knowledge base, you can trace it to the specific transcript, turn range, model, and prompt version that produced it.

ML/NLP teams follow five reproducibility principles that apply here: version everything (code + data + model + config), use content-addressed storage for deduplication, maintain provenance chains from output to input, define pipelines as code, and treat extraction outputs as immutable — never edit in place, always re-extract and replace.

---

## When to upgrade: scale guidance

| Fact count | Recommended format | Rationale |
|---|---|---|
| **500–2,000** (you are here) | JSON + JSONL in git | Zero infrastructure, maximum agility |
| **2,000–10,000** | Same, possibly add SQLite for queries | Cross-session analytics become useful |
| **10,000–50,000** | JSONL + SQLite primary | JSON files become unwieldy; need indexed lookups |
| **50,000–100,000** | SQLite with migration plan | Approaching SQLite's practical ceiling for complex queries |
| **100,000+** | PostgreSQL required | Concurrent access, pgvector, PGroonga, row-level security |

JSON files break down around **10,000–50,000 facts** — not from file size, but from the inability to run cross-session queries without loading everything into memory. JSONL stays viable longer as a streaming format but lacks indexing for random access. SQLite handles up to ~100K facts comfortably for a single-user scenario; migrate to PostgreSQL when you need concurrent writers, vector search, or Korean full-text via PGroonga.

The **JSONL canonical format scales gracefully across all stages**: it's the extraction output, the git-tracked artifact, the SQLite import source, and the PostgreSQL replay log. Even at 500K facts (~250MB at ~500 bytes/fact), JSONL files process efficiently with streaming line-by-line readers.

---

## A note on sqlite-vec

Not worth adding to your 2-day critical path. Vector search delivers value in the final PostgreSQL + pgvector system, not the intermediate stage. sqlite-vec (v0.1.7, March 2026) is a promising Mozilla Builders project with 6.6K GitHub stars, but it's still alpha-quality for ANN indexes and adds embedding generation to an already tight timeline. For the CLAUDE.md-first workflow, structured text search via `grep` and `jq` on your JSON/JSONL files is faster to implement and sufficient for 500–2,000 facts.

---

## Conclusion

The path from 91 transcripts to actionable knowledge has three phases, each with a clear deliverable. **Phase 1 (days 1–2)**: extract to JSON-per-session with soft-normalized entities, generate JSONL replay log, auto-generate reference docs, hand-curate root CLAUDE.md. **Phase 2 (when JARVIS launches)**: import via direct SQL upserts using content-hash idempotency, run entity resolution pipeline as a second pass. **Phase 3 (ongoing)**: hook new Claude Code sessions into the extraction pipeline (consider claude-memory-compiler's approach), regenerate reference docs, manually update root CLAUDE.md.

The most counterintuitive finding: at personal scale (50–500 knowledge articles), **structured index files outperform vector similarity search** for retrieval. This validates prioritizing clean, well-organized CLAUDE.md reference documents over vector search infrastructure. The intermediate format isn't just a waypoint to JARVIS — the JSON files and auto-generated docs are themselves the primary knowledge retrieval system until JARVIS proves its value in production.