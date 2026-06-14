# JARVIS 캡스톤 최종보고서

## 1. 프로젝트 수행 목적

### 1.1. 프로젝트 정의

JARVIS는 AI 클라이언트가 사용자와 나눈 대화의 핵심 맥락을 클라우드에 저장하고, 이후 다른 세션이나 다른 AI 클라이언트가 MCP(Model Context Protocol) 도구를 통해 과거 맥락을 회상할 수 있도록 하는 AI 장기기억 시스템이다.

본 프로젝트에서 JARVIS는 단순한 메모장이나 대화 로그 저장소가 아니라, AI가 직접 사용할 수 있는 기억 서버로 정의된다. 사용자가 GPT, Claude, Codex 등 여러 AI 도구를 바꾸어 사용하더라도, 이전 세션에서 논의한 프로젝트 목표, 기술 선택 이유, 오류 해결 과정, 다음 작업 계획을 다시 불러와 작업을 이어갈 수 있도록 하는 것이 핵심이다.

JARVIS의 기본 동작 흐름은 다음과 같다.

1. 사용자가 AI와 프로젝트 관련 대화를 진행한다.
2. AI가 대화의 핵심 내용을 일기, 요약, 엔티티, 사실, 관계 형태로 정리한다.
3. AI가 MCP 도구를 통해 JARVIS 서버에 해당 내용을 저장한다.
4. 이후 다른 세션에서 사용자가 과거 맥락을 질문하면 AI가 JARVIS MCP 도구를 호출한다.
5. JARVIS는 저장된 기억을 검색하여 관련 사실, 근거, 대화 맥락을 반환한다.
6. AI는 회상된 내용을 바탕으로 이전 작업을 이어서 수행한다.

따라서 JARVIS는 “AI가 나와 함께 작업한 기록을 기억하고, 다음 세션에서 이어서 도와주는 시스템”이라고 요약할 수 있다.

### 1.2. 프로젝트 배경

최근 생성형 AI는 개발, 문서 작성, 기획, 자료 조사 등 다양한 작업에서 활용되고 있다. 그러나 현재 대부분의 AI 도구는 세션 단위로 맥락이 제한된다. 한 세션 안에서는 이전 대화를 기억하고 자연스럽게 답변할 수 있지만, 세션이 종료되거나 다른 AI 클라이언트로 이동하면 기존 대화 맥락이 유지되지 않는다.

이로 인해 장기간 프로젝트를 진행할 때 다음과 같은 문제가 발생한다.

- 이전 세션에서 결정한 기술 선택 이유를 다시 설명해야 한다.
- 해결했던 오류나 실패한 시도를 다른 세션에서 반복하게 된다.
- 프로젝트의 현재 상태와 다음 작업을 매번 새로 정리해야 한다.
- GPT, Claude, Codex 등 여러 AI 도구 사이에서 맥락이 공유되지 않는다.
- 팀 프로젝트에서 신규 참여자가 과거 의사결정 흐름을 파악하기 어렵다.

단순히 대화 로그를 파일로 저장하는 방식도 한계가 있다. 긴 로그를 사람이 직접 읽는 것은 비효율적이고, 전체 로그를 AI에게 다시 입력하는 것은 토큰 비용과 컨텍스트 길이 측면에서 부담이 크다. 또한 단순 로그는 “무엇을 결정했는가”, “왜 그렇게 결정했는가”, “어떤 기술과 연결되는가”를 바로 검색하기 어렵다.

JARVIS는 이러한 문제를 해결하기 위해 시작되었다. 대화 중인 AI가 이미 현재 맥락을 알고 있다는 점에 착안하여, AI가 스스로 대화 내용을 구조화하고 서버는 이를 저장, 검증, 색인, 검색하는 역할을 수행하도록 설계하였다. 이를 통해 서버에서 별도 LLM을 계속 호출하지 않아도 AI 장기기억 시스템을 구현할 수 있다.

### 1.3. 프로젝트 목표

본 프로젝트의 최종 목표는 MCP 기반 AI 장기기억 서버의 MVP를 구현하고, 실제 AI 세션 간 맥락 회상이 가능한 구조를 완성하는 것이다.

세부 목표는 다음과 같다.

1. AI 대화 내용을 워크스페이스 단위로 저장하는 서버를 구현한다.
2. 저장된 대화를 단순 텍스트가 아니라 episode, turn, entity, fact, relation, fragment로 구조화한다.
3. MCP 도구를 통해 AI 클라이언트가 기억을 저장하고 회상할 수 있도록 한다.
4. PostgreSQL, pgvector, PGroonga를 활용하여 의미 검색, 전문 검색, 그래프 탐색을 결합한 하이브리드 검색 구조를 구현한다.
5. 날짜별 일기와 주요 주제를 확인할 수 있는 웹 UI를 구현한다.
6. Docker Compose 기반 실행 환경을 제공하여 로컬에서 쉽게 실행할 수 있도록 한다.
7. GitHub Release를 통해 최종 제출 시점의 소스코드와 보고서 산출물을 고정한다.

본 프로젝트는 상용 서비스 전체를 완성하는 것을 목표로 하지 않는다. 캡스톤 범위에서는 “대화 저장 - 기억 검색 - 다른 세션에서 맥락 회상”이라는 핵심 흐름이 실제로 동작함을 보여주는 것을 목표로 한다.

## 2. 프로젝트 결과물의 개요

### 2.1. 프로젝트 구조

JARVIS는 AI 클라이언트, MCP 서버, FastAPI 백엔드, PostgreSQL 데이터베이스, 웹 UI로 구성된다.

전체 구조는 다음과 같다.

```text
AI Client
(GPT, Claude, Codex 등)
        |
        | MCP Tool Call
        v
JARVIS MCP Endpoint
        |
        v
FastAPI Backend
        |
        v
PostgreSQL Database
(pgvector + PGroonga + relational tables)
        |
        v
Web UI
(일기, 검색, 주제, 엔티티 확인)
```

각 구성 요소의 역할은 다음과 같다.

| 구성 요소 | 역할 |
| --- | --- |
| AI Client | 사용자와 대화하고, 필요한 경우 MCP 도구를 호출하여 기억을 저장하거나 회상한다. |
| MCP Endpoint | AI 클라이언트가 호출하는 도구 인터페이스를 제공한다. |
| FastAPI Backend | REST API, MCP 도구 처리, 저장, 검색, 브리핑 로직을 담당한다. |
| PostgreSQL | episode, turn, entity, fact, relation, fragment 등 핵심 데이터를 저장한다. |
| pgvector | 의미 기반 벡터 검색을 담당한다. |
| PGroonga | 한국어 및 키워드 전문 검색을 담당한다. |
| Web UI | 저장된 일기, 최근 작업, 주요 주제, 검색 결과를 사람이 확인할 수 있게 한다. |

프로젝트의 주요 디렉터리 구조는 다음과 같다.

```text
jarvis/
  src/jarvis/
    main.py                 FastAPI 앱 진입점
    mcp_adapter.py          MCP 도구 정의
    api/v1/                 REST API 라우터
    core/                   저장, 회상, 검색, 브리핑 핵심 로직
    models/                 SQLAlchemy 테이블 모델
    web/index.html          단일 파일 웹 UI
  alembic/                  DB 마이그레이션
  docs/
    JARVIS_DEFINITIVE.md    설계 기준 문서
    research/               기술 조사 및 연구 노트
    capstone/               주간보고서 및 최종보고서
  docker-compose.yml        로컬 실행 환경
  Dockerfile                서버 실행 이미지
  Dockerfile.db             PostgreSQL 확장 포함 DB 이미지
  README.md                 실행 방법 및 프로젝트 설명
```

JARVIS의 핵심 데이터 모델은 다음과 같다.

| 데이터 모델 | 설명 |
| --- | --- |
| Workspace | 프로젝트 단위의 기억 공간이다. 서로 다른 프로젝트의 기억이 섞이지 않도록 분리한다. |
| Episode | 하나의 대화 또는 하루 작업 단위의 기록이다. summary, diary_entry, human_summary 등을 포함한다. |
| Turn | 사용자와 AI의 발화 단위이다. 대화 흐름 복원에 사용된다. |
| Entity | 프로젝트, 기술, 사람, 기능, 개념 등 기억의 중심이 되는 대상이다. |
| KnowledgeFact | 특정 entity에 대한 구조화된 사실이다. predicate와 object_value로 표현된다. |
| EntityRelation | entity 간 관계를 나타낸다. 그래프 탐색에 사용된다. |
| Fragment | 의미 검색을 위한 자연어 조각이다. 구조화된 fact만으로 부족한 대화 맥락을 보완한다. |

### 2.3. 프로젝트 결과물

본 프로젝트의 최종 결과물은 MCP 기반 AI 장기기억 서버, 하이브리드 검색 파이프라인, 웹 UI, 실행 환경, 제출 문서로 구성된다.

#### 2.3.1. MCP 기반 기억 저장 및 회상 도구

JARVIS는 AI 클라이언트가 직접 호출할 수 있는 MCP 도구를 제공한다. 주요 도구는 다음과 같다.

| MCP 도구 | 기능 |
| --- | --- |
| `jarvis_initialize_memory` | 세션 시작 시 워크스페이스의 최근 맥락과 사용 가능한 명령을 불러온다. |
| `jarvis_log_diary` | 현재 대화를 일기 형태로 저장한다. |
| `jarvis_recall_memory` | 자연어 질문을 기반으로 과거 기억을 검색한다. |
| `jarvis_brief_me` | 현재 워크스페이스 또는 전체 워크스페이스의 최근 상태를 브리핑한다. |
| `jarvis_explore_topic` | 특정 주제 주변의 엔티티와 관계를 탐색한다. |
| `jarvis_search_passages` | 구조화된 fact만으로 부족할 때 대화 passage를 검색한다. |
| `jarvis_get_episode_excerpt` | 특정 episode 안에서 질문과 관련된 부분을 발췌한다. |
| `jarvis_follow_relation` | 특정 entity와 연결된 주변 entity를 따라간다. |

이 도구들을 통해 AI는 현재 대화 내용을 저장하고, 이후 다른 세션에서 과거 맥락을 다시 회상할 수 있다.

#### 2.3.2. 일기 기반 기억 저장 모델

JARVIS는 raw transcript를 그대로 업로드하는 방식이 아니라, AI가 현재 컨텍스트를 바탕으로 대화 내용을 재구성하여 저장하는 방식을 사용한다. 저장되는 내용은 사용자 발화, assistant 요약, 전체 summary, diary_entry, human_summary, keywords, entities, facts, relations 등이다.

이 방식의 장점은 다음과 같다.

- 긴 원문 로그 전체를 저장하지 않아도 핵심 맥락을 보존할 수 있다.
- 로컬 transcript 파일 접근이 없는 AI 환경에서도 사용할 수 있다.
- 사람이 읽기 쉬운 일기와 AI가 검색하기 쉬운 구조화 데이터를 함께 저장할 수 있다.
- 향후 다른 AI 클라이언트에서도 동일한 방식으로 확장할 수 있다.

#### 2.3.3. 하이브리드 검색 구조

JARVIS는 단일 검색 방식에 의존하지 않고 세 가지 검색 방식을 결합한다.

| 검색 방식 | 역할 |
| --- | --- |
| pgvector 의미 검색 | 표현이 달라도 의미가 비슷한 과거 대화와 fact를 찾는다. |
| PGroonga 전문 검색 | 기술명, 프로젝트명, 키워드처럼 정확한 단어가 중요한 경우를 찾는다. |
| 그래프 탐색 | 특정 entity와 연결된 주변 개념과 관계를 따라가며 맥락을 확장한다. |

이를 통해 “PostgreSQL을 왜 선택했는가”, “MCP에서 어떤 문제가 있었는가”, “지난번 오류 해결 방법이 무엇이었는가” 같은 질문에 대해 단순 키워드 검색보다 풍부한 회상 결과를 제공할 수 있다.

#### 2.3.4. 웹 UI

웹 UI는 JARVIS에 저장된 기억을 사람이 확인하기 위한 화면이다. 주요 기능은 다음과 같다.

- 날짜별 episode 확인
- 최근 작업 브리핑 확인
- 주요 주제 확인
- 검색 결과 탐색
- entity 정보 확인
- 저장된 일기와 요약 확인

캡스톤 MVP에서는 복잡한 프론트엔드 프레임워크 대신 단일 파일 SPA 구조를 사용하였다. 이를 통해 배포와 유지보수를 단순화하고, 저장된 기억이 실제로 존재한다는 점을 빠르게 확인할 수 있도록 하였다.

#### 2.3.5. 실행 및 제출 결과물

최종 제출물에는 다음 항목이 포함된다.

- JARVIS MCP 서버 소스코드
- FastAPI REST API 및 MCP endpoint
- PostgreSQL 기반 데이터 모델 및 마이그레이션
- Docker Compose 실행 환경
- 웹 UI
- README 및 연구 문서
- 2주차부터 13주차까지의 주간보고서 Markdown 파일
- 최종보고서 Markdown 파일
- GitHub Release 제출 버전

제출용 GitHub Release 주소는 다음과 같다.

https://github.com/capstone-dutang/jarvis/releases/tag/v1.0.2-capstone

## 3. 프로젝트 수행 추진 체계 및 일정

### 3.1. 각 조원의 조직도

프로젝트는 3인 팀으로 진행되었으며, 전체 구조는 다음과 같다.

```text
JARVIS 캡스톤 팀
|
|-- 정진환: 프로젝트 관리 및 백엔드/API 구현
|-- 이학현: MCP, 검색 파이프라인, 데이터 모델 구현
|-- 전성빈: 문서화, UI, 테스트 및 발표자료 정리
```

각 조원은 담당 영역을 나누되, 주요 설계 결정과 최종 통합 과정은 팀 전체 회의를 통해 함께 검토하였다. 특히 데이터 모델, MCP 도구 설계, 최종 시연 흐름은 세 명이 함께 논의하여 결정하였다.

### 3.2. 역할 분담

| 이름 | 주요 역할 | 세부 수행 내용 |
| --- | --- | --- |
| 정진환 | 프로젝트 관리, 백엔드/API 구현 | FastAPI 서버 구조 설계, REST API 구현, 워크스페이스 및 episode 저장 흐름 구현, 최종 통합 점검 |
| 이학현 | MCP 연동, 데이터 모델, 검색 파이프라인 | MCP 도구 설계, PostgreSQL/pgvector/PGroonga 구조 검토, KnowledgeFact 및 EntityRelation 모델 정리, 회상 검색 기능 구현 |
| 전성빈 | 문서화, 웹 UI, 테스트 및 발표자료 | 주간보고서 및 최종보고서 정리, 웹 UI 화면 구성, 테스트 시나리오 작성, 발표 흐름과 제출 산출물 정리 |

역할은 위와 같이 나누었지만, 실제 개발 과정에서는 기능 간 연관성이 높아 각 조원이 서로의 작업을 검토하고 보완하였다. 예를 들어 MCP 도구 설계는 백엔드 API와 데이터 모델 모두에 영향을 주기 때문에 정진환과 이학현이 함께 검토하였고, 웹 UI와 문서화는 전성빈이 중심이 되어 정리하되 팀 전체의 피드백을 반영하였다.

### 3.3. 주 단위의 프로젝트 수행 일정

| 주차 | 주요 예정 작업 | 수행 결과 |
| --- | --- | --- |
| 2주차 | 프로젝트 문제 정의 및 기획 | AI 세션 간 맥락 단절 문제를 핵심 문제로 정의하고, JARVIS의 기본 방향을 AI 장기기억 서버로 설정하였다. |
| 3주차 | 기능 범위 및 핵심 비전 정리 | 기억 저장, 회상, 워크스페이스, MCP 연동을 MVP 핵심 기능으로 정리하였다. |
| 4주차 | 기술 스택 및 초기 아키텍처 선정 | FastAPI, PostgreSQL, pgvector, PGroonga, Docker Compose 기반 구조를 선정하였다. |
| 5주차 | 백엔드 MVP 구현 시작 | FastAPI 서버, SQLAlchemy 모델, Alembic 마이그레이션, Docker Compose 환경을 구성하였다. |
| 6주차 | 워크스페이스 및 맥락 저장 구조 구체화 | Workspace, Episode, Entity, KnowledgeFact, EntityRelation 중심의 저장 구조를 정리하였다. |
| 7주차 | 클라우드 컨텍스트 서버 방향 확정 | JARVIS를 여러 AI 클라이언트가 공유하는 클라우드 기억 서버로 재정의하였다. |
| 8주차 | MCP 호출 및 기억 캡처 전략 연구 | MCP 도구 호출 신뢰성 문제를 분석하고, 증분 저장, 원본 보존, 복구 경로를 포함한 캡처 전략을 설계하였다. |
| 9주차 | 하이브리드 검색 구현 | pgvector 의미 검색, PGroonga 전문 검색, EntityRelation 그래프 탐색을 결합한 회상 구조를 구현하였다. |
| 10주차 | 트랜스크립트 전처리 및 지식 추출 품질 개선 | 긴 대화 로그에서 불필요한 tool result와 반복 내용을 줄이고, source_quote 기반 검증 흐름을 정리하였다. |
| 11주차 | 회상 기능 고도화 | topic 탐색, passage 검색, episode 발췌, relation 탐색 기능을 추가하여 회상 범위를 확장하였다. |
| 12주차 | 일기 기반 기억 모델 및 웹 UI 구현 | `jarvis_log_diary` 중심의 저장 흐름과 날짜별 일기 확인용 웹 UI를 구현하였다. |
| 13주차 | 최종 통합 및 제출 준비 | README, 배포 설정, 최종 보고서, 주간보고서, GitHub Release를 정리하였다. |

프로젝트는 초기 기획 단계에서 시작하여, 데이터 모델 설계, 백엔드 구현, MCP 연동, 검색 고도화, 웹 UI 구현, 최종 문서화 순서로 진행되었다. 특히 9주차 이후에는 실제 회상 품질을 높이기 위해 검색 구조와 대화 맥락 발췌 기능을 집중적으로 개선하였다.

## 4. 참고 자료

본 프로젝트 수행 과정에서 참고한 자료와 산출물은 다음과 같다.

1. JARVIS GitHub Repository  
   https://github.com/capstone-dutang/jarvis

2. JARVIS 최종 제출용 GitHub Release  
   https://github.com/capstone-dutang/jarvis/releases/tag/v1.0.2-capstone

3. JARVIS README  
   `README.md`

4. JARVIS 설계 기준 문서  
   `docs/JARVIS_DEFINITIVE.md`

5. JARVIS 기술 연구 문서  
   `docs/research/`

6. 캡스톤 주간보고서  
   `docs/capstone/week-02-report.md` ~ `docs/capstone/week-13-report.md`

7. 최종보고서  
   `docs/capstone/final-report.md`

8. MCP Python SDK 및 Model Context Protocol 관련 자료

9. PostgreSQL, pgvector, PGroonga 공식 문서

10. FastAPI, SQLAlchemy, Alembic, Docker Compose 공식 문서