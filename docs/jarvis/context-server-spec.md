# JARVIS Context Server Specification

> 상태: 기준 문서
> 작성일: 2026-03-26
> 목적: JARVIS의 최신 방향을 하나의 문서로 고정한다.
> 대상 독자: 본인, 팀원, 미래의 구현 에이전트, 캡스톤 평가자
> 성격: 제품 비전 문서 + 기술 명세 초안 + 범위 통제 문서

---

## 0. 왜 이 문서가 필요한가

Jarvis 프로젝트는 원래 "사용자 정의 Workflow 실행 엔진"으로 출발했다. 그 방향은 아이디어 차원에서는 흥미로웠지만, 실제로 풀고 싶은 문제의 중심을 완전히 대변하지는 못했다.

최근 정리된 핵심 인식은 다음과 같다.

1. 내가 진짜로 겪는 문제는 "할 일을 자동 실행하는 것"보다 "작업 맥락이 세션과 환경에 갇혀 사라지는 것"이다.
2. `memory/` 폴더에 수동으로 기록하는 방식은 보조 수단일 뿐, 일관된 기억 시스템이 아니다.
3. 데스크톱에서 작업하다가 노트북으로 옮기거나, Claude Code 세션 A에서 세션 B로 넘어가거나, 새 에이전트를 띄우는 순간 맥락이 끊긴다.
4. 내가 원하는 것은 AI 하나가 똑똑한 것이 아니라, 어떤 AI를 쓰든 공통으로 연결되는 "내 작업 맥락 서버"다.
5. 이 구조는 개인에게 유용할 뿐 아니라, 팀원에게 공유되는 순간 자연스럽게 SecondBrain의 출발점이 된다.

따라서 JARVIS의 최신 정의는 더 이상 "workflow 앱"이 아니다.

JARVIS는 **클라우드 기반 계정/워크스페이스형 컨텍스트 서버**이며, 다양한 AI 클라이언트가 공통으로 읽고 쓰는 **외부 기억 인프라**다.

---

## 1. 한 줄 정의

JARVIS는 **어떤 AI 클라이언트든 동일한 작업 맥락을 읽고 쓸 수 있게 해주는 클라우드 기반 컨텍스트 서버**다.

더 길게 말하면:

- 사용자는 계정을 만들고
- 자신만의 워크스페이스를 가지며
- CLI 또는 MCP를 통해 작업 환경을 연결하고
- 작업 중 발생한 결정, 이유, 진행 상태, 문서 링크, 코드 맥락, 할 일, 질의응답 흔적을 서버에 축적한다
- 새로운 세션, 새로운 기기, 새로운 에이전트, 심지어 다른 모델이 접속하더라도 같은 맥락을 질의하고 이어받을 수 있다

---

## 2. 핵심 문제 정의

### 2.1 세션 기억은 작업 기억이지 장기 기억이 아니다

Claude Code, GPT, Gemini, Cursor 같은 도구는 대화 중에는 똑똑해 보인다. 하지만 이들의 기본 메모리 구조는 세션 단위다.

문제는 다음과 같다.

- 이전 세션에서 내린 결정이 다음 세션에 자동으로 이어지지 않는다
- 한 시간 넘게 작업하면 앞서 합의한 규칙을 잊는다
- 새 에이전트를 띄우면 다시 설명해야 한다
- 장비를 바꾸면 작업 흐름이 단절된다

즉, 모델의 성능과 별개로 "공통 장기 맥락 저장소"가 없다.

### 2.2 수동 문서화는 항상 늦고 불완전하다

지금까지 많이 쓰는 대안은 다음과 같다.

- `memory/` 폴더에 직접 기록하기
- README나 회의록에 요약하기
- 커밋 메시지로 의미를 남기기
- 노션/문서에 따로 정리하기

이 방식들의 한계는 동일하다.

- 기록이 누락된다
- 기록 시점이 늦다
- 무엇이 중요한지 기준이 일관되지 않다
- 최신 진실과 과거 흔적이 섞인다
- 새 AI 세션이 자동으로 이해하지 못한다

### 2.3 팀 협업에서 가장 큰 비용은 설명 비용이다

혼자 진도를 많이 나가는 사람일수록 팀에 설명하는 비용이 커진다.

- 왜 이 구조로 바꿨는지
- 원래는 무엇이었는지
- 지금 진짜 해야 하는 일이 무엇인지
- 과거 논의 중 무엇이 폐기되었는지

이 설명 비용은 대부분 반복적이며, 사실상 구조화된 맥락 저장과 검색으로 줄일 수 있다.

### 2.4 내가 원하는 것은 "기억하는 앱"이 아니라 "기억 레이어"다

중요한 발상의 전환은 여기 있다.

내가 원하는 것은 새로운 챗봇이 아니다.

내가 원하는 것은:

- 내가 쓰는 어떤 AI 도구 위에도 붙을 수 있고
- 어떤 세션에서도 이어지고
- 어떤 기기에서도 동일하며
- 나중에 팀원도 연결 가능한
- 공통의 외부 기억 계층이다

이것이 JARVIS의 본질이다.

---

## 3. 제품 철학

### 3.1 모델보다 맥락이 먼저다

좋은 답변은 좋은 모델만으로 나오지 않는다. 올바른 맥락이 적절한 시점에 제공되어야 한다.

JARVIS는 모델 자체를 만들지 않는다. 대신 모델이 사용할 수 있는 맥락의 질을 관리한다.

### 3.2 대화 로그를 그대로 저장하는 것으로는 부족하다

원시 대화는 필요하지만 충분하지 않다.

필요한 것은 다음과 같은 구조화다.

- 어떤 결정을 했는가
- 왜 그렇게 결정했는가
- 무엇이 변경되었는가
- 그 변경은 어느 파일/문서/작업과 연결되는가
- 지금 시점의 최신 진실은 무엇인가

즉, 저장은 로그로 하더라도 검색과 회상은 구조화된 단위로 되어야 한다.

### 3.3 기억은 세션 소유가 아니라 워크스페이스 소유다

JARVIS는 세션별 메모장 시스템이 아니다. 세션은 기억을 생산하는 통로일 뿐이다.

기억의 진짜 소유 단위는 `workspace`다.

- 개인 사용: 멤버 1명인 workspace
- 팀 사용: 멤버 여러 명인 workspace

이렇게 정의해야 개인용과 협업용이 한 모델로 이어진다.

### 3.4 JARVIS는 에이전트가 아니라 인프라다

JARVIS 서버는 상주 챗봇일 필요가 없다.

서버의 역할은 다음이다.

- 저장
- 인덱싱
- 관계 추적
- 검색
- 근거 제공
- 권한 관리

즉 JARVIS는 "답변하는 AI"가 아니라 "답변에 필요한 맥락을 보존하고 공급하는 서버"다.

### 3.5 협업은 나중 기능이 아니라 자연 확장이다

처음에는 개인용으로 시작해도, 계정 시스템과 워크스페이스, contributor 모델이 있으면 팀 공유는 자연스럽게 확장된다.

따라서 협업을 별도의 전혀 다른 제품으로 보지 않는다.

- 개인의 컨텍스트 서버가 기본형
- 공유 가능한 컨텍스트 서버가 협업형

이 관점이 JARVIS와 SecondBrain의 관계를 가장 단순하게 만든다.

---

## 4. 최신 제품 정의

### 4.1 JARVIS

JARVIS는 다음의 조합이다.

- 클라우드 서버
- 사용자 계정 시스템
- 워크스페이스 및 contributor 관리
- 기억 저장 API
- 검색 및 회상 API
- CLI
- MCP 인터페이스

핵심은 "AI용 외부 기억 서버"다.

### 4.2 SecondBrain

SecondBrain은 JARVIS와 완전히 별개의 발명이 아니다.

SecondBrain은 다음을 더한 상위 제품층이다.

- 협업 UX 강화
- 웹 기반 운영 UI
- 팀 온보딩
- 역할/권한 관리 고도화
- 브리핑, 알림, 리뷰 플로우
- 더 넓은 도메인 확장

즉:

- JARVIS = 코어 엔진
- SecondBrain = 제품 경험 레이어

### 4.3 캡스톤에서의 JARVIS 포지셔닝

캡스톤에서는 JARVIS를 다음처럼 정의한다.

> "세션과 기기를 넘어 AI 코딩 맥락을 유지하는 클라우드 컨텍스트 서버"

이 정의의 장점:

- 문제 정의가 선명하다
- 데모가 쉽다
- 기술 깊이가 있다
- 나중에 협업 확장이 자연스럽다

---

## 5. 목표 사용자

### 5.1 1차 사용자: AI 코딩을 적극적으로 하는 개인 개발자

이 사용자는 다음 행동을 보인다.

- Claude Code, Cursor, GPT, Gemini 등을 병행 사용한다
- 프로젝트마다 맥락이 빠르게 변한다
- 세션이 길어질수록 설명 비용이 커진다
- 수동 메모로 버티지만 한계를 느낀다

### 5.2 2차 사용자: 혼자 앞서 나가는 팀 리드

이 사용자의 pain point:

- 본인은 맥락을 알고 있지만 팀원은 모른다
- 팀원에게 설명하는 시간이 너무 크다
- AI에게는 바로 시킬 수 있는데 사람에게는 브리핑이 필요하다

### 5.3 3차 사용자: 같은 워크스페이스를 공유받는 팀원

이 사용자는 다음 질문을 자주 한다.

- 뭐가 바뀌었지?
- 왜 이렇게 바뀌었지?
- 내가 지금 뭘 하면 되지?
- 이 결정은 어느 문맥에서 나왔지?

JARVIS는 이 질문에 "근거 기반"으로 답할 수 있어야 한다.

---

## 6. 대표 사용 시나리오

### 6.1 세션 전환

1. 데스크톱에서 Claude Code로 API 구조를 수정한다
2. 작업 중 "SQLite 대신 Postgres 사용"을 결정한다
3. 해당 결정과 이유가 JARVIS에 기록된다
4. 저녁에 노트북에서 새 세션을 연다
5. 새 에이전트가 JARVIS에 질의한다
6. "이 프로젝트는 왜 Postgres를 쓰나요?"에 근거와 함께 답변한다

### 6.2 에이전트 교체

1. 오전에는 GPT 기반 도구로 설계를 논의한다
2. 오후에는 Claude Code로 구현한다
3. 두 도구 모두 같은 JARVIS workspace를 사용한다
4. 구현 에이전트는 설계 결정을 이어받는다

### 6.3 작업 위임

1. 리드가 주말에 구조를 많이 바꾼다
2. 월요일에 팀원이 접속한다
3. 팀원은 "최근 변경사항 요약"을 JARVIS에서 받는다
4. 팀원은 "내가 맡아야 할 다음 작업"을 맥락 기반으로 파악한다

### 6.4 분산 환경 동기화

1. 로컬 CLI 환경에서 작업한다
2. 원격 VM이나 다른 PC에서도 작업한다
3. 환경만 다를 뿐 JARVIS 계정과 workspace는 동일하다
4. 기억은 세션이나 머신이 아니라 서버에 축적된다

---

## 7. 핵심 가치 제안

### 7.1 세션 불연속성 제거

JARVIS는 "세션이 바뀌면 설명을 다시 해야 하는 문제"를 줄인다.

### 7.2 맥락의 클라우드화

작업 맥락을 로컬 파일이 아니라 서버에 올림으로써, 장비와 환경 종속성을 줄인다.

### 7.3 모델 독립성

특정 LLM 하나에 종속되지 않는다.

- Claude도 쓸 수 있고
- GPT도 쓸 수 있고
- Gemini도 쓸 수 있고
- 미래의 다른 에이전트도 연결 가능하다

### 7.4 근거 기반 회상

단순히 "아마 이랬던 것 같아요"가 아니라,

- 언제
- 누가
- 어떤 맥락에서
- 어떤 근거로

라는 구조를 함께 제공해야 한다.

### 7.5 협업 확장성

처음에는 개인용이지만, contributor를 추가하는 순간 공유 맥락 서버가 된다.

---

## 8. 비목표

다음은 초기 JARVIS의 비목표다.

### 8.1 서버 내 상주 범용 에이전트

JARVIS 서버 자체가 계속 추론하고 대화하는 범용 비서를 만드는 것은 초기 목표가 아니다.

### 8.2 모든 일반 생활 기억 통합

영화 감상, 일기, 생활 로그까지 모두 품는 범용 life OS는 당장 목표가 아니다.

초기에는 "AI 코딩 맥락"에 집중한다.

### 8.3 완전한 실시간 협업 플랫폼

Google Docs 수준의 실시간 동시 편집은 범위가 아니다.

### 8.4 완전 자동 의사결정

JARVIS는 의사결정의 보조자이지, 프로젝트 전체를 자율 운영하는 시스템이 아니다.

### 8.5 IDE 대체

IDE를 대체하는 제품이 아니라, IDE와 에이전트 도구 위에 올라가는 기억 레이어다.

---

## 9. 시스템 개요

### 9.1 전체 구조

```text
[AI Client]
- Claude Code
- Cursor
- GPT
- Gemini
- 기타 MCP/CLI 클라이언트
        |
        | read/write/query
        v
[JARVIS API Layer]
- Auth
- Workspace
- Memory write
- Retrieval
- Evidence
- Contributor management
        |
        v
[Processing Layer]
- Normalization
- Embedding
- Relation extraction
- Index update
- Background jobs
        |
        v
[Storage Layer]
- PostgreSQL
- pgvector
- Full-text index
- raw event log
```

### 9.2 구조 해석

- AI는 클라이언트에 있다
- JARVIS는 클라우드 서버에 있다
- CLI와 MCP는 인터페이스다
- 핵심 로직은 서버가 가진다

즉 "클라이언트는 추론, 서버는 기억" 구조다.

---

## 10. 아키텍처 원칙

### 10.1 HTTP API가 코어이고 MCP는 어댑터다

아키텍처를 단순하게 유지하려면, 진짜 핵심은 MCP 자체가 아니라 서버 API여야 한다.

권장 구조:

- 코어: REST/HTTP API
- CLI: HTTP API를 호출하는 thin client
- MCP server: 같은 API를 도구 호출 형태로 노출하는 adapter

이렇게 해야:

- 특정 도구 표준에 종속되지 않고
- 테스트가 쉬우며
- 웹 UI 추가도 자연스럽다

### 10.2 서버는 agentless but not dumb

"서버에 LLM이 없다"는 말은 맞을 수 있다. 그러나 그것이 "서버는 단순 key-value 저장소"라는 뜻이면 안 된다.

서버는 최소한 다음의 지능적 후처리를 해야 한다.

- 입력 정규화
- 임베딩 생성
- 메모리 타입 분류
- 관계 생성
- 최신 상태 판별 보조
- retrieval ranking
- evidence bundling

### 10.3 raw log와 structured memory를 함께 보존한다

하나만 가지면 안 된다.

- raw log만 있으면 검색 품질이 떨어진다
- structured memory만 있으면 원본 근거가 사라진다

따라서 둘 다 있어야 한다.

### 10.4 최신 진실과 과거 흔적을 분리해 관리한다

예를 들어:

- 과거: "SQLite로 하자"
- 현재: "Postgres로 바꾸자"

둘 다 저장되어야 하지만, retrieval은 "현재 무엇이 진실인가"를 구분해서 제공해야 한다.

### 10.5 근거 없는 요약을 허용하지 않는다

AI 클라이언트가 retrieval 결과를 사용할 때는 가능한 한 다음을 함께 받아야 한다.

- 핵심 요약
- 연결된 memory item
- 원본 이벤트
- 관련 문서/파일
- 작성자와 시점

---

## 11. 데이터 모델의 핵심 개념

### 11.1 Workspace

기억의 최상위 소유 단위다.

필드 예시:

- `id`
- `name`
- `owner_user_id`
- `visibility`
- `created_at`
- `updated_at`

설명:

- 개인 workspace는 owner 혼자 쓰는 공간이다
- 팀 workspace는 contributor가 추가된 공간이다

### 11.2 User

플랫폼 계정 주체다.

필드 예시:

- `id`
- `email`
- `display_name`
- `handle`
- `created_at`

### 11.3 WorkspaceMember

workspace와 user의 연결 테이블이다.

필드 예시:

- `workspace_id`
- `user_id`
- `role`
- `joined_at`

`role` 예시:

- owner
- admin
- contributor
- viewer

### 11.4 Session

기억을 생산하는 연결 단위다.

필드 예시:

- `id`
- `workspace_id`
- `user_id`
- `client_type`
- `client_name`
- `machine_fingerprint`
- `started_at`
- `ended_at`

설명:

- Claude Code 세션
- Cursor 세션
- CLI 직접 사용 세션

모두 별도의 session으로 기록할 수 있다.

### 11.5 RawEvent

가장 원본에 가까운 입력 기록이다.

필드 예시:

- `id`
- `workspace_id`
- `session_id`
- `actor_user_id`
- `source_type`
- `source_ref`
- `content`
- `created_at`

`source_type` 예시:

- chat_message
- command_summary
- manual_note
- file_change_summary
- retrieval_query
- external_hook

### 11.6 MemoryItem

검색과 회상에 사용되는 구조화 기억 단위다.

필드 예시:

- `id`
- `workspace_id`
- `type`
- `title`
- `summary`
- `body`
- `status`
- `importance`
- `created_by`
- `created_at`
- `updated_at`
- `valid_from`
- `valid_to`

`type` 예시:

- decision
- rationale
- task
- progress
- constraint
- fact
- question
- answer
- reference

### 11.7 MemoryRelation

기억 간 연결이다.

필드 예시:

- `from_memory_id`
- `to_memory_id`
- `relation_type`
- `confidence`
- `created_at`

`relation_type` 예시:

- supports
- contradicts
- supersedes
- derived_from
- relates_to
- blocks
- implements
- references

### 11.8 EvidenceLink

구조화 기억이 어떤 원본에 근거하는지 연결한다.

필드 예시:

- `memory_id`
- `raw_event_id`
- `excerpt`
- `created_at`

### 11.9 ArtifactLink

기억을 실제 파일/문서/PR/커밋과 연결한다.

필드 예시:

- `memory_id`
- `artifact_type`
- `artifact_ref`

`artifact_type` 예시:

- file
- commit
- branch
- document
- url

---

## 12. 왜 `user_id 컬럼만 추가`로는 부족한가

겉보기에는 "기록마다 user_id만 붙이면 팀 협업도 되지 않나?"라는 생각이 들 수 있다. 하지만 실제로는 `workspace`가 반드시 필요하다.

이유는 다음과 같다.

1. 권한 경계가 필요하다
2. 검색 범위를 정의해야 한다
3. 같은 사용자가 여러 프로젝트를 가질 수 있다
4. 개인 기억과 팀 기억을 동일한 추상화로 다루려면 소유 컨테이너가 있어야 한다

즉 정답은:

- `user_id`만 있는 구조가 아니라
- `workspace_id + actor_user_id` 구조다

개인은 "멤버 한 명짜리 workspace"로 표현하면 된다.

---

## 13. 쓰기 파이프라인

### 13.1 입력 방식

초기 입력은 크게 세 가지다.

1. explicit write
2. conversational capture
3. derived write

#### explicit write

사용자가 명시적으로 기록을 남긴다.

예:

- "이 결정 저장해"
- "현재 구조를 메모해"
- "이유까지 남겨"

#### conversational capture

클라이언트가 작업 중 대화를 요약해 JARVIS에 보낸다.

예:

- 세션 종료 시 핵심 결정 요약 전송
- 일정 간격마다 change summary 전송

#### derived write

다른 기록을 바탕으로 후처리로 memory item이 생성된다.

예:

- raw event 여러 개에서 하나의 decision item 도출
- 진행 상황 로그에서 task 갱신

### 13.2 서버 처리 단계

1. 인증 확인
2. workspace 권한 확인
3. raw event 저장
4. 정규화
5. 임베딩 생성
6. memory item 생성 또는 업데이트
7. relation 추출
8. evidence 연결
9. 검색 인덱스 갱신

### 13.3 주의점

모든 구조화를 클라이언트에게 맡기면 안 된다.

이유:

- Claude는 Claude 식으로 쓰고
- GPT는 GPT 식으로 쓰고
- Gemini는 Gemini 식으로 써서
- 결국 저장 형식이 분열된다

그래서 서버는 최소한의 canonical schema를 강제해야 한다.

---

## 14. 검색 파이프라인

### 14.1 검색 목표

검색은 단순 문장 유사도 검색이 아니라 다음을 만족해야 한다.

- 현재 진실 우선
- 관련 결정과 이유 포함
- 원본 근거 제시
- 관련 파일/문서 연결

### 14.2 질의 유형

질의는 대략 네 종류다.

1. fact lookup
2. rationale lookup
3. status lookup
4. recall lookup

예시:

- fact lookup: "이 프로젝트 DB 뭐 쓰지?"
- rationale lookup: "왜 Postgres로 바꿨지?"
- status lookup: "지금 내가 해야 할 일 뭐지?"
- recall lookup: "저번에 auth 구조 어떻게 정리했더라?"

### 14.3 검색 단계

1. workspace 범위 결정
2. 질의 의도 분류
3. 키워드 검색
4. 벡터 검색
5. relation expansion
6. superseded/active 상태 반영
7. evidence 묶기
8. 응답 패키지 반환

### 14.4 반환 포맷

검색 응답은 단순 텍스트가 아니라 structured payload여야 한다.

예시:

- `answer_summary`
- `current_truth`
- `related_memories`
- `evidence`
- `related_artifacts`
- `confidence`

---

## 15. 시간 개념

장기 맥락 시스템에서는 시간 처리가 중요하다.

초기 버전에서 최소한 두 가지를 구분해야 한다.

- 기록된 시간: `created_at`
- 유효한 시간: `valid_from`, `valid_to`

예시:

- 3월 1일에 "SQLite 사용" 결정
- 3월 10일에 "Postgres로 변경"

이 경우:

- 과거 기억은 삭제하면 안 된다
- 하지만 retrieval은 최신 결정이 현재 truth임을 보여줘야 한다

후속 버전에서는 다음도 고려할 수 있다.

- event time
- mentioned time
- effective time

---

## 16. CLI와 MCP의 역할

### 16.1 CLI

CLI는 사람이 직접 쓰거나 스크립트에서 호출하기 쉬운 인터페이스다.

가능한 예:

- `jarvis login`
- `jarvis workspace use my-project`
- `jarvis note add`
- `jarvis decision record`
- `jarvis recall "왜 Postgres로 바꿨지?"`
- `jarvis sync session`

### 16.2 MCP

MCP는 AI 클라이언트가 JARVIS를 도구처럼 호출하는 인터페이스다.

예상 도구:

- `jarvis_write_memory`
- `jarvis_search_context`
- `jarvis_get_recent_changes`
- `jarvis_get_current_truth`
- `jarvis_list_tasks`

### 16.3 역할 구분

- CLI는 인간 친화적
- MCP는 에이전트 친화적
- 둘 다 같은 서버 API를 사용해야 한다

---

## 17. 서버에 LLM이 꼭 없어야 하는가

현재 방향은 "상주 LLM 없는 데이터 레이어"가 맞다. 다만 이 문장은 조금 더 정밀하게 이해해야 한다.

### 17.1 없는 것이 좋은 것

- 서버가 직접 대화형 추론을 수행할 필요는 없다
- 특정 상용 모델에 종속되지 않는 것이 좋다
- 운영비를 낮출 수 있다

### 17.2 그래도 남는 지능적 처리

완전히 LLM-free여야 한다는 강박은 필요 없다.

초기에는 다음처럼 단계적으로 접근할 수 있다.

#### Stage A

- 서버는 임베딩만 수행
- 구조화 책임은 클라이언트 요약에 많이 의존

#### Stage B

- 서버는 규칙 기반 정규화 + 임베딩 수행
- 일부 relation extraction 추가

#### Stage C

- 필요 시 아주 작은 분류기 또는 소형 모델을 선택적으로 사용
- 예: type classification, contradiction hint

즉 "서버는 챗봇이 아니다"가 본질이지, "서버는 절대 어떤 모델도 쓰면 안 된다"가 본질은 아니다.

---

## 18. 협업 확장 방식

### 18.1 개인에서 팀으로의 확장

개인용이 기본이다.

1. 사용자가 계정 생성
2. 개인 workspace 생성
3. CLI/MCP 연결
4. 혼자 사용

이후:

5. contributor 초대
6. 팀원이 같은 workspace에 접속
7. 팀 단위 맥락 공유

즉 협업은 별도 대수술이 아니라 membership 확장이다.

### 18.2 팀원이 얻는 가치

- 변경 브리핑
- 현재 진실 질의
- 결정 이유 확인
- 작업 상태 파악

### 18.3 향후 필요한 협업 기능

후속 버전에서 고려:

- role 기반 visibility
- review queue
- unresolved question 목록
- per-user digest
- conflict notification

---

## 19. UI에 대한 현재 입장

초기 JARVIS의 코어는 UI가 아니라 API/CLI/MCP다.

하지만 장기적으로는 웹 UI가 필요하다.

초기 웹 UI는 최소한 다음만 있으면 된다.

- 로그인
- workspace 선택
- 최근 변경 목록
- 검색/회상 화면
- memory item 상세
- contributor 관리

SecondBrain 레이어에서는 여기에 다음이 붙는다.

- 브리핑 대시보드
- 역할별 뷰
- 프로젝트 보드
- richer review UX

---

## 20. 캡스톤 구현 범위

캡스톤에서 다 하려 하면 망한다. 따라서 "무엇을 안 할지"까지 포함해 범위를 고정해야 한다.

### 20.1 캡스톤 필수 구현

1. 계정 시스템
2. workspace 생성
3. contributor 추가
4. memory write API
5. retrieval API
6. CLI
7. MCP adapter
8. 근거 포함 검색

### 20.2 캡스톤 데모 시나리오

권장 데모:

1. 데스크톱에서 세션 A로 설계 결정 기록
2. 노트북에서 세션 B 시작
3. 새 에이전트가 JARVIS에서 컨텍스트 조회
4. "왜 이렇게 바뀌었지?" 질문에 근거 기반 답변
5. contributor로 팀원 계정 추가
6. 팀원도 같은 workspace 맥락 조회

이 데모는 매우 강하다. 문제 정의와 해결이 한 번에 보이기 때문이다.

### 20.3 캡스톤에서 미룰 것

- 완전한 생활 기억 플랫폼
- 음성 캡처
- 광범위 웹 모니터링
- 자동 태스크 분배
- 복잡한 실시간 UI

---

## 21. 성공 기준

### 21.1 제품 성공 기준

다음이 되면 성공이다.

1. 서로 다른 세션에서 동일 workspace 맥락이 이어진다
2. 서로 다른 기기에서도 동일한 기억을 조회할 수 있다
3. 다른 AI 클라이언트가 같은 맥락을 읽고 활용할 수 있다
4. 결정 이유를 근거와 함께 회상할 수 있다
5. contributor 추가만으로 공유 맥락이 형성된다

### 21.2 캡스톤 평가 기준으로의 성공

- 문제 정의가 명확하다
- 구현 범위가 집중되어 있다
- 기술적 깊이가 있다
- 데모가 직관적이다
- 확장 가능성이 보인다

---

## 22. 리스크와 대응

### 22.1 가장 큰 리스크: 너무 많은 걸 하고 싶어지는 것

대응:

- 초기 타깃을 "AI 코딩 맥락"으로 고정
- 문서/일상/음성은 후순위

### 22.2 쓰기 품질 불균일

여러 모델이 제각기 다른 형식으로 메모리를 쓰는 문제가 생길 수 있다.

대응:

- canonical API schema
- 서버 측 정규화
- required fields 최소 강제

### 22.3 검색 정확도 문제

관련 기억이 있어도 retrieval이 현재 truth를 잘 못 고를 수 있다.

대응:

- status/validity 필드 설계
- relation 모델 설계
- evidence 중심 응답

### 22.4 팀 공유 시 정보 오염

여러 사람이 쓰기 시작하면 잡음이 늘어난다.

대응:

- actor 구분
- role 관리
- source metadata 저장

---

## 23. 이전 방향과의 관계

기존 workflow 엔진 문서는 완전히 쓸모없어진 것이 아니다. 다만 중심 문제를 설명하는 문서로는 더 이상 맞지 않는다.

이전 방향이 남긴 자산:

- FastAPI/React/Postgres 스택
- 비정형 입력 처리 관심
- 검색과 벡터 DB에 대한 문제의식

하지만 현재 기준에서는 다음이 바뀌었다.

- 핵심 주제: workflow 실행 -> context persistence
- 제품 정체성: 개인 비서 앱 -> 클라우드 맥락 서버
- 확장 경로: 단독 앱 -> workspace 기반 협업 확장

---

## 24. 앞으로의 문서 구조 제안

현재 이 문서는 기준 문서다. 이후 세부 설계를 분리할 수 있다.

권장 후속 문서:

1. API spec
2. data model spec
3. CLI command spec
4. MCP tool spec
5. retrieval design
6. auth and workspace model
7. capstone demo script

---

## 25. 최종 요약

JARVIS는 더 이상 "무엇을 자동 실행하는 개인 비서 앱"이 아니다.

JARVIS는:

- 세션을 넘고
- 기기를 넘고
- 클라이언트를 넘고
- 나중에는 사람까지 넘어서

동일한 작업 맥락을 유지시키는 **클라우드 기반 컨텍스트 서버**다.

개인 사용은 시작점일 뿐이고, contributor가 붙는 순간 팀의 공유 맥락 서버가 된다.

따라서 JARVIS와 SecondBrain은 서로 완전히 다른 아이템이 아니라,

- JARVIS가 코어 엔진이고
- SecondBrain이 그 위의 협업 제품 경험

이라고 이해하는 것이 가장 자연스럽다.

캡스톤에서는 이 중에서도 가장 강한 코어만 남긴다.

> "AI 코딩 시대에, 어떤 세션과 어떤 에이전트에서도 이어지는 클라우드 맥락 서버"

이 한 문장이 지금 JARVIS의 정체성이다.
