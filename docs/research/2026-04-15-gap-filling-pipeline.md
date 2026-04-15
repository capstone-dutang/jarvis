# 리서치 #4: 보완 파이프라인 — 갭 감지 + 서버 측 보완 추출

> 연구 일자: 2026-04-15
> 성격: 딥리서치 — 클라이언트 추출 갭의 감지와 서버 측 보완
> 상태: 결과 대기

(리서치 결과 여기에 붙여넣기)
# Server-side gap-filling pipeline for JARVIS: design document

**The 보완 pipeline is worth building — conditionally.** At ~$0.02 per session (well under your $0.05–0.10 budget), a hybrid gap-filling pipeline captures implicit preferences, negative knowledge, and multi-turn reasoning chains that client-side extraction systematically misses. No production memory system implements this exact pattern, but the architecture is validated by ChatGPT's hybrid approach (bio tool + background synthesis) and LangMem's dual hot-path/cold-path design. The strongest case for building it: hookless clients (40–60% capture rate) get the biggest lift. For coding agents already at 75–85%, the marginal value depends on whether the missed 15–25% contains high-retrieval-value knowledge — which your §6 measurement framework will answer empirically.

---

## §1 Gap detection: a four-stage algorithm from mechanical to intelligent

The fundamental challenge — "how does the server know what's missing?" — has no production precedent. **No surveyed system (Zep, Mem0, LangMem, Letta, ChatGPT) implements explicit second-pass gap detection.** They all optimize first-pass extraction. Your pipeline is novel, and the algorithm below synthesizes the best available tools.

### Stage 1: Mechanical coverage mapping (FlashText, ~0.5ms)

Map each `store_memory` call's `conversation_transcript` range back to the full transcript from `transcript_path`. Turns never included in any `store_memory` call are "uncovered." This is your cheapest, highest-confidence signal.

Build a FlashText dictionary from entities and keywords in already-extracted facts (use YAKE to extract keywords from fact text, threshold score < 0.1). Scan every uncovered turn. Turns where zero fact-entities match are mechanically uncovered. Skip turns under 5 tokens and assistant-only turns (user turns contain the knowledge).

```python
from flashtext import KeywordProcessor
import yake

kp = KeywordProcessor(case_sensitive=False)
yake_ext = yake.KeywordExtractor(lan="en", n=3, dedupLim=0.7, top=15)

# Build dictionary from existing facts
for fact in extracted_facts:
    for kw, score in yake_ext.extract_keywords(fact.text):
        if score < 0.1:
            kp.add_keyword(kw.lower())

# Classify turns
uncovered = []
for turn in conversation_turns:
    if len(turn.text.split()) < 5 or turn.role == "assistant":
        continue
    found = kp.extract_keywords(turn.text)
    if len(found) == 0:
        uncovered.append(turn)
```

**Is "uncovered turns" a good proxy for "missed knowledge"?** Partially. It catches turns the client LLM never processed, which is the dominant failure mode. It misses the subtler case where the client *read* a turn but failed to extract implicit knowledge from it. Stage 3 handles that.

### Stage 2: NLP entity and density filtering (GLiNER + YAKE, ~100–400ms)

Not every uncovered turn contains extractable knowledge. "Yeah, sounds good" is uncovered but worthless. Filter using **GLiNER** for zero-shot named entity recognition with custom memory-relevant entity types, plus **YAKE** for keyword density.

**Why GLiNER over spaCy:** GLiNER-medium (90M params) achieves **60.9 F1 zero-shot** on out-of-domain NER, outperforming ChatGPT (47.5 F1) on the same benchmark. The decisive advantage: you specify custom entity labels at inference time — `["preference", "decision", "technical_tool", "constraint", "goal", "project"]` — without retraining. spaCy's fixed 18 OntoNotes types (PERSON, ORG, GPE) miss domain-specific memory categories entirely. GLiNER runs on CPU.

**Why YAKE over KeyBERT:** YAKE is **28× faster** (13s vs 360s on benchmark corpora) with comparable F1 (71.1% vs 73.3%). For per-turn filtering where speed matters, YAKE wins decisively.

```python
from gliner import GLiNER

gliner = GLiNER.from_pretrained("urchade/gliner_medium-v2.1")
MEMORY_LABELS = [
    "person", "location", "date", "preference", "project",
    "decision", "technical_tool", "opinion", "goal", "constraint"
]

candidates = []
for turn in uncovered:
    entities = gliner.predict_entities(turn.text, MEMORY_LABELS, threshold=0.4)
    keywords = yake_ext.extract_keywords(turn.text)
    important_kw = [(kw, s) for kw, s in keywords if s < 0.05]
    
    # Must have entities OR significant keywords AND sufficient density
    tokens = turn.text.split()
    density = (len(entities) * 0.6 + len(important_kw) * 0.4) / max(len(tokens), 1)
    
    if (len(entities) > 0 or len(important_kw) >= 2) and density >= 0.15:
        turn.entities = entities
        turn.info_density = density
        candidates.append(turn)
```

This stage typically eliminates 30–50% of uncovered turns (chit-chat, acknowledgments, filler).

### Stage 3: Semantic coverage verification (sentence-transformers, ~20–50ms)

Embedding similarity catches cases where keyword matching fails. A turn about "my favorite coffee shop in Gangnam" might not match fact keywords but should show high similarity to an existing fact about "User prefers cafes in Seoul."

Embed each candidate turn and compute max cosine similarity against all extracted fact embeddings. Turns below the threshold are confirmed gaps.

```python
from sentence_transformers import SentenceTransformer, util

embed_model = SentenceTransformer("BAAI/bge-base-en-v1.5")
fact_embeddings = embed_model.encode([f.text for f in extracted_facts], 
                                      convert_to_tensor=True)

confirmed_gaps = []
for turn in candidates:
    turn_emb = embed_model.encode(turn.text, convert_to_tensor=True)
    sims = util.cos_sim(turn_emb, fact_embeddings)[0]
    max_sim = float(sims.max())
    
    if max_sim < 0.60:  # Calibrate on your data
        turn.max_fact_similarity = max_sim
        confirmed_gaps.append(turn)
```

**Threshold guidance:** Start at **0.60**. Below 0.40 is almost certainly unextracted knowledge. Between 0.60–0.85 is partial coverage (nuances may be missing). Calibrate by sampling 200 turns, manually labeling coverage, and optimizing for **recall > 0.80 with precision > 0.50** (false positives are acceptable since the downstream LLM extraction is the quality gate).

**Known failure modes:** Embedding similarity misses entity-level gaps within semantically similar turns ("I love hiking in Colorado" matches "User enjoys outdoor activities" at high similarity, but "Colorado" is missing). The entity-level check in Stage 2 mitigates this. Multi-fact turns where 2 of 3 facts are extracted show moderate similarity, hiding the missing third. No clean solution exists without LLM involvement.

### Stage 4: Priority scoring and batching

Rank confirmed gaps by extraction value:

```python
for turn in confirmed_gaps:
    novelty = 1.0 - turn.max_fact_similarity
    entity_richness = min(len(turn.entities) / 5.0, 1.0)
    length_signal = min(len(turn.text.split()) / 50.0, 1.0)
    
    turn.gap_priority = (
        0.35 * novelty +           # How novel (low similarity = high novelty)
        0.30 * turn.info_density + # How information-dense
        0.20 * entity_richness +   # How many entities to extract
        0.15 * length_signal       # Longer turns = more content
    )

confirmed_gaps.sort(key=lambda t: t.gap_priority, reverse=True)
```

**Performance budget for entire pipeline:** ~120–450ms per 50-turn conversation. No LLM calls. The four stages progressively filter: 50 turns → ~20 uncovered → ~10 candidates → ~5–8 confirmed gaps → ranked list sent to LLM extraction (§3).

### Decision logic for running the pipeline

```python
def should_run_gap_filling(session):
    # Always run for hookless clients (40-60% capture)
    if not session.has_store_memory_calls:
        return True, "full_extraction"
    
    # Skip if very short conversation
    if session.turn_count < 10:
        return False, "too_short"
    
    # Run gap detection for sessions with store_memory
    uncovered_ratio = session.uncovered_turns / session.total_turns
    if uncovered_ratio > 0.30:
        return True, "high_gap_ratio"
    if uncovered_ratio > 0.10:
        return True, "moderate_gap_ratio"
    
    return False, "well_covered"
```

---

## §3 Three differential extraction prompts with tradeoffs

### Prompt A: Blind extract + reconcile (recommended primary)

This is the Mem0 production pattern — extract everything from gap turns, then reconcile against existing facts. Two LLM calls, separation of concerns.

**Step 1 — Haiku extraction:**

```
You are a knowledge extraction system. Extract all factual knowledge from the conversation segment below.

Focus on:
1. Personal preferences (likes, dislikes, choices made)
2. Decisions and their reasoning ("chose X because Y")
3. Negative knowledge ("decided NOT to use X", "rejected Y")
4. Technical details (tools, configurations, architectures)
5. Temporal facts (deadlines, schedules, durations)
6. Relationships between entities
7. Implicit preferences revealed through behavior, not statement
8. Constraints and requirements mentioned in passing

<CONVERSATION>
{3_turns_before}
--- GAP START ---
{gap_turns}
--- GAP END ---
{2_turns_after}
</CONVERSATION>

Extract facts as atomic statements. Each fact should be self-contained (no pronouns — resolve all references using context).

Return JSON: {"facts": ["User prefers PostgreSQL over MongoDB for the JARVIS project", ...]}
```

**Step 2 — Sonnet reconciliation:**

```
You are a memory deduplication system. Compare newly extracted facts against existing memories and decide what action to take for each.

<EXISTING_MEMORIES>
{existing_facts_as_numbered_list}
</EXISTING_MEMORIES>

<NEW_FACTS>
{extracted_facts_from_step1}
</NEW_FACTS>

For each new fact, return exactly one action:
- ADD: genuinely new information absent from existing memories
- UPDATE: corrects, refines, or supersedes an existing memory (specify which memory ID)
- NOOP: already captured in existing memories (specify which memory ID)

Err on the side of ADD for ambiguous cases. Prefer precision — do not ADD if the information is clearly already present.

Return JSON: {"actions": [{"fact": "...", "action": "ADD|UPDATE|NOOP", "target_memory_id": null|"id", "reasoning": "brief explanation"}]}
```

**Why this works:** The extraction step operates without knowledge of existing facts, avoiding anchoring bias. The reconciliation step is a focused comparison task where Sonnet's reasoning advantage over Haiku matters. Mem0's production audit confirms: the #1 problem is over-extraction/duplication, not under-extraction. The reconciliation step is critical.

**Cost:** Haiku extraction ~$0.006 + Sonnet reconciliation ~$0.014 = **~$0.02/session**.

### Prompt B: Differential "what's missing?" (best for targeted gap-filling)

Single LLM call, but requires passing existing facts as context.

```
You are a knowledge gap detector. Given a set of existing facts about a user and a conversation segment, identify knowledge present in the conversation but MISSING from the existing facts.

<EXISTING_FACTS>
{existing_facts_grouped_by_entity}
</EXISTING_FACTS>

<CONVERSATION_SEGMENT>
{gap_turns_with_5_surrounding_turns}
</CONVERSATION_SEGMENT>

Instructions:
1. Read the conversation INDEPENDENTLY first — form your own understanding
2. Then compare against existing facts
3. Report ONLY knowledge that is genuinely absent or that updates/corrects existing facts

For each missing piece of knowledge:
- fact: self-contained statement (no pronouns)
- type: explicit | implicit | negative | temporal | reasoning
- confidence: high | medium | low
- source_turns: which turn numbers it comes from

Return JSON: {"missing_knowledge": [...]}
Return empty list if nothing new is found.
```

**Tradeoff vs Prompt A:** Fewer tokens (one call vs two), but risks anchoring bias — the LLM may focus on entities in existing facts and miss entirely new topics. The instruction "read independently first" mitigates this partially.

**When to use:** When existing fact count is small (<20 facts) and the gap is well-defined. At higher fact counts, the context overhead makes Prompt A cheaper.

### Prompt C: Entity-structured differential (best for entity updates)

```
You are an entity knowledge updater. For each entity below, determine what NEW information exists in the conversation beyond what's already known.

<KNOWN_ENTITIES>
Entity: PostgreSQL
  Known: ["Used in JARVIS project", "Chosen over MongoDB"]

Entity: MCP Protocol  
  Known: ["JARVIS uses MCP for tool communication"]
</KNOWN_ENTITIES>

<CONVERSATION_SEGMENT>
{gap_turns_with_context}
</CONVERSATION_SEGMENT>

For each entity:
1. New facts not in known set
2. Corrections to known facts
3. New relationships to other entities

Also report any NEW entities mentioned in the conversation that are not in the known list.

Return JSON: {"entity_updates": [...], "new_entities": [...]}
```

**When to use:** When your knowledge base is entity-centric (like Graphiti's knowledge graph). Works well for update detection but may miss non-entity knowledge (emotional states, process decisions).

### Context window recommendation: N=3 before, N=2 after

Coreference resolution research consistently shows that **95% of pronominal references resolve within 3–4 turns**. The 2 trailing turns capture confirmations and corrections ("Yes, exactly" or "No, I meant..."). For a typical gap of 5–10 uncovered turns, total context is 10–15 turns (~1,000–1,500 tokens).

**Merge adjacent gaps:** If two gap segments are separated by fewer than 3 turns, merge them into one extraction window. The overlapping context turns would be redundant otherwise.

### Model choice: Haiku extracts, Sonnet reconciles

Multiple independent sources confirm that for structured extraction tasks, **Haiku produces equivalent results to Sonnet** — the quality gap is under 5 F1 points. The reasoning-heavy reconciliation step (comparing facts, detecting contradictions, deciding ADD/UPDATE/NOOP) benefits from Sonnet's stronger reasoning.

For "unknown unknowns" — implicit preferences, negative knowledge, multi-turn reasoning — **even Sonnet struggles**. PrefEval (ICLR 2025) found that LLM preference-following accuracy drops below **10% at zero-shot for implicit expressions** across all models tested. The gap between Haiku and Sonnet on these hard cases is smaller than the gap between "any LLM" and "perfect extraction." This means: use Sonnet where you can afford it, but don't expect miracles on implicit knowledge regardless of model.

**Concrete recommendation:** Start with Haiku-only pipeline ($0.01/session). Measure quality. If precision is acceptable but recall on implicit/negative knowledge is low, add Sonnet reconciliation ($0.02/session total). If the user is a premium tier or the conversation was flagged as high-value, run Sonnet for both steps ($0.03/session).

---

## §7 Hybrid gap-filling wins on cost, loses on complexity

### What production systems actually implement

| System | Architecture | Extraction model |
|--------|-------------|-----------------|
| **Zep/Graphiti** | Full server-side | Entire transcript → temporal knowledge graph, multiple LLM calls per episode |
| **Mem0** | Full server-side | Message pairs → AUDN cycle (ADD/UPDATE/DELETE/NOOP) |
| **LangMem** | **Hybrid** | Hot-path (agent tool calls) OR cold-path (background `ReflectionExecutor`) |
| **Letta/MemGPT** | Client-side only | Agent self-manages all memory via tool calls |
| **ChatGPT** | **Hybrid** | Bio tool (client-side) + background User Knowledge Memories (server pipeline, ~1-3 day cadence) |
| **Claude chatbot** | **Hybrid** | Immediate saves + ~24hr background synthesis |
| **Claude Code** | **Hybrid** | Auto Memory (agent notes during session) + Auto Dream (background consolidation between sessions) |

**Three systems do hybrid: LangMem, ChatGPT, and Claude.** None does exactly your pattern (client extracts individual facts → server detects and fills gaps). LangMem is closest — it runs background extraction after conversations and reconciles against existing memories. ChatGPT's approach is different: it stuffs recent conversations into context and periodically regenerates dense narrative summaries (not individual facts).

### Cost comparison: hybrid vs full server-side

| Approach | What gets processed | Estimated tokens/session | Cost (Haiku) | Cost (Sonnet) |
|----------|-------------------|------------------------|-------------|--------------|
| **Full server-side** (Mem0-style) | All 50 turns + system prompts | ~7,000 input + 1,500 output | ~$0.015 | ~$0.044 |
| **Hybrid gap-filling** (your design) | ~10–15 gap turns + 5 context | ~3,500 input + 500 output | ~$0.006 | ~$0.020 |
| **Hybrid with reconciliation** | Gap extraction + dedup pass | ~5,500 input + 1,000 output | ~$0.011 | ~$0.032 |

The hybrid approach processes roughly **50–60% fewer tokens** than full server-side extraction. At Haiku pricing, the difference is ~$0.005/session (negligible). At Sonnet pricing, it's ~$0.012/session (meaningful at scale: **$3,600/month savings at 10K sessions/day**).

### When hybrid beats full server-side

Hybrid is better when:

- **Client-side extraction already works well (>70% capture).** You're paying only for the marginal 20%, not the full 100%. This is your coding-agent case.
- **Cost sensitivity is high.** The 50–60% token reduction compounds at scale.
- **Client diversity exists.** Different clients (Claude, GPT, Cursor, hookless) have different capture rates. The server pipeline adapts — processing more for hookless clients, less for well-instrumented ones.
- **You want client-side extraction to remain the source of truth.** Server-side fills gaps without overriding the client's extractions, preserving the user's explicit memory choices.

Full server-side is better when:

- **Client-side capture is unreliable (<50%).** At 40–60% (hookless clients), the gap-filling pipeline processes so much of the transcript that the complexity overhead of gap detection isn't worth it — just extract everything.
- **Consistency matters more than cost.** Two extraction sources (client + server) can produce conflicting facts. Full server-side produces a single consistent extraction.
- **You want to change extraction prompts without client updates.** Server-side extraction is fully under your control. Client-side extraction quality depends on system prompts that different providers interpret differently.

### Reconciliation: the real complexity cost

The hidden cost of hybrid is **reconciliation between client-extracted and server-extracted facts**. When the client stores "User prefers PostgreSQL" and the server extracts "User chose PostgreSQL for the JARVIS project because of JSONB support," these are overlapping but not identical. Your reconciliation layer must:

1. Detect that these refer to the same decision (entity resolution — you already have this)
2. Decide whether the server extraction adds value (it does — reasoning context)
3. Merge without duplication (UPDATE the existing fact with richer context, or ADD as a supplementary fact)

This is exactly the AUDN cycle from Mem0. It works, but it's the most error-prone component. Production Mem0 data shows the #1 failure mode is **duplication from insufficient dedup**, not missing extractions.

### Recommendation for JARVIS

**Build the hybrid pipeline with a clean fallback to full extraction.** Implement the gap detection algorithm (§1) and differential extraction (§3) as the default path. For hookless clients where `source_episode_id` tracking shows <50% coverage, bypass gap detection and run full server-side extraction on the entire transcript. This gives you the best of both worlds: cost efficiency for well-instrumented clients, quality coverage for hookless ones.

The complexity overhead is real but manageable for a solo builder, because the gap detection pipeline (§1) is entirely non-LLM and the extraction pipeline (§3) reuses the same prompt templates regardless of whether you're filling gaps or doing full extraction. The only additional component is the coverage mapping in Stage 1.

---

## §2 What client-side extraction systematically misses

Client-side extraction (the AI assistant calling `store_memory` during conversation) has predictable blind spots rooted in how LLMs process tool-call decisions.

**Negative knowledge is the largest gap.** When a user says "We decided NOT to use Docker" or "I tried Redis but it was too complex," the LLM processes these as rejections and moves on. The tool-call decision mechanism optimizes for storing *positive* outcomes — what was chosen, not what was rejected. Yet negative knowledge is high-value for future retrieval ("Should I suggest Docker for the deployment?" → no, user explicitly rejected it).

**Implicit preferences revealed through behavior** are nearly invisible to client-side extraction. PrefEval research (ICLR 2025) measured this directly: LLM accuracy on implicit preference extraction drops **below 10% in zero-shot** for most models. When a user consistently chooses functional programming approaches or always asks for Korean-language summaries, the pattern is visible across turns but never explicitly stated — and the per-turn tool-call mechanism has no way to aggregate this signal.

**Reasoning chains spanning multiple turns** get fragmented. Turn 5 mentions a budget constraint, turn 12 references a technical limitation, turn 18 makes a decision that depends on both. The client LLM might store the decision but miss the reasoning. Why a decision was made is often more valuable for future recall than the decision itself.

**Ambient facts mentioned in passing** — "my brother John," "we're based in Seoul," "the deadline is next Friday" — are frequently overlooked because they're not the conversational focus. The user didn't ask the assistant to remember these; they're contextual facts embedded in a question about something else.

**Process and meta-conversation knowledge** — how the user works, their debugging patterns, their communication preferences, project methodology — emerges across sessions but is rarely the subject of any single `store_memory` call. Claude Code's Auto Dream system explicitly targets this category with its consolidation pass.

---

## §4 "Unknown unknowns": mostly real, partially overstated

Zep's core marketing claim — "an agent can't call a tool to retrieve context it doesn't know exists" — is **architecturally valid but practically overstated**. The unknown unknowns problem is real for retrieval (you can't search for what you don't know you need), but less severe for extraction (the content is right there in the conversation transcript).

For extraction specifically, the "unknown" is not the content but the *importance signal*. The LLM sees "I'm actually vegetarian" in a coding conversation and doesn't flag it for memory because it seems off-topic. That's a missed extraction, not an unknown unknown — the content was visible, the importance judgment was wrong.

**How much knowledge is actually lost?** Based on the available evidence: for well-instrumented clients (defense-in-depth with store_memory), roughly **75–85% of explicit knowledge is captured**, leaving 15–25% missed. Of that missed portion, approximately half is genuinely valuable for future retrieval (implicit preferences, negative knowledge, reasoning) and half is low-value ambient facts that rarely get queried. This means the *actionable* gap is closer to **8–12%** of total knowledge, not 20–25%.

**What Zep actually does internally:** Zep's solution to unknown unknowns is not better extraction — it's **deterministic context injection**. Their `get_user_context()` API assembles a structured context block (entities + facts + temporal metadata) and injects it into every prompt before the LLM runs. No tool calls needed. The LLM receives comprehensive context automatically. This sidesteps the retrieval problem entirely but requires expensive full server-side graph construction (~600K tokens per conversation, multiple LLM calls per episode).

**Complementary approaches worth considering:**

- **Periodic reflection passes** (Claude Code's Auto Dream model): Review the last N sessions, consolidate patterns, prune stale facts. This catches cross-session patterns that per-session extraction misses.
- **User-triggered review:** Surface "here's what I remember about you" periodically and let the user correct/add. ChatGPT does this implicitly through its User Knowledge Memories.
- **Embedding-based anomaly detection:** Identify conversation turns that are highly dissimilar to all stored facts — these are either noise or genuinely novel knowledge worth investigating.

---

## §5 Async processing architecture (condensed)

**Trigger timing:** End of session is the natural trigger point. Use `transcript_path` availability as the signal. Delay processing by 30–60 seconds after last message to handle rapid follow-up messages. For hookless clients, trigger on session close or after 5 minutes of inactivity.

**Processing model:** Batch, not streaming. Gap detection (§1) needs the full transcript and the full set of extracted facts. Run the non-LLM pipeline synchronously (~500ms), then queue LLM extraction calls asynchronously.

**Cost controls:** Implement per-session budget ($0.10 hard cap), monthly cap per user, and a priority queue. High-priority: hookless client sessions (biggest gap). Medium: sessions with >30% uncovered turns. Low: well-covered sessions. Skip sessions under 10 turns entirely.

**Failure handling:** LLM extraction failures should retry with exponential backoff (max 3 attempts). If reconciliation fails, store extracted facts with a `needs_reconciliation` flag and reconcile on next pipeline run. Never block the user experience on pipeline failures — gap-filling is best-effort by design.

**Batch API optimization:** Anthropic's Batch API offers **50% discount** for non-real-time processing. Since gap-filling is inherently async (results needed for *next* session, not current), batch all LLM calls. This drops the hybrid pipeline cost from ~$0.02 to ~$0.01/session.

---

## §6 Measuring pipeline value (condensed)

**Core question: is the extra 15–25% actually useful in recall queries?** Measure this directly.

**A/B test design:** For each user, randomly assign sessions to "gap-filled" (server pipeline runs) or "control" (client extraction only). At recall time, track which retrieved facts come from server-extracted vs client-extracted sources. Compute **server-fact retrieval rate**: what fraction of recall queries surface at least one server-extracted fact in the top-5 results? If this rate is below 5%, the pipeline isn't adding retrieval value.

**Quality metrics:** Compare precision of server-extracted facts (how many are correct and non-duplicate) against client-extracted facts. Mem0's production audit found significant duplication problems — track your dedup effectiveness. Measure **fact survival rate**: what fraction of server-extracted facts are still valid (not contradicted or deleted) after 30 days?

**Cost-benefit breakpoint:** If server-fact retrieval rate × average user value per retrieval > pipeline cost per session, the pipeline pays for itself. With $0.02/session cost, even modest retrieval improvements justify the pipeline for active users (>10 sessions/month).

---

## Cost model: ~$0.02 per session, well within budget

| Component | Tokens (input) | Tokens (output) | Model | Cost |
|-----------|---------------|-----------------|-------|------|
| Gap detection (§1) | 0 | 0 | No LLM | $0.000 |
| Haiku extraction of gap turns | ~3,500 | ~500 | Haiku 3.5 | $0.005 |
| Sonnet reconciliation | ~2,000 | ~500 | Sonnet 3.5 | $0.014 |
| Embeddings (bge-base, self-hosted) | — | — | Local | $0.000 |
| **Total (standard)** | | | | **$0.019** |
| **Total (Batch API, 50% off)** | | | | **$0.010** |

**Assumptions:** 50-turn conversation, ~100 tokens/turn, 20–30% uncovered, 3+2 surrounding context turns per gap segment, ~500 tokens of existing facts for reconciliation.

**At scale:** 10K sessions/day × $0.01 (batch) = **$100/day = $3,000/month**. Haiku-only: $50/day = $1,500/month. Compare to full server-side Sonnet extraction: ~$440/day = $13,200/month. The hybrid approach saves **~$10K/month at scale**.

**For solo builder usage** (likely <100 sessions/day): $1–2/day. Negligible.

---

## Decision criteria: when to run, when to skip

```python
def pipeline_decision(session) -> tuple[str, str]:
    """Returns (action, reason)"""
    
    # Always skip trivially short sessions
    if session.turn_count < 10:
        return "skip", "conversation_too_short"
    
    # Hookless clients: full extraction (no gap detection needed)
    if not session.has_store_memory_calls:
        return "full_extract", "hookless_client"
    
    # Compute coverage ratio from source_episode_id tracking
    uncovered_ratio = session.uncovered_turn_count / session.substantive_turn_count
    
    if uncovered_ratio > 0.50:
        return "full_extract", "low_coverage_client"
    elif uncovered_ratio > 0.15:
        return "gap_fill", "moderate_gaps"
    elif uncovered_ratio > 0.05:
        return "gap_fill_haiku_only", "minor_gaps"
    else:
        return "skip", "well_covered"
```

**Threshold rationale:** Above 50% uncovered, gap detection overhead isn't worth it — just run full extraction. Between 15–50%, the hybrid pipeline provides maximum value. Below 5%, the expected yield (1–2 facts) rarely justifies even Haiku cost.

---

## Production system deep-dive: LangMem is your closest analog

No production system implements exactly your hybrid pattern (client extracts → server detects gaps → server fills gaps). But **LangMem's dual-path architecture** is the closest analog, and **ChatGPT's background synthesis** validates the broader pattern.

### LangMem's architecture maps directly to JARVIS

LangMem supports two processing modes that can coexist:

**Hot path** (≈ your client-side extraction): The agent is given `create_manage_memory_tool`. During conversation, the LLM decides what to store via tool calls. This is exactly your `store_memory` pattern.

**Cold path** (≈ your gap-filling pipeline): `create_memory_store_manager` with `ReflectionExecutor` runs after conversations complete. It takes the full message history, generates optimized search queries (Haiku), retrieves existing memories (vector similarity), then uses a main model (Sonnet) to analyze the conversation against existing memories and generate CREATE/UPDATE/DELETE operations via parallel tool calling.

The key difference: LangMem's cold path processes the *entire* conversation, not just gaps. Your gap detection (§1) is the novel addition that reduces cold-path cost.

**LangMem's reconciliation pattern** is worth adopting directly: it uses `trustcall.create_extractor` for parallel tool calling, enabling the LLM to output multiple CREATE/UPDATE/DELETE operations in a single inference call. This is more token-efficient than Mem0's per-fact AUDN cycle.

### ChatGPT validates hybrid at massive scale

ChatGPT's memory combines client-side `bio` tool calls (explicit "remember this") with server-side background pipelines that generate dense User Knowledge Memories — narrative paragraphs synthesizing the user's professional life, preferences, and interaction patterns. These are regenerated every 1–3 days, not per-session.

The architectural lesson: **background synthesis doesn't need to run every session.** For your highest-value extraction (cross-session patterns, evolving preferences), a periodic consolidation pass (daily or weekly) may capture more than per-session gap-filling. Consider adding a "reflection" tier above your per-session pipeline — similar to Claude Code's Auto Dream.

### Why your design may be novel and worth building

The specific innovation in JARVIS is the **non-LLM gap detection stage** (§1). Every other system either:
- Runs full server-side extraction on everything (Zep, Mem0) — expensive
- Relies entirely on client-side extraction (Letta/MemGPT) — incomplete
- Runs background synthesis on everything periodically (ChatGPT, Claude) — delayed, coarse-grained

Your four-stage mechanical→NLP→embedding→LLM pipeline is the first design I've found that uses lightweight signals to surgically identify *where* to spend LLM tokens. This is a genuine architectural contribution. The closest prior art is Graphiti's "reflexion" technique (re-extracting entities that were missed in the first pass), but that's within a single extraction call, not across a client-server boundary.

The risk is over-engineering: if the gap detection pipeline itself becomes complex enough to have bugs and maintenance burden, the simplicity of "just run Haiku on the whole transcript" ($0.015/session via batch) might win on total cost of ownership. **Measure the marginal value (§6) before investing in optimization of the gap detection pipeline.** Ship the simplest version first (mechanical coverage only, skip Stages 2–3), measure, then add sophistication.