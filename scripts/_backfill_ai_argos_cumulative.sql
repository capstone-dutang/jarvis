-- ai-argos 누적 요약 백필
-- ws_id = 95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd
-- 기존 04-04~04-11 daily_subject_summaries 4행은 ON CONFLICT DO NOTHING 으로 보존
-- 신규 행은 today=CURRENT_DATE 로 박음

-- 1) workspaces.cumulative_summary
UPDATE workspaces
SET cumulative_summary = $ws_summary$Argos BTC 자동매매 — 1h봉 known-place(아는 자리) 매매법 연구. 4-04~04-11 지형도/맥락 피처 설계, 5-31 Known-Unknown Gate 6 결정 + paper alpha +0.05~+0.11R/trade. 6-02 CodexArgos 리프레임(triple-barrier + meta-label, US recovery book 유망). 다음: PiT·생존편향·체결비용 실전성 검증.$ws_summary$
WHERE id = '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd';

-- 2) daily_subject_summaries — Argos
INSERT INTO daily_subject_summaries (workspace_id, subject_id, date, summary, turn_count)
VALUES (
  '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd',
  '42984701-51f2-4636-8d18-bf01c8ab7cc5',
  CURRENT_DATE,
  $argos$BTC 1h 자동매매 연구. 4-04~04-11 지형도(BTC 가격구간 사실의 지도)·맥락 피처 설계·1년 백테스트 직접 분석 — phase6_v2 +531%, context +1017% 재현되지만 누수로 실전엣지 X 결론. 5-31 Known-Unknown Gate(memory/known/consensus/rr/conflict 6 게이트) 도입, paper alpha +0.05~+0.11R/trade. 6-02 CodexArgos 리프레임 — triple-barrier label + meta-label, model q97 + recovery rank ≥70% + delay_1d_wide15 (US Exp019 +0.406R PF1.46, KR weak). 사용자 진짜 알파(레벨+가격행동 동적 판단)는 기계화 미완. 다음: PiT/survivorship/delisted/체결비용 실전성 검증, policy search 중단.$argos$,
  0
)
ON CONFLICT (workspace_id, subject_id, date) DO UPDATE SET
  summary = EXCLUDED.summary,
  updated_at = now();

-- 3) daily_subject_summaries — 전략 아틀라스 & R:R 연구
INSERT INTO daily_subject_summaries (workspace_id, subject_id, date, summary, turn_count)
VALUES (
  '95782196-d7f8-4d8e-a4dd-7a71cf3fe4cd',
  '75f545ef-ef28-4ca0-be58-8715190c034d',
  CURRENT_DATE,
  $atlas$6-01 266개 매매법 1h봉 전수 채점 — 확정 엣지 0개. 1h 왕복 수수료 ≈0.43 ATR 가 약한 방향 엣지를 잠식하는 벽. 추세추종 베타도 2022 하락장 −0.285R로 손실. 발견: 변동성 압축 게이트(압축 뒤 분출 +기대, 횡보 함정 — 전 패밀리 공통 가장 견고한 결), 평균회귀는 거꾸로 모멘텀 우위. R:R 시스템 — 먼 고정타겟(6~10R)이 트레일링 우수, 손절 넓게 3 ATR, 좋은 조건 4개+ 쌓으면 +0.36R PF 1.64. 후보 14종(펀딩 컨트래리언·VIX 스프레드 등) shortlist, 4h/1d 멀티TF 일부 미완. AI 차트판단 재검정 +0.114R p=0.19 유망하나 미확정.$atlas$,
  0
)
ON CONFLICT (workspace_id, subject_id, date) DO UPDATE SET
  summary = EXCLUDED.summary,
  updated_at = now();
