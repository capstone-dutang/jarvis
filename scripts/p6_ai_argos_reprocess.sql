-- Phase P6 step 4: ai-argos episode reprocessing
-- Workspace: 95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd (ai-argos)
--
-- Goal: knowledge_facts / entity_relations were 0 in this workspace despite
-- 8 episodes. This script ingests domain knowledge derived from each
-- episode summary (BTC 자동매매 백테스트 / DP labeling / pyarrow / 지형도 /
-- 승률 77% 허상 / 기대수익 / 파이프라인 검증).
--
-- Entities are deduped via name_normalized = lower(name). New entities are
-- created only if no existing match — we already have 10 entities in ai-argos
-- (Argos, 2026-04-11T15, 2026-04-11T18, and 7 episode_topic anchors).
--
-- Argos canonical: 42984701-51f2-4636-8d18-bf01c8ab7cc5

\set ON_ERROR_STOP on
BEGIN;

-- ── Step 1: Ensure all domain entities exist ──────────────────────────────
-- Use INSERT ... ON CONFLICT DO NOTHING via the unique constraint on
-- (workspace_id, name_normalized). Generate fresh uuids only for new rows.

INSERT INTO entities (id, workspace_id, name, name_normalized, entity_type, created_at)
VALUES
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', 'DP', 'dp', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '진입점', '진입점', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '백테스트', '백테스트', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '시뮬레이션', '시뮬레이션', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '수익률', '수익률', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', 'DP 분류 체계', 'dp 분류 체계', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', 'pyarrow', 'pyarrow', 'product', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', 'pickle', 'pickle', 'product', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', 'Python 3.13', 'python 3.13', 'product', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '데이터 파이프라인', '데이터 파이프라인', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', 'Phase 3', 'phase 3', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', 'Phase 5', 'phase 5', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', 'Phase 6', 'phase 6', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '지형도', '지형도', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', 'BTC 가격 패턴', 'btc 가격 패턴', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '구간 분류 체계', '구간 분류 체계', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '승률', '승률', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '전이시점', '전이시점', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '진입 구간', '진입 구간', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '기대수익', '기대수익', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '거래내역', '거래내역', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '문서 정합성', '문서 정합성', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', '검증 방법론', '검증 방법론', 'concept', NOW()),
  (gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', 'BTC 자동매매', 'btc 자동매매', 'concept', NOW())
ON CONFLICT (workspace_id, name_normalized) DO NOTHING;


-- ── Step 2: knowledge_facts — one CTE per episode for clarity ──
-- Each fact: (entity_id, predicate, object_value, source_episode_id,
--             source_quote, trust_level, valid_from, recorded_at)

-- Helper view for resolution:
-- Inline subselect: SELECT id FROM entities WHERE workspace_id=ws AND name_normalized=...

-- ── Episode fde09d0d (2026-04-04 오전, pyarrow vs pickle) ──
INSERT INTO knowledge_facts (id, workspace_id, entity_id, predicate, object_value,
                              source_episode_id, source_quote, trust_level, valid_from, recorded_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', e.id, k.predicate, k.object_value,
       'fde09d0d-a451-4fd7-a2d7-8324d76d8fae', k.source_quote, 'grounded'::trustlevel, NOW(), NOW()
FROM (VALUES
  ('pyarrow',          'has_compatibility_issue',  'Python 3.13 호환성 문제',
     '2026-04-04 오전 Argos 백테스트 데이터 파이프라인 작업. pyarrow vs pickle 우회 결정 (Python 3.13 호환성 문제)'),
  ('pickle',           'considered_as_workaround', 'pyarrow 호환성 우회 수단',
     'pyarrow vs pickle 우회 결정 (Python 3.13 호환성 문제) — pickle 우회 시 Phase 3,5,6에서도 계속 pickle 의존하게 됨'),
  ('Argos',            'made_decision',            'pyarrow 해결 우선 (pickle 우회 거부)',
     'pickle 우회 시 Phase 3,5,6에서도 계속 pickle 의존하게 됨. pyarrow 해결 우선 결정'),
  ('백테스트',         'covers_period_from',       '2020-01',
     '백테스트 과거 데이터 2020-01 시점부터 확인'),
  ('데이터 파이프라인','is_blocked_by',            'pyarrow Python 3.13 호환성',
     '2026-04-04 오전 Argos 백테스트 데이터 파이프라인 작업. pyarrow vs pickle 우회 결정')
) AS k(entity_name, predicate, object_value, source_quote)
JOIN entities e ON e.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                AND e.name_normalized = lower(k.entity_name);

-- ── Episode c76831e7 (2026-04-04 오후, 지형도 완전성 보고) ──
INSERT INTO knowledge_facts (id, workspace_id, entity_id, predicate, object_value,
                              source_episode_id, source_quote, trust_level, valid_from, recorded_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', e.id, k.predicate, k.object_value,
       'c76831e7-c662-403d-9b7e-58bfbff68445', k.source_quote, 'grounded'::trustlevel, NOW(), NOW()
FROM (VALUES
  ('지형도',           'is_defined_as',            'BTC 가격 패턴/구간 분류 체계',
     '지형도 (BTC 가격 패턴/구간 분류 체계) 완전성 보고 요청'),
  ('지형도',           'reported_state',           '완전성 보고 미흡 (제대로 연구 안 됨)',
     '''지형도 제대로 연구하기로 한거 아닌가'' — 사용자가 진행 우선순위 정정'),
  ('Argos',            'priority_corrected_to',    '지형도 연구 우선',
     '사용자가 진행 우선순위 정정 — 지형도 제대로 연구하기로'),
  ('BTC 가격 패턴',    'is_part_of',               '지형도',
     '지형도 (BTC 가격 패턴/구간 분류 체계)')
) AS k(entity_name, predicate, object_value, source_quote)
JOIN entities e ON e.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                AND e.name_normalized = lower(k.entity_name);

-- ── Episode 41204a78 (2026-04-04 밤, 진행 점검) ──
INSERT INTO knowledge_facts (id, workspace_id, entity_id, predicate, object_value,
                              source_episode_id, source_quote, trust_level, valid_from, recorded_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', e.id, k.predicate, k.object_value,
       '41204a78-44d5-4223-93c8-ce315908df24', k.source_quote, 'grounded'::trustlevel, NOW(), NOW()
FROM (VALUES
  ('Argos',            'progress_checked',         '진행 OK, 짚고 넘어갈 부분 없음 (2026-04-04 밤)',
     '2026-04-04 밤 Argos 작업 진행 점검 — 짚고 넘어갈 부분 없는지 확인 + 진행 OK 결정'),
  ('백테스트',         'is_in_stage',              '안정화 흐름',
     '백테스트/데이터 파이프라인 안정화 흐름의 일부'),
  ('데이터 파이프라인','is_in_stage',              '안정화 흐름',
     '백테스트/데이터 파이프라인 안정화 흐름의 일부')
) AS k(entity_name, predicate, object_value, source_quote)
JOIN entities e ON e.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                AND e.name_normalized = lower(k.entity_name);

-- ── Episode c7f12542 (2026-04-05, DP 종류별 수익률 토론) ──
INSERT INTO knowledge_facts (id, workspace_id, entity_id, predicate, object_value,
                              source_episode_id, source_quote, trust_level, valid_from, recorded_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', e.id, k.predicate, k.object_value,
       'c7f12542-4c9c-41b4-83dc-5f36befa9fee', k.source_quote, 'grounded'::trustlevel, NOW(), NOW()
FROM (VALUES
  ('DP',               'has_aspect',               '종류별 수익률 담보 가능성',
     '각 DP(진입점) 종류별 수익률 담보 가능성, DP 분류 체계'),
  ('DP',               'aka',                      '진입점',
     'DP(진입점) 종류별 수익률'),
  ('DP 분류 체계',     'requires_clarity_before',  '시뮬레이션 실행',
     '백테스트 검증의 정확한 기준 정립을 위해 시뮬레이션 전 이론적 명확성 확보 시도'),
  ('백테스트',         'requires_baseline',        'DP 분류 체계 + 종류별 수익률 담보',
     '백테스트 검증의 정확한 기준 정립을 위해 시뮬레이션 전 이론적 명확성 확보 시도'),
  ('시뮬레이션',       'preceded_by',              'DP 분류·수익률 깊은 토론',
     '시뮬레이션 전 깊은 대화 — 각 DP(진입점) 종류별 수익률 담보 가능성')
) AS k(entity_name, predicate, object_value, source_quote)
JOIN entities e ON e.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                AND e.name_normalized = lower(k.entity_name);

-- ── Episode e7c9680a (2026-04-10, 검증 방법론) ──
INSERT INTO knowledge_facts (id, workspace_id, entity_id, predicate, object_value,
                              source_episode_id, source_quote, trust_level, valid_from, recorded_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', e.id, k.predicate, k.object_value,
       'e7c9680a-b240-43f9-bc7c-e215394c47e2', k.source_quote, 'grounded'::trustlevel, NOW(), NOW()
FROM (VALUES
  ('검증 방법론',      'is_chosen_as',             '1년 백테스트 거래내역 하나하나 분석',
     '1년 백테스트 거래내역 하나하나 분석 결정'),
  ('백테스트',         'has_period',               '1년',
     '1년 백테스트 거래내역 하나하나 분석'),
  ('거래내역',         'has_issue',                '다른 세션 응답이 최신 백테스트 내역 아님',
     '다른 세션에서 확인 요청했지만 최신 백테스트 내역 아닌 것 받음'),
  ('문서 정합성',      'needs_verification',       '최신 백테스트 내역과 일치 여부',
     '백테스트 문서 정합성 확인 필요')
) AS k(entity_name, predicate, object_value, source_quote)
JOIN entities e ON e.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                AND e.name_normalized = lower(k.entity_name);

-- ── Episode f4bd30b0 (2026-04-11 오후, 승률 77% 허상) ──
INSERT INTO knowledge_facts (id, workspace_id, entity_id, predicate, object_value,
                              source_episode_id, source_quote, trust_level, valid_from, recorded_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', e.id, k.predicate, k.object_value,
       'f4bd30b0-f182-451c-8b94-fa9c1b7c5762', k.source_quote, 'grounded'::trustlevel, NOW(), NOW()
FROM (VALUES
  ('승률',             'reported_value',           '77%',
     '2026-04-11 오후 Argos 승률 77% 허상 토론'),
  ('승률',             'identified_as',            '허상 (false signal)',
     '승률 77% 허상 토론. 전이시점만 줬을 때 맞추는 거 / 진입하면 안되는 구간이 없다는 의문'),
  ('전이시점',         'creates_problem',          '전이시점만 줬을 때 맞추는 거 (실전과 무관)',
     '전이시점만 줬을 때 맞추는 거 / 진입하면 안되는 구간이 없다는 의문'),
  ('진입 구간',        'has_unresolved_issue',     '진입하면 안되는 구간이 없다는 의문',
     '진입하면 안되는 구간이 없다는 의문'),
  ('백테스트',         'requires_deconstruction',  '통계의 진짜 의미 분해',
     '백테스트 통계의 진짜 의미 분해')
) AS k(entity_name, predicate, object_value, source_quote)
JOIN entities e ON e.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                AND e.name_normalized = lower(k.entity_name);

-- ── Episode aeb821bf (2026-04-11 오후, 기대수익 토론 막힘) ──
INSERT INTO knowledge_facts (id, workspace_id, entity_id, predicate, object_value,
                              source_episode_id, source_quote, trust_level, valid_from, recorded_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd', e.id, k.predicate, k.object_value,
       'aeb821bf-e35b-49f9-b9b7-3079ada47330', k.source_quote, 'grounded'::trustlevel, NOW(), NOW()
FROM (VALUES
  ('기대수익',         'discussion_blocked',       'AI 답변이 핀트 못 잡음, 토론 흐름 일시 중단',
     '기대수익 토론 진행 막힘. AI 답변이 핀트 못 잡음, 토론 흐름 일시 중단'),
  ('Argos',            'session_outcome',          '기대수익 토론 중단 (2026-04-11 오후)',
     'Argos 기대수익 토론 진행 막힘')
) AS k(entity_name, predicate, object_value, source_quote)
JOIN entities e ON e.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                AND e.name_normalized = lower(k.entity_name);

-- ── Episode 77370b41 (summary '18', needs_resummarize per P5) ──
-- Skipped — bad summary, no usable content. Will be re-summarized in a later phase.


-- ── Step 3: entity_relations ──
-- Each: (from_entity_id, to_entity_id, relation_type, weight, source_episode_id)

-- Episode fde09d0d (pyarrow vs pickle)
INSERT INTO entity_relations (id, workspace_id, from_entity_id, to_entity_id,
                               relation_type, weight, source_episode_id, valid_from, created_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd',
       fe.id, te.id, k.relation_type::relationtype, k.weight,
       'fde09d0d-a451-4fd7-a2d7-8324d76d8fae', NOW(), NOW()
FROM (VALUES
  ('데이터 파이프라인','depends_on','pyarrow', 1.0),
  ('pyarrow',          'contradicts','pickle', 0.8),
  ('백테스트',         'depends_on','데이터 파이프라인', 1.0),
  ('Argos',            'part_of','BTC 자동매매', 1.0)
) AS k(from_name, relation_type, to_name, weight)
JOIN entities fe ON fe.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND fe.name_normalized = lower(k.from_name)
JOIN entities te ON te.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND te.name_normalized = lower(k.to_name);

-- Episode c76831e7 (지형도 완전성)
INSERT INTO entity_relations (id, workspace_id, from_entity_id, to_entity_id,
                               relation_type, weight, source_episode_id, valid_from, created_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd',
       fe.id, te.id, k.relation_type::relationtype, k.weight,
       'c76831e7-c662-403d-9b7e-58bfbff68445', NOW(), NOW()
FROM (VALUES
  ('BTC 가격 패턴',    'part_of','지형도', 1.0),
  ('구간 분류 체계',   'part_of','지형도', 1.0),
  ('지형도',           'depends_on','BTC 가격 패턴', 0.9),
  ('Argos',            'depends_on','지형도', 0.9)
) AS k(from_name, relation_type, to_name, weight)
JOIN entities fe ON fe.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND fe.name_normalized = lower(k.from_name)
JOIN entities te ON te.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND te.name_normalized = lower(k.to_name);

-- Episode c7f12542 (DP 분류 + 수익률 토론)
INSERT INTO entity_relations (id, workspace_id, from_entity_id, to_entity_id,
                               relation_type, weight, source_episode_id, valid_from, created_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd',
       fe.id, te.id, k.relation_type::relationtype, k.weight,
       'c7f12542-4c9c-41b4-83dc-5f36befa9fee', NOW(), NOW()
FROM (VALUES
  ('DP',               'related_to','진입점', 1.0),
  ('DP',               'related_to','수익률', 0.9),
  ('DP 분류 체계',     'depends_on','DP', 1.0),
  ('시뮬레이션',       'depends_on','DP 분류 체계', 0.9),
  ('백테스트',         'depends_on','시뮬레이션', 0.8),
  ('백테스트',         'supports','수익률', 0.7)
) AS k(from_name, relation_type, to_name, weight)
JOIN entities fe ON fe.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND fe.name_normalized = lower(k.from_name)
JOIN entities te ON te.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND te.name_normalized = lower(k.to_name);

-- Episode e7c9680a (검증 방법론)
INSERT INTO entity_relations (id, workspace_id, from_entity_id, to_entity_id,
                               relation_type, weight, source_episode_id, valid_from, created_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd',
       fe.id, te.id, k.relation_type::relationtype, k.weight,
       'e7c9680a-b240-43f9-bc7c-e215394c47e2', NOW(), NOW()
FROM (VALUES
  ('검증 방법론',      'depends_on','거래내역', 1.0),
  ('검증 방법론',      'depends_on','문서 정합성', 0.9),
  ('백테스트',         'related_to','검증 방법론', 1.0)
) AS k(from_name, relation_type, to_name, weight)
JOIN entities fe ON fe.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND fe.name_normalized = lower(k.from_name)
JOIN entities te ON te.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND te.name_normalized = lower(k.to_name);

-- Episode f4bd30b0 (승률 77% 허상)
INSERT INTO entity_relations (id, workspace_id, from_entity_id, to_entity_id,
                               relation_type, weight, source_episode_id, valid_from, created_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd',
       fe.id, te.id, k.relation_type::relationtype, k.weight,
       'f4bd30b0-f182-451c-8b94-fa9c1b7c5762', NOW(), NOW()
FROM (VALUES
  ('승률',             'contradicts','진입 구간', 0.8),
  ('전이시점',         'related_to','승률', 0.9),
  ('백테스트',         'supports','승률', 0.7),
  ('진입 구간',        'part_of','백테스트', 0.7)
) AS k(from_name, relation_type, to_name, weight)
JOIN entities fe ON fe.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND fe.name_normalized = lower(k.from_name)
JOIN entities te ON te.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND te.name_normalized = lower(k.to_name);

-- Episode aeb821bf (기대수익 토론 막힘)
INSERT INTO entity_relations (id, workspace_id, from_entity_id, to_entity_id,
                               relation_type, weight, source_episode_id, valid_from, created_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd',
       fe.id, te.id, k.relation_type::relationtype, k.weight,
       'aeb821bf-e35b-49f9-b9b7-3079ada47330', NOW(), NOW()
FROM (VALUES
  ('기대수익',         'related_to','수익률', 1.0),
  ('기대수익',         'related_to','백테스트', 0.8),
  ('Argos',            'related_to','기대수익', 0.9)
) AS k(from_name, relation_type, to_name, weight)
JOIN entities fe ON fe.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND fe.name_normalized = lower(k.from_name)
JOIN entities te ON te.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND te.name_normalized = lower(k.to_name);

-- Episode 41204a78 — light relation
INSERT INTO entity_relations (id, workspace_id, from_entity_id, to_entity_id,
                               relation_type, weight, source_episode_id, valid_from, created_at)
SELECT gen_random_uuid(), '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd',
       fe.id, te.id, k.relation_type::relationtype, k.weight,
       '41204a78-44d5-4223-93c8-ce315908df24', NOW(), NOW()
FROM (VALUES
  ('데이터 파이프라인','related_to','백테스트', 1.0)
) AS k(from_name, relation_type, to_name, weight)
JOIN entities fe ON fe.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND fe.name_normalized = lower(k.from_name)
JOIN entities te ON te.workspace_id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
                 AND te.name_normalized = lower(k.to_name);


COMMIT;

-- ── Verification ──
SELECT 'facts' AS kind, COUNT(*) AS n
  FROM knowledge_facts WHERE workspace_id='95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
UNION ALL
SELECT 'relations', COUNT(*)
  FROM entity_relations WHERE workspace_id='95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd'
UNION ALL
SELECT 'entities', COUNT(*)
  FROM entities WHERE workspace_id='95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd';
