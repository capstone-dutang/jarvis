# 자비스 비전 확정 — LLM이 일기를 쓰는 클라우드

> 작성일: 2026-05-12
> 성격: 시뮬레이션 + 사용자 직관 대화로 도출된 자비스 진짜 정체성. 비전 재정의(2026-05-07)의 후속 정련.
> 상태: 활성

---

## 1. 핵심 정체성 한 줄

> **자비스 = AI(사용자의 코딩 에이전트)가 사용자 대화의 일기를 클라우드에 쓰는 시스템.**

기존 비전(2026-05-07)의 "git for AI conversations"와 동일하지만, 시뮬레이션을 거쳐 더 명확해진 점:
- **AI가 일기 작성자** (단순 백업이 아니라 정제 + 요약 + 키워드 + 분류까지)
- **클라우드가 강점** (raw도 정제도 색인도 다 자비스 안. 디스크 의존 X)
- **노이즈 제거 ≠ 압축**. 의미는 100% 보존. 일기는 짧은 게 아니라 깔끔한 것.

---

## 2. 스코프 결정 (2026-05-12)

### 1차 스코프: 로컬 기록이 남는 코딩 에이전트
- Claude Code, Codex 등 jsonl 형태 + timestamp 정확한 클라이언트
- 각 message에 ISO 8601 ms-precision timestamp ⇒ 시간 grouping 자동
- tool_use / tool_result 등 풍부한 메타데이터

### 후순위: 클라우드 채팅 (claude.ai 웹 등)
- timestamp 없음 (대화 단위 날짜만)
- 사용자가 복붙 + 시간 정확도 양보 시 가능
- 1차 검증 끝난 후 확장

---

## 3. 시뮬레이션 도중 도출된 결정사항

### 3.1 정제 = AI 직접 (subprocess 우회 X)
- 시뮬레이션 시도: `claude -p` subprocess
- 발견: 긴 prompt + 복잡한 JSON schema에서 schema drift, JSON wrapper 깨짐 등 매우 불안정
- 진짜 흐름: **사용자가 자기 AI에게 "이거 자비스에 올려"라고 명령** → 그 AI(현재 대화의 Claude)가 자기 도구로 처리 (Read + Bash + API call)
- subprocess 한계는 시뮬레이션 한정, 실사용 인입 흐름에는 없음

### 3.2 휴리스틱 메타 필터 폐기
- 시도: "user text < 200 char" 같은 size 기준 → 사용자 짧은 메시지("도커 먼저 해줘")도 메타로 잘못 분류
- 결론: 휴리스틱은 false positive/negative 불가피
- 대신: **AI 판단**으로 노이즈 인식 (thinking 블록, 명시적 시스템 자동 prompt, 의례 transition)

### 3.3 청크/큐 패턴 — 입력 크기 대응
- 100+ turn episode를 한 번에 모델 처리 시도 → schema 무너짐
- 결론: 큰 입력은 청크 또는 turn 단위로 순차 처리
- 다만 진짜 실사용에서는 AI가 자기 대화를 in-context로 가지고 있어 청크 불필요. 시뮬레이션 한정 문제.

### 3.4 raw도 클라우드에 — 디스크 의존 X
- 초기 설계: cleaned turns만 자비스, raw는 사용자 디스크 jsonl
- 사용자 정정: "자비스 강점은 클라우드. 다 자비스에 있어야"
- 채택: `episodes.content` = raw 원문 통째 / `turns` 테이블 = cleaned
- 깊이 회상 시 raw 회수 가능

### 3.5 노이즈 정의 (보존적 정제)
**제외**: thinking 블록 / 명시적 시스템 자동 prompt ("Your task is to create a detailed summary...") / 단순 transition turn ("읽어볼게요" 류)
**압축**: tool_use → 한 줄 (`[Bash] command=...`), tool_result → 처음 600자 + 잘림 표시 (raw에서 회수 가능)
**보존**: 사용자 모든 입력, AI 결정/설명/보고 전체

실측: 101 raw turns → 93 cleaned (8 turn만 제거). 14배 압축은 너무 공격적이었음.

### 3.6 ingest 한 번에 4종 — AI가 일기 쓸 때 같이
한 호출에 같이:
- `turns` (cleaned, UI/회상 본문)
- `raw_content` (클라우드 백업)
- `summary` (한 문단, 회상 색인)
- `keywords` (5-10개, 검색 색인)

별도 단계 X. AI가 일기 쓸 때 위 4개 다 작성.

### 3.7 색인 = 자동(시간) + 별도(AI 판단)
**자동** (turns.timestamp 활용):
- 일/주/월/년 grouping (query GROUP BY)
- 키워드 검색 (metadata.keywords FTS)

**별도 AI 판단**:
- 주제(subject) 분류 — `classify-turns` API
- 일별/주별/월별 요약 (`daily_subject_summaries`) — 사용자 "오늘 정리해" 명령 시

---

## 4. 작동 검증된 흐름 (2026-05-12 실측)

### 입력 (예시)
- `~/.claude/projects/F--brain/6414940f-f2dc-4014-89c8-374297f2688e.jsonl`
- 101 turns (시스템 prompt + thinking + tool_use/result 다수)

### 처리
1. **parse**: jsonl → message list
2. **clean** (`cleanup_preserve.py`):
   - thinking 제외
   - 시스템 자동 prompt 제외
   - tool_use → `[Bash] command=...` 한 줄
   - tool_result → 600자 + 잘림 표시
   - 의례 transition 제외
3. **AI(나)가 summary + keywords 작성** (cleaned turns 보고)
4. **POST /ingest-transcript**: workspace_id + cleaned turns + raw_content + summary + keywords + metadata

### 결과
- `episodes.content` = 535,669 chars (raw 그대로)
- `turns` 테이블 = 93 rows (cleaned)
- `episodes.metadata.summary` = "사용자가 자비스 MCP 사용 가능 여부 확인 → 4개 도구 안내. 이어 자비스 프로젝트 맥락을..."
- `episodes.metadata.keywords` = ["JARVIS", "MCP", "4개 도구", "JARVIS_DEFINITIVE.md", "store.py", ...]
- 각 turn에 ms-precision timestamp → 시간 grouping 자동

### UI 회상 시
- 사이드바: 5월 → 2주차 → 12일 (timestamp 기반 자동)
- 메인: cleaned turns 줄글 (UI 표시)
- 우측: summary
- 검색: keywords / search_passages
- 깊이 회상: episode.content (raw 원본) 회수

---

## 5. 발견된 안티패턴 (이후 안 함)

| 안티패턴 | 이유 |
|---|---|
| 사이즈 휴리스틱 (200자 미만 = 메타) | 짧은 정상 요청까지 메타로 분류 |
| claude -p subprocess + long prompt + schema | schema drift, JSON wrapper 깨짐 |
| 14배 압축 (의미 일부 손실) | 일기 = 깔끔이지 짧지 X. 회상 시 발췌 불가 |
| raw를 사용자 디스크에 둠 | 자비스 클라우드 강점 위반 |
| ingest와 분류를 별도로 (정제 → 나중에 keywords) | AI가 한 번에 쓸 때 같이 작성하는 게 자연스러움 |

---

## 6. 남은 일

### 가까운 (이번 시뮬레이션 안에서 가능)
1. **이번 episode 분류** — JARVIS top-level + 자비스 맥락 파악 child 같은 subjects 매핑. classify-turns API
2. **다른 episode 4-9개 더 정제 ingest** — 시간순/cwd 다양하게. 자동 흐름 검증
3. **하루 요약 reflect** — 같은 날 episode 모이면 일별 요약 생성

### 중기 (별도 작업)
4. UI에서 정제된 일기 보이는지 확인 + 조정 (지금은 후순위)
5. MCP 도구로 진짜 실사용 검증 (세션 재시작 필요)
6. ingest 워크플로우를 사용자 AI client에 명시 (`store_memory` MCP 설명 갱신)

### 장기 (다음 비전 사이클)
7. 클라우드 채팅 (claude.ai 웹 등) 지원 — timestamp 부재 대응
8. 다른 AI provider (GPT/Gemini 등) 형식 통합
9. 일/주/월/년 색인 자동 reflect 워크플로우 자동화

---

## 7. 결론

자비스의 정체성은 이제 명확하다:

- **AI가 일기 작성자** (정제 + 요약 + 키워드 + 분류 모두 AI)
- **클라우드 = 완전 백업 + 색인** (raw도 자비스 안)
- **로컬 코딩 에이전트 우선** (timestamp 정확도 + 풍부한 메타)
- **휴리스틱/subprocess 없음** (사용자 AI 자체가 처리)
- **노이즈 제거지 압축 X** (의미 100% 보존)

오늘 한 episode로 흐름 작동 검증됨. 다음은 흐름 확장 (다른 episode + 분류 + 요약) 후 UI.
