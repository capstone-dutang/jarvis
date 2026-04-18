# 의미적 사실 중복 제거 (Semantic Fact Dedup) — 적용 가능한 리서치

> 연구 일자: 2026-04-19
> 성격: 프로덕션 시스템 dedup 알고리즘의 소스 레벨 조사 + JARVIS 적용 설계
> 상태: 적용 대기

## 배경

- **현재 dedup**: `(entity, predicate, object_value)` byte-exact 매치
- **실측 결과**: 6개 세션 / 207 fact에서 중복 **0건** 검출 → 완전 실패
- **이미 가진 인프라**: pgvector 0.8 + HNSW, `dragonkue/multilingual-e5-small-ko` (384d), NLI (nli-deberta-v3-xsmall), `_resolve_predicate`(cosine > 0.85 병합)
- **목적**: byte-exact를 넘어서는 3-way 분기 — `dedup` / `supersede` / `new` — 의 실측 기반 알고리즘 도출

---

## 1. 프로덕션 시스템이 실제로 하는 일

### 1.1 Graphiti — "임계값 없이 LLM에 위임" 전략

소스: `graphiti_core/utils/maintenance/edge_operations.py`, `dedup_helpers.py`, `node_operations.py`, `prompts/dedupe_edges.py`.

**Edge (fact) dedup 흐름 — `resolve_extracted_edge()`**:
1. **Fast path**: `related_edges == 0 AND existing_edges == 0` → LLM 호출 생략, 바로 new로 확정.
2. **Exact match**: `_normalize_string_exact()`로 소문자화 + 공백 정규화한 뒤 `(source_uuid, target_uuid, normalized_fact)` 전부 일치하면 기존 edge 재사용, 에피소드 UUID만 append. **유사도 임계값 자체가 없음**.
3. **Candidate 수집**: 동일 entity 페어에 묶인 edge만 후보 (entity-blocking). hybrid search (embedding + BM25)로 뽑음.
4. **LLM 호출**: `ModelSize.small`로 `dedupe_edges.resolve_edge` 프롬프트 실행. Pydantic `EdgeDuplicate` 반환 — `duplicate_facts: list[int]`, `contradicted_facts: list[int]`.
5. **Temporal invalidation** (`resolve_edge_contradictions`): 기존 edge의 `invalid_at = new_edge.valid_at`, `expired_at = utc_now()` 설정. 역방향 체크도 수행 (새 edge가 이미 오래된 사실을 만드는 경우).

프롬프트 원문(시스템 메시지): "*You are a fact deduplication assistant. NEVER mark facts with key differences as duplicates.*" 핵심 제약: **"숫자/날짜/한정사(qualifier) 차이가 있으면 duplicate 아님"**. 그리고 "*duplicate AND contradicted*" 상태가 동시 가능 (같은 관계지만 값이 갱신되었을 때).

**Node (entity) dedup — `node_operations.py`**:
- `NODE_DEDUP_COSINE_MIN_SCORE = 0.6` — 이 값 **미만이면 후보에서 제외**.
- Deterministic 경로: `_normalize_string_exact` → entropy filter (Shannon ≥ 1.5 bits, len ≥ 6, tokens ≥ 2) → MinHash/LSH (32 permutations, band size 4) → Jaccard ≥ 0.9.
- 실패 시 LLM 에스컬레이션.

**핵심 시사점**: Graphiti는 **pre-filter용 0.6만 사용**, 실제 duplicate/contradicted 판정은 LLM이 한다. 즉 "cosine > 0.85면 dedup"식 숫자 규칙을 쓰지 않는다.

### 1.2 Mem0 — "Top-10 + LLM이 4-way 결정"

소스: `mem0/memory/main.py` line ~1173 (`top_k=10`), line 1196-1208 (LLM generate).

```python
existing_results = self.vector_store.search(
    query=parsed_messages,
    vectors=query_embedding,
    top_k=10,
    filters=search_filters,
)
```

- **임계값 없음**. 무조건 top-10 retrieve → LLM에게 던진다.
- LLM이 **ADD / UPDATE / DELETE / NOOP** 중 하나를 JSON으로 반환 (arXiv:2504.19413).
  - ADD: 의미상 동등한 기존 메모리 없음 → 새로 삽입
  - UPDATE: 기존을 보강 ("plays cricket" → "loves cricket with friends") — 원본 ID 유지
  - DELETE: 모순되는 기존 메모리 제거
  - NOOP: 이미 있거나 불필요
- 기본 `threshold`가 `None → 0.1`로 바뀐 건 "명백히 무관한 꼬리를 잘라내는" 용도일 뿐, dedup 결정에는 쓰이지 않는다.
- **파괴적 업데이트**: 기존 레코드를 덮어쓰거나 삭제. 히스토리는 `~/.mem0/history.db` SQLite 감사 로그. 이중 시간(bitemporal) 모델 없음.

### 1.3 LangMem — "Trustcall + JSON Patch로 LLM이 패치 생성"

- `MemoryStoreManager.enable_inserts / enable_updates / enable_deletes` 플래그로 허용 연산 제어. **수치 임계값이 드러난 코드 없음**.
- 기반은 `trustcall.create_extractor` — LLM이 기존 JSON 문서 전체를 재생성하는 대신 **JSON Patch** (RFC 6902) 연산을 생성. "patch-don't-post" 철학. UUID 보존.
- dedup은 결국 LLM의 판단. "novel information"이 감지되면 신규 생성, "contradictory or supplementary"면 업데이트.

### 1.4 Zep (Graphiti의 상용 버전)

arXiv:2501.13956. 3개 검색 함수 병행: **cosine semantic + full-text + BFS**. Dedup은 Graphiti와 동일 파이프라인. n=4 이전 메시지만 추출 컨텍스트로 제한해 long-context 열화를 회피.

### 1.5 패턴 요약

| 시스템      | Pre-filter 임계값 | 최종 판정자         | 3-way/4-way |
| ----------- | ----------------- | ------------------- | ----------- |
| Graphiti    | node cosine ≥ 0.6 | LLM (small model)   | dup/contra  |
| Mem0        | 없음 (top-10)     | LLM                 | A/U/D/NOOP  |
| LangMem     | 없음              | LLM (Trustcall)     | insert/update/delete |
| Zep         | Graphiti와 동일   | LLM                 | dup/contra  |

**공통점**: 모두 **"수치 임계값만으로 dedup을 확정하지 않는다"**. 임베딩은 후보 검색(retrieval) 역할이고, dedup 확정은 LLM이 담당. 수치 경계로 자동 병합하는 설계는 **소규모 메모리 + 즉각 응답** 요구가 없으면 선택하지 않는 패턴이다.

---

## 2. 임계값 권장값 — multilingual-e5 현실

### 2.1 E5 점수 분포의 구조적 특성

Hugging Face `intfloat/multilingual-e5-large` discussions #10 (유지관리자 답변):

> *"The model uses a small temperature of 0.01 for InfoNCE contrastive loss, which naturally produces high similarity scores across the board."*

관찰된 분포:
- **완전히 다른 문장**: cosine 0.74 – 0.84
- **무관한 단어**: cosine 0.79 – 0.84
- **실사용 범위**: 0.7 – 1.0에 압축됨

유지관리자 공식 입장: **"절대값은 신뢰 지표가 아니다. 상대 순위만 쓰라."** 재스케일링 제안:
```
new_score = 2 * (old_score - 0.85) / (1.0 - 0.7)
```
→ (-1, 1) 범위로 선형 변환.

### 2.2 JARVIS가 쓰는 `dragonkue/multilingual-e5-small-ko` 구체 수치

모델 카드는 **NDCG@10 평균 0.6888** (7개 한국어 retrieval 데이터셋)만 공개. **STS/KorSTS 벤치마크 수치 없음**. 페어와이즈 유사도 임계값은 **반드시 자체 검증셋으로 경험적으로 측정해야 한다**.

### 2.3 임계값 밴드별 실패 모드 (E5 계열 기준)

| 밴드     | 해석 (문헌)                       | 실패 모드                                                                 |
| -------- | --------------------------------- | ------------------------------------------------------------------------- |
| **0.70** | "같은 주제" 수준. 거의 모든 문장 쌍이 여기 이상 | False positive 폭증. "커피 좋아함" vs "차 좋아함"도 0.75 이상 나옴.  |
| **0.80** | 관련성 약함                        | E5에서는 **무관 문장도 이 영역 진입**. 필터로 쓰면 5-10% 무관 통과.       |
| **0.85** | **동일 topic + 관련 predicate**    | 현재 JARVIS `_resolve_predicate` 임계값. 관측: 수용할 만하지만 "invariant_rule" 같은 동일 predicate 내 **서로 다른 규칙을 묶어버림** (리포트의 5회 중복 사례 주범으로 의심). |
| **0.90** | **사실상 같은 의미의 패러프레이즈** | 가장 실용적. "MCP 연결 완료" vs "localhost:8002 가동 중"은 0.90 못 넘을 가능성 높음. recall 감소. |
| **0.92** | 거의 동일 표현                    | 정밀도 최상. 문제: 한국어 ↔ 영어 페어가 여기 잘 도달 못함 (교차언어에서 0.87~0.91 한계). |
| **0.95** | 단어 수준 near-identical          | byte-exact보다 약간 관대한 수준. 실제 "다른 wording"은 거의 못 잡음.   |

NVIDIA SemDeDup은 near-identical 중복 제거에 `eps=0.01` (cosine ≥ 0.99)를 씀 — 웹 크롤 규모 데이터 정제용. MPNet paraphrase MRPC 최적 **0.671** (MDPI 연구). 도메인별로 편차 크다는 방증.

### 2.4 OpenAI ada-002 / BGE 대비 calibration 차이

- **OpenAI ada-002**: 분포가 더 평탄. 전형적 dedup 임계값 **0.85 – 0.88**에서 동작.
- **BGE-large-en / BGE-m3**: E5와 유사한 contrastive 학습, 분포도 비슷하게 압축 (0.65 – 1.0). dedup 임계값 **0.88 – 0.92**가 일반.
- **multilingual-e5-small**: 분포가 가장 압축 (0.7 – 1.0). 단순 임계값 이식은 **절대 금지**.

**JARVIS 결론**: 외부 권장값 숫자를 그대로 가져오면 틀린다. 자체 라벨링 100 – 200쌍 (duplicate / refinement / new) 만든 뒤 F1 최대화 지점을 찾아야 한다. 그 전까지는 **"LLM에 위임" 패턴 (Graphiti/Mem0)**을 차용하는 것이 안전.

---

## 3. Predicate-aware 분기 — 같은 predicate, 다른 object

### 3.1 문제 정의

`JARVIS.invariant_rule`이 5번 등장 — 5개 서로 다른 규칙인가 (다른 측면), 아니면 같은 규칙의 5가지 표현(같은 측면)인가?

### 3.2 Predicate 타입 분류 (구현 힌트)

세 타입으로 분리하면 분기가 명확해진다:

| Predicate 타입    | 예                                  | 같은 (entity, predicate) 쌍 의미                 | Dedup 전략             |
| ----------------- | ----------------------------------- | ------------------------------------------------ | ---------------------- |
| **State (단일값)** | age, current_status, current_model  | **하나만 활성**. 새 값은 기존을 supersede.       | 현재 JARVIS 방식 OK    |
| **Attribute (리스트)** | invariant_rule, known_limitation, uses_technology | **여러 개 공존 가능**. 각각 다른 사실.        | byte-exact dedup **불충분**, 의미 기반 dedup 필수 |
| **Relation**      | depends_on, implements, collaborates_with | 두 entity 사이의 관계. 여러 개 가능.            | entity pair 블로킹     |

현재 JARVIS는 모든 predicate를 **State처럼** 취급 — `supersede` 로직이 "같은 (entity, predicate)면 기존을 대체"하기 때문. 이게 `invariant_rule`에는 잘못된 모델.

### 3.3 Graphiti의 해결: Edge 그 자체가 여러 개

Graphiti는 같은 (subject, predicate, object) 페어에 여러 edge fact가 **공존 가능**. dedup 프롬프트가 "key differences"를 직접 보고 결정. 즉 **predicate 타입을 추출 시점에 분류하지 않고, dedup 시점에 LLM이 각 쌍을 비교**.

### 3.4 JARVIS에 맞는 현실적 타협

전면 LLM 위임은 비용/지연 부담. 다음 휴리스틱 제안:

**단계 A — Predicate 카테고리 힌트 (추출 프롬프트에 추가)**:
```
predicate가 다음 중 무엇인지 분류:
- STATE: 시점마다 단 하나의 값 (예: current_phase, age)
- ATTRIBUTE: 여러 값이 병존 (예: invariant_rule, known_limitation)
- RELATION: 두 entity 관계 (예: depends_on)
```

**단계 B — Dedup 분기**:
- STATE: 기존 로직 유지 (supersede on predicate match).
- ATTRIBUTE: **object-level 의미 중복 검사**. object embedding cosine + NLI 보조.
- RELATION: entity pair 블로킹 후 동일 처리.

이 분류를 LLM 추출 시점에 저장하면 dedup 시 공짜로 쓸 수 있다 (비용 증가 미미, prompt token 약 50).

---

## 4. NLI + 임베딩 결합 3-way 분기

### 4.1 사용 시점

| 신호                 | 쓰임                                                   |
| -------------------- | ------------------------------------------------------ |
| **Embedding cosine** | 후보 retrieval (top-k) + 표면 유사도 pre-filter        |
| **NLI entailment**   | 함의 관계 (F_new → F_old 또는 그 반대)                 |
| **NLI contradiction** | 충돌 (supersede 트리거)                                |
| **NLI neutral**      | "관련 있지만 별개" 신호                                |

### 4.2 Graphiti가 혼합 안 쓰는 이유 + JARVIS가 혼합해야 하는 이유

Graphiti는 NLI를 전혀 안 쓴다 — LLM 한 번으로 dedup + contradiction 둘 다 판정. JARVIS는 **이미 NLI를 돌리고 있고 LLM 호출을 추가하기 싫음** → 하이브리드가 비용 대비 효과 최고.

### 4.3 제안 3-way 결정 트리

입력: 새 fact F_new, 동일 entity (옵션: 동일 predicate cluster) 활성 fact 집합 {F_old}.

```
for F_old in candidates (cosine > 0.55 pre-filter, top-10):
    cos = cosine(embed(F_new.triple_text), embed(F_old.triple_text))
    nli = run_nli(F_new.text, F_old.text)  # contradiction, entailment, neutral

    # --- dedup 분기 ---
    if F_new.predicate == F_old.predicate and F_new.object == F_old.object:
        return DEDUP  # byte-exact
    if cos >= 0.93 and nli.entailment >= 0.70:
        return DEDUP  # 패러프레이즈 + 함의
    if cos >= 0.88 and nli.entailment >= 0.85 and predicate_type == ATTRIBUTE:
        return DEDUP  # 높은 함의는 강한 증거

    # --- supersede 분기 ---
    if nli.contradiction >= 0.85:
        return SUPERSEDE(F_old)  # Graphiti와 동일 강도
    if nli.contradiction >= 0.70 and has_change_language(F_new.source_quote):
        return SUPERSEDE(F_old)  # "바뀜/더 이상 X 아님" 단서
    if F_new.predicate_type == STATE and F_new.predicate == F_old.predicate:
        return SUPERSEDE(F_old)  # 상태형은 최신이 이김 (현재 로직 유지)

    # --- refinement 분기 (둘 다 유지하되 링크) ---
    if nli.entailment >= 0.70 and 0.70 <= cos < 0.88:
        return REFINEMENT(F_old)  # 별도 fact로 저장, `refines` 엣지

return NEW
```

**임계값 근거**:
- `cos >= 0.93`: E5의 압축 분포 고려, 0.92보다 약간 올림. 오탐 줄이는 방향.
- `nli.entailment >= 0.70`: Graphiti/Zep 논문 그대로.
- `nli.contradiction >= 0.85`: "auto-supersede" 표준 (Graphiti + 다수 KG 논문).
- `cos >= 0.55` pre-filter: 현재 `_check_nli_contradictions`의 값 유지. E5에서 "완전 무관"의 하한선.
- `predicate_type` 조건: 섹션 3 힌트 활용.

### 4.4 Embedding only vs NLI only vs 혼합 — 벤치마크 가이드

- **Embedding only**: 빠름 (~50ms). E5에서 false positive 허용 범위 넓음. 중문 dedup에는 부적합.
- **NLI only**: 정확 (~28ms/pair). 후보가 많으면 O(n) 증가. Pre-filter 없으면 2000 fact에서 수 분.
- **혼합 (권장)**: Embedding으로 top-10 블로킹 (E5 retrieval은 "순위" 잘 맞음) → NLI로 최종 판정. 이게 Graphiti의 philosophy (retrieval + LLM)와 동형, LLM 대신 NLI가 들어간 것.

---

## 5. JARVIS 구체 제안

### 5.1 제안 dedup 알고리즘 (pseudocode)

```python
async def resolve_new_fact(db, workspace_id, entity, fact_hint, episode, transcript):
    # Step 0: quote grounding → trust level (기존 유지)
    trust = grounded if verify_quote(fact_hint.source_quote, transcript) else low_trust

    # Step 1: predicate 해결 + 타입 분류
    resolved_pred = await _resolve_predicate(db, entity.id, fact_hint.predicate)
    pred_type = fact_hint.predicate_type  # STATE/ATTRIBUTE/RELATION (추출 시 분류)

    # Step 2: byte-exact dedup (현재 로직 유지)
    dup = await find_exact(db, entity.id, resolved_pred, fact_hint.object)
    if dup is not None:
        link_episode_reinforcing(dup, episode)
        return dup

    # Step 3: 의미 기반 retrieve — 같은 entity, 선택적으로 같은 predicate cluster
    new_text = f"{entity.name} {resolved_pred} {fact_hint.object}"
    new_vec = embed_for_storage(new_text)  # ← passage: 프리픽스로 통일

    candidates = await pgvector_topk(
        db, workspace_id,
        filter=f"entity_id={entity.id} AND superseded_at IS NULL",
        vector=new_vec,
        k=10,
        min_cosine=0.55,
    )

    # Step 4: NLI 배치
    nli_pairs = [(new_text, c.triple_text) for c in candidates]
    nli_results = detect_contradictions(new_text, [(c.triple_text, c.cosine) for c in candidates])

    # Step 5: 결정 트리 (섹션 4.3)
    decision, target = classify_semantic(
        cos_list=[c.cosine for c in candidates],
        nli_list=nli_results,
        pred_type=pred_type,
        change_language=detect_change_language(fact_hint.source_quote),
    )

    if decision == DEDUP:
        link_episode_reinforcing(target, episode)
        return target
    if decision == SUPERSEDE:
        target.superseded_at = func.now()
        target.valid_to = func.now()
        return insert_new_fact(...)
    if decision == REFINEMENT:
        new_fact = insert_new_fact(...)
        db.add(FactRelation(from_id=new_fact.id, to_id=target.id, kind="refines"))
        return new_fact
    # NEW
    new_fact = insert_new_fact(...)
    # 기존 supersede 경로 (같은 entity + 같은 predicate + STATE면 덮어쓰기)
    if pred_type == STATE:
        await supersede_same_predicate(db, entity, resolved_pred, new_fact)
    return new_fact
```

### 5.2 임계값 정리 (JARVIS 초기값)

| 용도                         | 값    | 근거                                    |
| ---------------------------- | ----- | --------------------------------------- |
| 후보 pre-filter (cosine ≥)   | 0.55  | 현재 `_check_nli_contradictions` 값 재사용 |
| DEDUP 자동 (cos ≥ + NLI 함의 ≥) | 0.93 / 0.70 | E5 압축 분포 + Graphiti 함의 표준      |
| SUPERSEDE 자동 (NLI 모순 ≥)  | 0.85  | Graphiti `resolve_edge_contradictions`  |
| SUPERSEDE 리뷰 (NLI 모순 ≥)  | 0.70  | + change language 단서 있을 때          |
| REFINEMENT (cos 범위)        | 0.70 – 0.88 | 함의 있지만 표현이 다를 때 — 공존     |
| Predicate 해결 (현재)        | 0.85  | 현 유지, 단 `predicate_type == ATTRIBUTE`일 땐 **0.92로 상향**(다른 규칙을 묶지 않도록) |

### 5.3 성능: 50ms/embed는 허용 가능한가

- 실측 단위: E5-small ONNX int8, ARM64에서 **5 – 15ms/query** (코드 주석). 50ms는 x86 최악 케이스.
- store_memory 호출당 임베딩 호출 수:
  - 새 fact text: 1회
  - 후보 top-k 재계산: pgvector HNSW로 DB 측에서 처리 (Python 측 임베딩 추가 0회)
  - 현 `_resolve_predicate`: 후보 predicate 개수만큼 임베딩 (N회) — **여기가 실제 병목**.
- 개선안:
  - **Predicate 임베딩은 entity별 캐싱** (dict in-memory, invalidate on new predicate).
  - Fact text는 이미 1회만 embed, 문제없음.
  - NLI batch 처리 (`CrossEncoder.predict`는 이미 배치 지원).
- 결론: **batching 필요성 낮음**. 현재 구조에서 `_resolve_predicate`의 N회 embed만 캐싱으로 제거하면 store당 임베딩이 1 – 2회로 수렴. 네트워크 왕복 수준 (< 30ms).

### 5.4 구현되지 않은 필수 전제

- [ ] `KnowledgeFact.fact_embedding` 컬럼 추가 (현재 별도 `Embedding` 테이블만 있음 → pgvector HNSW 쿼리 시 join 비용). 또는 fact_id → embedding 직조회 쿼리로 top-k 짜기.
- [ ] 추출 프롬프트에 `predicate_type` 필드 추가 (`STATE/ATTRIBUTE/RELATION`).
- [ ] `FactRelation(from_id, to_id, kind)` 테이블 — `refines`, `supersedes` 관계 저장 (현재 `superseded_at`만 있음).
- [ ] 검증용 라벨셋 100쌍 (실제 6 세션 / 207 fact에서 수동 어노테이션) — 임계값 튜닝 grid search 가능.

---

## 6. 주의사항 & 열린 질문

1. **현재 `_resolve_predicate`가 `embed_text` (query: 프리픽스)를 양쪽에 사용**. E5의 비대칭 의도 위반 — predicate 간 유사도는 "양쪽 다 passage:" 또는 "양쪽 다 query:" 중 하나로 통일해야 일관됨. 현재도 동작하지만 점수가 살짝 낮게 나오고 있을 가능성.
2. **predicate_type 분류를 LLM에게 시키면 OOS에서 얼마나 일관적인가** — 프롬프트에 명시 규칙 넣어도 "current_status"와 "status" 사이에서 흔들릴 수 있음. 추출-시 분류 + dedup-시 재검증 이중 구조가 필요할 수 있음.
3. **한국어-영어 교차 dedup**: E5 multilingual이지만 Korean ↔ English 패러프레이즈 쌍의 cosine 한계는 관측치로 0.87 – 0.91. 임계값 0.93은 이 경우 놓침. cross-lingual 경로는 임계값을 **0.85로 낮추고 NLI 함의 ≥ 0.80** 요구로 보완 권장 (섹션 4.3 트리 하나 추가).
4. **refinement 경로의 UX 부작용**: `refines` 엣지로 둘 다 유지하면 recall에서 "중복처럼 보이는" 두 fact가 검색될 수 있음. `recall_memory`에서 refine chain 가장 최신만 기본 노출하는 필터 필요.
5. **NLI 모델의 한국어 성능**: `nli-deberta-v3-xsmall`은 영어 중심 학습. 한국어 fact 텍스트에서는 성능 저하 가능. `klue/roberta-large-xnli` 같은 한국어 XNLI fine-tuned 모델로 교체하는 옵션 비교 필요 (별도 리서치).

---

## 7. Adoption — 구체적 JARVIS 변경 사항

### Phase 1 — 저위험 교정 (1 – 2일)
- [ ] `_resolve_predicate`에서 `embed_text` → `embed_for_storage`로 양쪽 통일 (섹션 6.1 버그).
- [ ] `_resolve_predicate` entity-level 캐싱 (섹션 5.3).
- [ ] `_check_nli_contradictions`의 cosine pre-filter 0.55 유지, 단 **top-10 제한** 추가 (현재는 같은 entity의 모든 active fact 반복).

### Phase 2 — 의미 기반 dedup 도입 (3 – 5일)
- [ ] `FactHint` 스키마에 `predicate_type: Literal["STATE","ATTRIBUTE","RELATION"]` 추가.
- [ ] 추출 프롬프트 (`extract_knowledge.py`)에 predicate 분류 지시 추가.
- [ ] `store_fact`에 의미 기반 dedup 경로 추가 (섹션 5.1 pseudocode). byte-exact 실패 후 pgvector top-k → NLI 결정 트리 적용.
- [ ] ATTRIBUTE 타입의 predicate 해결 임계값을 0.92로 상향 (현재 0.85는 invariant_rule 같은 케이스에서 별개 규칙을 묶음).

### Phase 3 — 모델 설계 강화 (5 – 7일)
- [ ] `FactRelation` 테이블 추가 (`refines`, `supersedes` 명시적 관계).
- [ ] `KnowledgeFact.fact_embedding` 컬럼 추가 + HNSW 인덱스 — top-k 쿼리 latency 10배 감소.
- [ ] `recall_memory`에서 refine chain 필터 (기본: 최신만, 옵션: 전체 체인).

### Phase 4 — 검증 (2 – 3일)
- [ ] 기존 6개 세션 / 207 fact에서 수동 라벨 100쌍 (dedup/refinement/supersede/new).
- [ ] 제안 알고리즘 재실행 → 중복 탐지 0 → 목표 15 – 30건 (전체의 7 – 15%).
- [ ] False merge rate (잘못 합쳐진 fact) ≤ 3% 유지 확인.
- [ ] 임계값 grid search (cos: 0.85/0.88/0.90/0.92/0.93, entail: 0.65/0.70/0.75/0.80) → F1 최적점 고정.

### 성공 지표
- 207 fact 재처리 시 dedup 히트 ≥ 15건
- 동일 `invariant_rule` 중복 (표현만 다른 케이스) 1회 이상 자동 병합
- OOS 새 세션에서 supersede 오탐 < 1회 / 100 fact
- store_memory 평균 지연 증가 < 30%
