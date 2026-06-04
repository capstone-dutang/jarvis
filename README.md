# 🧠 JARVIS

> **AI가 매일 일기를 쓰는 클라우드 기억 저장소.**
> 세션이 바뀌고, 기기가 바뀌고, AI가 바뀌어도 — 작업의 맥락은 이어진다.

JARVIS는 코딩 에이전트(Claude Code, Codex 등)가 나눈 대화를 **하루치 일기**로 클라우드에 기록하고,
다른 세션의 AI가 그 일기에서 **과거 맥락을 한 번의 도구 호출로 회상**하게 하는 개인 AI 장기기억 시스템입니다.

```
어제의 나          오늘의 나
 (세션 A)           (세션 B)
   │                   │
   │ "오늘 작업 올려"   │ "그때 왜 그렇게 했지?"
   ▼                   ▼
 log_diary  ──▶  [ JARVIS ]  ──▶  recall_memory
                 클라우드 기억          맥락 회수 → 작업 이어가기
```

---

## ✨ 왜 JARVIS인가

한 세션에서 끝낸 작업을 다른 폴더·다른 날·다른 AI로 이어갈 때, 보통은 긴 로그를 처음부터 다시 읽어야 합니다.
JARVIS는 그 맥락 회수를 **`recall_memory("그때 왜 그렇게 했지")` 한 줄**로 대체합니다.

> *"어떤 세션에서든 과거의, 아예 다른 세션의 대화를 찾아내 맥락을 이어갈 수 있는 것 — 이게 JARVIS가 필요한 이유."*

### 핵심 철학 — 클라이언트가 쓰고, 서버는 검증·검색만 한다

기존 메모리 시스템(Letta·LangMem·Zep·OpenAI Memory)은 **서버에서 LLM을 돌려** 대화를 분석합니다.
JARVIS는 **대화 중인 AI가 이미 맥락을 안다**는 점을 활용해, 서버는 LLM 추론이 **0** — 저장·색인·검색만 합니다.
일기·요약·지식 추출은 클라이언트 AI가 자기 컨텍스트에서 수행하므로 서버 비용이 들지 않고, 어떤 AI 클라이언트든 붙을 수 있습니다.

---

## 📔 텍스트 4중 레이어

하나의 **episode**(하루의 한 작업 세션)는 네 겹으로 저장됩니다 — 같은 대화를 목적별로 다르게 본 것:

| 레이어 | 시점 | 용도 |
|---|---|---|
| **turn** | 대화 그대로 | 사용자 발화는 verbatim, AI 발화는 핵심만 함축 |
| **summary** | 3인칭 색인용 | 의미 검색·전문 검색 소스 |
| **diary_entry** | 제3자 객관 사건 일지 | 메인 화면 "그날 무슨 일이 있었나" |
| **human_summary** | 2~3줄 쉬운 한국어 | 사람이 한눈에 |

> raw 트랜스크립트는 통째로 올리지 않습니다. AI가 자기 컨텍스트를 **기억으로 재구성**해 기록합니다
> (Claude 앱처럼 raw 접근이 없는 환경에서도 동작 + 토큰 절감).

---

## 🔍 회상(recall) 파이프라인

`PostgreSQL` **하나** 안에서 세 가지 검색을 합쳐 RRF로 랭킹하고 MMR로 다양성 재정렬합니다:

- **pgvector** — 의미 기반 벡터 검색 (로컬 ONNX 임베딩, 외부 API 호출 0)
- **PGroonga** — 한국어 전문(full-text) 검색
- **entity_relations** — 지식 그래프 2-홉 탐색 (질문에서 앵커 엔티티를 뽑아 그 주변을 회상)

= 벡터 DB이면서 그래프 RAG. 모든 **사실(fact)** 은 출처 인용(`source_quote`)으로 grounding되고,
믿음이 바뀌면 옛 값도 지우지 않고 남겨 **변천사**를 보여줍니다 — 마음이 바뀐 것 자체가 정보라는 일기 의미론.

### 데이터 모델

```
workspace  (프로젝트 1개 = 워크스페이스 1개)
  └── episode        하루의 한 작업 세션 (summary · diary_entry · human_summary)
        ├── turn     대화 한 줄 (user / assistant)
        └── fact     entity ─predicate─▶ object  (+ source_quote, 시간순 누적)
              └── fragment  의미 검색용 자연어 조각 (임베딩)
        entity ◀─relation─▶ entity   지식 그래프 (wikilink)
```

---

## 🛠 MCP 도구

Claude Code·Codex 등 MCP 클라이언트에서 자연어로 호출됩니다.

| 도구 | 언제 |
|---|---|
| `jarvis_initialize_memory` | 세션 시작 — 워크스페이스 컨텍스트 로드 |
| `jarvis_log_diary` | "오늘 작업 올려" — 일기 + 요약 + 키워드 + 엔티티/사실/관계를 한 번에 기록 |
| `jarvis_recall_memory` | "그때 뭐였지" — 엔티티 앵커 하이브리드 회상 |
| `jarvis_brief_me` | "오늘 뭐 하지 / 자비스에 뭐 있어" — ASCII 브리핑 카드 |
| `jarvis_explore_topic` | 회상이 좁을 때 — 주제 지형도 먼저 |
| `jarvis_search_passages` | 순수 의미 구절 검색 (앵커 무시) |
| `jarvis_search_episodes` | 전문(키워드) 검색 |
| `jarvis_get_episode_excerpt` | 특정 episode 본문 발췌 |
| `jarvis_follow_relation` | 엔티티 관계 따라가기 |
| `jarvis_manage_workspace` | 워크스페이스 생성·조회 |
| `jarvis_open_ui` | 웹 UI URL 안내 |

---

## 🖥 웹 UI

`http://localhost:8002/` — 단일 파일 SPA(`src/jarvis/web/index.html`).

- **일기 우선 뷰**: 날짜를 고르면 그날의 일기 카드들, 카드 하단 "대화록 펼치기"로 원본 대화
- 좌측 날짜 트리(GitHub 잔디) · 상단 주제 탭 · 우측 요약 패널
- 검색(`⌘K`) · 위키(엔티티) 모달 · Today's Brief 카드

---

## 🚀 빠른 시작

```bash
# 1) 서버 + DB 기동 (server :8002, db :5440)
docker compose up -d

# 2) Claude Code에 MCP 등록
claude mcp add --transport http jarvis http://localhost:8002/mcp

# 3) 웹 UI 열기
#    http://localhost:8002/
```

기동 후 헬스 체크: `curl http://localhost:8002/health`

---

## 🗂 프로젝트 구조

```
src/jarvis/
  ├── main.py            FastAPI 앱 + 웹 UI 서빙
  ├── mcp_adapter.py     MCP 도구 (streamable-http)
  ├── api/v1/memory.py   REST 엔드포인트 (ingest-and-index, recall, diaries-by-date …)
  ├── core/              recall · store · turn_ingest · brief_engine · sanitizer …
  ├── models/tables.py   SQLAlchemy 스키마
  └── web/index.html     단일 파일 SPA
alembic/                 DB 마이그레이션
docs/JARVIS_DEFINITIVE.md  설계 단일 기준 문서
```

---

## ⚙️ 기술 스택

| 레이어 | 기술 |
|---|---|
| API 서버 | Python 3.11 · FastAPI |
| MCP | mcp Python SDK (Streamable HTTP) · OAuth 2.1 |
| DB | PostgreSQL 16 · pgvector · PGroonga |
| 임베딩 | `dragonkue/multilingual-e5-small-ko` (ONNX, 로컬·외부 API 0, 이미지에 사전 탑재) |
| 검색 | pgvector + PGroonga FTS + 그래프(Recursive CTE) → RRF + MMR |
| UI | 단일 파일 바닐라 SPA |
| 인프라 | Docker Compose |

---

## 📚 문서

- [docs/JARVIS_DEFINITIVE.md](docs/JARVIS_DEFINITIVE.md) — 설계의 단일 기준 문서
- [docs/research/](docs/research/) — 기술 리서치 노트

---

<sub>캡스톤 프로젝트 · 클라우드 기반 AI 장기기억 — “AI가 쓰는 일기”.</sub>
