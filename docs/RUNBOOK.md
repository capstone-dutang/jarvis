# 자비스 운영 체크리스트

> 이번까지 반복된 운영 실수들. 재발 방지용.

## 마이그레이션 / 코드 수정 후 체크리스트

**서버 재시작은 필수.** 최소 2회 발생한 실수.

순서:
1. `alembic upgrade head` 적용 완료 확인
2. DB 스키마 변경이면 `psql`로 컬럼/함수 확인
3. 실행 중인 서버 프로세스 종료 (`tasklist`로 PID 확인 후 `taskkill`)
4. 서버 재시작 (`python -m uvicorn jarvis.main:app --app-dir src --port 8002`)
5. `curl http://localhost:8002/docs`로 startup 확인
6. 로그에서 "Episode worker started" 확인
7. 간단 쿼리로 기능 확인

**재시작 안 하면 발생하는 증상**:
- recall score가 전부 1.0으로 동일 → ILIKE fallback 확정 (hybrid_graph_search 시그니처 불일치)
- 로그에 `UndefinedFunctionError: function hybrid_graph_search(...) does not exist`

## DB 정리 순서 (추출 결과 삭제, 에피소드 보존)

personal 워크스페이스 예시:

```sql
-- FK 고려해서 자식부터 부모로
DELETE FROM entity_relations WHERE workspace_id = '...';
DELETE FROM fragments WHERE workspace_id = '...';
DELETE FROM knowledge_facts WHERE workspace_id = '...';
DELETE FROM entities WHERE workspace_id = '...';
DELETE FROM embeddings WHERE workspace_id = '...';
UPDATE episodes SET processing_status = 'pending' WHERE workspace_id = '...';
```

주의:
- test-workspace는 건드리지 말 것
- episodes는 보존 (content가 원본 transcript)
- embeddings에는 episode/entity/fact/fragment 4 종류 있음. 전부 삭제하면 백필 필요
- 재처리하면 워커가 자동으로 pending 집어감 (서버 재시작 불필요, 이미 재시작 됐다면)

## Fragment 백필 (content + 임베딩)

기존 fragment의 content가 triple인 경우 source_quote로 교체:

```sql
UPDATE fragments f
SET content = trim(kf.source_quote)
FROM knowledge_facts kf
WHERE f.source_fact_id = kf.id
  AND f.workspace_id = '...'
  AND kf.source_quote IS NOT NULL
  AND length(trim(kf.source_quote)) >= 10;

DELETE FROM embeddings
WHERE workspace_id = '...' AND source_type = 'fragment';
```

그 다음 Python으로 임베딩 재생성:
```python
from jarvis.core.worker import _backfill_fragment_embeddings
await _backfill_fragment_embeddings()
```

## Leiden community 재계산 (수동)

배치 완료 훅에서 자동 실행되지만, 중간에 필요하면:

```python
from jarvis.core.context_assembly import recompute_communities
await recompute_communities(db, workspace_id)
```

## Orphan embedding 정리 (수동)

워커 배치 훅에서 자동 실행. 수동:

```sql
DELETE FROM embeddings WHERE source_type = 'entity' AND source_id NOT IN (SELECT id FROM entities);
DELETE FROM embeddings WHERE source_type = 'fact' AND source_id NOT IN (SELECT id FROM knowledge_facts);
DELETE FROM embeddings WHERE source_type = 'fragment' AND source_id NOT IN (SELECT id FROM fragments);
DELETE FROM embeddings WHERE source_type = 'episode' AND source_id NOT IN (SELECT id FROM episodes);
```

## HNSW 인덱스 리빌드

대량 임베딩 삽입 후 HNSW 성능 저하 시:

```sql
REINDEX INDEX CONCURRENTLY ix_embedding_vector_hnsw;
REINDEX INDEX CONCURRENTLY ix_entity_name_embedding_hnsw;
```

워커 배치 훅에서 자동 실행. 트랜잭션 밖에서만 가능 (AUTOCOMMIT 필요).

## 환경 변수

- `JARVIS_DATABASE_URL=postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis`
- 기본 포트 5440, 서버 8002

## Personal 워크스페이스 ID

테스트/개발용: `2d92735f-c858-4398-b4dd-d28423208e17`

## 디버깅 체크리스트 (recall 품질 이상 시)

1. 서버 재시작 확인 (가장 흔한 원인)
2. score 분포: 전부 1.0이면 fallback, 0.01~0.02 좁으면 RRF 정상이지만 차별화 약함
3. 서버 로그에서 `Hybrid search FAILED` 또는 `Fallback search` 확인
4. Stage 1 pool 확인: recall.py `_hybrid_search_sql`을 직접 호출해서 관련 fact가 rank 몇에 있는지
5. FTS 0건 매칭 확인: `e.name &@~ :q` 단독 실행
6. 쿼리 전처리 결과 확인: `preprocess_query(q).fts_query` 출력
7. entity seed 확인: `_extract_query_entities` 결과에 기대 entity 포함되는지
