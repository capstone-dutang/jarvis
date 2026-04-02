# JARVIS 절대문서

> 이 문서 하나로 자비스의 모든 것을 안다.
> 최종 갱신: 2026-04-02 (기억 위기 분석 + 캡처 아키텍처 전면 개편)
> 상태: 초안 — 빈 곳을 채워나가며 확정

---

## 1. 자비스란

AI 클라이언트들이 공유하는 클라우드 기억 서버.

세션이 바뀌어도, 기기가 바뀌어도, AI가 바뀌어도 작업 맥락이 이어진다.

GPT에서 한 대화의 결정을 Claude가 알고 있고, 웹 브라우저에서 그 맥락을 그래프로 볼 수 있다.

---

## 2. 풀려는 문제

1. AI 세션이 바뀌면 맥락이 사라진다 — 매번 다시 설명해야 한다
2. 수동 문서화(memory 폴더, README 등)는 항상 늦고 불완전하다
3. AI 도구가 다양해졌는데(Claude, GPT, Gemini, Cursor), 이들 사이에 공통 기억이 없다
4. 팀원이 합류하면 설명 비용이 가장 크다

이 문제는 코딩에만 해당하지 않는다. 원고를 쓰다가 세션이 바뀌면 "3장 주인공 설정이 뭐였지?"를 다시 설명해야 하고, 일상 대화에서 나온 약속이나 결정도 세션이 끊기면 사라진다. 자비스는 **작업 영역을 가리지 않는 범용 맥락 서버**다. 코딩, 글쓰기, 기획, 일상 대화 — 대화에서 지식이 생기는 모든 곳에 적용된다.

---

## 3. 핵심 아이디어

### 클라이언트가 구조화하고, 서버가 검증한다 — 근데 AI의 자발적 호출에 의존하지 않는다

기존 AI 기억 시스템(Zep, LangMem, Letta, OpenAI Memory)은 전부 서버에서 LLM을 돌려서 대화를 분석하고 구조화한다. 비용이 발생하고, 서버가 무겁다.

자비스는 이를 뒤집는다:

- 대화를 하고 있는 AI 클라이언트(GPT, Claude 등)가 이미 전체 맥락을 알고 있다
- 그 AI에게 "대화 원문 + 네가 추출한 엔티티/사실/관계"를 함께 보내게 한다
- 서버는 LLM 추론 없이, 검증/정규화/임베딩/저장만 한다

**핵심 교훈 (2026-04-02 확인)**: MCP 서버는 AI가 도구를 호출하지 않으면 대화를 볼 수조차 없다. 현재 LLM은 메모리 도구를 체계적으로 무시한다 — 즉각적 보상이 없는 "미래 지향적 행동"이기 때문. 이 문제는 프롬프트 최적화로 해결 불가능.

**따라서 자비스는 3경로 캡처로 기억을 보장한다:**

1. **Path A (주력)**: 세션 종료 시 Claude Code `prompt` 타입 Stop 훅이 결정론적으로 발동 → 클라이언트의 LLM이 대화에서 지식을 추출하여 store_memory 호출. AI의 자발적 의지 불필요.
2. **Path B (안전망)**: 대화 원본을 Episode로 즉시 저장 + 경량 키워드/엔티티 추출(YAKE, GLiNER). 크래시에도 생존.
3. **Path C (폴백)**: 다음 세션 시작 시 미처리 세션 감지 → AI에게 처리 요청.

대화 중 AI가 자발적으로 store_memory를 호출하면 **보너스** — 실시간으로 더 풍부한 기억이 축적됨. 안 해도 Path A/B/C가 보장.

결과: 서버에 LLM 추론 비용이 0이다. 로컬 임베딩 모델(~113MB ONNX int8)과 문자열 매칭만 사용하며, 세션 종료 추출은 클라이언트의 기존 LLM을 활용한다.

---

## 4. 사용자 시나리오

### 시나리오 1: GPT → Claude 세션 전환 (개발 작업)

```
1. 사용자가 ChatGPT(웹)에서 JARVIS MCP 서버를 등록한다
   → 브라우저 팝업 → JARVIS 로그인 (OAuth 2.1)
   → ChatGPT가 JARVIS 워크스페이스에 연결됨

2. ChatGPT에서 프로젝트에 대해 대화한다
   → GPT가 새로운 사실을 배울 때마다 자동으로 store_memory 호출 (최소 5턴마다 폴백)
   → 대화 원문 + 엔티티/사실/관계 → JARVIS 서버에 축적

3. 사용자가 Claude.ai로 넘어간다
   → 설정에서 JARVIS MCP 서버 등록 + 같은 계정 로그인
   → Claude가 세션 시작 시 recall_memory 자동 호출
   → GPT 대화에서 축적된 결정/사실/맥락이 반환됨

4. Claude가 맥락을 이어받아 대화를 계속한다
   → "이 프로젝트는 PostgreSQL을 쓰기로 했습니다 (3/26 결정)"
```

### 시나리오 1-b: GPT → Claude 세션 전환 (원고 집필)

```
1. GPT에서 소설 3장의 플롯을 논의한다
   → store_memory: 엔티티(주인공 민수, 조연 하나), 사실(3장은 과거 회상,
     민수의 동기는 복수가 아니라 속죄), 관계(민수 → 하나: 일방적 신뢰)

2. Claude로 넘어가서 4장을 쓰려 한다
   → recall_memory("3장까지의 인물 설정과 플롯")
   → 민수의 동기, 하나와의 관계, 3장의 분위기까지 맥락으로 반환

3. Claude가 4장 초안을 3장과 일관되게 이어서 작성
```

이처럼 자비스의 데이터 모델(Entity, KnowledgeFact, EntityRelation)은 코딩에 특화된 것이 아니다. "PostgreSQL"이든 "주인공 민수"든 똑같이 엔티티이고, "uses_db"든 "동기는 속죄"든 똑같이 사실(fact)이다.

### 시나리오 1-c: 새 세션에서 프로젝트 맥락 파악

기존 문제: 프로젝트 문서가 여러 개고, 시간순으로 작성되면서 서로 충돌하는 내용이 생긴다. 새 AI 세션이 이걸 읽으면 "어느 게 최신이지?" 혼란이 발생한다.

```
1. 새 Claude 세션이 시작된다
   → recall_memory("JARVIS 프로젝트 현황")

2. 서버가 반환하는 것:
   → 현재 진실만 (superseded된 옛 사실은 제외)
   → 최신순 정렬
   예: "구조화 방식: 클라이언트가 구조화+서버가 검증 (3/26 확정)"
       "인프라: Oracle Cloud Always Free (3/31 확정)"
       "임베딩: multilingual-e5-small-ko 로컬 (3/31 확정)"

3. AI는 충돌 없는 정합적 상태를 받아서 바로 작업 시작
   → "서버가 구조화" vs "클라이언트가 구조화" 같은 혼란 없음
   → 더 궁금하면 history 필드에서 변경 이력 + 이유 확인 가능
```

이것이 "문서 3개를 읽고 어느 게 맞는지 판단하는" 기존 방식과의 핵심 차이다. 자비스에는 충돌하는 문서가 없고, 시간축으로 관리되는 사실만 있다.

### 시나리오 2: 팀원 온보딩

```
1. A가 3주간 혼자 작업 → 매 대화마다 JARVIS에 자동 축적

2. B가 팀에 합류
   → JARVIS 계정 생성 → A의 워크스페이스에 contributor로 초대됨

3. B가 AI 도구를 열고 JARVIS 연결
   → recall_memory("프로젝트 현황 알려줘")
   → 3주치 결정/진행상태/구조/할 일이 근거와 함께 반환

4. B는 회의 한 번 없이, 문서 한 장 안 읽고 온보딩 완료
```

### 시나리오 3: 웹 UI 기억 그래프

**핵심 원칙 (리서치 검증): 전체 그래프를 절대 기본 뷰로 보여주지 않는다.** 검색 우선 + 점진적 확장이 유일하게 작동하는 패턴. TheBrain, Neo4j Bloom, Obsidian 전부 로컬 이웃 뷰만 유용하고 전역 그래프는 실패.

```
1. 사용자가 브라우저에서 JARVIS에 로그인

2. 검색바가 중심 — 여기서 시작
   → "PostgreSQL" 검색 → 해당 노드로 이동
   → 또는 워크스페이스 루트 노드 하나에서 시작

3. 점진적 확장 (Progressive Disclosure)
   → 노드 클릭 → 1홉 이웃이 애니메이션으로 펼쳐짐
   → 더 클릭 → 더 깊은 노드 확장
   → 가시 노드는 항상 50~150개 이내 유지

4. 노드를 클릭하면 사이드 패널:
   → 해당 엔티티의 사실 목록
   → 각 사실의 원본 대화 발췌 (근거)
   → 시간에 따른 변경 이력 (SQLite → PostgreSQL)

5. 시간축 시각화
   → 하단 시간 슬라이더로 "이 시점의 진실" 필터
   → superseded 사실: 30% 투명도 + dashed 엣지 + gray
   → "show history" 토글로 과거 사실 오버레이

6. 대화할수록 실시간으로 그래프가 성장
```

**기술 구현**: React Flow + dagre 레이아웃. React.memo() 필수, 가시 노드 50~150개 제한으로 성능 보장. 5000+ 엔티티 시 그래프 탐색을 서버 사이드 API로 이전.

---

## 5. 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                    AI Clients                            │
│  ChatGPT(웹/데스크톱) · Claude(웹/데스크톱/Code) · 기타    │
└──────────────────────┬──────────────────────────────────┘
                       │ MCP (Streamable HTTP + OAuth 2.1)
                       │ store_memory / recall_memory
                       ▼
┌─────────────────────────────────────────────────────────┐
│                  JARVIS API Server                        │
│                    (FastAPI)                              │
│                                                          │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ OAuth 2.1 │  │ MCP Endpoint │  │   REST API        │  │
│  │ Provider  │  │ /mcp         │  │   /api/v1/...     │  │
│  └──────────┘  └──────┬───────┘  └────────┬──────────┘  │
│                       │                    │             │
│              ┌────────▼────────────────────▼──────┐     │
│              │        Core Logic                   │     │
│              │  검증 · 엔티티 해소 · 사실 저장       │     │
│              │  모순 감지 · 검색                    │     │
│              └──────┬─────────────┬───────────────┘     │
│                     │             │                      │
│          ┌──────────▼───┐  ┌─────▼──────────────┐      │
│          │ Embedding     │  │  PostgreSQL 16      │      │
│          │ Model (로컬)  │  │  + pgvector         │      │
│          │ multilingual  │  │  + PGroonga         │      │
│          │ -e5-small-ko  │  │  episodes           │      │
│          │ 384차원, ~500MB│  │  entities           │      │
│          └──────────────┘  │  knowledge_facts    │      │
│                             │  entity_relations   │      │
│                             │  embeddings         │      │
│                             └─────────────────────┘      │
└──────────────────────────────────────────────────────────┘
                                          │
                                          ▼
                               ┌─────────────────────┐
                               │      Web UI          │
                               │  React + React Flow  │
                               │  기억 그래프 시각화    │
                               └─────────────────────┘
```

### 접근 경로 정리

| 누가 | 어떻게 접근 | 용도 |
|------|-----------|------|
| AI 클라이언트 | MCP (Streamable HTTP) | 자동 맥락 저장/회상 |
| 사람 (터미널) | CLI → REST API | 수동 기록, 회상, 설정 |
| 사람 (브라우저) | Web UI → REST API | 그래프 시각화, 관리 |

**HTTP API가 코어이고, MCP/CLI/Web UI는 전부 어댑터.**

---

## 6. 데이터 모델

### 전체 구조

```
Workspace (기억의 소유 단위)
├── User (via WorkspaceMember, 역할: owner/admin/contributor/viewer)
├── Session (기억을 생산하는 연결 단위, client_type: chatgpt/claude/cli/web)
│    └── Episode (대화 원문 보존, 불변, Path B로 자동 저장 — AI 호출 불필요)
├── Entity (엔티티 노드: person, project, technology, file, concept, ...)
├── KnowledgeFact (구조화된 사실, bitemporal) — 구조적 쿼리용
│    └── entity_id + predicate + object_value
│    └── 4타임스탬프: valid_from, valid_to, recorded_at, superseded_at
│    └── source_episode_id, source_quote (grounding 검증용)
├── Fragment (자연어 텍스트 조각, 300자 이내) — 시맨틱 검색용
│    └── content + type(fact/decision/error/preference/procedure/relation)
│    └── keywords[], importance, source_episode_id
│    └── 새 기억마다 KnowledgeFact + Fragment 양쪽 저장
├── EntityRelation (엔티티 간 관계: supports, contradicts, depends_on, ...)
├── ArtifactLink (기억 → 파일/커밋/URL 연결)
└── Embedding (검색용 벡터, 384차원)
```

### 도메인 불변조건

1. **Episode는 AI 호출 없이 자동 저장, 절대 수정/삭제 불가** — Path B의 핵심, 원본 보존
2. **KnowledgeFact는 삭제 대신 supersede** — superseded_at 설정 + 새 사실 insert
3. **모든 KnowledgeFact는 source_episode_id 필수** — 근거 없는 기억 불허
4. **새 기억은 KnowledgeFact + Fragment 양쪽에 저장** — 구조적 쿼리 + 시맨틱 검색 이중 보장
5. **검색은 항상 workspace 범위 내** — 권한 경계
6. **클라이언트는 힌트를 제공하되, 서버가 최종 권한** — 서버가 거부/수정 가능

### Bitemporal 모델

하나의 사실에 4개의 타임스탬프가 붙는다. **4개 전부 서버가 관리한다** — AI 클라이언트의 시간 감각을 신뢰하지 않는다.

| 타임스탬프 | 의미 | 누가 찍는가 | 예시 |
|-----------|------|-----------|------|
| valid_from | 이 사실이 보고된 시점 | **서버 (NOW())** | 2026-03-31T14:23:47Z |
| valid_to | 이 사실이 대체된 시점 | **서버 (supersede 시)** | NULL (아직 참) |
| recorded_at | 시스템 기록 시점 | **서버 (NOW())** | valid_from과 동일 |
| superseded_at | 새 사실로 교체된 시점 | **서버 (supersede 시)** | NULL (현재 믿음) |

**왜 서버 시각인가:**
- AI가 "지난주에 결정했어"라고 해도 정확히 언제인지 파싱할 필요 없음
- store_memory가 호출된 순간이 곧 "이 사실이 시스템에 입력된 시점"
- TIMESTAMPTZ는 8바이트 — 사실 100만 개여도 8MB, 용량 문제 없음
- 같은 날 안에서도 초 단위로 순서 구분 가능

**예시:**
```
14:00:00 — store_memory: JARVIS uses_db SQLite
  → valid_from = 14:00:00

17:30:00 — store_memory: JARVIS uses_db PostgreSQL
  → 서버가 자동 supersede:
    옛 사실: superseded_at = 17:30:00, valid_to = 17:30:00
    새 사실: valid_from = 17:30:00

"16시 시점에 뭐가 참이었지?"
  → WHERE valid_from <= 16:00 AND (valid_to IS NULL OR valid_to > 16:00)
  → SQLite (당시에는 이게 참이었음)
```

**Phase 2 확장**: AI가 "이건 1월에 결정된 건데"라고 소급 정보를 보낼 때를 위해 `valid_from_override` optional 필드 추가 가능. Phase 1에서는 전부 서버 시각.

"현재 진실"을 조회할 때: `WHERE superseded_at IS NULL` — 대체되지 않은 사실만 반환.
"변경 이력"을 조회할 때: 같은 entity + predicate의 모든 사실을 시간순으로 보여줌.

---

## 7. 기억 캡처 파이프라인

기억은 3경로로 캡처된다. 어떤 단일 경로가 실패해도 나머지가 보장.

### Path A: 세션 종료 추출 (주력, 결정론적)

Claude Code의 `prompt` 타입 Stop 훅이 세션 종료 시 발동:

```json
{
  "hooks": {
    "Stop": [{
      "hooks": [{
        "type": "prompt",
        "prompt": "이 대화에서 핵심 사실, 결정, 선호도를 추출하여 store_memory를 호출하세요.",
        "timeout": 30
      }]
    }]
  }
}
```

- 클라이언트의 기존 LLM이 실행 (추가 인프라/비용 0)
- AI의 자발적 의지 불필요 — 훅이 결정론적으로 발동
- Memento MCP의 AutoReflect와 동일 효과, Gemini CLI 의존 없음

### Path B: 원본 대화 즉시 저장 (안전망, 크래시 내성)

모든 대화는 store_memory 호출 여부와 관계없이 Episode로 저장.
추가로 경량 메타데이터 추출:
- YAKE: 키워드 추출 (CPU, ~10ms)
- GLiNER: 엔티티 인식 (ONNX ~100MB, ~100ms)

이 메타데이터만으로도 기본적인 검색이 가능. 크래시/강제 종료에도 원본이 생존.

### Path C: 다음 세션 복구 (최후 폴백)

SessionStart 훅이 미처리 세션을 감지 → AI에게 처리 요청:
- "이전 세션이 정리되지 않았습니다. 원본을 읽고 핵심을 추출해주세요."
- Path A가 실패한 경우(브라우저 강제 종료 등)의 복구 경로

### 대화 중 자발적 호출 (보너스)

AI가 대화 중 store_memory를 자발적으로 호출하면 실시간으로 더 풍부한 기억이 축적됨.
트리플 트리거 가이드: 토픽 전환 / 5턴 폴백 / 중요 이벤트.
단, **이것에 의존하지 않는다** — Path A/B/C가 주력.

### store_memory 입력

### 입력 (리서치 기반 최적 스키마)

설계 원칙: 전 필드 required(없으면 빈 문자열), 최대 2레벨 중첩, 15개 미만 속성, confidence score 없음(노이즈), source_quote로 서버 검증.

```json
{
  "workspace_id": "...",
  "session_id": "...",
  "provider": "openai",
  "conversation_transcript": "사용자: DB를 뭘로 할까?\nAI: PostgreSQL이...",
  "entities": [
    {
      "name": "PostgreSQL",
      "entity_type": "product",
      "source_quote": "PostgreSQL이 좋겠습니다"
    }
  ],
  "facts": [
    {
      "subject": "JARVIS",
      "predicate": "uses_db",
      "object": "PostgreSQL",
      "temporal": "",
      "source_quote": "좋아 그걸로 가자"
    }
  ],
  "conversation_summary": "프로젝트 DB를 PostgreSQL로 결정"
}
```

**스키마 설계 근거:**
- **entity_type**: enum 제약 → 카테고리 hallucination 완전 제거 (정확도 44% 향상)
  - `["person", "organization", "location", "event", "concept", "product", "preference", "procedure", "other"]`
- **source_quote**: 원문에서 해당 추출의 근거가 되는 정확한 구절. 서버가 transcript와 대조하여 fabrication 감지 (grounding으로 98.9% 제거 가능)
- **confidence score 없음**: LLM은 응답의 87%에 최고 확신도 부여, 정답/오답 간 차이 0.6~5.4%로 사실상 노이즈
- **temporal은 문자열**: "지난주", "since 2023" 그대로 보내고 서버가 파싱. 단, Phase 1에서는 서버 시각 사용
- **predicate는 free-form + 가이드**: description에 "works_at, lives_in, prefers, uses 등 snake_case 사용" 안내

### 서버 처리 순서

두 단계로 분리한다. 동기 트랜잭션으로 데이터를 확정하고, 임베딩은 비동기로 처리한다.
이렇게 하면 임베딩 생성이 실패해도 데이터는 안전하고, 클라이언트 응답이 빨라진다.

```
[트랜잭션 A — 동기, 즉시 응답] ─────────────────────────

1. 인증 + workspace 권한 확인

2. Episode 생성
   → 대화 원문을 그대로 불변 저장
   → provider(chatgpt/claude/gemini), model, 턴 범위 기록

3. 스키마 검증 (Pydantic)
   → 형식 불량 즉시 거부

4. Source Quote 검증 (서버 LLM 없이 품질 보장의 핵심)
   → 각 entity/fact의 source_quote를 Episode 원문에서 substring/fuzzy 매칭
   → 매칭 실패 시 fabrication 의심 → 해당 항목 플래그 (저장은 하되 low_trust 태그)
   → 매칭 성공 시 grounded로 표시
   → 이것만으로 fabrication 98.9% 제거 가능 (AGREE 프레임워크 기반)

5. 엔티티 해소 (3단계 파이프라인)
   → Stage 1: 정규화 + 별칭 사전 (<1ms)
     · Unicode NFKC 정규화, 소문자화
     · 별칭 사전: "포스트그레스"→"postgresql", "k8s"→"kubernetes" 등
   → Stage 2: 임베딩 후보 검색 (15~35ms)
     · 엔티티명을 벡터화 → pgvector에서 cosine > 0.75인 상위 10개 후보
   → Stage 3: 하이브리드 스코어링 (<1ms)
     · RapidFuzz 문자열 유사도 + 임베딩 cosine을 가중 합산
     · 한국어↔영어 크로스링구얼: 문자열 5% + 임베딩 95%
     · 같은 언어: 문자열 40% + 임베딩 60%
     · ≥ 0.92: 자동 병합
     · ≥ 0.85: 병합 + 로그
     · ≥ 0.78: 리뷰 후보로 플래그
     · < 0.78: 새 엔티티 생성

6. Predicate 해소 (엔티티 해소와 동일 패턴)
   → AI가 같은 사실을 다른 predicate로 보내는 문제 해결 ("나이" vs "age" vs "첫경험 나이")
   → 같은 entity의 기존 active predicate 목록을 가져옴
   → 새 predicate와 기존 predicate의 임베딩 cosine + fuzzy 유사도 계산
   → 가중치: 임베딩 70% + fuzzy 30%
   → ≥ 0.85: 기존 predicate로 매핑 (예: "age" → "나이")
   → < 0.85: 새 predicate 그대로 사용

7. KnowledgeFact 생성
   → 같은 entity + (해소된) predicate에 기존 사실이 있으면 supersede 처리
   → (기존 사실 superseded_at = NOW, 새 사실 insert — 원자적)

8. EntityRelation 생성

→ 1~8이 하나의 DB 트랜잭션. 하나라도 실패하면 전체 롤백.
→ 클라이언트에게 즉시 "저장 완료" 응답 반환.

[트랜잭션 B — 비동기, 백그라운드] ─────────────────────

8. 임베딩 생성 (로컬 모델, 15~30ms/건)
   → KnowledgeFact, Episode, Entity의 텍스트를 384차원 벡터로 변환
   → pgvector에 저장

→ 실패 시 재시도 큐에 적재.
→ 임베딩이 아직 없는 사실은 FTS + 그래프 탐색으로 검색 가능.
→ 임베딩 완료되면 벡터 검색에도 노출.
```

---

## 8. 검색 파이프라인 (recall_memory)

AI 클라이언트가 세션 시작 시, 또는 과거 맥락이 필요할 때 호출.

### 입력

```json
{
  "workspace_id": "...",
  "query": "이 프로젝트 DB 뭐 쓰기로 했지?",
  "limit": 10
}
```

### 서버 처리 순서 — 하이브리드 검색

3가지 검색을 병렬 실행한 뒤, RRF로 합산. 전체 P95 목표: <50ms.

**① 벡터 검색 (의미 유사도)**
- 질의를 로컬 모델(multilingual-e5-small-ko)로 임베딩
- pgvector HNSW 인덱스에서 cosine similarity 상위 N개
- "DB"와 "데이터베이스"처럼 같은 의미의 다른 표현도 잡힘

**② 전문 검색 (키워드 매칭)**
- **PGroonga** — 한국어+영어 혼합 자동 처리 (CJK는 N-gram, Latin은 word 토큰화)
- pg_trgm은 한국어에 쓸모없음 (3byte trigram = 한국어 1글자 → 의미 없음)
- 업그레이드 경로: textsearch_ko (mecab-ko) 추가로 형태소 분석 + ts_rank 지원

**③ 그래프 탐색**
- ①②에서 찾은 엔티티를 seed로 Recursive CTE BFS 2~3홉 탐색
- 10K~100K 노드에서 **1~20ms** (Alibaba Cloud 벤치마크 기반)
- cycle detection: path 배열로 방문 노드 추적
- 핵심 인덱스: `(from_entity_id, relation_type)` 복합 + `valid_to IS NULL` 부분 인덱스

**합산: Reciprocal Rank Fusion (RRF)**
- 각 검색에서의 순위를 `1/(k + rank)` 공식으로 점수화
- 여러 검색에서 동시에 상위면 최종 점수가 높아짐
- k=60 (표준값)
- 가중치 조정: 한국어 위주 → FTS 가중치 낮추고 벡터 가중치 올림

**후처리:**
- superseded 필터링 → 현재 진실만 상위에
- **Soft decay 적용** → `final_score = rrf_score × importance × e^(-λ × days_since_access)`
  - 사실을 삭제하지 않되, 오래 안 쓰인 기억은 자연스럽게 뒤로 밀림
  - 유형별 반감기: preference=120일, decision=90일, fact=60일, procedure=30일
  - 다시 접근하면 importance 복원 (인간 기억의 "한번 떠올리면 다시 선명해짐")
- 근거 묶기 → 각 사실에 원본 대화 발췌(Episode excerpt) 첨부

### 출력

```json
{
  "results": [
    {
      "fact": {
        "entity": "JARVIS",
        "predicate": "uses_db",
        "object": "PostgreSQL",
        "type": "decision",
        "grounded": true,
        "valid_from": "2026-03-26"
      },
      "evidence": {
        "excerpt": "사용자: 좋아 그걸로 가자\nAI: PostgreSQL로 확정합니다...",
        "episode_id": "...",
        "recorded_at": "2026-03-26T15:30:00Z"
      },
      "related_entities": ["pgvector", "Oracle Cloud"],
      "history": [
        {"object": "SQLite", "valid_from": "2026-03-20", "superseded_at": "2026-03-26"}
      ],
      "score": 0.87
    }
  ]
}
```

---

## 9. MCP 도구

4개. 도구 수를 최소로 유지해야 AI 클라이언트의 컨텍스트 비용이 관리 가능하다 (10개 미만 권장, 30개 넘으면 정확도 급락).

### manage_workspace

- **언제 호출**: 워크스페이스 생성/전환/이름변경/목록조회 시
- **뭘 보냄**: action ("list" / "create" / "switch" / "rename") + name
- **서버가 반환**: 워크스페이스 목록 또는 작업 결과
- **존재 이유**: 사용자가 workspace UUID를 직접 다루지 않게 하기 위함. 이름으로 생성/전환. AI가 "워크스페이스 만들어줘"같은 요청도 처리 가능
- **설계 결정**: workspace_id를 UUID 대신 이름으로 관리. 모든 도구(initialize/store/recall)가 workspace 이름을 받으면 서버가 name→id 해소

### initialize_memory

- **언제 호출**: 세션 시작 시 (이름이 "initialize"라서 AI가 높은 확률로 자동 호출)
- **뭘 보냄**: workspace 이름 (또는 UUID). 비워두면 워크스페이스 목록 반환
- **서버가 반환**: 점진적 세션 시작 컨텍스트:
  - Stage 1: 정적 앵커 (~100 토큰, 항상 로드 — 워크스페이스 정체성, 핵심 선호)
  - Stage 2: 핵심 프로필 (valid_to IS NULL, importance > 0.9, ~300 토큰)
  - Stage 3: 미처리 세션 감지 시 알림 (Path C 폴백 트리거)
  - + 메모리 프로토콜 지침 ("store_memory 호출 권장, 하지만 필수는 아님 — Stop 훅이 보장")
- **존재 이유**: MCP `instructions` 필드를 무시하는 클라이언트가 많음. 이 도구가 사실상 instructions의 역할을 대체

### store_memory

- **언제 호출**: AI가 자발적으로 (보너스 — Path A Stop 훅이 주력이므로 여기에 의존하지 않음)
- **뭘 보냄**: 대화 원문 + 엔티티/사실/관계 힌트 + provider
- **서버가 하는 일**: 검증 → KnowledgeFact + Fragment 이중 저장 → 비동기 임베딩
- **도구 설명 설계** (리서치 기반 — WHAT과 WHEN을 동시 명시):

```
"Use this when you: (1) learn a new fact about the user — preferences, 
background, goals, technical stack; (2) the user makes a decision or states 
a preference; (3) the user corrects or updates previously known information; 
(4) a meaningful topic concludes with actionable insights.
Do NOT store: greetings, small talk, information already stored, or your 
own responses. Also call this if 5+ substantive exchanges have passed 
without storing. Each memory should be a self-contained statement."
```

### recall_memory

- **언제 호출**: 과거 맥락이 필요할 때
- **뭘 보냄**: 자연어 질의 + 필터(선택)
- **서버가 반환**: 관련 사실 + 근거(원문 발췌) + 변경 이력
- **응답 크기 제한**: 25,000 토큰 이내로 서버 측 truncation (Claude Code 제한)

### 도구 설계 원칙 (리서치 기반)

- **"Use this when..." 패턴**: 도구 설명의 첫 문장에 호출 조건을 명시, 부정 조건도 포함
- **readOnlyHint**: recall_memory와 initialize_memory에 설정 → 자동 승인 유도
- **에러 메시지는 AI가 읽는 것**: "Try again with content under 5000 characters" 같이 처방적으로 작성
- **instructions 필드도 설정하되 의존하지 않음**: 무시하는 클라이언트가 있으므로 initialize_memory가 백업
- **응답 크기 제한**: recall_memory 응답은 25,000 토큰(~100,000자) 이내로 서버 측 truncation
- **레이트 리밋**: 120/min reads, 30/min writes (리서치 기반). AI 친화적 429 메시지 반환

### 클라이언트별 동작 차이

| | Claude | ChatGPT | Gemini |
|---|---|---|---|
| 도구 자동 호출 | **자동** (맥락 필요 시) | **수동** (사용자 명시 요청 시) | 제한적 (tools/list만) |
| 에러 메시지 전달 | 정상 | **깨짐** (모델이 못 읽음) | 미확인 |
| 추론 모델 MCP | 지원 | **미지원** (o4-mini 등) | 미확인 |
| 시연 권장 | **최적** | 제약 있음 | 아직 미성숙 |

캡스톤 시연은 **Claude 기반**으로 진행한다. ChatGPT에서는 사용자가 "이전 맥락 가져와" 같이 명시적으로 요청하면 동작하며, 이는 자비스의 한계가 아니라 ChatGPT의 MCP 구현 특성이다.

---

## 10. 인증 체계

### OAuth 2.1 (MCP 표준)

MCP 스펙(2025-03-26 이후)이 요구하는 인증 표준. ChatGPT, Claude.ai, Claude Desktop 전부 이 방식으로 remote MCP 서버에 연결한다.

### 흐름

```
1. AI 클라이언트가 JARVIS MCP URL에 접근
2. JARVIS가 OAuth 메타데이터 반환
   GET /.well-known/oauth-authorization-server
3. Dynamic Client Registration (RFC 7591)
   → 클라이언트가 자기 자신을 자동 등록 → client_id 발급
4. OAuth 2.1 + PKCE 플로우
   → 브라우저 팝업 → JARVIS 로그인 → 동의 → authorization code
   → access_token + refresh_token 발급
5. 이후 MCP 호출마다 Bearer token 포함
```

### JARVIS가 구현하는 엔드포인트

```
/.well-known/oauth-authorization-server   — OAuth 메타데이터
/oauth/register                           — Dynamic Client Registration
/oauth/authorize                          — 로그인 + 동의 화면
/oauth/token                              — 토큰 발급/갱신
```

### Web UI / CLI 인증

- Web UI: 같은 OAuth 플로우 또는 이메일+비밀번호 → JWT 세션
- CLI: `jarvis login` → 브라우저 기반 OAuth 플로우 → 토큰 로컬 저장

---

## 11. 기술 스택

| 레이어 | 기술 | 역할 |
|--------|------|------|
| API 서버 | Python + FastAPI | 비동기 HTTP, MCP SDK 호환 |
| MCP | mcp Python SDK | Streamable HTTP 엔드포인트 |
| OAuth | mcp SDK 내장 OAuthAuthorizationServerProvider | OAuth 2.1 provider (PKCE, DCR) — SDK가 엔드포인트 자동 생성 |
| DB | PostgreSQL 16 + pgvector + PGroonga | 관계형 + 벡터 + 한국어 FTS 통합 |
| 임베딩 | dragonkue/multilingual-e5-small-ko (ONNX int8) | 384차원, 로컬 실행, 한국어 특화, ~113MB(양자화), 5~15ms/건 |
| 엔티티 해소 | RapidFuzz + 임베딩 cosine + 별칭 사전 | 3단계 파이프라인, 크로스링구얼 동적 가중치 |
| 검증 | Pydantic | 스키마 검증 |
| 그래프 탐색 | PostgreSQL Recursive CTE | BFS 2~3홉, 1~20ms (10K~100K 노드) |
| CLI | typer 또는 click | API thin client |
| Web UI | React + React Flow | 기억 그래프 시각화 |
| 인프라 | GCP (90일 무료 크레딧 $300+) | Cloud Run + Cloud SQL 또는 GCE |

### 외부 API 의존: 없음

자비스 서버는 외부 API 호출 없이 완전 자체 완결된다.
임베딩은 로컬 모델, DB와 검색은 PostgreSQL, 인증은 자체 OAuth provider.
네트워크 장애나 외부 서비스 과금 없이 독립적으로 동작한다.

---

## 12. 인프라

### GCP (90일 무료 크레딧)

팀원 계정의 GCP 90일 무료 크레딧($300+, 약 42만원)을 활용.

| 구성 | 용도 | 비고 |
|------|------|------|
| Cloud Run | FastAPI + MCP 서버 + 임베딩 모델 | scale-to-zero, 메모리 최대 32GB 설정 가능 |
| Cloud SQL (PostgreSQL 16) | pgvector + PGroonga | 관리형 DB, 자동 백업 |
| 또는 GCE e2-medium+ | 전부 한 VM에서 | 더 단순, Docker Compose로 운영 |

크레딧 기간(90일) 내에 캡스톤 시연 완료 가능. 이후 서비스화 시 인프라 재검토.

임베딩 모델(ONNX int8, ~400MB)은 Cloud Run 또는 GCE에서 로컬 실행.
외부 API 의존 없음 — 서버에 전부 포함.

### 캡스톤 시연

- 개발: 로컬 PostgreSQL + Docker Compose
- 시연: GCP 배포 (실제 HTTPS URL) 또는 로컬 + ngrok 터널링

---

## 13. 경쟁 환경 (2026-03 기준, 30+ MCP 메모리 서버 분석)

### 시장 구조: 4개 캠프

MCP 메모리 서버는 4개 아키텍처 캠프로 나뉜다:

1. **LLM-free 단순 저장소** — 공식 server-memory (JSONL, substring 검색, 주간 44K 다운로드). 임베딩/시간 없음.
2. **임베딩 있는 LLM-free** — doobidoo/mcp-memory-service (SQLite-vec, ONNX 임베딩, ~1,500 stars). 단일 temporal만.
3. **서버 LLM 있는 상용 플랫폼** — Zep/Graphiti, Mem0($24M), Letta, Cognee, Supermemory, Hindsight.
4. **기존 도구 활용** — Notion MCP, Obsidian MCP(24+개), 파일시스템.

### 상세 비교

| | 공식 server-memory | doobidoo/mcp-memory | Zep/Graphiti | Mem0 | **JARVIS** |
|---|---|---|---|---|---|
| 저장소 | JSONL 파일 | SQLite-vec | Neo4j/FalkorDB | Qdrant+graph | **PG+pgvector+PGroonga** |
| 검색 | substring | semantic(ONNX) | hybrid(벡터+그래프) | semantic(cloud) | **3-way RRF (벡터+FTS+그래프)** |
| 서버 LLM | 없음 | 없음(옵션) | **4-6 호출/에피소드** | **GPT-4.1-nano** | **없음** |
| 시간 모델 | 없음 | 단일(recorded_at) | **bitemporal (4ts)** | decay | **bitemporal (4ts, 서버 시각)** |
| 엔티티 해소 | 없음 | 통합만 | **3-tier(LLM 포함)** | 기본 | **3단계(LLM 없음, 20~40ms)** |
| 멀티프로바이더 | 기술적 가능 | 기술적 가능 | 기술적 가능 | 기술적 가능 | **1st class 설계** |
| 비용 | $0 | $0 | LLM 비용 | $249/월(그래프) | **$0** |

### JARVIS만의 빈 공간 (검증됨)

30+ MCP 메모리 서버 중 아래 조합을 가진 솔루션은 **0개**:

| 차별점 | 기존 경쟁자 수 |
|--------|-------------|
| 클라이언트 구조화 + 임베딩 + 지식그래프 | **0** |
| Bitemporal + 서버 LLM 없음 | **0** |
| 멀티프로바이더 워크스페이스 (1st class) | **0** |

**자비스의 위치**: 공식 서버의 "LLM-free 클라이언트 구조화" 철학 + Graphiti의 "bitemporal 지식그래프" 정교함. 이 두 극단의 정확히 중간.

**경제적 논거**: Graphiti는 에피소드당 LLM 4~6회 호출. Mem0는 매 add_memory마다 GPT-4.1-nano 호출. 메모리 플랫폼이 확장되면 서버 LLM 비용이 지배적. JARVIS는 이 비용을 클라이언트에 전가 — AI가 이미 돌아가고 있으니 추가 비용 0.

### 전략적 리스크 3가지

1. **공식 MCP 서버 진화** — 임베딩+시간 추가하면 직접 경쟁
2. **Graphiti 포크** — 누군가 서버 LLM 제거하고 클라이언트 위임으로 바꿀 수 있음
3. **플랫폼 네이티브 메모리** — Claude Auto Dream, Gemini context fusion 등이 발전하면 크로스프로바이더 수요 감소 가능

→ 단, 플랫폼 메모리는 사일로(ChatGPT↔Claude 불통), 비정형(텍스트 요약), 비시간적(valid_from/to 없음). JARVIS가 푸는 문제와 겹치지 않음.

---

## 14. 구현 Phase

### Phase 1: MVP — 동작하는 데모

- PostgreSQL 스키마 생성 (전체 테이블)
- FastAPI 서버 뼈대
- OAuth 2.1 provider (authlib)
- Workspace/User CRUD
- store_memory API (Episode 저장 + 검증 + Fact 생성 + 임베딩)
- recall_memory API (하이브리드 검색 + 근거 포함 응답)
- MCP 어댑터 (Streamable HTTP)
- CLI 뼈대 (jarvis init, login, recall)

**검증 기준**: ChatGPT에서 대화 → Claude에서 recall → 맥락 이어짐

### Phase 2: 품질 보장

- 전체 검증 파이프라인 (source_quote 검증, 엔티티 해소, 모순 감지)
- Bitemporal supersede 함수
- 그래프 확장 검색 (BFS)
- 대화 정규화 (Claude/OpenAI/Gemini → canonical format)
- 세션 요약 자동 생성

**검증 기준**: 모순된 사실 입력 → 올바르게 supersede + 최신 진실 반환

### Phase 3: 캡스톤 시연

- Web UI (React + React Flow)
  - 로그인 + 워크스페이스 선택
  - 기억 그래프 시각화
  - KnowledgeFact 상세 + 근거 보기
- Contributor 초대 + 팀 온보딩 데모
- Oracle Cloud 배포
- 시연 시나리오 전체 통과

**검증 기준**: 시나리오 1(GPT→Claude 전환) + 시나리오 2(팀 온보딩) + 시나리오 3(웹 그래프)

---

## 15. CLI 명령어

```bash
jarvis init                              # MCP 자동 등록 + 워크스페이스 연결
jarvis login                             # OAuth 브라우저 인증
jarvis logout
jarvis whoami

jarvis workspace create <name>
jarvis workspace list
jarvis workspace use <name>
jarvis workspace invite <email> [--role contributor]

jarvis recall "왜 Postgres로 바꿨지?"     # 자연어 회상
jarvis recall --type decision --limit 5

jarvis status                            # 워크스페이스 요약
jarvis status --recent 7d               # 최근 7일 변경
```

---

## 16. 대화 정규화

Claude, OpenAI, Gemini가 각각 다른 형식으로 대화를 구조화한다. 서버는 canonical format으로 정규화하여 Episode에 저장한다.

### 정규화 전략

- store_memory 입력에 `provider: "claude" | "openai" | "gemini"` **필수 필드**
- 서버가 provider를 보고 해당 **어댑터**로 canonical format 변환
- 새 provider 추가 시 어댑터 하나만 추가하면 됨

### Canonical Message Format

```json
{
  "role": "assistant",
  "content": [
    {"type": "text", "text": "..."},
    {"type": "thinking", "text": "..."}
  ],
  "tool_calls": [
    {"id": "tc_1", "name": "jarvis_recall", "arguments": {"query": "..."}}
  ]
}
```

### Provider별 어댑터 정규화 규칙

| 엣지 케이스 | 처리 |
|-------------|------|
| Gemini role이 "model" | → "assistant" |
| Claude tool_call이 content 안에 | → 별도 tool_calls 필드로 추출 |
| OpenAI tool arguments가 JSON 문자열 | → JSON.parse()로 객체 변환 |
| OpenAI role: "tool" | → "tool_result" |
| Gemini functionCall (camelCase) | → tool_calls 필드로 통일 |
| content가 flat string (OpenAI) | → [{"type": "text", "text": "..."}] |
| system prompt 위치 | → 별도 system_prompt 필드 |
| Claude thinking / OpenAI reasoning | → {"type": "thinking"} |

---

## 17. 실패 시나리오 대응

### AI가 MCP 도구를 안 부른다 — 이것은 예상이 아니라 확인된 사실

- 3경로 캡처(Path A/B/C)가 이 문제의 근본 해법
- Path A(Stop 훅)가 AI 의지 없이 결정론적으로 추출
- Path B(Episode 자동 저장)가 크래시에도 원본 보존
- 대화 중 자발적 호출은 보너스로 취급

### 긴 대화에서 앞부분 유실

- Path B가 Episode를 자동 저장하므로 원본은 항상 보존
- Path A(Stop 훅)가 세션 종료 시 전체 대화를 처리하므로 앞부분 유실 위험 감소

### 잘못된 구조화 힌트 (hallucination)

- 검증 파이프라인 (스키마 → source_quote 매칭 → 엔티티 해소)
- Episode 원문은 항상 보존 → 나중에 재처리 가능

### 기억 충돌 — 계층적 모순 탐지

1. **predicate supersede** (결정론적, O(1)): same entity+predicate → 즉시 supersede
2. **NLI 모델** (`cross-encoder/nli-deberta-v3-xsmall`, 22M params, ~28ms CPU): top-5 유사 기억과 대조 → 시맨틱 모순 감지 (87.77% 정확도)
3. **entailment 감지**: NLI의 entailment 점수로 중복/동의어 기억 병합
4. 모호한 경우만 다음 세션에서 AI에게 판정 위임

---

## 부록: 리서치 상태

### 해소됨 (2026-03-31 리서치)

- [x] **엔티티 해소** — 3단계 파이프라인 확정 (별칭사전 → 임베딩후보 → 하이브리드스코어링), 크로스링구얼 가중치, threshold 확정
- [x] **임베딩 모델** — Gemini API → dragonkue/multilingual-e5-small-ko 로컬 전환. 한국어 특화, 384차원, ~500MB, 15~30ms/건
- [x] **한국어 FTS** — PGroonga 확정 (pg_trgm 불가, pg_bigm 대비 50배 빠름, ARM64 호환)
- [x] **그래프 탐색** — Recursive CTE 확정 (Apache AGE 불필요), 10K~100K 노드에서 1~20ms
- [x] **트랜잭션 경계** — 동기(Episode~Relation) / 비동기(임베딩) 분리 확정
- [x] **경쟁 환경** — 30+ MCP 메모리 서버 분석, JARVIS 조합(LLM-free + bitemporal + 멀티프로바이더)은 기존 0개 확인
- [x] **MCP 구현 패턴** — mcp SDK v1.26.0 + FastAPI ASGI 마운트, stateless_http=True, SDK 내장 OAuth
- [x] **기억 캡처 위기** — AI가 도구를 안 부르는 문제 확인, 3경로 캡처(Stop훅+Episode자동+세션복구) 아키텍처 확정
- [x] **이중 저장소** — KnowledgeFact(구조적) + Fragment(시맨틱) 이중 저장 확정 (Graphiti/Zep 3-tier 검증)
- [x] **soft decay** — bitemporal 유지 + 검색 점수에 중요도 감쇠 적용, 유형별 반감기 확정
- [x] **NLI 모순 탐지** — cross-encoder/nli-deberta-v3-xsmall (22M, 87.77%, ~28ms CPU) 확정
- [x] **store_memory 스키마 설계** — enum entity_type(44% 정확도 향상), source_quote(grounding 검증), confidence 제거(노이즈), 2레벨 중첩/15속성 미만

### 미해소

- [x] ~~OAuth 2.1~~ — mcp SDK v1.26.0에 OAuthAuthorizationServerProvider 내장, 메서드 구현만 하면 엔드포인트 자동 생성. 또는 외부 IdP(Auth0) 사용 시 토큰 검증만
- [x] ~~대화 정규화 전략~~ — provider 필수 필드 + 서버 어댑터 패턴 확정
- [x] ~~대화 분할 전략~~ — 트리플 트리거 (토픽전환 + 5턴 폴백 + 이벤트) 확정, "when you learn something new" 프레이밍
- [x] ~~그래프 시각화 UX~~ — 검색 우선 + 점진적 확장 확정, React Flow 50~150 노드 제한, 시간 슬라이더
- [x] ~~React Flow 성능~~ — 500노드에서 버벅임, 1000+ 불가. expand/collapse로 50~150 가시 노드 유지하면 최적. 5000+ 시 서버사이드 탐색 전환
- [x] ~~MCP instructions 실효성~~ — 무시하는 클라이언트 다수 확인. initialize_memory 도구로 대체. ChatGPT는 수동 호출, Claude는 자동 호출 차이 확인

### Phase 2 강화 후보 (초기 리서치에서 발굴, 당장 불필요)

- **GLiNER (205MB)**: AI가 보낸 엔티티가 실제로 원문에 존재하는지 교차 검증. 한국어 모델(`taeminlee/gliner_ko`) 존재.
- **HeidelTime**: 원문에서 시간 표현을 독립 파싱하여 AI 제출값과 대조. 한국어 지원, 86% F1. (Phase 1에서는 서버 시각으로 대체)
- **Memory Decay**: `relevance = 0.4×recency + 0.3×frequency + 0.3×confidence`, 반감기 90일. 사실이 수천 개 쌓였을 때 검색 순위 품질 향상.
- **valid_from_override**: AI가 소급 시간 정보를 제출할 수 있는 optional 필드. Phase 1에서는 전부 서버 시각.
