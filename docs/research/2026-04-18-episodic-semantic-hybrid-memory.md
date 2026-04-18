# JARVIS Episodic + Semantic Hybrid Memory 심층 설계 리서치

> **조사일**: 2026-04-18 · **대상**: PostgreSQL 16 + pgvector + PGroonga, Oracle Cloud Always Free ARM64, MCP, 서버 LLM 배제 · **현재 규모**: 6 episodes, 567 facts

JARVIS가 지금 겪는 "판단·결정·맥락을 재현 못 함" 문제의 **단일 해답은 Graphiti식 이중 역참조(dual back-reference) 구조**다. 사실(fact)은 엣지 속성으로 `episodes: list[uuid]`를 들고, 에피소드는 역으로 `entity_edges: list[uuid]`를 들어 양방향이 O(1)로 붙는다. 하지만 Graphiti는 add 시 LLM을 5~6회 호출하므로, **JARVIS는 클라이언트 사이드 추출 + 서버 저장만 수행**하는 축소판으로 번역해야 한다. 본 보고서는 Graphiti를 코드 레벨로 완전 분해하고, 7개 경쟁 시스템과 비교한 뒤, JARVIS 전용 PostgreSQL DDL·MCP 시그니처·구현 로드맵을 제시한다.

---

## 1. Graphiti 완전 분해 — 누구나 15분이면 이해할 수 있는 구조

Graphiti(github.com/getzep/graphiti, v0.27.1 / 2026-02-12)는 **AI 에이전트용 실시간 바이-템포럴 지식 그래프 엔진**이다. 그래프 백엔드는 Neo4j / FalkorDB / Kuzu / Amazon Neptune 4종을 `graphiti_core/driver/`에서 추상화 지원한다 (JARVIS는 PostgreSQL이므로 드라이버 직접 이식은 불가하되 **스키마 패턴만 따온다**).

### 1.1 핵심 설계 원칙: 두 층을 물리적으로 분리

Graphiti는 "에피소드 기억(원본, 비손실)"과 "의미 기억(추출된 사실)"을 **별개 노드 타입**으로 저장하고, 둘을 **세 가지 경로**로 연결한다. 이것이 이 시스템의 전부라고 해도 과언이 아니다.

```
[EpisodicNode]  ──[:MENTIONS]──▶  [EntityNode]  ──[:RELATES_TO {fact}]──▶  [EntityNode]
  │                                                                             ▲
  │  entity_edges: [edge_uuid, ...]   ◀── 역참조 리스트 ──   episodes: [ep_uuid, ...]
  └──────────────────────────────────── 양방향 ────────────────────────────────┘
```

세 경로가 모두 중복 유지되는 이유는, 어떤 쿼리든 단일 홉으로 답하기 위해서다. (1) `MENTIONS` 관계로 그래프 순회, (2) `EntityEdge.episodes` 프로퍼티로 사실→에피소드 직접 조회, (3) `EpisodicNode.entity_edges` 프로퍼티로 에피소드→사실 직접 조회.

### 1.2 EpisodicNode 스키마 — `graphiti_core/nodes.py` L353~434

에피소드는 **원본 텍스트 그대로** 저장한다 (`store_raw_episode_content=True` 기본값). 요약·압축·청킹을 일체 하지 않는다.

```python
class EpisodicNode(Node):               # 그래프 라벨 :Episodic
    uuid: str                            # UUID4 PK
    name: str                            # human-readable 이름
    group_id: str                        # 멀티테넌시 파티션 (= JARVIS의 project_id)
    labels: list[str]
    created_at: datetime                 # T' 트랜잭션 시각 (DB 기록)
    valid_at: datetime                   # T 이벤트 시각 (= reference_time)
    source: EpisodeType                  # 'message' | 'json' | 'text'
    source_description: str              # 출처 설명 ("CRM 데이터" 등)
    content: str                         # ★ 원본 내용 전체
    entity_edges: list[str]              # ★ 이 에피소드에서 추출된 EntityEdge UUID 목록
```

**JARVIS 매핑**: `episodes` 테이블에 `content` 컬럼으로 원문을 그대로 보관. 500자 제한을 풀어야 한다는 뜻이다. `entity_edges`는 PostgreSQL에서는 별도 연결 테이블(`fact_episodes`)로 빼는 게 정규화에 맞다.

### 1.3 EntityEdge 스키마 — `graphiti_core/edges.py` L228~501, JARVIS 설계의 심장부

이 하나의 클래스가 **의미 기억의 전부**다. fact 텍스트, 임베딩, 시간, 그리고 **에피소드 역참조**를 동시에 들고 있다.

```python
class EntityEdge(Edge):                  # Cypher: (Entity)-[:RELATES_TO]->(Entity)
    uuid: str
    group_id: str
    source_node_uuid: str                # 주어 EntityNode
    target_node_uuid: str                # 목적어 EntityNode
    name: str                            # 관계 타입 (SCREAMING_SNAKE_CASE, 자유 형식)
    fact: str                            # 자연어 사실 ("Alice loves Adidas shoes")
    fact_embedding: list[float] | None   # fact 텍스트의 벡터 (1536d)
    episodes: list[str]                  # ★★★ 이 사실을 지지하는 EpisodicNode UUID 목록
    valid_at: datetime | None            # T: 사실이 현실에서 참이 된 시각
    invalid_at: datetime | None          # T: 사실이 거짓이 된 시각 (모순 발생 시 설정)
    expired_at: datetime | None          # T': 소프트 삭제 마커
    created_at: datetime                 # T': DB 기록 시각
    attributes: dict[str, Any]           # 관계별 커스텀 속성 (reason, confidence 등)
```

`episodes: list[str]`는 **여러 에피소드가 같은 사실을 지지**할 수 있음을 표현한다 (Alice가 Python을 좋아한다고 3번 말하면 3개의 UUID가 쌓인다). 이것이 곧 **confidence의 근거**이기도 하다.

**JARVIS 매핑**: `facts` 테이블에 `valid_at/invalid_at` 컬럼을 두고, 에피소드 다대다 관계는 `fact_episodes(fact_id, episode_id)` 연결 테이블로 정규화. `attributes`는 JSONB 컬럼으로 매핑.

### 1.4 `add_episode()` 동작 — LLM 6회가 하는 일, JARVIS에서는 클라이언트가 한다

시그니처(`graphiti_core/graphiti.py` L788):

```python
async def add_episode(name, episode_body, source_description, reference_time,
                      source=EpisodeType.message, group_id=None, ...) -> AddEpisodeResults
```

내부 흐름은 **6번의 LLM 호출**로 이뤄진다:

1. **엔티티 추출** (`prompts/extract_nodes.py`) — 이전 에피소드 4개 + 현재 메시지 → 엔티티 목록
2. **엔티티 중복 해제** (`prompts/dedupe_nodes.py`) — 코사인 유사도 + BM25로 기존 후보 찾고 LLM이 "같은가?" 판정
3. **사실 추출** (`prompts/extract_edges.py`) — 엔티티 쌍 사이의 관계를 삼중쌍으로 생성, `episodes=[현재 에피소드 UUID]` 세팅
4. **사실 중복 해제** (`prompts/dedupe_edges.py`) — 동일 엔티티 쌍의 기존 엣지만 비교(검색 공간 축소), 중복이면 `episodes` 리스트에 UUID만 append
5. **시간 추출** (`prompts/extract_temporal.py`) — "2주 전", "작년 여름" 같은 상대 표현 → 절대 datetime
6. **모순 처리** (`utils/maintenance/edge_operations.py::resolve_edge_contradictions`) — 의미적으로 충돌하는 기존 엣지의 `invalid_at`을 새 엣지의 `valid_at`으로 설정. **삭제하지 않는다.**

**핵심 인사이트(JARVIS 번역)**: 이 6단계는 모두 "텍스트 → 구조화된 트리플" 변환이다. JARVIS는 서버 LLM이 없으므로 **AI 클라이언트(Claude/GPT)가 MCP tool `add_episode`를 호출하기 전에 클라이언트 측 LLM이 이미 이 추출을 수행**한다. 즉 JARVIS 서버는 "이미 추출된 fact triple + episode text"를 받아 저장만 한다. 단, **모순 처리(6번)는 서버 측 규칙 기반**으로 가능하다 — 동일 `(project_id, subject, predicate)` 조합에 새 fact가 들어오면 기존 fact의 `invalid_at = NOW()` 자동 설정.

### 1.5 `search()` 반환 형태 — 기본은 facts만, episode는 별도 호출

```python
# 기본 인터페이스 — graphiti_core/graphiti.py ~L534
async def search(query, group_ids=None, num_results=10, center_node_uuid=None) -> list[EntityEdge]
# 고급 인터페이스
async def search_(query, config: SearchConfig) -> SearchResults
```

`SearchResults` 데이터클래스는 `edges + nodes + episodes + communities`를 담지만, **기본 `search()`는 오직 EntityEdge 리스트만 반환**한다. Episode 본문을 원하면 별도로 `retrieve_episodes(reference_time, last_n, group_ids)` 또는 fact의 `episodes[0]`으로 조회한다. 이것이 **Option A(2-stage)** 설계의 실례다.

검색 방법은 세 가지(논문 arXiv:2501.13956 §3.1)를 Reciprocal Rank Fusion으로 결합:
- `cosine_similarity`: `fact_embedding` 벡터 유사도
- `bm25`: `fact` 텍스트 키워드 검색
- `bfs`: `center_node_uuid` 시드에서 N-홉 그래프 탐색

리랭커는 `rrf`(기본) / `mmr` / `cross_encoder`(LLM) / `node_distance` 4종. JARVIS는 이미 RRF를 구현했으므로 동일 패턴.

### 1.6 Graphiti MCP 서버 시그니처 — JARVIS가 직접 참조할 가치

`mcp_server/main.py`의 `@mcp.tool()` 데코레이터로 등록된 8개:

| Tool | 핵심 파라미터 | 반환 |
|---|---|---|
| `add_episode` | `name, episode_body, source, source_description, reference_time` | episode UUID |
| `search_nodes` | `query, max_nodes=10, group_id` | EntityNode 리스트 |
| `search_facts` | `query, max_facts=10, group_id, center_node_uuid` | EntityEdge 리스트 (fact + episodes 속성) |
| `get_episodes` | `last_n, group_id` | EpisodicNode 리스트 |
| `get_entity_edge` | `uuid` | 단일 EntityEdge |
| `delete_episode` / `delete_entity_edge` | `uuid` | ok |
| `clear_graph` | — | ok |

**주목**: Graphiti MCP는 `search_facts`와 `get_episodes`를 **분리**한다. 검색 결과에 episode 본문을 섞어넣지 않는다. JARVIS의 `recall_memory` + `get_episode_excerpt` 2-tool 설계가 이 철학과 정확히 일치한다.

### 1.7 실제 벤치마크와 한계

**벤치마크**(arXiv 2501.13956 §4): LongMemEval(115k 토큰 대화) 기준 gpt-4o-mini에서 Zep **63.8% 정확도 / 3.20s / 1.6k 토큰**. Full-context(55.4%, 31.3s, 115k 토큰) 대비 컨텍스트 98.6% 절감, 지연 90% 감소. DMR에서 MemGPT(93.4%) 상회.

**알려진 실패 사례**:
- **Issue #1083**: LLM 비결정성으로 MENTIONS 관계 없는 **고아 엔티티** 발생. `remove_episode()` 후 정리 안 됨 (2025-11 open, PR #1130 진행 중).
- **Issue #1001**: FalkorDB `add_triplet()`에서 `source_node_uuid`가 None으로 저장되는 버그.
- **Issue #871**: 대용량 데이터셋에서 LLM이 너무 긴 JSON 반환 → `EOF while parsing`. Pydantic 검증 실패로 수집 중단.
- **LLM 비용**: 에피소드당 5~6회 호출 × `SEMAPHORE_LIMIT=10` 병렬 → gpt-4o-mini 기준 **에피소드당 $0.01~0.05**. 대규모 인덱싱 시 비용 폭발.
- **소규모 데이터 취약점**: 커뮤니티 감지(레이블 전파)는 노드 밀도가 낮으면 무의미. JARVIS의 6 episodes 규모에서는 `CommunityNode` 자체가 생성 안 되거나 잡음.

### 1.8 JARVIS로의 번역 매핑표

| Graphiti 메커니즘 | JARVIS 대안 |
|---|---|
| EpisodicNode (Neo4j) | `episodes` 테이블 (PostgreSQL) |
| EntityEdge `episodes: list[str]` | `fact_episodes(fact_id, episode_id)` 연결 테이블 |
| 6회 LLM 추출 | 클라이언트(Claude/GPT) 사전 추출 → MCP tool 인자로 전달 |
| 벡터/BM25/BFS + RRF | pgvector / PGroonga / Aho-Corasick 앵커 + 2-hop BFS + RRF (**이미 구현됨**) |
| `valid_at/invalid_at` | 동일 컬럼 채택 |
| 모순 처리 (LLM) | 서버 측 규칙: 동일 `(subject, predicate)`에 새 fact → 기존 `invalid_at=NOW()` |
| `group_id` | 이미 있는 `project_id` (1:1 매핑) |
| `search_facts` + `get_episodes` 분리 | `recall_memory` + `get_episode_excerpt` 분리 (Option A) |

---

## 2. 경쟁 시스템 7종 비교 — Graphiti가 왜 가장 가까운 참조인가

**Mem0**(github.com/mem0ai/mem0)는 **에피소드 개념이 없다**. 모든 메모리가 flat fact("User is vegetarian")로 저장되며, `memory.add()`에서 LLM이 ADD/UPDATE/DELETE를 결정한다. 결정 맥락이 원천적으로 소실된다. `infer=False` 모드로 임베딩만 저장하는 패턴이 JARVIS에 이식 가능하다. **v1.1 그래프 메모리**(`mem0/graphs/`)는 Neo4j/Kuzu/Memgraph를 지원하지만 여전히 episode 원본과의 FK는 없다.

**LangMem**(github.com/langchain-ai/langmem)은 **Episode 스키마를 명시적으로 정의**한다:
```python
class Episode(BaseModel):
    observation: str   # 무엇이 일어났는가
    thoughts: str      # 내부 추론 ("I ...")
    action: str        # 무엇을 했는가
    result: str        # 결과와 회고
```
이 `observation/thoughts/action/result` 4분할이 **결정·판단·맥락 보존의 표준 형태**다. JARVIS의 `episodes.metadata` JSONB에 이 4키 구조를 이식할 가치가 있다. 단, episode↔fact FK는 LangMem도 없다(네임스페이스 분리만). PostgreSQL 지원은 `AsyncPostgresStore`가 존재.

**Letta/MemGPT**(github.com/letta-ai/letta)는 OS 가상 메모리 은유로 3층 분리: `core_memory`(항상 컨텍스트), `recall_memory`(대화 이력 = **에피소딕**), `archival_memory`(에이전트가 명시 저장 = **시맨틱**). `archival_memory_search(query, page)`는 page-based passage 반환 — **Option B(passage 직접 반환)**. 하지만 archival과 recall 사이에 FK가 **없다**. PostgreSQL + pgvector를 공식 지원(`letta/orm/`)하므로 스키마 참조 가치 있음.

**Zep v1 → Graphiti 전환의 교훈**: v1은 세션별 message store + LLM 요약이었다. 문제는 (1) 정적 요약 — 나중 정보로 업데이트 불가, (2) 세션 간 엔티티 단절, (3) "Alice가 이전에 쓴 프레임워크" 같은 관계 쿼리 불가. 해결책이 **엣지 속성으로서의 fact + bi-temporal + 에피소드 FK**였다. JARVIS도 같은 함정을 피해야 한다: **episodes는 불변, facts만 업데이트**.

**CoALA**(Sumers et al. 2023, arXiv:2309.02427)는 이론적 배경을 제공한다. Working / Episodic / Semantic / Procedural의 4분법에서 **JARVIS는 Episodic + Semantic 2층**에 집중하면 된다(§3.2, §3.3). 핵심 인사이트는 §4의 "에피소딕 → Reflection → 시맨틱" 파이프라인인데, JARVIS는 서버 LLM이 없으므로 **클라이언트가 Reflection 담당**.

**HippoRAG 2**(arXiv:2502.14802, ICML 2025)는 **passage node를 1급 시민**으로 둔다. Phrase(엔티티)와 Passage(원문)를 같은 그래프에 넣고 PPR로 전파. 벤치마크상 **triple+passage 하이브리드가 triple-only 대비 Recall +12.5%, passage-only 대비 +6.1%**. 이것이 JARVIS 하이브리드의 강력한 경험적 근거다. PPR 자체는 PostgreSQL에서 직접 실행 못 하지만, JARVIS의 Aho-Corasick 앵커 + 2-hop BFS가 근사 대체.

**Generative Agents**(Park et al. 2023, arXiv:2304.03442)의 공식 `score = α·recency + β·importance + γ·relevance`은 PostgreSQL에서 그대로 구현 가능: `EXP(-0.05 * EXTRACT(EPOCH FROM (NOW() - last_accessed_at))/86400)`. 단 `importance`는 LLM 평가를 요구하므로 JARVIS에서는 클라이언트 제공 또는 고정값.

**MemoryBank**(Zhong et al. 2023, arXiv:2305.10250)의 **Ebbinghaus 망각 곡선**은 `strength FLOAT` 컬럼 하나로 PostgreSQL에 구현 가능. 접근 시마다 `strength = strength * EXP(-k·Δt)` 갱신. 선택적 확장 기능.

### 종합 비교 — 단일 표

| 시스템 | Episode 개념 | Fact↔Episode 링크 | Retrieval 반환 | LLM 필수 | PostgreSQL | JARVIS 이식 |
|---|---|---|---|---|---|---|
| **Graphiti** | ✅ EpisodicNode | ✅ **양방향 (list[uuid] × 2)** | EntityEdge (Option A) | 🔴 6회/episode | ❌ | **★ 가장 가까운 참조** |
| Mem0 | ❌ | ❌ | flat fact | 🔴 | 어댑터 필요 | Partial |
| LangMem | ✅ Episode 스키마 | ❌ (네임스페이스만) | Item (Option B) | 🔴 | ✅ `AsyncPostgresStore` | Episode 스키마만 이식 |
| Letta | ✅ recall_memory | ❌ | passage 직접 (Option B) | 🟡 | ✅ 기본 | 3층 개념 이식 |
| Zep v1 | 🟡 세션 요약 | 🟡 세션 내 | 요약 | 🔴 | N/A | 교훈만 |
| CoALA | ✅ 이론 | ✅ reflection | 해당 없음 | 🟡 | N/A | **설계 원칙** |
| HippoRAG 2 | ❌ (passage만) | N/A | passage + PPR | 🟡 | ❌ | Hybrid 근거 |
| Generative Agents | ✅ memory stream | 🟡 reflection 근거 | recency+importance+relevance | 🔴 | ❌ | **점수 공식** |
| MemoryBank | 🟡 대화 | ❌ | 벡터+강도 | 🟡 | ❌ | **망각 곡선** |

**결론**: Graphiti만이 episode↔fact 양방향 링크를 **코드 레벨에서 온전히 구현**했다. JARVIS는 Graphiti의 스키마 패턴 + LangMem의 Episode 4분할 + Generative Agents의 점수 공식 + MemoryBank의 망각 곡선을 **PostgreSQL에 합성**하는 것이 최적해다.

---

## 3. Episodic retrieval API 표준 모양 (2024~2025 실측)

### 3.1 반환 단위와 크기 디폴트

| 시스템 | 기본 limit | 반환 단위 | Size per item |
|---|---|---|---|
| Graphiti `search()` | `num_results=5` (예제), `SearchConfig.limit=10` | EntityEdge (fact string ~20~50 words) | ~50 tokens |
| Graphiti `retrieve_episodes()` | `last_n` 명시 | EpisodicNode (full content) | 가변 (수백~수천 tokens) |
| Mem0 `search()` | `top_k=3`~`limit=10` | atomic fact string | ~30~80 tokens |
| Letta `archival_memory_search` | `DEFAULT_ARCHIVAL_MEMORY_RESULTS` page | passage text 그대로 | 삽입 크기 (가변) |
| LangMem `search_memory_tool` | `limit=5`, `query_limit=10` | JSON document | 가변 |
| LlamaIndex `ChatMemoryBuffer` | `token_limit=3000` | ChatMessage 리스트 | 전체 3k 토큰 |
| LlamaIndex `VectorMemory` | `similarity_top_k=1` (매우 보수적) | ChatMessage | 가변 |
| LangChain `ConversationSummaryBuffer` | `max_token_limit=2000` | 요약+최근 | 2k 토큰 |

**합의된 디폴트**: **top 5~10개 item, item당 50~500 토큰**. 단일 응답 총량 ≈ **500~2,500 토큰**. Graphiti가 LongMemEval 실험에서 쓴 **1.6k 토큰 / 호출**이 가장 신뢰할 만한 실측치다.

### 3.2 2024~2025 RAG 청킹 문헌의 수렴점

- **Dense X Retrieval**(Chen et al., EMNLP 2024, arXiv:2312.06648): 검색 단위를 **proposition**(원자적 자기완결 사실)으로 바꾸면 passage 대비 Recall@20 **+10.1%**. JARVIS의 fact 추출과 동일한 철학.
- **Anthropic Contextual Retrieval**(2024-09): chunk에 **50~100 token context prepend** → BM25 병합 → reranker → top 20. 전통 RAG 5.7% 실패율을 **1.9%까지 67% 감소**.
- **Late Chunking**(Jina AI, EMNLP 2024, arXiv:2409.04701): 8192-token window로 전체 문서 인코딩 후 64~256 token 경계별 mean-pool. Cross-boundary context 보존.
- **RAPTOR**(ICLR 2024): 100-word leaf → semantic cluster → LLM 요약(기본 256 tokens). JARVIS에는 소규모라 불필요.
- **LongMemEval**(Wu et al., ICLR 2025, arXiv:2410.10813): 500개 질문, 5개 메모리 능력. **최고 성능 전략 = session decomposition + fact-augmented key expansion + time-aware query expansion**. 즉 "사실은 key, 에피소드는 value" 하이브리드가 최강.

### 3.3 긴 에피소드 처리

JARVIS의 현재 에피소드는 작지만, 향후 10k 토큰 초과 대비책:
- **1차(권장)**: 의미 단위(대화 교환 ~5턴) 분해 → 각 segment ~512 tokens → context prepend ("JARVIS 2026-04-18 제품 결정 세션: ") → `episode_passages` 테이블에 저장.
- **2차(선택)**: Late Chunking으로 embedding 품질 향상.
- **3차(불필요)**: RAPTOR 계층 요약은 에피소드 수 100+ 일 때 고려.

---

## 4. 추출 단계 — decision/reason/tradeoff는 fact인가 episode인가

### 4.1 Production 시스템의 실태

**어떤 프로덕션 시스템도 `DECIDES/CHOOSES/JUSTIFIES/REJECTS`를 미리 정의된 predicate 어휘로 표준화하지 않는다.** 이는 명확한 연구 결과다.

- **Graphiti**: `name` 필드가 free-form `SCREAMING_SNAKE_CASE`. `prompts/extract_edges.py`는 "FACT_TYPES가 있으면 매칭, 없으면 관계 술어에서 SCREAMING_SNAKE_CASE로 자유 생성". MCP 서버 내장 entity type은 `Preference, Requirement, Procedure, Location, Event, Organization, Document, Topic, Object` 9종 — **Decision/Choice 전용 타입 없음**. `Preference`가 "choices/opinions/selections"를 포함하나 `reason` 속성은 명시 안 됨.
- **LangChain LLMGraphTransformer**: `allowed_relationships=[]`(기본 = 자유), LLM이 도메인에 맞춰 "suing", "WORKS_AT", "CREATED" 등 동적 생성.
- **Neo4j LLM Graph Builder**: `allowedRelationshipTypes` 파라미터는 있지만 기본값 없음(domain-agnostic).
- **PROV-O / Schema.org / ConceptNet**: 학술 온톨로지에 `Desires`, `MotivatedByGoal`이 있지만 **production AI 메모리 시스템이 이를 채택한 사례 없음**.

### 4.2 Fact vs Episode — 실제 recall/precision 증거

**LongMemEval(2025)과 HippoRAG 2(2025)가 공통으로 확인**: triple-only보다 **triple + passage 하이브리드가 Recall @20을 6~12% 향상**. 결정·이유·비교 같은 복합 정보는 **둘 다 유지하는 것이 최적**이다.

- Fact로 뽑기의 장점: precision↑("왜 SecondBrain?" → `CHOSEN_OVER` edge 직접 hit), multi-hop 체인 가능, temporal invalidation 가능.
- Fact로 뽑기의 단점: 클라이언트 추출 정밀도(regex/SpaCy ~80%, LLM 필요), 맥락 손실(`reason="margin"`만 남고 "마진이 왜 중요한지"는 증발).
- Episode 보존의 장점: 완전 맥락, 재해석 가능, 추출 오류 없음.
- Episode 보존의 단점: recall↓(긴 대화 속 결정 구절 매몰), KU(지식갱신) 취약.

### 4.3 JARVIS 권장 Predicate 어휘 (client-side 추출 기준)

표준이 없으므로 **JARVIS가 직접 정의**한다. 클라이언트 LLM이 따를 수 있는 소규모 어휘:

| 카테고리 | Predicate |
|---|---|
| 선택/결정 | `CHOSE_OVER(A,B)`, `DECIDED_FOR(agent,X)`, `REJECTED(agent,X)`, `PREFERRED_OVER(A,B)` |
| 이유/근거 | `JUSTIFIED_BY(decision,reason)`, `MOTIVATED_BY(action,factor)` |
| 비교/고려 | `COMPARED_WITH(A,B)`, `CONSIDERED(agent,X)` |
| 폐기/변경 | `DEPRECATED(X)`, `REPLACED_BY(old,new)`, `INVALIDATED_BY(fact,reason)` |

`facts.attributes` JSONB에 `{reason, confidence, alternatives_considered, outcome, context_type}` 구조로 부가 정보 저장.

### 4.4 클라이언트 추출 feasibility

JARVIS는 서버 LLM이 없으므로 **클라이언트(Claude Code, Cursor 등)가 MCP 호출 전에 추출**한다. 현실적 정밀도:
- 명시적 표현("chose X over Y because Z"): LLM 추출 정밀도 **~95%**, regex로도 **~80%**.
- 암묵적 표현("X looks better for our margins"): LLM 필요, **~75%**.
- 복잡 비교("considering A, B, C... ultimately X"): LLM 필수, **~70%**.

→ **서버 측에는 regex fallback 유틸(`extract_decision_patterns`)을 제공하되, 주 추출은 클라이언트 LLM**에 맡긴다.

---

## 5. Fact ↔ Episode 링크 메커니즘 — Option A/B/C 선택

### 5.1 세 옵션 비용 비교 (JARVIS 현재 규모 기준)

| 측면 | Option A (2-stage) | Option B (1-stage eager) | Option C (flag) |
|---|---|---|---|
| 동작 | `recall_memory`→fact+episode_id → 필요 시 `get_episode_excerpt` | `recall_memory_v2`가 fact+passage 동시 반환 | `include_episode=true` flag |
| 토큰 (10 facts) | **500 + (필요 시 300)** = ~800 | 500 + 10×300 = **~3,500** | false: 500, true: ~3,500 |
| 레이턴시 | +1 MCP round-trip (5~50ms) | 단일 호출 | flag=false면 A와 동일 |
| MCP 적합성 | ✅ tool 분리 명확 | ⚠️ 응답 크기 폭발 | ✅ 유연, 복잡도↑ |
| 실측 사례 | Graphiti `search_facts` + `get_episodes` | Letta `archival_memory_search`, LangChain retriever | 드묾 |

**MCP 컨텍스트 오염 실측**: Claude Code + Task Master MCP에서 단일 쿼리 **240,600 토큰** 소비(114 tools 주입), tool definitions만으로 **63.7k 토큰(31.8%)** 관측. MCP 공식 스펙에는 응답 크기 하드 리밋이 없지만(JSON-RPC 2.0 기반), **클라이언트 컨텍스트 창이 곧 실질적 제한**(Claude 200k, Cursor 32~128k).

### 5.2 권장 결론

**JARVIS는 Option A를 기본, Option C를 선택적 병행**:
- 기본 `recall_memory`는 fact + `episode_ids[]`만 반환 (~500 tokens, 안전).
- 별도 `get_episode_excerpt(episode_id, query, max_chars=500)`가 passage 발췌.
- 고급 사용자를 위해 선택적 `include_episode_excerpt: bool = false` 파라미터 제공.

이것이 Graphiti(`search_facts` + `get_episodes`)와 동일한 철학이고, MCP tool 스키마의 토큰 비용도 최소화한다.

---

## 6. JARVIS 전용 권고 설계

### 6.1 episodes + facts + fact_episodes 스키마 (PostgreSQL DDL, 실행 가능)

```sql
-- ============================================================
-- JARVIS Episodic + Semantic Hybrid Memory Schema
-- PostgreSQL 16 + pgvector + PGroonga, Oracle Cloud ARM64
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgroonga;

-- ── 1. episodes: 원본 비손실 저장, 불변(append-only)
CREATE TABLE episodes (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          TEXT         NOT NULL,
    episode_type        TEXT         NOT NULL
                        CHECK (episode_type IN ('text','message','json','decision','reflection')),
    content             TEXT         NOT NULL,              -- 원본 전체 (500자 제한 해제)
    content_embedding   vector(1536),                        -- 전체 content 임베딩
    summary             TEXT,                                -- 짧은 요약 (client 제공 or 첫 N자)
    summary_embedding   vector(1536),                        -- 요약 임베딩 (빠른 근사 검색)
    start_at            TIMESTAMPTZ,                         -- 에피소드 이벤트 시작 (bi-temporal T)
    end_at              TIMESTAMPTZ,                         -- 종료 시각
    last_accessed_at    TIMESTAMPTZ  DEFAULT now(),          -- 망각 곡선용 (MemoryBank)
    importance          FLOAT        DEFAULT 0.5,            -- 0~1, client 제공 or heuristic
    strength            FLOAT        DEFAULT 1.0,            -- Ebbinghaus strength
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(), -- T' 트랜잭션
    metadata            JSONB        NOT NULL DEFAULT '{}'   -- LangMem 4분할(observation/thoughts/action/result) 등
);
COMMENT ON TABLE episodes IS 'Raw episodic memory. Immutable. Facts are extracted from these.';

-- ── 2. facts: 기존 테이블 확장 (subject/predicate/object + bi-temporal)
CREATE TABLE facts (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          TEXT         NOT NULL,
    subject             TEXT         NOT NULL,
    predicate           TEXT         NOT NULL,               -- SCREAMING_SNAKE_CASE, 자유 어휘
    object              TEXT         NOT NULL,
    fact_text           TEXT         NOT NULL
                        CHECK (length(fact_text) <= 500),    -- 응답 bloat 방지
    fact_embedding      vector(1536),
    valid_at            TIMESTAMPTZ,                         -- 사실이 참이 된 시각 (T)
    invalid_at          TIMESTAMPTZ,                         -- NULL=현재 유효, NOT NULL=무효화
    expired_at          TIMESTAMPTZ,                         -- 소프트 삭제 (T')
    confidence          FLOAT        DEFAULT 1.0,
    last_accessed_at    TIMESTAMPTZ  DEFAULT now(),
    strength            FLOAT        DEFAULT 1.0,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    attributes          JSONB        NOT NULL DEFAULT '{}'   -- {reason, alternatives_considered, outcome, context_type}
);

-- ── 3. fact_episodes: 양방향 다대다 링크 (Graphiti episodes[] + entity_edges[] 정규화)
CREATE TABLE fact_episodes (
    fact_id     UUID NOT NULL REFERENCES facts(id)    ON DELETE CASCADE,
    episode_id  UUID NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    role        TEXT DEFAULT 'source'
                CHECK (role IN ('source','supporting','contradicting','reinforcing')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (fact_id, episode_id)
);
CREATE INDEX idx_fact_episodes_episode ON fact_episodes (episode_id);  -- 역방향 조회

-- ── 4. episode_passages: 긴 에피소드의 청크 검색 (현재 규모에선 선택)
CREATE TABLE episode_passages (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    episode_id        UUID NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    chunk_index       INTEGER NOT NULL,
    content           TEXT NOT NULL,                        -- ~512 tokens 권장
    contextual_prefix TEXT,                                 -- Anthropic style 50~100 token prepend
    content_embedding vector(1536),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (episode_id, chunk_index)
);

-- ── 5. 인덱스 (현재 규모에 최적화)
CREATE INDEX idx_episodes_project_created ON episodes (project_id, created_at DESC);
CREATE INDEX idx_facts_project_valid_partial
    ON facts (project_id, valid_at DESC)
    WHERE invalid_at IS NULL;                               -- 현재 유효 fact만, partial index

-- PGroonga FTS (한국어 + 영어 TokenNgram)
CREATE INDEX idx_episodes_content_pgroonga
    ON episodes USING pgroonga (content)
    WITH (tokenizer='TokenNgram("unify_alphabet", true, "unify_digit", true)');
CREATE INDEX idx_facts_fact_text_pgroonga
    ON facts USING pgroonga (fact_text)
    WITH (tokenizer='TokenNgram("unify_alphabet", true)');

-- pgvector HNSW: 현재 567 rows는 seqscan이 더 빠름.
-- 1,000 rows 초과 시 아래 활성화:
-- CREATE INDEX idx_facts_embedding_hnsw
--     ON facts USING hnsw (fact_embedding vector_cosine_ops)
--     WITH (m=16, ef_construction=64);

-- ── 6. 뷰: recall_memory 구현용
CREATE VIEW current_facts_with_episodes AS
SELECT f.*,
       array_agg(fe.episode_id ORDER BY fe.created_at) FILTER (WHERE fe.episode_id IS NOT NULL)
           AS episode_ids
FROM facts f
LEFT JOIN fact_episodes fe ON fe.fact_id = f.id
WHERE f.invalid_at IS NULL
GROUP BY f.id;
```

**ARM64 / 소규모 주의사항**: Oracle Cloud Always Free(Ampere A1, 4 OCPU, 24GB)의 PostgreSQL pgbench 실측 **6,146 TPS / 1.3ms latency** — x86 E4 대비 +10% 우위. pgvector는 ARM NEON SIMD 최적화 포함(0.7+). PGroonga 4.0.6(2026-04-07) ARM64 지원. **567 facts 규모에서는 HNSW 인덱스 생성보다 seqscan이 더 빠르다** — planner가 자동 선택하므로 그냥 두면 된다. 1,000행 초과 시점에 HNSW로 전환.

### 6.2 MCP tool 시그니처 권고

```python
# ============================================================
# Tool 1: recall_memory  (Option A 기본)
# ============================================================
@mcp.tool()
async def recall_memory(
    query: str,
    project_id: str,
    limit: int = 10,                           # 최대 20으로 클램프
    include_invalid: bool = False,             # 무효화된 과거 fact 포함 여부
    time_from: datetime | None = None,         # bi-temporal 필터
    time_to: datetime | None = None,
    predicate_filter: list[str] | None = None, # ["CHOSE_OVER","REJECTED"] 등
) -> dict:
    """
    현재 유효한 fact 검색 (3-way hybrid: vector + PGroonga FTS + 2-hop BFS, RRF).
    Episode 본문은 episode_ids로 참조만 제공. 본문 필요 시 get_episode_excerpt 호출.
    
    Returns:
      {
        "facts": [{
          "id": "...", "fact_text": "...", "subject", "predicate", "object",
          "valid_at", "invalid_at", "confidence", "attributes": {...},
          "episode_ids": ["..."],   ← 2단계 조회용
          "score": 0.87
        }],
        "total": 3,
        "token_estimate": 480
      }
    
    Token budget: ~50 tokens/fact × 10 = ~500 tokens.
    """

# ============================================================
# Tool 2: get_episode_excerpt  (2-stage 완성)
# ============================================================
@mcp.tool()
async def get_episode_excerpt(
    episode_id: str,
    query: str,                                # 관련 구절 추출용
    project_id: str,                           # 격리 검증
    max_chars: int = 2000,                     # 500자 제한 해제 (권장 2000)
    mode: str = "relevant",                    # "relevant" | "full" | "head"
) -> dict:
    """
    특정 episode에서 query와 관련된 구절 반환.
    mode="relevant": PGroonga + 벡터로 관련 passage 추출
    mode="full": 전체 content (max_chars까지)
    mode="head": 선두 max_chars
    
    Returns:
      {
        "episode_id": "...", "episode_type": "decision",
        "excerpt": "...relevant passage...",
        "excerpt_offset": 1234,  ← 원문 내 위치
        "start_at": "...", "created_at": "...",
        "metadata": {...},  ← LangMem 4분할 있으면 포함
        "related_facts": [...]  ← 이 에피소드에서 추출된 fact 요약
      }
    
    Token budget: ~500 tokens per call (max_chars=2000 기준).
    """

# ============================================================
# Tool 3: add_episode  (클라이언트 추출 결과 저장)
# ============================================================
@mcp.tool()
async def add_episode(
    project_id: str,
    content: str,
    episode_type: str,                         # 'text'|'message'|'json'|'decision'|'reflection'
    start_at: datetime,
    end_at: datetime | None = None,
    summary: str | None = None,
    extracted_facts: list[dict] | None = None, # 클라이언트가 사전 추출한 triple 리스트
    metadata: dict | None = None,              # LangMem 4분할 등
) -> dict:
    """
    에피소드 추가. extracted_facts가 제공되면 facts 테이블에 동시 삽입 +
    fact_episodes 자동 링크. 서버 측 모순 처리:
    동일 (project_id, subject, predicate)의 기존 fact.invalid_at = NOW().
    
    extracted_facts 예시:
      [{"subject":"SecondBrain", "predicate":"CHOSEN_OVER", "object":"Argos",
        "fact_text":"SecondBrain chosen over Argos because of margin",
        "attributes":{"reason":"margin", "alternatives_considered":["Argos","SecondBrain"]},
        "valid_at":"2026-04-18T10:00:00Z"}]
    """
```

**recall_memory + get_episode_excerpt 2-tool 분리**가 Graphiti의 `search_facts` + `get_episodes`와 동일 패턴이고, MCP 컨텍스트 오염(실측 240k 토큰 폭주 사례)을 근본 차단한다. 통합형 `recall_memory_v2(include_episode=true)`는 고급 선택지로 남기되 기본 비권장.

### 6.3 하이브리드 검색 점수 공식 (이미 구현된 RRF에 추가)

```sql
-- Generative Agents + MemoryBank 공식을 PostgreSQL로
SELECT f.*,
    (1 - (f.fact_embedding <=> $1::vector))                                    AS relevance,
    f.importance * f.strength                                                   AS memory_weight,
    EXP(-0.05 * EXTRACT(EPOCH FROM (NOW() - f.last_accessed_at))/86400)         AS recency,
    -- 가중 합 (α,β,γ는 튜닝)
    0.5 * (1 - (f.fact_embedding <=> $1::vector))
  + 0.3 * (f.importance * f.strength)
  + 0.2 * EXP(-0.05 * EXTRACT(EPOCH FROM (NOW() - f.last_accessed_at))/86400)
      AS total_score
FROM facts f
WHERE f.project_id = $2 AND f.invalid_at IS NULL
ORDER BY total_score DESC
LIMIT $3;
```

기존 RRF(vector+FTS+BFS) 점수를 위 `relevance` 자리에 대입하면 4-factor 하이브리드 완성.

### 6.4 일관성·중복·바이-템포럴 전략

1. **Episodes는 절대 수정 금지** (Graphiti 설계 원칙). 사실 변경은 오직 `facts.invalid_at = NOW()` + 새 fact INSERT로만 표현.
2. **모순 처리 규칙**: `add_episode`에서 동일 `(project_id, subject, predicate)` 조합의 현재 유효 fact가 있고 `object`가 다르면 자동 invalidate + 새 fact 삽입. attributes의 `conflicts_with` 키에 이전 fact.id 저장.
3. **중복 스토리지 수용**: 567 facts × 200자 ≈ 114KB, 6 episodes × 5KB ≈ 30KB, 임베딩 3.5MB. 소규모에서 deduplication 불필요.
4. **Cascade 삭제**: `fact_episodes` FK 양방향 CASCADE. project 삭제는 application 레벨 트랜잭션(`DELETE FROM episodes WHERE project_id=$1; DELETE FROM facts WHERE project_id=$1;`)으로.
5. **Stale fact 감지**: 주기적으로 `valid_at > NOW() - INTERVAL '90 days' AND invalid_at IS NULL`인 fact 중 최근 `last_accessed_at`이 30일 이상 경과한 것을 플래그 — 재검증 후보.

### 6.5 구현 로드맵

**단기 (1~2주) — 최소 이식으로 "왜 SecondBrain?" 질문 해결**
1. `episodes` 테이블 생성 + `content` 500자 제한 해제 (DDL §6.1의 episodes + facts 변경분만).
2. `fact_episodes` 연결 테이블 추가, 기존 facts에 FK 링크 마이그레이션(가능한 범위).
3. `facts.valid_at / invalid_at / attributes` 컬럼 추가.
4. MCP tool `get_episode_excerpt(episode_id, query, max_chars=2000)` 신설 — PGroonga로 관련 구절 추출.
5. `recall_memory` 반환에 `episode_ids[]` 포함하도록 수정.
6. 클라이언트 프롬프트에 "결정·이유·비교는 `CHOSE_OVER`, `REJECTED`, `JUSTIFIED_BY` predicate로 추출하라" 가이드 추가.

이 단계만으로 "아르고스 vs SecondBrain" 판단 근거를 recall 결과에서 `episode_ids[0]` → `get_episode_excerpt`로 2-hop 조회 가능하게 된다.

**중기 (1~2개월) — 하이브리드 고도화**
1. `episode_passages` 테이블 도입(에피소드 >10k tokens 발생 시). Anthropic 스타일 contextual prefix 적용.
2. `episodes.importance / strength` + `facts.importance / strength` 컬럼과 접근 시 갱신 트리거(Ebbinghaus 망각).
3. `episode_type='decision'|'reflection'` 서브타입 + `metadata` JSONB에 LangMem `{observation, thoughts, action, result}` 4분할 강제.
4. 서버 측 모순 처리 규칙(`add_episode`에서 `(subject,predicate)` 충돌 감지 → 자동 invalidate).
5. 하이브리드 검색 점수 4-factor 합산(§6.3) — 기존 RRF 결과를 relevance로 주입.
6. `recall_memory`에 `predicate_filter`, `time_from/to` 파라미터 추가 — LongMemEval의 time-aware query expansion 대응.
7. 1,000 facts 초과 시점에 HNSW 인덱스 활성화(`m=16, ef_construction=64`).
8. Graphiti MCP 서버의 tool 시그니처와 호환 레이어 — 외부 Graphiti 클라이언트가 JARVIS에 붙을 수 있도록.

---

## 결론 — 세 줄 요약

**첫째**, JARVIS의 "왜/결정/맥락 실종" 문제는 사실 Graphiti가 2025년에 이미 풀어놓은 문제다. 해법은 `EpisodicNode`와 `EntityEdge`를 물리적으로 분리하되 **`EntityEdge.episodes[]`와 `EpisodicNode.entity_edges[]` 두 리스트로 양방향 역참조**를 상시 유지하는 것. PostgreSQL에서는 `fact_episodes(fact_id, episode_id)` 연결 테이블 하나로 정규화된다.

**둘째**, 서버 LLM이 없다는 JARVIS의 제약은 Graphiti의 6회 LLM 호출을 **클라이언트 측 사전 추출 + 서버 저장**으로 재배치하면 극복된다. 모순 처리만 서버 규칙(`(project_id,subject,predicate)` 충돌 시 `invalid_at=NOW()`)으로 대신한다. Predicate 어휘는 어떤 프로덕션 시스템도 표준화하지 않았으므로 JARVIS가 `CHOSE_OVER`, `REJECTED`, `JUSTIFIED_BY` 등 10개 남짓의 소어휘를 직접 정의해 클라이언트 프롬프트로 강제한다.

**셋째**, MCP tool은 **`recall_memory`(fact + episode_ids만) + `get_episode_excerpt`(지연 로드) 2-tool 분리**가 유일하게 안전한 선택이다. 1-stage eager(Option B)는 10 facts × 300 tokens = 3,500+ tokens의 컨텍스트 오염을 매 recall마다 반복하므로 불가(Task Master MCP의 240k 토큰 폭주 사례가 증거). Graphiti의 `search_facts` + `get_episodes` 분리가 이 판단의 검증된 선례다. 단기 1~2주 구현만으로 SecondBrain vs Argos 판단을 복원할 수 있고, 중기 2개월에서 Ebbinghaus 망각·bi-temporal·hybrid score까지 완성된다.
