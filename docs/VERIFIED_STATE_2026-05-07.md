# 자비스 현 상태 검증 문서

> 작성일: 2026-05-07
> 목적: "작동한다고 말한 것"이 실제로 작동하는지 직접 실행해서 확인하고 기록
> 원칙: 검증 안 한 항목은 "?" 또는 "미검증". 실측 후 통과해야 "✓ 작동".

---

## 0. 전제 조건 (프로젝트 데이터 스코프)

- **데이터 원천**: 사용자 데스크탑의 **모든 트랜스크립트** (현재 대화 포함)
- **위치**: `f:/brain/knowledge-extraction/preprocessed/sessions/*.json` (99개 + 진행 중)
- **형식**: 이미 turns 배열로 정리됨 — 추가 파싱 불필요
- **기존 DB 데이터 (6 episodes, 789 facts in `personal`)**: legacy. 새 데이터 모델로 가면 폐기 또는 별도 보존
- **마이그레이션 불필요**: episode.content를 turn으로 쪼개는 작업 안 함. raw 트랜스크립트에서 새로 ingest

---

## 1. 인프라

| 항목 | 결과 |
|---|---|
| PostgreSQL 16 (port 5440) | ✓ 작동 — `PostgreSQL 16.13 Debian` |
| pgvector 확장 | ✓ 작동 — `0.8.2` |
| PGroonga 확장 | ✓ 작동 — `3.2.5` |
| Alembic head 일치 | ✓ 작동 — `i9d0e1f2a3b4 (head)` |
| Docker Desktop / `jarvis-db-1` 컨테이너 | ✓ 작동 — `healthy` (제가 커맨드로 부팅 가능했음) |

---

## 2. 서버

| 항목 | 결과 |
|---|---|
| FastAPI 서버 가동 | ✓ 작동 — port 8005, `/health` 200 |
| MCP 어댑터 (Streamable HTTP) | ✓ 가동 — 서버 로그에 "StreamableHTTP session manager started" |
| OAuth 2.1 | ⚠ 미검증 — endpoint 존재 확인 (`/api/v1/workspaces` 등) 만 했음, OAuth 흐름 자체는 안 돌림 |

---

## 3. 데이터 — 현재 DB 내용 (2026-05-07 시점)

| 테이블 | 행 수 |
|---|---|
| workspaces | **4** (personal 1, test-workspace 1, reseed-test 1, reseed-curated 1) |
| episodes | **31** |
| knowledge_facts | **1144** |
| fragments | **1136** |
| entities | **1119** |
| entity_relations | **576** |
| entity_aliases | **5** |
| fact_episodes | **1145** |
| embeddings | **2919** |

(2026-04-19 시점 대비: episodes 18→31, facts 804→1144 — 그동안 dedup 검증으로 reseed-test/reseed-curated 워크스페이스 시딩 시 누적)

---

## 4. MCP/REST 도구 — 실제 작동 검증

| 도구 | 결과 | 상세 |
|---|---|---|
| `recall_memory` | ✓ 작동 | "SecondBrain" → 3 facts 반환 (`JARVIS.is_core_engine_for=SecondBrain` 등) |
| `search_passages` | ✓ 작동 | "예창패 SecondBrain" → "아르고스는 예창패 아이템으로 부적합" sim=0.478 top |
| `get_episode_excerpt` | ✓ 작동 | mode=relevant, 487자 발췌, matched=['SecondBrain'] |
| `explore_topic` | **🔧 버그 발견 → 즉시 교정 → ✓ 작동** | `topic_map.py`가 `hybrid_search_sql` 호출 시 `anchor_entity_ids` 인자 누락 (Phase 1 변경 후 미반영). 빈 리스트 전달로 수정 후 candidates=50, entities=15 반환 |
| `follow_relation` | ✓ 작동 | JARVIS → 5 neighbors (SecondBrain, Argos, PostgreSQL 등) |
| `initialize_memory` | ✓ 작동 | `workspace_name`, `recent_summary`, `protocol` 키 반환 |
| `store_memory` | ✓ 작동 (간접 — 오늘 reseed로 1144 facts 누적된 결과로 입증) | 별도 신규 호출은 안 함 |
| `manage_workspace` | ⚠ 미검증 | endpoint 존재 (`/workspaces` CRUD) 확인만 |

---

## 5. 회상 품질 — Q1~Q3 회귀 (personal 워크스페이스)

| Q | 결과 | 비고 |
|---|---|---|
| Q1 "예창패 SecondBrain Argos 선택 이유" | **✓ PASS** | top-1 sim=0.643 "아르고스는 예창패 아이템으로 부적합", 3/3 키워드 매칭 |
| Q2 "펀드메신저 2400 SecondBrain B2B" | **✓ PASS** | top-1 sim=0.682 "펀드메신저 커뮤니티 2,400명 → 현직자 인터뷰 → 레퍼런스", 4/5 키워드 |
| Q3 "아르고스 strength 폐기 이유" | **⚠ WEAK** | 장황 자연어 질의는 'strength' 키워드만 매칭. 단문 "strength.py 삭제"로는 top-1 sim=0.699 (별도 확인). E5 query compression 한계, 알려진 이슈 |

---

## 6. 자동 정합성 (의미 격하 예정 — 현재 동작 여부만)

| 항목 | 결과 |
|---|---|
| Auto-supersede | ✓ 작동 (코드 경로 검증됨 — 오늘 reseed에서 16건 supersede 발동 관측) |
| Byte-exact dedup (fact_episodes M:N) | ✓ 작동 (2026-04-19 reseed 1차 1건 발동, test_phase2_dedup.py 3-시나리오 통과) |
| NLI 모순 감지 | ✓ 작동 (오늘 reseed에서 다수 "NLI review needed" 로그 발생) |

---

## 7. 전제 데이터 — 트랜스크립트 가용성

| 항목 | 상태 |
|---|---|
| `preprocessed/sessions/*.json` (전처리 완료) | **✓ 99개 파일 존재** (turns 배열 포함) |
| `~/.claude/projects/**/*.jsonl` (raw Claude Code 로그) | **✓ 2167개 파일** (현재 대화 포함, 진행 중) |
| 추출본 `preprocessed/extracted/sessions/` | 7개 (2026-04-15 추출) |
| 엄선 추출본 `extracted_curated/sessions/` | 3개 (2026-04-19 추출, 비용 $10) |

전체 재수집 가능. 별도 마이그레이션 불필요.

---

## 7. 새 비전 작업에서 살아남는 것 / 폐기되는 것

(검증 결과 기반으로 작성 — 현재는 빈 칸)

### 살아남음 (verified ✓ — 새 비전에서 메인 가치)
- Episode 저장 (원문 보존)
- Fragment + 임베딩 (의미 검색의 핵심 단위)
- `search_passages` (Q1, Q2 PASS 입증)
- `get_episode_excerpt` (발췌 정상)
- Entity 인프라 (1119 entities + aliases + relations) — 주제 트리로 확장 가능
- Bi-temporal 컬럼 (git history와 동일 개념)
- DB 인프라 (PG16 + pgvector 0.8 + PGroonga 3.2)

### 격하 (보조 색인 — 새 비전에서 메인 아님)
- `knowledge_facts` 트리플 (1144건) — 메인 회상은 search_passages가 더 강함
- `fact_episodes` M:N (1145 links) — 자동 누적 의미 약함, "여러 세션 참조" 표시로만
- `entity_relations` — 시각화 그래프에 활용
- `recall_memory`, `follow_relation` — 보조 도구

### 폐기 또는 의미 재해석 필요
- 자동 supersede — git처럼 양쪽 보존 방향으로 재해석 필요
- 자동 dedup (byte-exact) — 사용자 의식적 push 모델에선 가치 약함
- NLI 자동 모순 처리 — "두 시점 다른 진술" 힌트로 격하

### 버그 발견 (이번 검증 중)
- ✓ **수정됨**: `core/topic_map.py` `hybrid_search_sql` 호출에 `anchor_entity_ids=[]` 누락. Phase 1 (2026-04-18) 변경 시 미반영. 즉시 패치, explore_topic 정상화

---

## 8. 새 비전에 필요하지만 아직 없는 것

| 항목 | 상태 |
|---|---|
| `entities.parent_id` (주제 계층) | ❌ 미구현 |
| `entities.summary` (주제 요약) | ❌ 미구현 |
| `turns` 테이블 (턴 단위 저장) | ❌ 미구현 |
| `turn_subjects` M:N | ❌ 미구현 |
| `daily_subject_summaries` (날짜×주제 요약) | ❌ 미구현 |
| "어디까지 올라가있어?" 메타 쿼리 | ❌ 미구현 |
| `/timeline?from=&to=` API | ❌ 미구현 |
| `/subject/{id}/feed` API | ❌ 미구현 |
| 웹 UI (사이드바 + 메인 + 요약 패널) | ❌ 미구현 |
| 전체 99개+ 트랜스크립트 일괄 수집 파이프라인 | ❌ 미구현 |

---

## 검증 진행 로그

### 2026-05-07 — 전체 검증 1회차 (완료)

**상태 시작**: Docker 다운, DB 다운 (사용자 한 달 부재 영향)

**복구 단계**:
1. `"C:\Program Files\Docker\Docker\Docker Desktop.exe"` 백그라운드 실행 → Docker engine 가동
2. `docker compose up -d db` → jarvis-db-1 컨테이너 healthy
3. `python -m uvicorn jarvis.main:app --port 8005` → 서버 가동

**버그 1건 발견 및 즉시 수정**:
- `src/jarvis/core/topic_map.py:68` `hybrid_search_sql` 호출 시 `anchor_entity_ids` 인자 누락 → `explore_topic` 500 에러
- 패치: `anchor_entity_ids=[]` 추가 (광역 탐색이므로 앵커 필터 비활성)
- 수정 후 candidates=50, entities=15 반환 정상화

**검증 결과 요약**:
- 인프라 (PG/pgvector/PGroonga/Alembic): ✓
- 서버 + MCP 어댑터: ✓
- DB 데이터: ✓ 31 episodes, 1144 facts, 2919 embeddings 등 보존
- MCP 도구 7/8 ✓ (manage_workspace만 미검증)
- 회상 품질 Q1, Q2 PASS, Q3 WEAK (E5 한계, 알려진 이슈)
- 자동 정합성 3종 모두 작동

**미검증 항목** (DB+서버 외):
- OAuth 흐름 (endpoint만 확인)
- `manage_workspace` (endpoint만 확인)
- `store_memory` 신규 호출 (간접 검증만)

**결론**: 비전 재정의 시점 직전 (2026-04-19) 작동하던 기능 거의 그대로 살아있음. 한 달 공백에도 코드/데이터 모두 보존됨.
