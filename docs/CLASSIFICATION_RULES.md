# 주제 분류 룰 (2026-05-12)

> 청크 단위 자동 분류 시 적용되는 규칙. AI가 사용자 컨펌 없이 자동 수용.

## 1. Subject 정의

- **최상위 주제 (top-level)**: 사용자가 능동적으로 다루는 프로젝트, 사람, 개념, 도메인
  - 예: JARVIS, Argos, fundmessenger, SecondBrain, 캡스톤, 예창패, 자료구조
- **하위 주제 (sub)**: 최상위 주제의 의미 있는 컴포넌트나 측면
  - 예: `JARVIS > 인증`, `JARVIS > UI`, `fundmessenger > backend`

## 2. Chunk 정의

- **단위**: 한 episode 내 의미적으로 연속된 turn 묶음
- **크기**: 보통 10~50 turns. 토픽 전환되면 새 청크
- **판단**: AI가 episode 첫/끝 + 중간 샘플 보고 결정
  - 단일 토픽 episode → 전체 1 청크
  - 다중 토픽 episode → 토픽별 청크

## 3. 기존 vs 신규 — 동일성 판단

### 통합해야 함 (같은 주제)
- **언어 변형**: 펀드메신저 / fundmessenger / 펀드메 / 펀드메시지
- **대소문자/공백**: JARVIS / Jarvis / jarvis, SecondBrain / Second Brain / second-brain
- **약어/풀네임**: argos-crypto / Argos, jvs / JARVIS

### 분리해야 함 (다른 주제)
- **명백히 다른 프로젝트** — 디렉토리 이름이 우연히 같아도 (예: 두 프로젝트 모두 `backend` 하위 폴더)
- **컴포넌트는 하위로** — 부모 프로젝트가 있으면 `parent_id` 설정, 단독 top-level X

### 일반어 처리
- **단독 일반어 금지**: `backend`, `frontend`, `src`, `lib`, `tests` 자체로 top-level 만들지 말 것
- 항상 `<프로젝트> > <컴포넌트>` 형태로
- 부모 프로젝트가 불분명하면 episode 내용으로 추정

## 4. 다중 주제 (M:N) 규칙

- **여러 subject에 link 가능** (한 turn/chunk가 여러 주제 다루면)
- **단순 언급 금지**: "Argos는 트레이딩 봇이야" 같은 비교 언급만으로는 link X
- **실질 논의 기준**: 그 주제에 관한 결정/계획/분석/질문이 있으면 link O

## 5. 새 subject 생성 기준

- 기존 목록에 매칭되는 것이 없거나, 명확히 별개의 주제일 때만 생성
- **하위 subject 생성 적극 권장**: 부모가 있는 게 자연스러우면 명시
- 너무 일반적이거나 단발적인 이름 피함 ("질문", "토론", "작업" 같은 추상어)

## 6. 필터링 — 이건 분류 안 함

- **순수 도구 출력만**: bash 명령 결과, 파일 내용 dump, 에러 로그만 있는 turn → 무시
- **메타 대화**: "안녕", "감사", "잠시만" 같은 의례 turn → 어느 주제에도 link X
- **계기 없는 짧은 turn**: 1-2단어, 답변만 등 분류 단서 없으면 skip

## 7. 한국어/영어 혼합

- 사용자 프로젝트 자료의 70%+ 한국어, 코드/식별자 영어
- 분류 시 두 언어 모두 매칭 (자비스 = JARVIS, 펀드메신저 = fundmessenger)
- 새 subject 이름은 **원본의 표기 유지** (사용자가 자주 쓰는 형태)

## 8. 예시

| 케이스 | 분류 |
|---|---|
| `f:/fundmessenger/backend` 에서 API 설계 | `fundmessenger`, `fundmessenger > backend` |
| `f:/brain` 에서 JARVIS 설계 토론 | `JARVIS` (cwd가 brain이지만 내용이 JARVIS) |
| `f:/brain/jarvis` 에서 JARVIS UI 작업 | `JARVIS`, `JARVIS > UI` |
| Argos와 SecondBrain 비교 후 SecondBrain 선택 | `SecondBrain`, `Argos` (둘 다 실질 논의) |
| 캡스톤 발표 자료 작성하면서 JARVIS 언급 | `캡스톤`, `JARVIS` |
| `git status` 결과만 있는 turn | 분류 안 함 |

## 9. 적용 우선순위

1. 기존 subject 매칭 (canonical 이름 + alias)
2. 부모 프로젝트 식별
3. 하위 컴포넌트 생성 여부 결정
4. 단순 언급 vs 실질 논의 구분
5. 다중 subject link
