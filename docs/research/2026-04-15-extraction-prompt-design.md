# 리서치 #1: 대화→구조화 지식 추출 프롬프트 설계

> 연구 일자: 2026-04-15
> 성격: 딥리서치 — 추출 프롬프트 + production 시스템 분석
> 상태: 결과 대기

(리서치 결과 여기에 붙여넣기)
# Extracting structured knowledge from AI conversation transcripts

**The highest-quality extraction pipeline is multi-pass, source-grounded, and schema-enforced — not single-prompt.** Production systems like Graphiti use 6–10 LLM calls per episode, not one, because multi-stage extraction with gleaning catches 30%+ more entities than single-pass approaches. The key engineering insight across every production system examined: extraction prompts that demand verbatim source quotes, constrain entity types to enums, and process entities before relations produce dramatically better knowledge graphs than naive "extract everything" prompts. Below is a full literature review, production prompt forensics from 8 systems, evidence-backed prompt engineering patterns, and a ready-to-use prompt library targeting JARVIS's exact schema.

---

## 1. What the research says about dialogue-to-KG extraction

### The dialogue-document extraction gap is enormous

Extracting triples from conversations is fundamentally harder than from documents. Vossen et al. (2024, arXiv:2412.18364) built dedicated NLU models for extracting symbolic triples from social dialogue and achieved only **51.1% precision for complete triples** on single utterances — dropping substantially for multi-turn conversational triples. The dominant subjects and objects were pronouns "I" and "you." Document-level IE systems like REBEL (Huguet Cabot & Navigli, EMNLP 2021) achieve far higher precision because they process well-formed encyclopedic text with explicit entity mentions and unambiguous relations.

Five specific challenges distinguish dialogue extraction from document extraction: (1) **heavy pronoun use** requiring multi-turn coreference resolution, (2) **ellipsis** — incomplete utterances depending on prior context, (3) **implicit information** where decisions are communicated through agreement/silence rather than explicit statements, (4) **speaker perspective mixing** facts with opinions, emotions, and judgments, and (5) **fragmented facts** where a single piece of knowledge is distributed across multiple turns between speakers.

PAED (Zhu et al., ACL 2023) provides the most relevant entity taxonomy: **105 relation types** for persona attribute extraction from dialogues, organized around personal attributes like `like_music`, `work_as`, `live_in_city`, `have_family`. Their PersonaExt dataset contains 1,896 re-annotated triplets. For JARVIS's open-domain personal memory, this taxonomy is directly applicable as a starting vocabulary for predicates. GoLLIE (Sainz et al., ICLR 2024) demonstrates that **annotation guidelines matter more than example count** — defining entity types as Python classes with docstring descriptions outperforms GPT-3.5 on zero-shot NER, validating the enum-constrained entity type approach.

### Chain-of-thought improves extraction, with caveats

ERA-CoT (ACL 2024) breaks extraction into five steps: entity extraction → explicit relationship extraction → implicit relationship inference → integration → final prediction. This hierarchical approach consistently outperforms flat extraction. CoT-ER (Ma et al., EMNLP 2023 Findings) shows CoT improves relation extraction on common-sense domains (Wikipedia-based FewRel 1.0) but **not on specialized domains** where LLM knowledge is limited — suggesting CoT helps most when the LLM has relevant world knowledge.

GPT-NER (Wang et al., NAACL 2025) introduces a **self-verification strategy**: after extracting entities, the LLM is prompted to verify whether each entity actually belongs to its claimed type. This reduces hallucinated entities substantially and is the first LLM-based approach to match fully supervised NER baselines. The self-verification pattern is directly implementable in JARVIS's extraction pipeline.

For few-shot examples, the evidence converges on **3–5 examples** as the sweet spot. A 2025 Nature study on dynamic prompting for NER found 5-shot improves F1 by 8.8%, while 10-shot shows diminishing returns at 6.3%. GoLLIE's ablation confirms that **quality and diversity of examples matters more than quantity** — similarity-based retrieval of examples outperforms random selection.

### Korean-English code-switching is under-researched

No dedicated Korean-English code-switching NER paper was found. XLM-RoBERTa (Conneau et al., 2020) is the dominant multilingual model for code-switched NER, noted as "well suited for code-switching." Korean-specific models (KLUE-RoBERTa) outperform multilingual models on Korean-only text due to better tokenization, but for mixed Korean-English text, the practical recommendation is to use multilingual embeddings (which JARVIS already does with multilingual-e5-small-ko) and preserve original language in source quotes. KnowCoder-X (2024, arXiv:2411.04794) demonstrates code-based multilingual IE with unified schema representation across languages.

### AGREE framework clarification

AGREE (Ye et al., NAACL 2024) is about **citation grounding in generated text**, not extraction per se. It fine-tunes LLMs to self-ground claims and provide citations to retrieved documents, achieving >30% relative improvement in grounding quality. The exact "98.9% fabrication reduction" figure appears to originate from empirical testing of the source_quote grounding pattern specifically, not from the AGREE paper itself. The core mechanism — requiring the LLM to anchor every output claim to a verbatim passage — is what JARVIS's `source_quote` field implements.

---

## 2. How production systems actually extract knowledge

### Graphiti: the gold standard for temporal conversation memory

Zep's Graphiti (github.com/getzep/graphiti) uses the most sophisticated extraction pipeline found. Each episode triggers **6–10+ LLM calls** through this sequence: entity extraction → entity reflexion (catch missed entities) → edge/fact extraction → edge reflexion → node deduplication → edge deduplication/contradiction detection → entity summary generation → embedding generation.

The entity extraction prompt instructs:

```
You are an AI assistant that extracts entity nodes from conversational messages.
Your primary task is to extract and classify the speaker and other significant
entities mentioned in the conversation.
...
Pronoun references such as he/she/they or this/that/those should be disambiguated
to the names of the reference entities. Only extract distinct entities from the
CURRENT MESSAGE. Don't extract pronouns like you, me, he/she/they, we/us as entities.
```

Graphiti's edge extraction uses **SCREAMING_SNAKE_CASE** relation types and critically includes bi-temporal fields — `valid_at` (when fact became true) and `invalid_at` (when fact stopped being true) — both in ISO 8601. The edge dedup prompt performs **simultaneous duplicate detection AND contradiction detection**: given existing facts and a new fact, it identifies both semantic duplicates and facts the new information invalidates. This is the production implementation of correction handling.

Key lesson from Graphiti Issue #1193: users report the multi-call pipeline is expensive. Issue #1171: pronoun-referenced entities from previous messages get missed, causing incorrect edge creation — the reflexion/gleaning step was added specifically to address this.

### Mem0: cautionary tale of permissive extraction

Mem0 (github.com/mem0ai/mem0) uses a simpler two-phase pipeline: fact extraction → ADD/UPDATE/DELETE/NOOP decision. The extraction prompt instructs the LLM to be a "Personal Information Organizer" focused on preferences, personal details, plans, health info, and professional details. The critical update prompt presents old memories alongside new facts and asks the LLM to classify each as ADD, UPDATE, DELETE, or NONE.

**The most important finding from Mem0 is Issue #4573**: a production audit of 10,134 memory entries found **97.8% were junk**. Root causes: the permissive extraction prompt captured everything including boot-file restating (52.7% of junk) and created duplicate extractions (same fact stored 50–200+ times). The issue author concluded: "The extraction prompt is the bottleneck, not the model." This directly validates JARVIS's approach of strict source_quote grounding and enum-constrained entity types as noise reduction mechanisms.

Mem0's graph memory entity extraction uses a notably simple prompt: "You are a smart assistant who understands entities and their types in a given text." It resolves self-references ("I", "me", "my") to the user's ID — a pattern JARVIS should adopt.

### Microsoft GraphRAG: gleaning as multi-pass extraction

GraphRAG (github.com/microsoft/graphrag) uses a single extraction prompt with **gleaning** — additional passes using a `CONTINUE_PROMPT`: "MANY entities and relationships were missed in the last extraction. Remember to ONLY emit entities that match any of the previously extracted types. Add them below using the same format." Default entity types are `[organization, person, geo, event]`, configurable or auto-discovered.

GraphRAG defaults to **300-token chunks** with 100-token overlap (reduced from 1,200 in v0.2.0), though documentation recommends **1,200 tokens with 1 gleaning** for better quality. The extraction prompt uses a distinctive tuple-delimited format with custom separators rather than JSON, and includes **3 detailed few-shot examples** with fictional characters. Entity extraction is estimated to constitute ~75% of indexing cost.

### Letta/MemGPT: the agent-as-extractor paradigm

Letta takes a fundamentally different approach: **the conversational LLM itself decides what to store** via tool calls (`core_memory_append`, `core_memory_replace`). There is no separate extraction pipeline. The ChatMemory class provides "human" and "persona" memory blocks (each with a 2,000-character default limit), and the agent writes to them as part of its response generation. This is elegant but means extraction quality depends entirely on the conversational model's judgment, with no dedicated extraction optimization. For JARVIS's batch processing of historical sessions, this approach is less suitable than a dedicated extraction pipeline.

### LangChain LLMGraphTransformer: clean schema enforcement

LangChain's prompt is notable for its explicit coreference instruction: "If an entity, such as 'John Doe', is mentioned multiple times in the text but is referred to by different names or pronouns (e.g., 'Joe', 'he'), always use the most complete identifier for that entity throughout the knowledge graph." It uses two modes: structured output (preferred, with dynamically-generated Pydantic schemas) and JSON parsing fallback. Default entity types: `PERSON, PLACE, ORGANIZATION`. Default relations: `USED_BY, USED_FOR, LOCATED_IN, PART_OF, WORKED_ON, HAS, IS_A, BORN_IN, DIED_IN, HAS_ALIAS`.

### LlamaIndex, Cognee, and Hindsight

LlamaIndex's `SchemaLLMPathExtractor` uses `Literal` types for entity and relation constraints with a validation schema mapping which relations are valid for which entity types — a pattern directly applicable to JARVIS. Cognee uses Pydantic-based structured output with either Instructor or BAML, achieving backend-agnostic extraction. Hindsight (Vectorize) organizes memory into four logical networks (World Facts, Agent Experiences, Synthesized Observations, Evolving Beliefs) and achieves **83.6% on LongMemEval** using an open-source 20B model with its retain/recall/reflect architecture.

---

## 3. Evidence-backed prompt engineering patterns

### Multi-pass extraction wins, but the margin depends on implementation

MuSEE (Multi-stage Structured Entity Extraction) "markedly outperforms" single-stage extraction by breaking the task into entity detection → property extraction → value generation. CogCanvas (2025) achieves **32.4% accuracy on LoCoMo** using a two-pass gleaning strategy (vs 24.6% for single-pass RAG), specifically targeting in the second pass: entities referred to by pronouns, omitted subjects, and implicit causal relationships. Graphiti's reflexion prompts serve the same function.

For JARVIS, the practical trade-off: **use single-pass for real-time extraction** (where latency matters and the model sees fresh context) and **multi-pass with gleaning for batch processing** (where quality is paramount and cost is irrelevant).

### Schema enforcement eliminates parsing failures

OpenAI's Structured Outputs use **constrained sampling via context-free grammars** — the model literally cannot produce output violating the schema. This gives **100% schema compliance** at the API level. Anthropic's tool_use provides equivalent schema enforcement through tool input schemas. The critical finding from "Let Me Speak Freely?" (Tam et al., 2024): **format restrictions degrade reasoning performance by 10–15%** on reasoning tasks, but classification/extraction tasks show minimal degradation. The recommendation: use JSON with schema enforcement for extraction (it's classification-like), but if you need complex reasoning about what to extract, use a **two-step pattern** — free-form reasoning first, structured formatting second (recovered accuracy from 48% to 61% in production testing).

### Source quote enforcement: deterministic post-processing is the gold standard

Three complementary patterns enforce verbatim quotes:

1. **Prompt-level**: "The source_quote must be findable via exact string match in the original text. If you cannot find a verbatim quote, do not extract the fact."
2. **Architectural**: Deterministic Quoting (Matt Yeung) — the LLM generates references to source text; a **separate deterministic module** replaces potentially-hallucinated quotations with verbatim copies from source material. "The only way to guarantee that an LLM has not transformed text: don't send it through the LLM in the first place."
3. **Validation**: Google LangExtract automatically detects extractions that cannot be located in source text → sets `char_interval = None`. Post-process: `[e for e in result.extractions if e.char_interval]`.

For JARVIS: implement all three. The prompt demands source_quote; the post-processor verifies each quote exists as an exact substring in the source transcript; any extraction with a non-matching quote is rejected.

### Positive framing beats negative instructions

Research shows LLMs **perform worse with negative prompts as they scale** — the "reverse activation" problem means "don't extract greetings" can paradoxically focus the model on greetings. Hugging Face's official guidance: "Instructions should focus on 'what to do' rather than 'what not to do'." The effective pattern is category-gating: "Classify each extracted fact into one of: decision, preference, biographical_fact, project_status, technical_choice. If a statement does not fit any of these categories, do not extract it."

---

## 4. Conversation-specific extraction challenges

### Implicit knowledge: gleaning with inference instructions

For extracting implied decisions like "let's go with Postgres" (implying rejection of alternatives discussed earlier), the effective prompt pattern combines explicit instruction with a gleaning pass:

```
Extract both EXPLICIT and IMPLIED information:
- Decisions implied by choosing one option (implies rejection of discussed alternatives)
- Agreement/disagreement communicated through "yeah, that makes sense" or "hmm, I'm not sure"
- Facts implied by actions described ("I already set up the Postgres instance" = Postgres was chosen)

For each implied extraction, use the source_quote from the statement that implies the fact.
```

CogCanvas's second-pass gleaning specifically targets implicit causal relationships and achieved +7.8pp over single-pass approaches.

### Correction handling: Graphiti's contradiction detection

For "no, not SQLite, I meant PostgreSQL," Graphiti's edge dedup prompt simultaneously checks for contradictions: given `FACT INVALIDATION CANDIDATES` and a `NEW FACT`, the LLM returns which existing facts the new fact contradicts. Those contradicted facts receive an `invalid_at` timestamp rather than being deleted. The prompt instruction for JARVIS:

```
When the user corrects previous information:
- Extract the NEW correct fact with the current timestamp as valid_from
- Mark it as a correction by including "corrects" in the predicate
- The source_quote should capture the correction statement itself

Example: "no, not SQLite, I meant PostgreSQL"
→ fact: {subject: "user", predicate: "corrects_database_choice_to", object: "PostgreSQL",
   source_quote: "no, not SQLite, I meant PostgreSQL"}
```

### Emotional and preference signals in Korean

For "Oracle 때문에 빡쳤다" (was pissed off because of Oracle), structured sentiment analysis extracts (holder, target, expression, polarity) tuples directly mappable to the JARVIS schema. The source quote must preserve the original Korean:

```json
{
  "facts": [{"subject": "user", "predicate": "has_negative_sentiment_toward",
             "object": "Oracle", "source_quote": "Oracle 때문에 빡쳤다"}],
  "entities": [{"name": "Oracle", "entity_type": "technology",
                "source_quote": "Oracle 때문에 빡쳤다"}]
}
```

The prompt instruction: "Preserve source quotes in their original language. If the user speaks Korean, the source_quote must be in Korean. Do not translate quotes."

### Code-heavy conversations: extract intent, not implementation

The effective filtering instruction: "Treat code blocks as context. Extract decisions, rationale, and architectural choices ABOUT code, not the code itself. If a code block demonstrates a technical decision, extract the decision in natural language with a source_quote from the surrounding discussion, not from inside the code block."

Pre-processing can help: programmatically replace code blocks with `[CODE BLOCK: {language} - {line_count} lines]` before sending to the extraction LLM, reducing token usage and preventing syntax-level extraction.

### AI-assistant noise: category-gated filtering

AI conversations contain noise categories absent from human-human dialogue. The anti-pattern list for JARVIS:

- Tool use meta-messages: "Let me search for that", "I'll look that up"
- Capability hedging: "As an AI, I can't...", "I don't have access to..."
- Search result framing: "Based on the search results...", "According to the documentation..."
- Conversational scaffolding: greetings, acknowledgments, "Sure, I can help with that"
- Self-referential: "Let me think about this step by step"

Use positive framing: "Extract ONLY substantive knowledge — decisions made, facts stated about real entities, preferences expressed, problems identified, and action items. Skip conversational scaffolding and the AI assistant's meta-commentary about its own process."

---

## 5. Measuring extraction quality without drowning in annotation

### Benchmarks and what they actually measure

**LongMemEval** (Wu et al., ICLR 2025) tests five abilities: information extraction, multi-session reasoning, temporal reasoning, knowledge updates, and abstention. It spans ~115K to ~1.5M tokens of chat history. Key finding: commercial chat assistants show a **30% accuracy drop** in sustained interactions, and even with perfect recall, "accurately reading retrieved items is still non-trivial." Zep/Graphiti scores **94.8%** on the DMR subset.

**LoCoMo** (Maharana et al., ACL 2024) tests very long-term conversational memory with 1,986 questions across five reasoning types. Its critical finding: RAG works best when "dialogues are transformed into a database of assertions (observations) about each speaker's life and persona" — **directly validating extraction-based approaches** like JARVIS. However, LLMs still lag humans by 56%, especially on temporal reasoning (73% gap).

**KGGen's MINE metrics** (Stanford) provide the most useful evaluation framework for extraction itself: MINE-1 measures information retention (how much knowledge the KG captured — OpenIE captures ~30%, GraphRAG ~48%, KGGen ~66%), and MINE-2 measures usefulness for downstream QA.

### Precision-recall trade-off for memory systems

For downstream LLM consumption, **lean toward recall**: ACM research (2025) on KGQA found "high recall—even at the cost of precision—can lead to better overall performance, as LLMs are resilient to input noise." However, Mem0's 97.8% junk problem shows that extremely low precision creates its own costs. The source_quote grounding pattern provides a natural precision floor — you can only extract what's actually said.

### LLM-as-judge: useful with guardrails

LLM judges achieve **80–90% agreement** with human evaluators, matching inter-annotator agreement. But known biases include verbosity bias (>90% preference for longer answers), self-enhancement bias (GPT-4 favors itself with 10% higher win rate), and position bias. For extraction evaluation, use **focused rubric questions** ("Does each extracted entity appear in the source text?") rather than holistic quality judgments, and use a **different model** than the extractor as judge.

### Regression testing without human annotation

Build a golden test set of 50–100 annotated conversation segments. Run extraction on every pipeline change and compute entity/triple F1. Use semantic regression testing: snapshot outputs, compute cosine similarity between old and new versions, flag drops below threshold. Include 5–10 canary conversation segments covering edge cases (temporal references, corrections, bilingual mixing, code-heavy discussions) and verify extraction on every prompt change.

---

## 6. Batch architecture for 91 historical sessions

### Process chronologically with a running entity list

Strong evidence favors **sequential chronological processing** with context-aware entity resolution. Graphiti processes episodes incrementally, resolving each new entity against existing graph nodes. Neo4j's KG construction guide recommends providing "a list of node types extracted in the previous chunks" to maintain consistency. Claude's KG cookbook advises: "When a new document arrives, extract its entities, resolve them against the existing canonical set."

For 91 sessions, sequential processing is not a scale bottleneck (even at 2 minutes per session, the full batch completes in ~3 hours). The benefit: proper temporal ordering enables fact invalidation when later sessions contradict earlier ones, and coreference resolution across sessions (session 1's "my React project" links to session 45's "the frontend framework").

### Full-session processing, not chunking

For extraction (distinct from retrieval), the ATOM framework discovered a **"forgetting effect"** where LLMs omit facts in longer contexts. However, most conversation sessions are under 15K tokens — well within modern model context windows. Full-session processing preserves coreference context critical for dialogue. Only segment sessions exceeding 15K tokens into ~5K-token chunks with 500-token overlap.

### Tiered deduplication: extract with context, merge with embeddings

Use a hybrid approach:

1. **At extraction time**: Pass the current canonical entity list (names + types + aliases) in the extraction prompt, instructing the LLM to use existing canonical names when referring to known entities.
2. **Post-extraction batch**: Run embedding-based deduplication. Cosine similarity ≥0.92 auto-merges; 0.80–0.92 flags for review.
3. **Maintain an alias map**: `{"react": "React.js", "PG": "PostgreSQL", "포스트그레스": "PostgreSQL"}` that grows across sessions.

---

## 7. Ready-to-use prompt library

### Prompt A: main extraction (single-pass, quality-first batch processing)

**Recommended model**: Claude Sonnet/Opus or GPT-4o. Temperature: 0.

```xml
<system>
You are a precise knowledge extraction system for a personal memory server. Your task is to extract structured entities, facts, and relations from an AI assistant conversation transcript.

<schema>
You MUST output valid JSON matching this exact schema:
{
  "entities": [
    {
      "name": "canonical name of the entity",
      "entity_type": "person|project|technology|concept|preference|organization|location|event|resource",
      "source_quote": "exact verbatim substring from the transcript"
    }
  ],
  "facts": [
    {
      "subject": "entity name (must match an entity in the entities list)",
      "predicate": "snake_case_verb_phrase describing the relationship",
      "object": "the value, target, or description",
      "source_quote": "exact verbatim substring from the transcript"
    }
  ],
  "relations": [
    {
      "from": "source entity name (must match an entity in the entities list)",
      "to": "target entity name (must match an entity in the entities list)",
      "relation_type": "snake_case relationship type",
      "source_quote": "exact verbatim substring from the transcript"
    }
  ]
}
</schema>

<rules>
1. SOURCE QUOTE GROUNDING (MANDATORY): Every extracted item MUST include a source_quote that is an EXACT verbatim substring from the transcript. The quote must be findable via exact string match. If you cannot identify a verbatim quote supporting an extraction, do NOT extract it.

2. ENTITY TYPES: Use ONLY these values for entity_type: person, project, technology, concept, preference, organization, location, event, resource. Do not invent new types.

3. PREDICATES: Use free-form snake_case for predicates. Be specific and descriptive. Examples: uses_for_database, decided_against, prefers_over, has_negative_sentiment_toward, works_on, plans_to_migrate_to.

4. WHAT TO EXTRACT — extract ONLY substantive knowledge:
   - Decisions made and their rationale
   - Technology choices, architecture decisions, tool selections
   - Personal preferences, opinions, and sentiments (positive and negative)
   - Biographical and professional facts about the user
   - Project status, goals, and plans
   - Problems identified and solutions chosen
   - Corrections and updates to previously stated facts

5. WHAT TO SKIP — do NOT extract:
   - The AI assistant's meta-commentary ("Let me search for that", "Based on the results")
   - Greetings, pleasantries, conversational scaffolding
   - Code syntax, variable names, import statements, stack traces (extract decisions ABOUT code, not code itself)
   - The AI's capabilities or limitations statements
   - Restated information from system prompts or tool outputs

6. COREFERENCE: Resolve pronouns ("he", "she", "it", "that") to their full entity names. Use the most complete identifier available. If the user refers to themselves, use "user" as the entity name.

7. CORRECTIONS: When the user corrects information ("no, I meant X"), extract the corrected version. Use predicate "corrects_to" or "changed_decision_to" to mark corrections.

8. LANGUAGE: Preserve source_quote in the original language used in the transcript. If the user speaks Korean, the source_quote must be in Korean. Entity names should use their canonical form (e.g., "PostgreSQL" not "포스트그레스" unless the Korean name is the canonical one).

9. IMPLICIT KNOWLEDGE: Extract decisions implied by choosing one option over discussed alternatives. Extract sentiments implied by emotional language.
</rules>

<examples>
<example>
<transcript>
User: 데이터베이스를 PostgreSQL로 가기로 했어. SQLite도 생각해봤는데 JSONB 때문에 PostgreSQL이 더 나아.
Assistant: 좋은 선택이네요. PostgreSQL의 JSONB는 확실히 유연하죠.
User: 그리고 ORM은 안 쓸 거야. 직접 SQL 쓰는 게 더 나아. Prisma 써봤는데 별로였어.
</transcript>
<output>
{
  "entities": [
    {"name": "PostgreSQL", "entity_type": "technology", "source_quote": "데이터베이스를 PostgreSQL로 가기로 했어"},
    {"name": "SQLite", "entity_type": "technology", "source_quote": "SQLite도 생각해봤는데"},
    {"name": "Prisma", "entity_type": "technology", "source_quote": "Prisma 써봤는데 별로였어"},
    {"name": "JARVIS", "entity_type": "project", "source_quote": "데이터베이스를 PostgreSQL로 가기로 했어"}
  ],
  "facts": [
    {"subject": "user", "predicate": "chose_database", "object": "PostgreSQL", "source_quote": "데이터베이스를 PostgreSQL로 가기로 했어"},
    {"subject": "user", "predicate": "considered_but_rejected", "object": "SQLite", "source_quote": "SQLite도 생각해봤는데 JSONB 때문에 PostgreSQL이 더 나아"},
    {"subject": "user", "predicate": "prefers_raw_sql_over_orm", "object": "true", "source_quote": "ORM은 안 쓸 거야. 직접 SQL 쓰는 게 더 나아"},
    {"subject": "user", "predicate": "has_negative_experience_with", "object": "Prisma", "source_quote": "Prisma 써봤는데 별로였어"},
    {"subject": "PostgreSQL", "predicate": "chosen_for_feature", "object": "JSONB support", "source_quote": "JSONB 때문에 PostgreSQL이 더 나아"}
  ],
  "relations": [
    {"from": "user", "to": "PostgreSQL", "relation_type": "selected_as_database", "source_quote": "데이터베이스를 PostgreSQL로 가기로 했어"},
    {"from": "PostgreSQL", "to": "SQLite", "relation_type": "chosen_over", "source_quote": "SQLite도 생각해봤는데 JSONB 때문에 PostgreSQL이 더 나아"}
  ]
}
</output>
</example>

<example>
<transcript>
User: I've been looking into MCP servers for the memory layer. The project name is JARVIS.
Assistant: Interesting! What's your tech stack so far?
User: TypeScript for the MCP server, Python for heavy processing. I'm using Claude through the Anthropic API primarily, but it needs to work with OpenAI too.
User: For the embedding model I'll go with multilingual-e5-small-ko since I talk in both Korean and English.
</transcript>
<output>
{
  "entities": [
    {"name": "JARVIS", "entity_type": "project", "source_quote": "The project name is JARVIS"},
    {"name": "MCP", "entity_type": "technology", "source_quote": "MCP servers for the memory layer"},
    {"name": "TypeScript", "entity_type": "technology", "source_quote": "TypeScript for the MCP server"},
    {"name": "Python", "entity_type": "technology", "source_quote": "Python for heavy processing"},
    {"name": "Claude", "entity_type": "technology", "source_quote": "Claude through the Anthropic API"},
    {"name": "Anthropic API", "entity_type": "technology", "source_quote": "Claude through the Anthropic API primarily"},
    {"name": "OpenAI", "entity_type": "organization", "source_quote": "it needs to work with OpenAI too"},
    {"name": "multilingual-e5-small-ko", "entity_type": "technology", "source_quote": "multilingual-e5-small-ko since I talk in both Korean and English"},
    {"name": "user", "entity_type": "person", "source_quote": "I've been looking into MCP servers"}
  ],
  "facts": [
    {"subject": "JARVIS", "predicate": "is_a", "object": "MCP memory server", "source_quote": "MCP servers for the memory layer. The project name is JARVIS"},
    {"subject": "JARVIS", "predicate": "uses_language", "object": "TypeScript", "source_quote": "TypeScript for the MCP server"},
    {"subject": "JARVIS", "predicate": "uses_language_for_processing", "object": "Python", "source_quote": "Python for heavy processing"},
    {"subject": "user", "predicate": "primary_llm_provider", "object": "Anthropic API", "source_quote": "Claude through the Anthropic API primarily"},
    {"subject": "JARVIS", "predicate": "requires_compatibility_with", "object": "OpenAI", "source_quote": "it needs to work with OpenAI too"},
    {"subject": "JARVIS", "predicate": "uses_embedding_model", "object": "multilingual-e5-small-ko", "source_quote": "multilingual-e5-small-ko since I talk in both Korean and English"},
    {"subject": "user", "predicate": "speaks_languages", "object": "Korean and English", "source_quote": "I talk in both Korean and English"}
  ],
  "relations": [
    {"from": "JARVIS", "to": "MCP", "relation_type": "implements_protocol", "source_quote": "MCP servers for the memory layer"},
    {"from": "JARVIS", "to": "multilingual-e5-small-ko", "relation_type": "uses_for_embeddings", "source_quote": "For the embedding model I'll go with multilingual-e5-small-ko"}
  ]
}
</output>
</example>
</examples>

<existing_entities>
{{CANONICAL_ENTITY_LIST}}
</existing_entities>
When referring to entities that match existing entities above, use the exact canonical name from the list.
</system>

<user>
Extract all entities, facts, and relations from the following conversation transcript. Follow the schema and rules exactly. Every extraction must include a verbatim source_quote.

<transcript>
{{TRANSCRIPT}}
</transcript>

<metadata>
Session ID: {{SESSION_ID}}
Date: {{SESSION_DATE}}
</metadata>

Output only the JSON object. No explanation or commentary.
</user>
```

### Prompt B: real-time extraction (minimal, client-side, $0 cost)

**Recommended model**: Claude Haiku or GPT-4o-mini. Temperature: 0. This runs during conversation as part of the AI client's response cycle.

```xml
<system>
Extract new knowledge from the latest user message in this conversation. Output JSON matching this schema:

{"entities": [{"name": "...", "entity_type": "person|project|technology|concept|preference|organization|location|event|resource", "source_quote": "..."}], "facts": [{"subject": "...", "predicate": "...", "object": "...", "source_quote": "..."}], "relations": [{"from": "...", "to": "...", "relation_type": "...", "source_quote": "..."}]}

Rules:
- Extract ONLY from the latest user message (the delta — what's new this turn)
- source_quote must be an exact substring from the user's message
- Predicates in snake_case
- Resolve pronouns to entity names using conversation context
- Skip greetings, meta-talk, code syntax
- Preserve Korean source_quotes in Korean
- If nothing substantive to extract, return {"entities": [], "facts": [], "relations": []}
</system>

<user>
<conversation_context>
{{RECENT_MESSAGES}}
</conversation_context>

<latest_message>
{{LATEST_USER_MESSAGE}}
</latest_message>

Extract new knowledge from the latest message only. JSON output:
</user>
```

### Prompt C: correction and supersede detection

**Recommended model**: Claude Sonnet or GPT-4o. Temperature: 0.

```xml
<system>
You detect corrections, updates, and superseded information in conversation transcripts. When a user corrects or updates previously stated information, extract both the correction and what it supersedes.

<schema>
{
  "corrections": [
    {
      "new_fact": {
        "subject": "...",
        "predicate": "...",
        "object": "the corrected/updated value",
        "source_quote": "exact verbatim quote of the correction"
      },
      "supersedes": {
        "subject": "...",
        "predicate": "...",
        "object": "the old/incorrect value",
        "source_quote": "exact verbatim quote of the original statement if present, otherwise null"
      },
      "correction_type": "explicit_correction|preference_change|decision_reversal|factual_update",
      "valid_from": "ISO 8601 timestamp or null"
    }
  ]
}
</schema>

<patterns>
Detect these correction patterns:
- "no, not X, I meant Y" → explicit_correction
- "actually, it's Y" → explicit_correction
- "I changed my mind, let's use Y instead" → decision_reversal
- "I used to prefer X but now I prefer Y" → preference_change
- "Update: we're now using Y" → factual_update
- "아니 X 말고 Y" (not X, Y) → explicit_correction
- "X에서 Y로 바꿨어" (changed from X to Y) → decision_reversal
</patterns>

<example>
<transcript>
User: Let's use SQLite for the database.
...
User: Actually no, not SQLite, I meant PostgreSQL. We need JSONB support.
</transcript>
<output>
{
  "corrections": [
    {
      "new_fact": {"subject": "user", "predicate": "chose_database", "object": "PostgreSQL", "source_quote": "not SQLite, I meant PostgreSQL. We need JSONB support"},
      "supersedes": {"subject": "user", "predicate": "chose_database", "object": "SQLite", "source_quote": "Let's use SQLite for the database"},
      "correction_type": "explicit_correction",
      "valid_from": null
    }
  ]
}
</output>
</example>

<example>
<transcript>
User: 처음에는 REST API로 했는데 지금은 MCP로 바꿨어.
</transcript>
<output>
{
  "corrections": [
    {
      "new_fact": {"subject": "user", "predicate": "uses_protocol", "object": "MCP", "source_quote": "지금은 MCP로 바꿨어"},
      "supersedes": {"subject": "user", "predicate": "uses_protocol", "object": "REST API", "source_quote": "처음에는 REST API로 했는데"},
      "correction_type": "decision_reversal",
      "valid_from": null
    }
  ]
}
</output>
</example>
</system>

<user>
Analyze this transcript for corrections, updates, and superseded information.

<transcript>
{{TRANSCRIPT}}
</transcript>

If no corrections are found, return {"corrections": []}.
Output only JSON.
</user>
```

### Prompt D: session summary generation

**Recommended model**: Claude Sonnet or GPT-4o. Temperature: 0.2.

```xml
<system>
Generate a concise session summary for a personal memory system. The summary should capture what was discussed, decided, and accomplished — optimized for future retrieval and context-setting.

<schema>
{
  "session_summary": {
    "title": "brief descriptive title (under 10 words)",
    "summary": "2-4 sentence narrative summary of the session",
    "key_decisions": ["decision 1", "decision 2"],
    "topics_discussed": ["topic1", "topic2"],
    "action_items": ["action 1", "action 2"],
    "open_questions": ["question 1"],
    "emotional_tone": "productive|frustrated|exploratory|decisive|confused|excited",
    "language_used": "en|ko|mixed"
  }
}
</schema>

<rules>
- The summary should be useful for recalling "what happened in this session" months later
- Focus on OUTCOMES and DECISIONS, not the conversational flow
- Include specific technology names, version numbers, and concrete details
- key_decisions should be actionable statements, not vague descriptions
- If the session was bilingual, write the summary in the dominant language used
- Limit to the most important 3-5 decisions and topics
</rules>

<example>
<transcript>
User: JARVIS 프로젝트에 MCP 서버를 TypeScript로 만들기로 했어. 데이터베이스는 PostgreSQL, embedding은 multilingual-e5-small-ko.
Assistant: 좋은 스택이네요. FTS는 어떻게 하실 건가요?
User: 한국어 FTS는 PGroonga 쓸 거야. pg_trgm은 한국어 안 돼서.
</transcript>
<output>
{
  "session_summary": {
    "title": "JARVIS MCP 서버 기술 스택 결정",
    "summary": "JARVIS 프로젝트의 핵심 기술 스택을 확정했다. MCP 서버는 TypeScript, DB는 PostgreSQL, 임베딩은 multilingual-e5-small-ko. 한국어 FTS는 pg_trgm 대신 PGroonga를 선택.",
    "key_decisions": ["MCP server in TypeScript", "PostgreSQL as database", "multilingual-e5-small-ko for embeddings", "PGroonga for Korean FTS (not pg_trgm)"],
    "topics_discussed": ["JARVIS tech stack", "Korean full-text search", "embedding model selection"],
    "action_items": [],
    "open_questions": [],
    "emotional_tone": "decisive",
    "language_used": "mixed"
  }
}
</output>
</example>
</system>

<user>
Generate a session summary for this conversation:

<transcript>
{{TRANSCRIPT}}
</transcript>

<metadata>
Session ID: {{SESSION_ID}}
Date: {{SESSION_DATE}}
</metadata>

Output only JSON.
</user>
```

### Prompt E: entity resolution (batch deduplication)

**Recommended model**: Claude Sonnet or GPT-4o. Temperature: 0.

```xml
<system>
You are an entity resolution system. Given two candidate entities with their context, determine if they refer to the same real-world entity.

<schema>
{
  "is_same_entity": true|false,
  "confidence": "high|medium|low",
  "canonical_name": "the preferred canonical name if same entity, null if different",
  "reasoning": "one sentence explanation"
}
</schema>

<rules>
- Consider name variations (React vs React.js vs ReactJS), abbreviations (PG vs PostgreSQL), Korean/English variants (포스트그레스 vs PostgreSQL)
- Consider entity_type: "React" the library vs "react" the verb are different entities
- Use source_quote context to disambiguate
- If genuinely ambiguous, set is_same_entity to false (prefer false negatives over false merges)
- canonical_name should be the most complete, standard form
</rules>

<example>
<input>
Entity A: {"name": "PG", "entity_type": "technology", "source_quote": "PG로 마이그레이션하자"}
Entity B: {"name": "PostgreSQL", "entity_type": "technology", "source_quote": "PostgreSQL's JSONB support"}
</input>
<output>
{"is_same_entity": true, "confidence": "high", "canonical_name": "PostgreSQL", "reasoning": "PG is a common abbreviation for PostgreSQL, both used in database context"}
</output>
</example>

<example>
<input>
Entity A: {"name": "React", "entity_type": "technology", "source_quote": "React component for the dashboard"}
Entity B: {"name": "React", "entity_type": "concept", "source_quote": "how did the team react to the news"}
</input>
<output>
{"is_same_entity": false, "confidence": "high", "canonical_name": null, "reasoning": "First is React.js library, second is the verb 'to react' — different entity types and meanings"}
</output>
</example>
</system>

<user>
Are these two entities the same?

Entity A: {{ENTITY_A_JSON}}
Entity B: {{ENTITY_B_JSON}}

Output only JSON.
</user>
```

### Prompt F: extraction quality self-check (LLM-as-judge)

**Recommended model**: Use a DIFFERENT model from the extractor. If extracted with Claude Sonnet, judge with GPT-4o, or vice versa. Temperature: 0.

```xml
<system>
You are an extraction quality auditor. Given an original transcript and extracted knowledge (entities, facts, relations), evaluate the extraction quality on specific criteria.

<schema>
{
  "scores": {
    "source_quote_accuracy": {"score": 1-5, "failures": ["list of extractions with invalid quotes"]},
    "entity_completeness": {"score": 1-5, "missed_entities": ["entities mentioned but not extracted"]},
    "fact_accuracy": {"score": 1-5, "hallucinated_facts": ["facts not supported by transcript"]},
    "relation_validity": {"score": 1-5, "invalid_relations": ["relations that are incorrect or unsupported"]},
    "noise_level": {"score": 1-5, "noise_items": ["extractions that are trivial or non-substantive"]}
  },
  "overall_score": 1-5,
  "critical_issues": ["list of the most important problems"],
  "suggestions": ["specific improvements"]
}
</schema>

<scoring_rubric>
source_quote_accuracy:
  5: Every source_quote is an exact verbatim substring found in the transcript
  3: Most quotes are accurate but some have minor modifications
  1: Multiple quotes are fabricated or substantially paraphrased

entity_completeness:
  5: All substantive entities mentioned in the transcript are extracted
  3: Major entities are captured but some secondary entities are missing
  1: Multiple important entities are missed

fact_accuracy:
  5: Every extracted fact is directly supported by the transcript
  3: Most facts are accurate but some make unsupported inferences
  1: Multiple facts are hallucinated or misattributed

relation_validity:
  5: All relations correctly represent connections stated in the transcript
  3: Most relations are valid but some are questionable
  1: Multiple relations are incorrect or fabricated

noise_level:
  5: Every extraction is substantive and worth storing in a memory system
  3: Some trivial or obvious extractions included
  1: Dominated by noise, greetings, or meta-commentary
</scoring_rubric>
</system>

<user>
Evaluate this extraction against the original transcript.

<transcript>
{{TRANSCRIPT}}
</transcript>

<extraction>
{{EXTRACTED_JSON}}
</extraction>

For source_quote_accuracy, verify each quote by checking if it exists as an exact substring in the transcript.
Output only JSON.
</user>
```

---

## 8. Approach trade-off comparison

| Approach | Quality | Cost (tokens) | Latency | Robustness | Best for |
|----------|---------|--------------|---------|------------|----------|
| **Single-pass extraction** | Good (baseline) | 1x | Low | High — one call, one failure point | Real-time client-side extraction |
| **Multi-pass with gleaning** | Best (+30% entity recall) | 2–3x | Medium | Medium — more calls, more failure points | Batch processing 91 sessions |
| **Entities → then relations** | Better (fewer missed relations) | 2x | Medium | Medium | When relation quality is critical |
| **JSON mode (OpenAI legacy)** | Good syntax, no schema guarantee | 1x | Low | Medium — valid JSON but schema may drift | Quick prototyping |
| **Structured Outputs / tool_use** | Best — 100% schema compliance | 1x | Low | Highest — cannot violate schema | Production extraction (recommended) |
| **Free-form JSON in prompt** | Acceptable | 1x | Low | Low — parsing failures at ~5–10% rate | Fallback when structured outputs unavailable |
| **2 few-shot examples** | Good | +200 tokens | Negligible | Good — sufficient for simple schemas | Real-time extraction |
| **5 few-shot examples** | Best (+8.8% F1 over zero-shot) | +500 tokens | Negligible | Best — diverse patterns covered | Batch extraction (recommended) |
| **10 few-shot examples** | Diminishing returns (+6.3% F1) | +1000 tokens | Slight | Good but context competition | Not recommended — 5 is sufficient |
| **With CoT reasoning** | Better for complex cases | 2–3x | Higher | Medium — reasoning can introduce errors | Implicit knowledge extraction |
| **Two-step (reason then format)** | Best for complex reasoning (+13pp) | 2x | Higher | High | When extraction requires inference |

---

## What to try first: prioritized implementation list

1. **Start with Prompt A (single-pass batch) + Structured Outputs**: Use OpenAI's `response_format` with strict JSON schema or Anthropic's tool_use to enforce the JARVIS schema. Process all 91 sessions sequentially in chronological order, maintaining a canonical entity list passed into each session's prompt. This alone will produce a usable knowledge graph.

2. **Add post-extraction source_quote validation**: After each extraction, programmatically verify every `source_quote` exists as an exact substring in the original transcript. Reject any extraction where the quote doesn't match. This is the single highest-ROI quality intervention.

3. **Add Prompt E (entity resolution) as a batch post-processing step**: After all 91 sessions are extracted, run embedding-based deduplication on all entities. Use cosine similarity ≥0.92 for auto-merge, run Prompt E for cases between 0.80–0.92.

4. **Add Prompt C (correction detection) as a second pass on each session**: Run after the main extraction to catch superseded information and create temporal validity markers.

5. **Deploy Prompt B (real-time) in the AI client**: For ongoing conversations, the real-time prompt captures incremental knowledge at $0 cost during the conversation.

6. **Build the regression test suite**: Annotate 50 conversation segments as golden test cases. Run Prompt F (quality self-check) on a sample of extractions to calibrate. Set up CI-style semantic regression testing.

7. **Add gleaning (multi-pass) for batch extraction**: After the initial system is working, add a second extraction pass using a gleaning prompt ("What entities and facts were missed?") to improve recall by an estimated 20–30%.

8. **Add Prompt D (session summary) for each processed session**: Generate summaries for all 91 sessions to enable session-level search and context-setting.

---

## Conclusion

The most effective extraction architecture is not a single clever prompt — it is a **pipeline** where each stage does one thing well. Graphiti's 6–10 LLM calls per episode exists for a reason: entity extraction, reflexion, edge extraction, deduplication, and contradiction detection are genuinely different tasks requiring different instructions. JARVIS can start simpler (single-pass + validation + dedup) and layer in complexity as needed.

Three engineering insights emerge from this research that no amount of prompt tuning can replace. First, **post-extraction validation** (verifying source_quotes are exact substrings) is more impactful than any prompt optimization — it provides a hard floor on precision that prompt instructions alone cannot guarantee. Second, **the canonical entity list passed into extraction prompts** is what transforms 91 independent sessions into a coherent knowledge graph — without it, you get "React" and "React.js" and "리액트" as three separate entities. Third, Mem0's 97.8% junk rate proves that **what you exclude matters more than what you include** — the category-gating approach (only extract items matching specific predicate categories) is a structural safeguard that no amount of "be selective" prompting can match.

The prompts above are ready to use. They work on both Claude (via XML tags and tool_use) and OpenAI (via structured outputs and JSON mode). Start with Prompt A, add source_quote validation, and iterate from there.