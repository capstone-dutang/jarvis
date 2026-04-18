# JARVIS 다중 프로젝트 retrieval 개선 로드맵

> **조사일**: 2026-04-18 (오전) · **별칭**: "LLM 위키 리서치" · **상태**: 아카이브. 이 리서치의 핵심 권고인 `project_id` 명시 태깅은 사용자 비전("교차 프로젝트 하이퍼링크 유지, 위키형 항해 가능한 외부 뇌")과 충돌해 **명시적으로 배제**됨. 대신 entity-anchored retrieval만 채택.

**결론 먼저**: JARVIS의 현재 실패는 데이터 규모나 커뮤니티 파편화 때문이 아니라 **"쿼리 시점에 project 경계를 강제하는 구조적 필터가 없다"**는 단 하나의 구조적 결함 때문이다. 이번 달 안에 해야 할 일은 명확하다. **fact·entity 테이블에 `project_id` 컬럼을 필수화하고, Aho-Corasick 별칭 사전으로 쿼리에서 앵커 엔티티를 추출한 뒤, 그 앵커의 project_id를 `WHERE project_id = ?` 하드 필터로 벡터·PGroonga·그래프 BFS 세 경로 모두에 밀어넣는 것**이다. 이것만으로 "자비스 구현에서 무엇부터 해야 하는가?" 같은 쿼리의 top-1 정확도는 구조적으로 해결된다.

조사한 9개 상용 시스템(Mem0, Zep/Graphiti, Letta, Cognee, Mem.ai, Glean, NotebookLM, Reflect, Obsidian Copilot) 중 **7개가 "쓰기 시점 명시적 태깅 + 읽기 시점 하드 구조 필터"를 공통 패턴으로 채택**하고 있으며, 쿼리 시점에 LLM으로 scope를 추론하는 시스템은 Letta(에이전트가 태그 선택) 단 하나뿐이다. 학술 측에서도 MES-RAG(NAACL 2025)와 LlamaIndex `MetadataFilters`가 동일한 결론에 도달한다. 반대로 자동 추론에 의존하는 Mem.ai는 내부 retrieval을 공개조차 하지 않는 "vibe-based" 제품이며, Glean의 "자동 분류"는 사실 source system에 이미 존재하는 스키마를 크롤할 뿐이다. **JARVIS에게 증거는 분명하다: 명시적 태깅이 정답이다.**

Leiden의 419 singleton(51%)은 **원인이 아니라 증상**이다. 817 엔티티에 엣지 567개 → 평균 차수 1.39로 percolation threshold 근처의 극도로 sparse한 그래프다. Hossain·Sarıyüce 2025가 측정한 KG의 "55–60%가 차수 1"이라는 관측과 JARVIS 수치가 거의 정확히 일치한다. 커뮤니티 파편화를 고치려면 알고리즘을 바꿀 게 아니라 (1) 연결 성분 분해 후 giant component에만 Leiden을 돌리고, (2) CPM의 `resolution_parameter`를 기본 1.0이 아닌 **0.05**로 낮추고, (3) 임베딩 cosine > 0.8 동의어 엣지로 그래프를 densify해야 한다.

---

## 접근법 A~E 비교 테이블

| 축 | 구조 | JARVIS 적합도 | 구현 복잡도 | CPU 비용(ARM64) | 실측 품질 근거 |
|---|---|---|---|---|---|
| **A. Entity-anchored retrieval** | Aho-Corasick 별칭사전 매칭 → 앵커의 project_id를 하드 필터 | ★★★★★ | 낮음 (1~2일) | <1 ms | MES-RAG(NAACL 2025): 엔티티 격리 저장소로 +25% entity QA 정확도 |
| **B. Project 파티셔닝** | 명시 `project_id` + pgvector 0.8 `iterative_scan='relaxed_order'` + 선택적 partial index | ★★★★★ | 낮음 (3~5일) | 무시 가능 | Mem0·Graphiti·NotebookLM의 공통 프로덕션 패턴 |
| **C. Query 분류 (비-LLM)** | Entity density + 쿼리 길이 + IDF-sum + predicate 사전 | ★★★★ | 중간 (1~2주) | <5 ms | ACL IWSDS 2025 (2025.iwsds-1.14): 규칙 기반이 LLM classifier와 F1 comparable, CPU/지연 대폭 감소 |
| **D. Graph centrality rerank (PPR)** | Shallow PPR (2~3 iter, damping=0.5), giant component 제한, degree-normalized | ★★★ | 중간 (1개월) | <10 ms (igraph prpack, 817 노드) | HippoRAG 2: Recall@5 +12.5%; NodeRAG: shallow PPR이 deep PPR 우위(MuSiQue +4.6) |
| **E. Cross-encoder rerank** | BGE-reranker-v2-m3 (MIT, ONNX int8) | ★★ | 중간 (1~2주) | 200~400 ms for top-20 | MTEB/BEIR에서 표준 성능 향상 |

핵심 관찰: **A+B 조합만으로 JARVIS가 겪는 cross-project leakage의 대부분(본 리포트 추정 85%+)이 해결**된다. D·E는 "앵커 추출이 실패한 broad 쿼리" 보완용이다.

---

## 현재 시스템 진단: 무엇이 무엇의 원인인가

**가장 먼저 바로잡아야 할 잘못된 인과 모델**: "Leiden 파편화 → retrieval 품질 저하". 조사 결과 이 인과는 성립하지 않는다. 오히려 역방향이다 — JARVIS의 그래프가 너무 sparse(평균 차수 1.39)하기 때문에 Leiden이 singleton을 양산할 수밖에 없고, 그 sparse 그래프 위에서 현재 구성된 3-way RRF는 앵커를 전혀 활용하지 못해 범용 키워드 "구현"이 벡터·FTS·BFS 세 경로 모두에서 cross-project를 횡단한다. **singleton은 증상이고, 원인은 (1) 엣지 부족과 (2) project scope 필터 부재 두 가지**다.

실측 쿼리 3개를 분해하면 동일한 실패 모드가 보인다. "자비스 구현에서 무엇부터 해야 하는가?"는 쿼리에 `자비스`라는 명확한 앵커가 있음에도 불구하고 시스템이 그 앵커를 "필수 조건"이 아니라 "soft signal"로만 쓰고 있다. 벡터 임베딩에서 "구현"의 semantic weight가 "자비스"와 비슷하거나 크면 fundmessenger/Argos의 "구현" 관련 fact가 코사인 거리에서 승리한다. PGroonga의 BM25에서도 마찬가지로 "구현"이 high-frequency term이 아닐 경우 IDF가 커져 문서 간 score 차이를 지배한다. 이는 임베딩 품질이나 BM25 파라미터의 문제가 아니라 **"앵커를 hard filter로 쓰지 않는 아키텍처 자체의 결함"**이다.

## B축 핵심: pgvector 0.8 iterative_scan이 기술적 전제조건

과거 pgvector에서 `WHERE project_id = 'jarvis'`를 HNSW 위에 올리면 post-filter가 작동해 `ef_search=40` 후보 중 jarvis 소속만 남겨 실질 결과가 3~4개로 떨어지는 well-known 문제가 있었다. **2024년 11월 릴리스된 pgvector 0.8**은 `hnsw.iterative_scan = 'relaxed_order'` 옵션으로 이 문제를 구조적으로 해결한다 — HNSW 탐색을 LIMIT이 찰 때까지 이터러티브하게 확장한다(github.com/pgvector/pgvector #678, AWS Aurora blog, Nile blog 모두 확인). JARVIS는 이 버전으로 올리는 것이 **모든 후속 작업의 전제**다.

권장 설정: `SET LOCAL hnsw.iterative_scan = 'relaxed_order'; SET LOCAL hnsw.ef_search = 100; SET LOCAL hnsw.max_scan_tuples = 20000;` 그리고 `CREATE INDEX facts_project_btree ON facts (project_id);`를 기본 인덱스로 둔다. 프로젝트가 10개 이하인 현재 규모에서는 partial index `WHERE project_id = 'jarvis'`를 hot project에만 추가하는 것이 효과적이지만, 지금 당장은 불필요하다. **halfvec(16-bit float)**로 저장 용량을 절반으로 낮추는 것도 Always Free tier에서는 의미 있는 최적화다.

## A축 핵심: Aho-Corasick 별칭 사전이 JARVIS의 킬러 피처

JARVIS는 이미 `CROSS_LINGUAL_ALIASES` 수동 사전을 가지고 있다. 이것을 Aho-Corasick automaton(Rust 구현 `ahocorasick_rs`, GIL-free, 실측 <1ms)에 싣고 쿼리 전처리 단계에 **필수로** 삽입하는 것이 A축 전부다. 자비스↔JARVIS, 아르고스↔Argos, 세컨드브레인↔SecondBrain 같은 canonical이 매칭되는 순간 그 엔티티의 `project_id`를 읽어 검색 컨텍스트의 hard filter로 주입한다. 쿼리에서 여러 프로젝트의 엔티티가 동시에 매칭되면(예: "자비스 vs 세컨드브레인") 각 프로젝트에 대해 독립적으로 retrieval을 돌려 concat 한다 — Mem0·Graphiti가 명시적으로 권장하는 client-side union 패턴이다.

사전에 없는 엔티티를 커버하려면 **GLiNER multi-v2.1**(Apache 2.0, 한국어 지원, ONNX int8에서 30~80ms/쿼리)을 batch ingestion 단계에서만 호출해 alias dictionary를 점진적으로 확장한다. 쿼리 hot path에서는 Aho-Corasick만 쓰고 NER은 fallback조차 두지 않는 것이 권장이다 — 500~1000 fact 규모에서는 GLiNER 50ms가 쿼리 latency 전체를 두 배로 키운다. 앵커 추출이 완전 실패하면? 그때만 "broad query" 경로로 떨어져 기존 RRF를 전 프로젝트에 대해 돌리고, MMR + community cohesion(아래 D)로 다양화한다.

## D축 현실적 적용: HippoRAG 2가 아니라 "shallow PPR on giant component"

HippoRAG 1·2와 NodeRAG 논문을 정독한 결과 PPR 파라미터 선택이 JARVIS 같은 소규모 KG에서 매우 비직관적이다. **HippoRAG 소스 코드의 default damping은 0.1, CLI는 0.5**로, 웹 관행인 0.85와 완전히 다르다(github.com/OSU-NLP-Group/HippoRAG의 `src/hipporag.py` 4190d180 커밋 확인). 이유는 단순하다 — 작은 그래프에서 PPR은 seed로부터 멀어질수록 teleport baseline `(1-α)/N`에 가까워지는데, damping이 높으면 walker가 seed에서 너무 멀리 퍼져 noise가 된다. **JARVIS에서는 damping=0.5, power iteration 2~3회(NodeRAG shallow PPR 권장)에서 시작해 {0.1, 0.3, 0.5}를 스윕**하는 것이 올바른 접근이다.

또 하나 반드시 적용할 것은 **degree-normalized PPR** (Wilson·Laenen·Rohe 2019, JRSS-B, https://doi.org/10.1111/rssb.12349): 최종 점수를 `π_i / d_i^0.5`로 나눠 hub node 편향을 제거한다. 817 노드에서 `igraph.personalized_pagerank(implementation='prpack')`는 <5ms로 실행되므로 hot path에 넣어도 문제없다. **다만 PPR은 A+B가 먼저 적용된 이후의 개선**이다. project_id 필터로 줄어든 서브그래프 위에서 PPR을 돌려야 의미 있는 signal을 준다. 그 전에는 cross-project noise가 PPR score를 지배한다.

## Leiden 커뮤니티 수리: 알고리즘이 아니라 그래프를 고쳐라

Traag·Waltman·van Eck 2019를 다시 읽으면 Leiden은 refinement phase에서 명시적으로 **singleton partition에서 시작**한다. 따라서 singleton 출현 자체는 알고리즘의 버그가 아니라 "더 나은 할당이 없음"의 증거다. graspologic의 `hierarchical_leiden`이 고립 노드를 조용히 결과에서 제외하는 것(`"Isolate nodes in the input graph are not returned"` docstring 확인)도 같은 철학이다.

JARVIS가 해야 할 4단계:
1. **진단 먼저**: `igraph.connected_components(mode='weak')`로 컴포넌트 수와 giant component 크기를 측정. 컴포넌트가 수십 개라면 이것이 singleton 산출의 1차 원인이다.
2. **그래프 densification**: 엔티티 임베딩 간 cosine > 0.8인 쌍에 `synonymy` 엣지를 추가. HippoRAG가 OpenIE 이후 이 단계를 반드시 넣는 이유다. 목표는 평균 차수 ≥ 4.
3. **Leiden은 giant component에만**: `giant = g.connected_components(mode='weak').giant()` 위에서 `CPMVertexPartition`, `resolution_parameter=0.05`, `n_iterations=-1`(수렴까지) 실행. 수치 스윕은 {0.01, 0.05, 0.1, 0.5}로.
4. **Post-merge singleton**: 잔여 singleton 중 degree=1짜리는 Seurat의 `GroupSingletons` 패턴대로 이웃 노드의 커뮤니티에 흡수. degree=0 진짜 isolate는 `orphan` 플래그로 두고 별도 처리.

결과 예상: 419 singleton → 50개 미만. 하지만 다시 강조: **이것이 retrieval 품질을 직접 개선하지는 않는다.** 커뮤니티가 깨끗해지면 C축 community cohesion rerank가 효과를 발휘할 수 있게 되는 것이 실제 이득이다.

## 시나리오 5개 검증

| 쿼리 | A+B 적용 후 예상 동작 | 실패 위험 |
|---|---|---|
| "자비스 구현에서 무엇부터 해야 하는가?" | Aho-Corasick이 `자비스→JARVIS` 매칭 → `project_id='jarvis'` 하드 필터 → RRF가 JARVIS fact만 가지고 경쟁 → "구현"이 범용 단어여도 **스코프가 이미 JARVIS로 축소**되어 top-1 정확도 ~0.95 예상 | 낮음 |
| "최근 DB 관련해서 결정한 거 뭐 있지?" | 앵커 없음 → broad-query 경로. Predicate 사전에서 "결정"을 `decision` predicate에 매핑, "최근"을 temporal boost로 해석(`score × exp(-0.01·age_days)`). 여전히 cross-project 가능 | **중간-높음**. 사용자에게 "어느 프로젝트?" 재질문 또는 최근성+predicate로 보정 필요 |
| "요즘 무슨 작업 많이 했어?" | 완전 broad. 앵커 없음, predicate 없음. Activity 스캔 모드로 전환 — fragment 테이블에서 최근 N일 집계 + 커뮤니티별 요약 제시 | 낮음(단, 이것이 "memory" 기능이지 search 기능이 아님을 API 분리로 표현해야) |
| "JARVIS uses_db?" | 구조 쿼리 — 파서가 `subject=JARVIS, predicate=uses_db` 추출 → KG 직접 lookup. RRF 우회 | 매우 낮음 |
| "자비스 vs 세컨드브레인 차이" | Aho-Corasick이 두 앵커 매칭 → project=[jarvis, secondbrain] 각각 독립 retrieval → 결과 병치 | 낮음(MCP 응답 스키마에 `per_project: [...]` 필드 추가 권장) |

쿼리 2·3이 보여주는 중요한 설계 원칙: **retrieval 시스템은 앵커가 없는 broad 쿼리를 "실패"로 반환할 것이 아니라 별도 mode로 라우팅**해야 한다. "활동 스캔"과 "specific lookup"은 본질적으로 다른 API다.

## 우선순위 로드맵

**Phase 1 — 이번 주~다음 주 (리서치 불필요, 즉시 가능)**
- pgvector 0.8로 업그레이드, `iterative_scan='relaxed_order'` 세션 기본값. B-tree index on `project_id` 추가.
- `fact`·`entity` 테이블에 `project_id NOT NULL` 컬럼 추가. 기존 데이터는 episode_id→project_id 매핑으로 일괄 백필(6 에피소드이므로 수동 매핑으로 충분).
- `CROSS_LINGUAL_ALIASES`를 `aliases(canonical_id, alias_text, project_id, lang)` 테이블로 승격. 초기 seed는 수동. `ahocorasick_rs` PyPI 설치 후 query_preprocessing 파이프라인에 매칭 단계 삽입.
- 3-way search의 vector·PGroonga·graph BFS CTE 세 곳 모두에 `WHERE project_id = $anchor_project` 삽입. 앵커 없을 때는 필터 생략.
- 예상 효과: "자비스 구현", "아르고스 개발", "세컨드브레인 사업계획" 세 실측 실패 쿼리 모두 top-1 정확. 구현 비용 1~2 인일.

**Phase 2 — 1~2개월 (중기)**
- Leiden 재구성: connected components 분해, giant component + CPM(γ=0.05), post-merge singleton. Community cohesion rerank(modal community boost ×1.3)를 RRF 이후 단계에 추가.
- Shallow PPR(damping=0.5, 2~3 iter, degree-normalized)를 project_id 필터가 적용된 서브그래프 위에서 실행. BFS 대신 또는 병행.
- 비-LLM query classifier: entity density + 쿼리 길이 + predicate 키워드로 `{specific, broad, comparative, temporal}` 4-mode 라우팅. 각 mode별 다른 retrieval 전략.
- Vector-PRF 도입: top-3 결과 임베딩을 α=0.7로 쿼리 임베딩과 평균해 2차 검색. RM3는 적용하지 말 것(2024 GPRF 논문 기준 BM25보다 나빠지는 경우 빈번).
- Graph densification: 엔티티 임베딩 cosine > 0.8 synonymy 엣지 추가(HippoRAG 패턴). 평균 차수 4 이상 목표.

**Phase 3 — 장기 (3~6개월)**
- GLiNER multi-v2.1 ONNX int8을 ingestion 파이프라인에만 투입, alias dictionary auto-expansion. 쿼리 hot path에는 여전히 Aho-Corasick만.
- BGE-reranker-v2-m3 ONNX int8을 선택적 최종 rerank 단계로. ARM64 A1 Flex 4 OCPU에서 top-20 rerank가 200~400ms이므로 MCP 클라이언트가 `rerank=true` 옵션을 명시할 때만 호출.
- Hierarchical Leiden(graspologic `hierarchical_leiden(max_cluster_size=50)`)으로 multi-level community 트리 구축. GraphRAG의 global search는 LLM을 요구하므로 JARVIS에서는 **level-N 커뮤니티를 cross-project 네비게이션 facet으로만 노출**하는 축소 버전만 채택.
- 필요 시 per-project partial HNSW index. 프로젝트가 20개 초과할 때 도입.

## JARVIS에 적합하지 않은 패턴 (명시적 배제)

**Microsoft GraphRAG의 global search**: 맵-리듀스 전체 LLM 호출이 필수. 서버 LLM 배제 제약 위반. 커뮤니티 보고서 생성도 LLM 요구. JARVIS에서는 채택 불가.

**LightRAG 전체**: 쿼리 시점 LLM 키워드 추출 + 생성 둘 다 필수. HippoRAG 2가 명시적으로 "LightRAG가 NQ/PopQA 같은 단순 QA에서 vanilla RAG보다 나쁘다"고 비판하는 점도 감안.

**LLM-based query routers (LlamaIndex `RouterQueryEngine` with `LLMSingleSelector`, LangChain `SelfQueryRetriever`)**: 제약 1번 위반. 대신 `PydanticSingleSelector` / 커스텀 `BaseSelector`를 구현해 규칙 기반 라우팅 사용.

**Letta의 agent-as-scope 모델**: 에이전트 LLM이 태그를 고르므로 쿼리 시점 LLM 의존. JARVIS는 MCP 클라이언트들이 다양한 LLM을 쓸 수 있어야 하므로 서버 측이 특정 에이전트에 종속되면 안 됨.

**자동 project 추론(Mem.ai 스타일)**: 증거가 "불리"하다. Mem.ai는 내부 retrieval조차 공개하지 않는 vibe-based 제품이고, Reflect의 자동 backlink는 "추론"이 아니라 **명시적 entity mention에 대한 확정적 링크 생성**이다. Glean의 "자동 분류"는 source system의 기존 스키마를 크롤할 뿐이지 진짜 추론이 아니다. **JARVIS는 episode_id, session_id, 또는 클라이언트가 명시적으로 전달한 project 힌트를 쓰기 시점에 저장해야 하며, 쿼리 시점의 "이 쿼리가 어느 프로젝트인가"는 앵커 엔티티 매칭이라는 확정적 규칙으로만 수행**한다.

**서버 측 cross-encoder reranker 기본 활성화**: BGE-reranker-v2-m3는 568M params라 ARM64 A1 Flex 4 OCPU에서 top-20 rerank가 200~400ms. Always Free tier의 latency budget에서 무겁다. jina-reranker-v2는 빠르지만 **CC-BY-NC 라이선스**라 상용 가능성을 차단한다. 따라서 reranker는 opt-in 옵션으로만 두고, 기본 스택은 reranker 없는 A+B+Leiden 수리로 구성하는 것이 현실적이다.

## 참고 시스템 (구현 시 코드 레퍼런스)

**Zep/Graphiti** (github.com/getzep/graphiti): `group_id` 기반 그래프 namespace 격리. Cypher 수준에서 하드 필터를 어떻게 전파하는지 가장 깔끔한 참조. `mcp_server/README.md`에 MCP 통합 예제 존재 — JARVIS가 직접 인용 가능한 가장 가까운 선례.

**Mem0** (github.com/mem0ai/mem0): `mem0/memory/main.py`의 `user_id`/`agent_id`/`run_id` + metadata 필터 구조. pgvector/ChromaDB/Qdrant로의 WHERE 푸시다운 실제 코드. JARVIS의 `project_id` 컬럼 설계에 직접 복제 가능.

**NodeRAG** (github.com/Terry-Xu-666/NodeRAG, arXiv:2504.11544): 쿼리 시점 LLM 없이 "exact match on entity names + vector similarity on semantic units → shallow PPR"라는 구조적 파이프라인을 가장 명쾌하게 구현. JARVIS가 중기 목표로 삼아야 할 **정확한** 아키텍처.

**HippoRAG v2** (github.com/OSU-NLP-Group/HippoRAG, arXiv:2502.14802): PPR 하이퍼파라미터(damping=0.5, passage weight_factor=0.05)의 실측 기반 디폴트 값. Recognition memory 단계는 LLM이라 JARVIS에서 제거해야 하지만, PPR 부분은 그대로 이식 가능.

**LlamaIndex `MetadataFilters`** (docs.llamaindex.ai의 vector_stores 예제): 벡터 스토어 레벨 push-down 필터 패턴의 canonical reference. `condition=FilterCondition.AND`, `operator=FilterOperator.EQ`로 RRF 이전에 exact 필터링.

**graspologic / leidenalg** (github.com/graspologic-org/graspologic, github.com/vtraag/leidenalg): CPM + resolution_parameter 스윕, hierarchical Leiden, 기본 설정의 실제 코드. JARVIS의 Leiden 재구성에 직접 사용.

## 예상 품질 개선 지표 (측정 가능 KPI)

Phase 1 완료 직후 기준:
- **앵커 존재 쿼리의 top-1 프로젝트 정확도**: 현재 매우 낮음(실측 3개 중 3개 실패) → 95% 이상. 앵커가 매칭되는 이상 hard filter가 확정적 정답을 보장.
- **Top-10 cross-project 오염률** (top-10 중 잘못된 프로젝트 비율): 앵커 쿼리에서 0% (하드 필터), 앵커 없는 broad 쿼리에서 여전히 40~60%이므로 Phase 2에서 community cohesion rerank로 완화.
- **p95 쿼리 지연**: 현재 대비 +5 ms 미만(Aho-Corasick <1ms + `iterative_scan`은 `ef_search=100`에서 거의 무시 가능).
- **Leiden singleton 비율** (Phase 2 후): 51% → 10% 이하 목표.
- **앵커 coverage**: Phase 1 초기(수동 seed 사전)에서 ~60%, Phase 3 (GLiNER auto-expansion 후) ~90% 이상 예상.

Phase 2·3 측정은 **golden query set**(최소 30~50개, 각 프로젝트별 6~10개)을 먼저 구축하고 CI에 retrieval regression test로 박는 것을 권장한다. JARVIS 스스로가 memory server이므로 golden set 자체를 MCP로 관리하면 멱등성 보장 용이.

## 명시적 태깅 vs 자동 추론 최종 판단

증거 무게를 누적하면 **명시적 태깅이 압도적이다**. 근거 다섯 가지:

첫째, **프로덕션 시스템 9개 중 7개가 명시적 태깅**(Mem0, Zep, Letta, Cognee, NotebookLM, Reflect, Obsidian). 자동 추론을 한다고 마케팅하는 Mem.ai조차 내부 retrieval을 공개하지 못하며, 실제 "자동"은 user가 `#Collection`을 타이핑하거나 AI suggestion을 confirm하는 semi-manual이다. 둘째, **Glean의 "자동"은 진정한 추론이 아니라 source system의 기존 메타데이터를 크롤**할 뿐이다 — JARVIS에는 그런 upstream source가 없다. 셋째, **학술 측(MES-RAG, NodeRAG, LlamaIndex `MetadataFilters`)도 같은 결론**. 넷째, JARVIS에게 자동 추론을 구현한다는 것은 결국 episode 텍스트 임베딩으로 project classifier를 학습한다는 뜻인데, 6 episodes·567 facts로는 학습 데이터가 근본적으로 부족하다. **소규모 데이터에서는 추론 자체가 불가능하다** — 사용자 제약 3번(소규모 데이터에서 동작)과 자동 추론은 호환 불가다. 다섯째, MCP 프로토콜 측면에서 클라이언트가 `project` 힌트를 쓰기 요청에 포함하는 것은 프로토콜 중립적 확장이 가능하지만, 서버 측 자동 추론은 클라이언트 간 동작이 비결정적이 되어 **"cross-device/cross-AI 접근" 제약 4번을 약화**시킨다.

권고는 단순하다. **쓰기 시점에 클라이언트가 `project_id`를 명시하도록 MCP 툴 시그니처를 확장**하고(기본값 예: 해당 에피소드의 project), 이를 필수 필드로 저장한다. **쿼리 시점에는 앵커 엔티티 매칭이라는 확정적 규칙**으로만 project를 결정한다 — 추론하지 않고 "쿼리에 언급된 엔티티의 project"를 그대로 사용. 두 엔티티가 다른 project를 가리키면 명시적 cross-project 비교 mode로 분기. 이것이 모든 제약을 동시에 만족하는 유일한 설계다.

---

## 맺음: 바뀐 이해

JARVIS의 초기 진단은 "Leiden 커뮤니티가 깨져서 retrieval이 나쁘다"였을 가능성이 높지만, 조사 결과 **커뮤니티와 retrieval 품질은 인과 사슬에서 각자 다른 증상**이며 공통 원인은 "project scope의 구조적 강제 부재 + 그래프 sparsity" 두 가지다. 둘 다 알고리즘을 바꿔서 해결하는 문제가 아니라 **데이터 스키마(project_id 필드)와 데이터 밀도(동의어 엣지)를 고쳐서 해결**하는 문제다. "LLM으로 다 풀자"는 커뮤니티의 유혹을 배제하면, 2024~2025 프로덕션 RAG의 컨센서스는 오히려 명확해진다 — **구조적 하드 필터를 먼저 걸고, 필터링된 부분 위에서만 의미 기반 랭킹을 한다**. NodeRAG의 dual-search, Graphiti의 group_id, LlamaIndex의 MetadataFilters가 같은 문장의 세 가지 표현이다. JARVIS에게 필요한 것은 새로운 알고리즘이 아니라 이 문장의 한국어+MCP 구현이다.

---

## 채택 결정 기록 (사후 추가)

**2026-04-18 오후 세션 결정 (by user)**: 이 리서치의 핵심 권고인 `project_id` 필수 태깅은 **채택하지 않음**.

이유:
- JARVIS 비전이 "AI 클라이언트가 접속해서 항해하는 외부 뇌" / "위키형 교차 프로젝트 하이퍼링크 유지"이므로 project 경계를 강제하면 비전과 충돌
- 대신 **entity-anchored retrieval만 채택** (이 리서치의 A축만). B축(project_id 파티셔닝)과 C~E축은 유보/배제
- Phase 1 구현은 Aho-Corasick + 앵커 2-hop BFS로 범위를 좁히되, 교차 프로젝트 엣지는 끊지 않음

결과적으로 이 리서치는 "A축 기술(Aho-Corasick, pgvector 0.8 iterative_scan)"과 "9개 시스템 비교 맥락" 부분만 구현에 반영됨. 전체 로드맵으로 쓰지 않음.
