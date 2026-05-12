# 비전 재정의 — 자비스 = AI 대화의 git + 노션 스타일 위키

> 작성일: 2026-05-07
> 성격: 사용자가 한 달 자비스를 떠나 있다가 캡스톤 주차별 보고서 작성을 계기로 도달한 비전 재정의
> 상태: 활성 — 다음 작업의 기준점

---

## 1. 이전 비전과 무엇이 달라졌는가

### 이전 (2026-04-19 이전): "AI를 위한 메모리 서버"

- 1차 사용자: AI 클라이언트
- 핵심 가치: 트리플 추출 + 시맨틱 검색으로 AI가 과거 결정/맥락 회상
- 약점으로 인식했던 것:
  - AI가 자발적으로 `store_memory` 안 부름 → Path A/B/C 우회 인프라 필요
  - 추출 품질이 회상 정확도 결정 → 시맨틱 dedup, NLI, predicate_type 등 정합성 보강 시급
- 자동 정합성을 핵심 기능으로 추구 (자동 supersede, 자동 dedup, 자동 NLI 모순감지)

### 이후 (2026-05-07): "AI 대화의 git + 노션 스타일 위키"

- 1차 사용자: **사용자 본인** (AI는 보조)
- 핵심 가치 5가지로 확장:
  1. **LLM Wiki (검색/회상)** — AI 에이전트가 과거 맥락 회수
  2. **백업/창고** — 사용자가 본인 발자취 영구 보관
  3. **발자취 시각화** — 사용자가 일/주제별로 본인 글 줄글로 봄
  4. **온보딩 도구** — 새 팀원에게 주제별 변천사 보여주기
  5. **AI 협업 공간** — 여러 도메인의 AI가 같은 자료를 공유
- "AI 자동 호출 안 함"이 더 이상 약점이 아님:
  - 사용자가 "자비스에 올려" 한마디 = git push. 감수 가능
  - "어디까지 올라가있어?" = git log
  - 이게 자연스러운 사용 모델
- 자동 정합성은 **힌트로 격하**:
  - 자동 supersede → "이 시점에 이 말, 나중에 저 말" 둘 다 보존
  - 자동 dedup → "비슷한 fact 발견" 힌트만, 사용자가 결정
  - 자동 NLI 모순감지 → "두 시점 진술 달라 보임" 표시만

---

## 2. 자비스의 새 정체성 — 한 줄 표현

> **자비스 = AI 대화의 git. 원문 보존 + 의미 검색 + 시간/주제 색인 + 사용자가 명시적으로 push.**

git 비유 매핑:

| git | 자비스 |
|---|---|
| commit | episode 저장 ("올려" 명령) |
| commit message | 사용자/AI가 작성한 요약 |
| log | 시간순 회수 (일/월/년 zoom) |
| grep / log -S | search_passages (의미 검색) |
| show | get_episode_excerpt |
| blame | follow_relation, entity 그래프 |
| tag | entity_aliases |
| branch | (없음 — 현실 시간은 한 갈래) |

---

## 3. 노션 스타일 UX

### 사이드바 = 주제 트리 (무한 계층)

```
📁 자비스
  ├ 📄 자비스 (개요)
  ├ 📁 자비스-UI
  │   ├ 📄 노션 스타일 트리
  │   └ 📄 시간 줌
  └ 📄 자비스-인증
📁 Argos
  ├ 📄 strength 모델
  ├ 📄 DP labeling
  └ ...
📁 SecondBrain
  ...
```

- 주제는 무한 깊이 트리 (parent_subject_id 체인)
- 같은 턴이 여러 주제에 동시 속할 수 있음 (M:N, 우선순위 없음)

### 본문 = 시간순 줄글

- 페이지 클릭 시 그 주제 관련 턴들이 시간순으로 줄글
- 위쪽에 AI가 작성한 주제 요약 헤더
- 일/월/년 zoom 토글
- 같은 페이지에서 "그 주제의 변천사"가 자연스럽게 보임 → 온보딩 자료 그대로 사용 가능

### 검색

- 전체 검색: search_passages (현재 작동 검증됨)
- 주제 필터: subject_id 한정 검색
- 시간 필터: timestamp range

---

## 4. 데이터 모델 — 새로 필요한 것

### 추가 (작음)

```sql
-- entities (=subjects)에 부모-자식 관계
ALTER TABLE entities ADD COLUMN parent_id UUID
    REFERENCES entities(id) ON DELETE SET NULL;
ALTER TABLE entities ADD COLUMN summary TEXT;  -- AI가 reflect로 채움

-- 턴 단위 저장 (현재는 episode.content에 통째)
CREATE TABLE turns (
    id UUID PRIMARY KEY,
    episode_id UUID REFERENCES episodes(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL,
    sequence INT NOT NULL,
    role TEXT NOT NULL,  -- user/assistant
    text TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    summary TEXT,  -- 옵션
    UNIQUE (episode_id, sequence)
);

-- 턴 ↔ 주제 다대다
CREATE TABLE turn_subjects (
    turn_id UUID REFERENCES turns(id) ON DELETE CASCADE,
    subject_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (turn_id, subject_id)
);
```

### 기존 그대로 유지

- `episodes` — 원문 통째 보존 (재처리 폴백 용도)
- `fragments` — 의미 검색 단위 (search_passages 핵심)
- `entities` — 이제 subjects로도 활용
- `entity_relations` — 시각화 그래프
- `entity_aliases` — 동의어
- `embeddings` — fragment/entity 임베딩

### 의미 격하 (보조 색인으로)

- `knowledge_facts` (트리플) — 진실 회상이 아니라 빠른 색인 후보
- `fact_episodes` (오늘 만든 M:N) — 자동 confidence 누적이 아니라 "여러 세션에서 참조됨" 표시
- 자동 supersede / NLI 모순감지 — 진실 결정자가 아니라 사용자에게 힌트만

---

## 5. 워크플로우 — "올려" 흐름

### 매 세션 끝 (가벼움)

```
사용자: "이 대화 자비스에 올려"
AI: store_memory 호출
    - raw transcript → episodes
    - 턴별 분리 → turns
    - 각 턴에 대해 기존 주제 검색
        - 매칭되는 주제: 자동 분류 제안
        - 없으면: "새 주제 만들까요?" 사용자에게 확인
    - 사용자 컨펌
AI: "저장 완료. X개 턴, Y개 주제에 연결됨"
```

### 하루 끝 (묵직함)

```
사용자: "오늘 한 거 자비스에 reflect해서 정리해"
AI: 그날 모든 episodes 묶어서
    - day-level 요약 작성
    - 새로 등장한 주제들 식별
    - 기존 주제들의 변천 정리
    - 사용자에게 보여주고 컨펌
저장: 일 요약 (또는 entities.summary 갱신)
```

### 회수 (조회)

```
사용자: "자비스 UI에 대해 어떤 얘기 있었지?"
→ subject_id로 turns 시간순 회수 → AI가 줄글로 정리

사용자: "지난주에 뭐 했지?"
→ 일별 timeline 회수 → AI가 zoom 뷰로 정리
```

---

## 6. 지금까지 한 작업 재평가

### 살아남는 것 (그대로 가치 있음)

- ✅ Episode 저장 — 원문 보존, 핵심 자산
- ✅ Fragment + 임베딩 — 의미 검색의 핵심
- ✅ `search_passages` — Q1-Q3 회귀로 입증된 회상 메인 경로
- ✅ `get_episode_excerpt` — 발췌 회수, 메인 경로
- ✅ Entity 인프라 (alias, 그래프) — 주제 트리로 확장 가능
- ✅ Bi-temporal 컬럼 (valid_from/to, recorded_at) — git history와 동일 개념

### 의미 격하 (보조로)

- 🟡 `knowledge_facts` 트리플 — 빠른 색인 후보 (메인 회상은 search_passages)
- 🟡 `fact_episodes` M:N (오늘 추가) — 자동 누적 아니라 "여러 세션에서 참조됨" 표시
- 🟡 `entity_relations` — 시각화 그래프
- 🟡 `recall_memory` / `follow_relation` / `explore_topic` — 보조 도구

### 우선순위 낮음 / 재배치

- 🔻 시맨틱 dedup — 자동 병합 대신 "비슷한 fact 발견" 힌트로
- 🔻 NLI 모순감지 — 자동 supersede 트리거 대신 "이 두 시점 다름" 표시로
- 🔻 자동 supersede — 사용자 비전에선 git처럼 둘 다 보존이 맞음
- 🔻 자동 수집 훅 (예전 Path A/B/C) — 사용자가 "올려" 명령하면 충분

---

## 7. 새 로드맵 — 우선순위별 작업

### 우선순위 1: 데이터 모델 확장 (3~4일)

- [ ] `entities.parent_id`, `entities.summary` 컬럼 추가 + 재귀 CTE 헬퍼
- [ ] `turns` 테이블 신설
- [ ] `turn_subjects` M:N 테이블 신설
- [ ] 기존 6 episodes를 turns로 분리하는 마이그레이션 스크립트

### 우선순위 2: 새 store_memory 워크플로우 (2~3일)

- [ ] AI가 turn 배열 + 주제 분류 제안을 보내는 새 스키마
- [ ] 사용자 컨펌 흐름 (양방향 상호작용 — MCP 어떻게 쪼갤지 결정)
- [ ] "어디까지 올라가있어?" 메타 쿼리 (`/status` 또는 `/last-uploaded`)

### 우선순위 3: 회수 API (1~2일)

- [ ] `/timeline?from=&to=` — 시간순 줄글
- [ ] `/subject/{id}/feed` — 주제별 시간순 줄글
- [ ] `/subject/{id}/tree` — 주제 트리 (사이드바용)
- [ ] 기존 `search_passages` / `get_episode_excerpt`에 subject 필터 옵션

### 우선순위 4: 일/월/년 zoom + reflect 흐름 (2~3일)

- [ ] 일 요약 저장 위치 (entities.summary? 별도 reflections 테이블?)
- [ ] reflect 워크플로우 — "오늘 정리해" 명령에 day-level 요약 생성·저장
- [ ] 월/년 zoom 집계 API

### 우선순위 5: 웹 UI MVP (1~2주)

- [ ] 노션 스타일 사이드바 트리 (재귀 컴포넌트)
- [ ] 페이지 본문 (주제별 줄글 또는 일별 줄글)
- [ ] 검색 박스
- [ ] OAuth 로그인 (이미 있음)

### 우선순위 6: 자동 정합성 → 힌트로 (선택)

- [ ] 자동 supersede / dedup / NLI 모순감지 동작을 끄고 "힌트만" 모드
- [ ] 사용자가 UI에서 "이 두 fact 합칠래" "이게 supersede야" 수동 결정
- [ ] 또는 그대로 두고 의미만 재해석 (백엔드 동작 변경 없이 문서/표현만 격하)

---

## 8. 명시적으로 안 하는 것

- ❌ 전체 91개 트랜스크립트 재시딩 — 새 데이터 모델 안정 전엔 무의미
- ❌ 시맨틱 dedup 깊은 튜닝 (오늘 리서치한 100쌍 grid search) — 우선순위 낮아짐
- ❌ 자동 수집 훅 인프라 (Path A/B/C) — "올려" 명령으로 대체
- ❌ recall_memory 트리플 회상 최적화 — 메인 회상은 search_passages

---

## 9. 리스크 / 미해결 디자인 결정

### 리스크

- **양방향 상호작용** — "AI가 주제 분류 제안 → 사용자 컨펌"이 한 번의 MCP 호출로 안 됨. 두 단계로 쪼개야 함. MCP 프로토콜 한계 안에서 어떻게 풀지 디자인 필요
- **기존 episodes turn 분리** — 현재 transcript 형식이 `[role] text` 정도라 파싱 가능하지만 일관성 낮음. 6개 episode라 수동 검증 가능
- **웹 UI** — 디자인 작업 무게가 적지 않음. MVP를 어디까지 단순화할지 결정 필요

### 미해결 디자인 결정

- **일 요약 저장 위치** — `entities.summary` 재활용 (subject별 요약) vs 별도 `reflections(date, scope, content)` 테이블
- **MCP 양방향 흐름** — 두 도구 호출로 쪼갤지 (제안 → 컨펌), 단일 호출 + 폴링으로 갈지
- **시간 zoom 집계 방식** — 일별 요약 N개를 그대로 보여줄지, 월/년에 별도 LLM reflect를 시킬지

---

## 10. 한 줄 결론

이전 자비스는 "AI가 알아서 잘 쌓는 똑똑한 메모리"를 추구했음. 새 자비스는 "사용자가 명시적으로 push하고 git처럼 신뢰하는 클라우드 저장소"임. 단순해지고, 본질에 집중하고, 사용자 본인이 1차 가치 소비자가 됨.

지금까지 만든 인프라는 거의 다 살아남으나, **의미가 재배치**됨 (자동 정합성 → 힌트, 트리플 → 보조 색인, 원문+의미검색 → 메인).
