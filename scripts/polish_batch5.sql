-- ep1 94880784
UPDATE episodes
SET metadata = metadata || jsonb_build_object('summary_original', COALESCE(summary,'')),
    summary = $$2026-04-16 페이즈 B 검증 + 머지 + 자비스 아키텍처 설명 세션. 페이즈 B 코드 3파일(gap_extraction/worker/mcp_adapter) 검수하니 전부 정확. 다른 환경에서 push된 맥락조립 리서치(submodular optimization, MMR+그래프 coherence, greedy O(N×K))가 research-notes.md와 충돌나서 stash→pull→pop으로 머지. 사용자가 "벡터 DB야 그래프 RAG이야" 물어봐서 "둘 다이면서 둘 다 아님 — pgvector+entity_relations+PGroonga를 PostgreSQL 하나에 합치고 RRF 합산"으로 설명. 사용자가 "재료 손질(추출) vs 요리(맥락 조립)" 비유 정립. Haiku=추출 blind, Sonnet=기존 DB와 대조해 ADD/UPDATE/NOOP 판정, 세션당 ~$0.02. 페이즈 C 플랜에서 resolve_entity의 query↔passage 비대칭 지적, 사용자가 보완해서 진행. 페이즈 D는 121개 전수 재처리 대신 5~6개 도메인/크기/언어 혼합 샘플로 검증하기로(펀드메신저+brain+아르고스 섞어 엣지케이스 확보).$$
WHERE id='94880784-88b1-46fb-af6a-05c3c87cb7a9';

-- ep2 7491ec22
UPDATE episodes
SET metadata = metadata || jsonb_build_object('summary_original', COALESCE(summary,'')),
    summary = $$2026-04-16 저녁 세션 인수인계 작업. 사용자가 "너 말이 짧아졌는데 컨텍스트가 차서 그래?"라고 물어, AI가 압박은 없지만 응답 품질이 자각 못한 채 떨어질 가능성 인정. 사용자가 "다른 세션으로 넘기는 게 낫겠다, 디테일한 프롬프트 줘 — 우리가 뭐 해왔고 새 리서치 두 개를 사용자에게 잘 설명할 수 있게"라고 요청. git status 확인하니 코드 수정 10파일 + 리서치 + 연구노트 전부 로컬에만 있어 미커밋. 전부 커밋·푸시 후 인수인계 프롬프트 작성 — 읽을 문서 순서, Phase A~C 완료 상태, Phase D 검증 결과(grounded 95.5%, relations 27개, 중복 0쌍), 새 발견 2건(assistant 턴 88% 버려짐 → 5-layer 필터 리서치 / recall flat list → community-aware MMR + adaptive K 리서치), 다음 작업 순서까지 디테일하게 포함.$$
WHERE id='7491ec22-d23a-419a-abf9-2daeb8a3884f';

-- ep3 76a6267a
UPDATE episodes
SET metadata = metadata || jsonb_build_object('summary_original', COALESCE(summary,'')),
    summary = $$2026-05-13 사용자의 "총체적 난국" 호소로 시작된 자비스 현황 전수 점검 세션. 사용자: "트랜스크립트를 너가 자비스에 올리고 자비스에 있는 걸로 회상하는 것만으로 완전한 프로젝트인데 빈 구멍이 너무 많음. 어떤 건 summary 없고, 어떤 건 회상 시스템적으로 안 됨. 어느 세션에서 대화해도 자꾸 '뭐라 해야 하지' 핀트 못 잡고 이상한 소리 반복함." AI가 "짐작 말고 실제 파일/DB 다 확인해 표로 정리하겠다"고 응답. 메모리(project_jarvis_next_plan / feedback_ai_is_worker / feedback_ingest_filter_noise) + 자비스 핵심 문서(JARVIS_DEFINITIVE / VERIFIED_STATE_2026-05-07 / README) 병렬 스캔. 인수인계서 작성 직후 흐름으로, 이 혼란이 다음 세션(이번 세션) 강제 layer Phase 1 도입의 직접 동기.$$
WHERE id='76a6267a-9b6d-4ce2-91a3-98da303400cc';

-- ep4 9b4a6baa
UPDATE episodes
SET metadata = metadata || jsonb_build_object('summary_original', COALESCE(summary,'')),
    summary = $$2026-04-17 저녁 Recall 품질 수복 — 설계-코드 갭 메우기 작업. 사용자가 플랜 검토 결과로 보완점 지적: (1) hybrid_graph_search SQL 함수의 p_fts_query TEXT 파라미터 추가는 시그니처 변경이라 CREATE OR REPLACE 불가, 마이그레이션에 DROP FUNCTION IF EXISTS 명시 필요. Stage 1 실측 문제 5건이 절대문서 Section 6/7/8에 이미 명시된 설계인데 구현이 빠진 것 — 새 리서치 불필요, 문서대로 구현. 문제: FTS 자연어 쿼리 0건, "자비스→JARVIS" 별칭 매칭 실패(cosine 0.41), Fragment 789개에 임베딩 0개, Decay/Importance 공식 미구현, Orphan 129개(embeddings 946 > entities 817). 플랜 파일 작성 후 ExitPlanMode로 승인 받고 Phase 1-1부터 entity_resolution.py 읽기 시작.$$
WHERE id='9b4a6baa-9a39-4c7c-acd5-d77c9d6fc5b4';

-- ep5 2d7bf59f
UPDATE episodes
SET metadata = metadata || jsonb_build_object('summary_original', COALESCE(summary,'')),
    summary = $$2026-04-17 밤 JARVIS Phase 1 Entity-Anchored Retrieval 구현 계획 세션. 비전: JARVIS는 "AI 클라이언트가 접속해 원하는 것을 항해해서 찾는 지식 그래프" — 검색 시스템 X, 탐색 가능한 외부 뇌. 결정사항: project_id 태깅 배제(교차 프로젝트 연결 끊지 않기 위해), 대신 질문에서 앵커 엔티티 찾아 1~2홉 이웃으로 Stage 1 범위 좁힘, LLM reranker 배제(비용/지연), Aho-Corasick 다국어 별칭 사전 채택. 환경 확인: ahocorasick_rs 미설치(신규 의존성), pgvector 0.8.2 이미 설치, alembic head=f6a7b8c9d0e1, entity_aliases 테이블 없음. AskUserQuestion 4건 수렴: 패턴 소스=entity.name+entity_aliases 둘 다, 앵커 매칭 시 graph+vector+fts 전부 앵커+이웃으로 한정, 기존 extract_query_entities 병행, 캐시 무효화는 이벤트 훅(entity 생성/이름 변경 시). 플랜 작성 후 사용자가 anchor_matching.py 언패킹 순서 버그 지적 — ahocorasick_rs API는 (pattern_index, start, end)인데 플랜은 start를 pattern_ix로 써서 Sub-Phase A 전체 동작 안 함, 즉시 3건 수정.$$
WHERE id='2d7bf59f-c0ba-4a9c-861d-f65f23c160e5';
