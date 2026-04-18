# 자비스 연구 노트 — 의사결정 과정 기록

> 시작: 2026-04-14
> 목적: 대화에서 나온 아이디어, 판단 과정, 실패와 수정을 시간순으로 기록
> 용도: 프로젝트 맥락 보존 + 캡스톤 회고용

---

<!-- 
  2026-03-26 ~ 2026-04-02 세션: 데스크탑에서 진행한 기록 여기에 추가 예정
  - 03-26: 클라우드 컨텍스트 서버 초기 리서치 (8도메인 종합)
  - 03-31: 경쟁 환경 분석 (30+ MCP 메모리 서버), MCP 구현 패턴, 한국어 FTS, 엔티티 해소
  - 04-01: 구 코드 전량 폐기 + 절대문서 기반 재시작, 인프라 Oracle→GCP 전환, 스키마 설계
  - 04-02: Memento MCP 비교 → 3경로 캡처 + 이중 저장소 + NLI 모순 탐지 아키텍처 개편, Phase 1 코드 작성
-->

---

## 2026-04-14

### 세션 1: 진행상황 파악 + submodule 동기화

#### 상황 인식

brain repo에서 `git pull` 후 jarvis submodule 상태 점검. submodule이 `5a0142e`(코드 없는 구 커밋)에 detached HEAD 상태였음. brain repo 포인터는 `3a5ef6b`(Phase 1 코드 포함)을 가리키고 있었고, remote origin/main에도 이미 푸시 완료 상태.

원인: 데스크탑에서 작업 + 푸시 후, 이 머신에서 `git submodule update`를 안 한 것. `git submodule update --init jarvis`로 해소.

#### 데스크탑 세션에서 확인된 것 (사용자 구두 보고)

1. **세션 간 맥락 보존 동작 확인**: Claude Code 내에서 서로 다른 세션 간에 자비스를 통한 맥락 보존이 실제로 작동함을 확인.
2. **Bitemporal supersede 동작 확인**: 기존에 저장된 정보를 수정하면 과거 내역을 지우는 게 아니라 타임라인별로 레거시가 남는 것을 확인. 절대문서의 bitemporal 설계가 의도대로 동작.
3. **핵심 한계 발견 — AI 도구 호출 의존성**: 프로젝트의 성공이 전적으로 사용하는 AI 에이전트의 도구 호출 능력에 의존한다는 문제. MCP 서버는 AI가 도구를 호출해야만 대화를 볼 수 있는 구조 — AI가 안 부르면 기억이 쌓이지 않음.
4. **Memento MCP 참고**: 도구 호출 의존 탈피를 위해 Memento MCP 프로젝트를 분석. Claude Code hooks의 prompt 타입(Stop 이벤트에 결정론적 발동)이 해법으로 도출됨 → 3경로 캡처 아키텍처로 전환.

#### 현재 상태 정리

**설계: 완료**
- 절대문서 확정 (데이터 모델, store/recall 파이프라인, MCP 도구 3개, OAuth 2.1, 기술 스택)
- 리서치 8건 완료
- 경쟁 분석: 자비스 조합(LLM-free + bitemporal + 멀티프로바이더)은 시장에 0개

**Phase 1 코드: 존재, 검증 미확인**
- store_memory (452줄), recall_memory (203줄), MCP 어댑터 (330줄), OAuth (200줄), CLI (254줄)
- DB 스키마 (alembic migration), Docker Compose, Dockerfile
- OAuth는 `TODO: Re-enable OAuth after MCP logic verification` 상태로 비활성
- 동작 확인은 데스크탑에서 했다고 함 — 이 머신에서는 미검증

**Phase 2 (품질 보장): 미착수**
- source_quote 검증, bitemporal supersede, NLI 모순 탐지

**Phase 3 (캡스톤 시연): 미착수**
- Web UI (React + React Flow), 배포

#### 3경로 캡처의 사용자 마찰 문제 — 미해결

절대문서의 3경로 캡처 중 Path A(Claude Code Stop 훅)에 대한 재검토.

**문제**: hooks는 사용자가 `~/.claude/settings.json`에 직접 설정해야 함. MCP 서버 등록만으로는 자동 설치 불가능 — MCP 프로토콜에 "서버가 클라이언트 설정을 수정하는" 메커니즘 자체가 없음. 게다가 Claude Code 전용 기능이라 ChatGPT, Claude.ai 웹, Gemini 등 다른 클라이언트에서는 아예 불가능.

**자비스의 UX 목표와의 충돌**: "MCP 서버 등록하면 끝"이 이상적인 사용자 경험인데, hooks 설정을 요구하는 순간 부가적인 task가 생김.

**현재 MCP 등록만으로 가능한 수단**:
- `initialize_memory` 도구로 세션 시작 시 AI에게 행동 지침 주입
- 도구 설명(description)과 MCP instructions 필드로 호출 유도
- 그러나 이것이 바로 "AI 의지에 의존" = 원래 문제 그 자체

**해법 발견 — initialize_memory에서 AI가 hooks를 자동 설정**

Memento MCP 분석 결과, Memento도 동일한 도구 호출 의존 문제를 가지고 있고 SKILL.md + instructions로 "더 잘 유도하기"를 택했을 뿐 근본적 해결은 못 함.

그런데 Claude Code는 파일 시스템 접근 권한이 있다. 핵심 아이디어:

1. 사용자가 MCP 서버 등록 (이건 어차피 해야 함)
2. AI가 `initialize_memory` 호출 (세션 시작 시 자동)
3. 서버 응답에 "~/.claude/settings.json에 Stop 훅을 추가하라"는 지침 포함
4. AI가 settings.json을 읽고 → hooks 미설정이면 추가
5. 다음 세션부터 Stop 훅이 결정론적으로 발동

사용자 입장에서는 **MCP 등록 한 번이 전부** — hooks 설정은 AI가 알아서 처리. 부가적인 task 0.

**검증 필요 사항**:
- hooks가 settings.json 수정 후 즉시 반영되는지, Claude Code 재시작이 필요한지
- AI가 settings.json 수정 시 사용자 승인 프롬프트가 뜰 수 있음 — 근데 "허용" 한 번 누르는 수준
- Claude Code 전용이라는 한계는 여전하지만, 다른 클라이언트는 instructions/도구 설명으로 유도하는 게 최선이라는 점은 변함없음

**판단**: Path A(Stop 훅)를 "수동 설정 필요한 파워유저 옵션"이 아니라 **"initialize_memory가 자동으로 셋업하는 기본 동작"**으로 승격 가능. 이것이 도구 호출 의존 문제의 현실적 최선해.

#### 캡처 전략 재정립 — Stop 훅은 안전망, 증분 저장이 주력

**Stop 훅의 한계 인식**: 세션 끝에 한 번에 몰아서 추출하는 것으로는 부족함. 컨텍스트 컴팩션이 이미 일어났으면 AI가 앞부분 대화를 기억 못 할 수 있고, 기억한다 해도 디테일이 유실됨.

**주력 = 대화 중간중간 증분 저장 (트리플 트리거)**:
- 토픽 전환 시
- 5턴 이상 store 없이 지나갔을 때 (안전망)
- 중요 이벤트 발생 시 (결정, 선호도 변경, 사실 정정)

**Stop 훅 = 최후의 안전망**: 중간에 놓친 게 있으면 마지막에 한 번 더 쓸어담는 역할. 타임아웃은 두지 않음.

**저장 품질 기준 — "핵심 사실 추출"이 아니라 "디테일이 살아있는 기록"**:
- 사용자의 감정 상태, 판단의 이유, 뉘앙스까지 포함
- "PostgreSQL로 결정"이 아니라 "PostgreSQL로 결정 — 이유: Oracle이 ARM에서 삽질이 많아서 빡쳐서 바꿈" 수준

**자비스의 존재 이유와 직결**: 자비스를 만드는 이유 자체가 컨텍스트 컴팩션으로 인한 디테일 유실 방지. 컴팩션이 일어나기 **전에** 이미 자비스에 저장해뒀으므로, 다음 세션에서 recall하면 컴팩션 이전의 디테일이 살아 돌아옴. 이것이 "세션 간 맥락 보존"의 진짜 의미.

#### 훅 메커니즘 실제 조사 — 두 번째 정정

공식 문서 직접 확인 결과, 이전 분석 두 번 다 부정확했음.

**훅의 실제 구조 (Claude Code 공식)**:
- 4가지 핸들러 타입: command, http, prompt, agent
- **prompt 타입**: 별도 모델(기본 Haiku)에게 단일턴 평가 → ok/not ok JSON 반환. 메인 대화에 지시 주입이 **아님**
- **agent 타입**: 서브에이전트 스폰, 도구 사용 가능하나 메인 대화와 **독립**
- **command/http**: 셸 명령이나 HTTP 호출, 결과는 exit code + stdout
- 훅이 메인 AI에게 할 수 있는 것: `additionalContext` 반환으로 **추가 맥락 주입**까지만. MCP 도구 호출을 직접 트리거하는 건 **불가능**

**훅은 코딩 에이전트 공통 포맷** (Claude Code만이 아님):
- Codex CLI: SessionStart, Stop, PreToolUse, PostToolUse, UserPromptSubmit
- Windsurf Cascade: 12개 이벤트 (pre/post 패턴)
- Cursor, Copilot, Kiro 등도 MCP + hooks 지원
- 전부 비슷한 구조 — 가드레일/관찰자 역할이 주, 에이전트 행동 직접 제어는 제한적

**PreCompact가 핵심이 아닌 이유**:
- 대부분 사용자는 컴팩션까지 쓰지 않음
- 1M 컨텍스트를 채우려면 한참 걸림 — 사실상 발동 안 됨
- 컴팩션 임박 시 AI가 대충하는 경향
- 중요한 건 **대화 중 잦은 빈도로 저장**하는 것

**`additionalContext` 활용 가능성**:
Stop 훅이 매 턴 발동할 때 `additionalContext`로 "아직 store_memory 안 했으면 하라"는 맥락을 주입하는 것은 가능. 하지만 이것도 AI가 "지시를 따를지"는 보장 못 함 — instructions보다 한 단계 강한 넛지 수준.

**현재 결론**: 훅으로 MCP 도구 호출을 결정론적으로 강제하는 건 불가능. `additionalContext`를 통한 매 턴 넛지가 현실적 최선이며, 근본적으로 AI 의지 의존을 벗어나지 못함. 이건 MCP 프로토콜과 코딩 에이전트 훅 시스템 모두의 공통 한계.

#### Memento MCP 로컬 소스 분석 — 실제 캡처 구조

memento-mcp를 로컬에 클론해서 직접 분석. (이전에는 gh API로 README/SKILL.md만 봤음 — 불충분했음)

**Memento의 실제 구조 (claude-code.md + SKILL.md + architecture.en.md)**:

1. **SessionStart 훅 (command 타입, 결정론적)**: curl로 서버에 직접 `context()` 호출. AI 의지 불필요 — 셸이 HTTP 요청을 직접 보냄
2. **대화 중 remember (AI 의지 의존)**: AI가 자발적으로 호출 (`trigger_type: "voluntary"`). SKILL.md에 "이때 호출하라" 극도로 구체적 명시
3. **세션 종료 시 reflect (AI 의지 의존)**: AI가 "오늘은 여기까지" 같은 종료 의도 감지 시 호출
4. **미반영 세션 후처리 (수동 안전망)**: 관리 콘솔에서 `reflect-all` API로 unreflected 세션 일괄 처리

**핵심 통찰 — command 훅으로 서버 직접 호출**:
Memento는 hooks에서 AI에게 "해라"고 지시하는 게 아니라 **curl로 서버 API를 직접 호출**하는 패턴을 사용. 이건 AI를 완전히 우회하는 결정론적 경로.

단, 이게 가능한 건 `context()`(recall)가 "서버에 질의만 하면 되는" 연산이기 때문. `remember`/`store_memory`는 **대화 내용을 보내야** 하는데 command 훅에서는 현재 대화 원문에 접근 불가 → curl만으로는 "뭘 저장할지" 결정 불가.

**Memento도 저장은 AI 의지 의존**: recall(context)만 결정론적, 나머지 저장(remember/reflect)은 전부 AI 의지 의존. SKILL.md의 극도로 구체적인 행동 지침으로 확률을 높이는 접근.

**`trigger_type` 필드**: Memento는 tool_feedback에 `sampled`(훅 샘플링) vs `voluntary`(AI 자발적) 구분을 두고 있음. 훅에서 트리거된 피드백과 AI가 자발적으로 보낸 피드백을 구분하여 품질 분석에 활용.

**자비스에 적용할 점**:
- SessionStart command 훅 → curl로 `initialize_memory` 직접 호출 (결정론적 세션 초기화)
- 저장은 instructions + 도구 설명으로 유도 — 이것이 현재 기술 수준의 한계
- SKILL.md 수준의 극도로 구체적인 행동 지침이 도구 호출 확률을 실질적으로 높임

#### 딥리서치 결과 — 도구 호출 신뢰성 + 구현 플레이북

멀티 클라이언트 범용 + 소프트 신뢰성(70-80%) + 구현 플레이북 방향으로 딥리서치 실시.

**이전 분석의 오류 정정:**

1. ~~"훅으로 MCP 도구 호출을 강제할 수 없다"~~ → **Stop 훅에서 `decision: "block"` 반환하면 AI가 계속 동작** — "store_memory 호출하고 끝내라"를 강제할 수 있음. 이건 가드레일이 아니라 **결정론적 체크포인트**.
2. ~~"훅에서 대화 원문 접근 불가"~~ → **`transcript_path`로 전체 대화 JSONL 파일 경로가 모든 훅 이벤트에 전달됨**. PreCompact 훅에서 transcript_path를 읽어 서버에 POST하면 컴팩션 전 전체 대화를 캡처 가능.
3. ~~"Memento SKILL.md가 1000줄"~~ → 실제로 없음. scrypster/memento는 ~30줄 CLAUDE.md 스니펫이 전부.

**신뢰성 숫자 (리서치 기반):**

| 환경 | 예상 신뢰성 | 근거 |
|------|-----------|------|
| Claude Code (Stop 훅 + instructions) | **85-95%** | Stop hook decision:block이 결정론적 체크포인트 |
| Codex CLI (Stop 훅) | **75-90%** | 실험적이나 같은 패턴 |
| Cursor (stop 훅) | **65-80%** | followup_message 패턴, 베타 |
| Windsurf (패시브 캡처) | **55-75%** (잠재적 90%+) | post_cascade_response_with_transcript로 전체 대화 비동기 POST |
| Claude Desktop/Web (hookless) | **40-60%** | MCP instructions + bootstrap 패턴만 |
| ChatGPT Desktop (hookless) | **30-50%** | MCP instructions 지원 불확실 |

**핵심 아키텍처 결정 — 듀얼 패스:**
- **Path 1 (코딩 에이전트)**: Stop 훅 decision:block으로 매 턴 store_memory 강제 + SessionStart curl로 context 로드
- **Path 2 (앱/웹 클라이언트)**: MCP instructions + bootstrap 패턴(initialize_memory 응답에 행동 지침 포함) + 사용자 명시 요청("이거 기억해")

**bootstrap 패턴**: `initialize_memory` 호출 시 서버가 관련 기억 + 행동 프라이밍을 함께 반환. "이 세션에서 결정/선호도/수정 발생 시 store_memory 호출하라"를 가장 최근 컨텍스트에 배치 — LLM이 가장 강하게 attend하는 위치.

**instruction 예산 한계**: frontier LLM은 ~150-200개 instruction을 일관되게 따를 수 있음. Claude Code 시스템 프롬프트가 ~50개 소비. 모든 자비스 instruction이 다른 모든 것과 경쟁. → 도구 4개, instructions 2KB 이내, CLAUDE.md 60줄 이내.

**Zep의 "unknown unknowns" 논점**: 도구 호출 기반 저장은 LLM이 "저장할 가치 있음"을 인식하지 못하는 정보를 놓침. Stop 훅이 코딩 에이전트에서 이를 보상. hookless 클라이언트에서는 사용자 명시 저장("이거 기억해")이 갭을 메움 — 이건 실패 모드가 아니라 **기대되는 인터랙션 패턴**.

**구현 플레이북**: 도구 스키마 4개(initialize_memory, store_memory, recall_memory, list_entities), MCP instructions 템플릿, CLAUDE.md 템플릿, Claude Code/Codex/Cursor/Windsurf별 hook 스크립트, bootstrap 응답 템플릿, 저장 빈도 휴리스틱, 디버깅 가이드까지 전부 포함. 바로 구현 가능한 수준.

**크로스 프로바이더 가치**: 기존 MCP 메모리 서버 중 프로바이더 간 기억 공유를 핵심 기능으로 내세운 곳 없음. GPT에서 결정 → 다음 Claude 세션에서 표면화, 이게 자비스의 진짜 차별점. `source_provider` 필드로 "이건 4/10 ChatGPT 세션에서 결정한 것"까지 보여줄 수 있음.

#### 두 번째 딥리서치 — defense-in-depth 플레이북

같은 주제로 두 번째 딥리서치. 첫 번째와 비교하여 새로운 핵심 발견:

**신뢰성 레이어별 스태킹 (두 번째 리서치가 더 정밀):**

| 레이어 | 단독 | 누적 |
|--------|------|------|
| 도구 설명만 | ~10-15% | 10-15% |
| + MCP instructions | ~20-30% | 25-35% |
| + CLAUDE.md | ~50-60% | 55-65% |
| + bootstrap 도구 | ~60-70% | 65-75% |
| + Stop 훅 (block+nudge) | — | **70-80%** |
| + PreCompact 훅 (서버 추출) | — | **75-85%** |

**Tool Search 지연 로딩 문제 (새 발견):**
Claude Code v2.1.9+에서 MCP 도구가 기본적으로 지연 로딩됨 — AI가 도구를 검색하기 전까지 도구 설명이 컨텍스트에 없음. 즉 도구 설명만으로는 proactive 행동을 유도할 수 없음. **CLAUDE.md가 "도구 쓰려는 의도"를 먼저 만들어야** 도구 설명이 로딩됨. 우선순위: hooks > CLAUDE.md > 도구 응답 내 지침 > MCP instructions > 도구 설명.

**도구 네이밍 충돌 (새 발견):**
여러 MCP 서버가 로딩되면 `store_memory` 같은 일반적인 이름이 충돌함. `jarvis_store_memory`로 네임스페이스 필요. MCPO 프록시에서 Claude가 5분간 엉뚱한 서버의 도구를 호출한 사례 보고됨.

**N턴마다 넛지 패턴 (새 발견):**
매 턴 Stop decision:block은 무한루프 위험 + 오버헤드. 카운터 파일로 3턴마다만 block하는 패턴이 더 실용적. `stop_hook_active` 체크는 **필수** — Claude-Mem에서 무한루프 버그 다수 보고.

**MCP sampling 사망 확인:**
클라이언트 12%만 지원 (VS Code Copilot, Cursor만). Claude Desktop/Code/ChatGPT 전부 미지원. 서버가 자발적으로 요청할 수도 없음 (클라이언트 요청 처리 중에만 가능). 아키텍처에서 배제.

**production 시스템의 공통 증언:**
"Without explicit instructions, Claude will acknowledge information but not actually save it. You MUST instruct Claude to use the tools." — 모든 production MCP 메모리 시스템이 동일한 문제를 문서화.

"Claude's tool-calling training optimizes for fulfilling user requests, not for proactive self-directed tool use." — 기억 저장은 즉각적 사용자 이익이 없는 자기 주도적 행동이라 모델의 기본 도구 호출 휴리스틱 바깥.

**두 리서치 합산 판단:**
- **70-80% 캡처는 달성 가능** (CLAUDE.md + bootstrap + Stop 훅 스태킹)
- **hookless 클라이언트는 40-60%가 현실적 천장** — 사용자 명시 요청("이거 기억해")이 갭을 메움
- **이건 실패 모드가 아니라 기대되는 인터랙션 패턴**으로 문서화해야 함
- **구현 플레이북 수준의 결과물** 확보 — 도구 스키마, hook 스크립트, instructions 템플릿, 저장 빈도 휴리스틱, 안티패턴 목록까지

#### transcript_path가 여는 가능성 — unreflected 세션 감지

Memento에 "정리 안 된 세션이 있으면 알려준다"는 기능이 있음. 처음에는 "AI가 도구를 안 부르면 서버가 세션 존재 자체를 모를 텐데?"라고 생각했으나, transcript_path의 존재로 해결됨:

**메커니즘**:
1. SessionStart/Stop 훅이 발동할 때마다 `transcript_path`(대화 JSONL 파일)를 서버에 POST
2. 서버는 세션 ID별로 transcript를 보유
3. AI가 store_memory를 한 번도 안 불렀어도, 훅이 transcript를 올려놨으므로 서버는 "이 세션에서 대화는 있었는데 정리(reflect/store)가 안 됐다"는 것을 알 수 있음
4. 다음 SessionStart 때 → "정리 안 된 세션 있음" 경고를 additionalContext로 주입
5. AI가 해당 transcript를 기반으로 뒤늦게라도 기억 추출 수행

**이것은 사실상 3경로 캡처의 Path C(세션 복구)를 결정론적으로 만드는 방법**. 기존 절대문서에서는 "다음 세션 시작 시 미처리 감지 → AI에게 처리 요청"이라고 되어있었는데, transcript_path 덕분에 서버가 미처리를 확실히 감지할 수 있음.

**자비스에 적용**: Stop 훅에서 transcript를 서버에 올리고 + 서버가 해당 세션의 store_memory 호출 유무를 추적 → 다음 SessionStart에서 "이전 세션 미정리" 알림.

#### 세션 1 전체 흐름 요약

이 세션에서 일어난 일의 순서:

1. **진행상황 파악**: brain git pull → jarvis submodule detached HEAD 발견 → update로 해소
2. **연구노트 생성**: 아르고스 연구노트 포맷 참고하여 자비스용 연구노트 신규 작성. 과거 세션 자리 비워둠.
3. **도구 호출 의존 문제 재인식**: 데스크탑 세션에서 확인된 핵심 한계 — AI가 도구를 안 부르면 기억이 안 쌓임
4. **훅 자동 설정 아이디어**: initialize_memory 응답으로 AI가 hooks를 자동 설정하게 하면 사용자 부담 0
5. **캡처 전략 논의**: Stop 훅은 안전망, 증분 저장이 주력. 저장 품질은 디테일이 살아있어야 함. 자비스 존재 이유 = 컴팩션 전 디테일 보존.
6. **훅 메커니즘 3번 정정**: (1) "독립 실행이라 맥락 모름" → (2) "지침이니까 맥락 앎" → (3) 실제 조사 결과 prompt/agent 타입은 독립, command/http만 서버 직접 호출. additionalContext로 넛지 가능하나 강제 불가.
7. **Memento MCP 로컬 클론 분석**: SessionStart curl 패턴, trigger_type(sampled/voluntary) 구분, unreflected 세션 관리 확인
8. **딥리서치 프롬프트 작성**: 사용자의 방향성 확인 — 멀티 클라이언트 범용, 소프트 신뢰성(70-80%), 구현 플레이북. 앱에서도 동작해야 함(hookless 클라이언트).
9. **딥리서치 2건 수행**: (1) 멀티 클라이언트 아키텍처 + 플레이북 (2) defense-in-depth 도구 호출 신뢰성 플레이북
10. **핵심 돌파구 발견**: Stop decision:block 패턴, transcript_path 전체 대화 접근, bootstrap 패턴, 레이어 스태킹으로 75-85% 달성 가능
11. **transcript_path → unreflected 세션 감지**: 훅이 transcript를 올리면 서버가 미정리 세션을 알 수 있음 → Path C가 결정론적으로 됨

**사용자 수정/지적 기록**:
- "훅이 왜 맥락을 몰라? 훅은 이벤트 기반으로 발동하는 지침 아니야?" — AI의 오분석 지적
- "PreCompact가 핵심이 아니야. 대부분 사용자는 컴팩션까지 안 써. 1M 컨텍스트를 채우려면 한참 걸려." — 실용적 판단
- "너가 직접 조사해봐. 별도 LLM 호출로 ok not ok하는 방식이라고?" — AI의 리서치 품질 의심 → 직접 조사 지시
- "훅은 Claude Code에만 있는 게 아니라 코딩 에이전트의 기본 포맷이 됐음" — 에이전트 생태계 인식 교정
- "멀티 클라이언트로 가고 싶어. GPT와 Claude 간 기억 공유가 가장 강력한 부분" — 자비스 핵심 가치 명시
- "앱에서도 쓸 수 있어야 함" — hookless 클라이언트 지원 필수 확인

---

## 2026-04-15

### 세션 1: 절대문서 업데이트 + 보완 파이프라인 설계 + transcript 시딩

#### 절대문서 04-14 리서치 반영

04-14 세션의 연구 결과 8가지를 절대문서에 반영:
1. Path A: ~~prompt 타입 Stop 훅~~ → `decision:block` 패턴
2. 주력 캡처: ~~Stop 훅~~ → 증분 저장(트리플 트리거), Stop 훅은 안전망
3. transcript_path → 서버가 미정리 세션 결정론적 감지 (Path C 보강)
4. 듀얼 패스 아키텍처 (훅 강화 85-95% + instruction 기반 40-60%)
5. defense-in-depth 레이어 스태킹 + 신뢰성 수치 테이블
6. 도구 네임스페이스 `jarvis_` 접두사 — 전체 문서 일괄 변경
7. Tool Search 지연 로딩(v2.1.9+) 대응
8. bootstrap 패턴 (initialize_memory 응답에 행동 프라이밍)

추가: Phase 3 Oracle → GCP 배포 수정, 부록 04-14 리서치 해소 항목 9건 추가.

#### MCP 도구 ≠ 스킬 — 인식 교정

사용자가 "initialize_memory는 초기화 도구인데 왜 호출 확률을 고려하느냐"라고 질문. 스킬(결정론적 발동)과 MCP 도구(AI 자발적 호출 필요)를 혼동한 것. MCP 프로토콜에는 "서버 연결 시 자동 호출" 메커니즘 자체가 없음.

해법: 첫 세션에서 AI가 initialize_memory를 호출하면 → hooks 자동 설정 → 두 번째 세션부터 SessionStart 훅이 curl로 서버 직접 호출(결정론적). **첫 세션 한 번만** AI 의지에 의존.

#### Codex CLI 호환성 확인

Codex CLI도 자비스에 필요한 핵심 3가지(SessionStart, Stop decision:block, transcript_path)를 전부 지원. 기대 신뢰성 75-90%. 실험적(experimental) 상태이나 기본 동작은 Claude Code와 동일 패턴.

크로스 프로바이더 가치 재확인: Claude Code에서 저장한 결정을 Codex CLI(OpenAI 모델)에서 recall하는 시나리오가 자비스의 핵심 차별점.

#### "외장 뇌" 비전과 자비스 설계 정합성 검토

사용자의 비전: "너하고 나하고 하는 대화가 계속 축적되어서 나의 외장화된 뇌가 되는 것"

**맞는 부분**: 데이터 모델(Entity/KnowledgeFact/EntityRelation + bitemporal), 검색(3-way RRF), 크로스 프로바이더 공유 — 전부 "외장 뇌"에 부합.

**사용자 지적으로 수정된 부분**:

1. ~~"어시스턴트 레이어가 별도로 필요"~~ → AI 클라이언트가 recall 결과를 받아 종합/제안하는 것 자체가 이미 어시스턴트 레이어. 자비스는 기억 저장/회수만 하면 되고 종합/제안은 AI 클라이언트의 기본 역할. 내가 메타적으로 못 본 부분.

2. **"서버 LLM 비용 0" → "서버 LLM 비용 최소화"로 전환**: 클라이언트 추출이 80%를 커버해도 나머지 20%에 unknown unknowns가 있을 수 있음. 서버가 Episode 원본과 store_memory 호출 기록을 대조해 **미처리 구간만** LLM으로 보완 추출. Memento/Zep(매 세션 100% 서버 처리) 대비 비용 1/5 수준.

사용자 원문: "서버 llm을 아예 안쓴다보다 그 부족한부분만 탐지해서 추가하는건 어떨까? 보완적으로"

#### 토픽 기반 정보 군집 + 일일 퀘스트 (사용자 메모 구조화)

사용자의 메모를 구조화: "오늘 할 거: 자비스 고도화" 한 줄 → 토픽 감지 → 정보 군집 로드 → 작업 시작.

- "정보 군집"은 자비스 데이터 모델의 Entity + 연결된 KnowledgeFact/Fragment들에 해당
- 군집 간 관계 = EntityRelation
- 이것이 매일 축적되면 사용자의 "세컨드브레인"이 됨
- 세컨드브레인 비전 문서에 미래 비전으로 추가 예정

#### Transcript 분석 — 초기 시딩 데이터 규모 확인

이 데스크탑의 Claude Code transcript를 실측:
- 91 메인 세션, JSONL 원본 813MB
- 유효 대화 텍스트: **4.3MB** (0.5%), thinking 포함 6.8MB
- 나머지 99.5%: tool_result(63%), 시스템 메타데이터(35%)
- Sonnet 기준 예상 비용: **$5-7** (API $110 예산으로 충분)

프로젝트별: fundmessenger 72세션(773MB), brain 15세션(39MB), 기타 미미.

#### 리서치 후보 5건 식별

transcript 시딩 + 보완 파이프라인 구현 전 검증 필요한 주제:
1. **추출 프롬프트 설계** — 대화→엔티티/사실/관계 최적 프롬프트, 기존 연구/production 사례
2. **청킹 전략** — 긴 세션 분할, 맥락 유지 vs 컨텍스트 윈도우
3. **세션 간 중복/충돌 처리** — 동일 토픽 반복 시 사실 병합/supersede
4. **보완 파이프라인 구체 설계** — 갭 감지 메커니즘 + 비동기 처리 흐름
5. **출력 포맷** — 자비스 서버 가동 전 중간 저장 형태

**사용자 판단**: 5개 전부 리서치 가치 있음. 구현 전 문서화 + 리서치 선행 확정.

#### 딥리서치 5건 수행 (7개 결과)

5건의 딥리서치 프롬프트를 작성하여 실행. 2번(청킹)은 3개를 돌려서 총 7개 결과:

1. **추출 프롬프트 설계** (759줄) — multi-pass gleaning +30%, Prompt A~F 라이브러리, Mem0 97.8% 쓰레기 검증, source_quote 3중 검증
2-1/2-2/2-3. **청킹 전략** (3건, 총 591줄) — 전처리 후 85%+ single-pass 충분, production 시스템 전부 고정청킹 폐기, 비용 $2-5
3. **세션 간 중복/충돌** (257줄) — 독립 추출 + 사후 병합이 순차보다 우수, entity-blocking + NLI 결정 트리, refinement 개념 도입
4. **보완 파이프라인** (498줄) — 4단계 LLM-free 갭 감지(novel 기여), Haiku+Sonnet 2단계, 세션당 $0.01-0.02
5. **중간 저장 + 즉시 활용** (350줄) — JSON+JSONL 하이브리드, CLAUDE.md 자동 생성이 가장 빠른 가치

#### 리서치 종합 — 핵심 수렴점

- **비용은 제약 아님**: 전체 91세션 $2-5, 20회+ 반복 가능
- **전처리 > 청킹**: tool_result 압축 90-95% 감소, 대부분 세션 single-pass
- **독립 추출 > 순차 처리**: #1과 #3 간 충돌 발생 → #3 근거가 강함 (context rot). entity 일관성은 사후 병합에서 해소
- **source_quote 사후 검증이 최고 ROI**: 프롬프트보다 검증이 효과적
- **refinement ≠ contradiction**: 점진적 구체화를 별도 처리 (refines edge)
- **갭 감지 파이프라인이 자비스의 novel 기여**: 기존 시스템에 전례 없음
- **CLAUDE.md 즉시 생성이 가장 빠른 가치**: 서버 없이도 활용 가능

리서치 결과 전부 절대문서에 반영 완료.

#### 사용자 수정/지적 기록

- "바로 구현으로 가지 말고 문서화하자. 리서치에서 좋은 결과가 많이 나왔었잖아" — 리서치 선행 원칙 재확인
- "어시스턴트 레이어가 별도로 필요하다고? AI가 recall 결과를 종합해서 말해주는 게 그거 아님?" — 메타 인식 교정
- "100% 자동 축적... 서버 LLM을 아예 안 쓴다보다 부족한 부분만 보완하는 건?" — 보완 파이프라인 아이디어 제안
- "AI가 별로 중요하지 않다고 판단한 게 정말 안 중요한 건지 보장 못 하잖아" — unknown unknowns 문제 인식
- "테스트 해보면 되지" — Codex CLI experimental 상태에 대한 실용적 태도
- "Phase 2 사후 병합이 서버 인프라 필요하다고? 로컬 Python으로 다 되잖아" — 잘못된 의존성 분석 교정
- "API 기한 없었음" — 불필요한 시간 압박 제거
- "나누면 올릴수 있지 않나" — 대형 세션 분할 아이디어 (git 업로드 + 추출 윈도우 동시 해결)
- "자비스의 방향이 정말 맞는걸까" — 세션 간 맥락 전달 품질에 대한 근본적 질문. 설계에서 이미 고려됨 (Fragment 300자 맥락 + Episode 원본 + 저장 품질 기준) 확인

#### 전처리 스크립트 구현 (세션 후반)

API 기한이 없다는 걸 확인한 후, 시딩 우선순위를 재조정. Phase 1 → Phase 2 → 시딩 순서가 맞지만, 전처리는 Phase 1/2와 무관하게 진행 가능하므로 먼저 구현.

**전처리 규칙 확정**:
- tool_result: 타입별 압축 (Read/Search/Bash/Edit), 에러는 전문 보존
- 반복 파일 읽기: FileReadTracker로 최종 버전만 보존
- thinking: **원문 그대로 포함** (요약 아닌 원문. $2 비용 차이로 결정 이유 보존 가치가 더 큼)
- 코드 블록 >20줄: 요약으로 교체
- 연속 도구 호출: 시퀀스 축소
- 대형 세션: 3000턴 단위 분할, user 메시지 경계에서만 자름

**구현 결과**:
- `knowledge-extraction/scripts/preprocess_transcripts.py` (~500줄)
- 90세션 처리, 실패 0, 807.7MB → 68.3MB (11.8x 압축)
- 54,807턴, 99파일 (대형 세션 2개가 9+2 parts로 분할)
- 최대 파일 5.0MB — git push 가능
- source_quote substring 매칭 검증 통과 ("자비스 관련문서들 읽어봐" → FOUND)

**출력 포맷**: 세션당 JSON. `turns` 배열(turn_id/timestamp/role/text) + `flat_text`(전체 연결, quote 검증용). 리서치 #5의 JSON+JSONL 하이브리드 중 JSON 부분 구현.

**thinking 포함 결정 근거**: 리서치 3건이 "1-2문장 요약" 추천했지만, 요약 자체가 LLM 호출 필요 → 전처리 단계에서 과함. 원문 포함 시 비용 +$2, 추출 시 AI가 유용한 부분만 필터링. 비용 대비 결정 이유 보존 가치가 높다고 판단.

**세션 분할 결정 근거**: 리서치 2-2의 decision tree에서 30K 토큰 미만은 single-pass 가능. 3000턴 기준 분할 → user 메시지 경계에서 자름 (대화 중간 끊김 방지). 토픽 기반 분할은 LLM 필요하므로 전처리 단계에서는 기계적 분할이 적절.

#### 세션 1 종합 — 이 세션에서 일어난 일

**설계 + 리서치:**
1. 절대문서에 04-14 리서치 8건 반영
2. 자비스 방향성 논의 (외장 뇌 비전, 토픽 군집, 일일 퀘스트)
3. 보완 파이프라인 설계 ("서버 LLM 0" → "비용 최소화")
4. Transcript 분석 (91세션, 4.3MB 유효, $2-5)
5. 딥리서치 프롬프트 5건 작성 + 7건 결과 수신
6. 리서치 종합 분석 (독립추출>순차, 전처리>청킹, source_quote 검증이 최고 ROI)
7. 절대문서에 리서치 7건 종합 반영

**구현:**
8. 전처리 스크립트 구현 + 90세션 처리 완료 (807MB → 68MB, 11.8x)
9. Phase 1 마무리: PGroonga 인덱스 3개 + hybrid_graph_search() SQL 함수 마이그레이션
10. Phase 1 마무리: Fragment 테이블 + 이중 저장 로직 (store_fact → KnowledgeFact + Fragment 동시 생성)
11. Phase 2: 그래프 검색 활성화 (recall.py seed_ids 전달 — 기존 하드코딩 [] 수정)
12. Phase 2: NLI 모순 감지 (nli_detection.py 신규 + store.py 연동) — 실제 contradiction 0.99 감지 검증
13. Phase 2: 갭 감지 4단계 LLM-free 파이프라인 (gap_detection.py)
14. Phase 2: 갭 추출 Anthropic API (gap_extraction.py)
15. Phase 2: 정규화 테스트 13/13 통과
16. Phase 2: 세션 요약 자동 생성 (_auto_summarize)

**감사 + 수정:**
17. 절대문서 vs 코드 전수 감사 (3개 에이전트 병렬) — critical 이슈 4개, high 5개, medium 4개 발견
18. EntityRelation 생성 로직 추가 (store.py + schemas.py에 RelationHint) — 감사에서 발견된 가장 큰 구멍
19. MCP 도구 jarvis_ 접두사 추가 (4개 도구 전부)
20. EntityRelation 실제 DB 생성 검증 (JARVIS depends_on PostgreSQL, pgvector part_of PostgreSQL)

**시딩 준비 최종 검수:**
21. 전처리 90세션 ✅, Prompt A ✅, store_memory API ✅, quote 검증 ✅
22. 빠진 것: extract_knowledge.py (추출 스크립트), ANTHROPIC_API_KEY 환경변수
23. 비용 재확인: 에이전트가 $134로 과대 추정 → 실측 기반 $2-5가 정확

**다음 할 일**: extract_knowledge.py 작성 → ANTHROPIC_API_KEY 설정 → 파일럿 3세션 추출 → 전체 배치

### 세션 2: Extra Usage 과금 조사 + extract_knowledge.py 구현

#### Extra Usage 과금 메커니즘 조사

시딩용 추출 스크립트에서 Claude API를 호출해야 하는데, `anthropic` Python SDK는 API 크레딧(별도 충전)이 필요함. 사용자에게 Max 구독의 Extra Usage 크레딧($200)이 있어서 이것으로 대체할 수 있는지 조사.

**배경**: 2026-04-04 Anthropic이 서드파티 도구(OpenClaw 등)의 구독 사용을 차단. 보상으로 1개월 구독금액 = Extra Usage 크레딧 지급. Boris Cherny(Anthropic) 공식 발언: "You can still use these tools with your Claude login via extra usage bundles."

**조사 결과**:
- Extra Usage ≠ API 크레딧 — 별도 과금 시스템이지만 가격은 동일 (standard API rates)
- `anthropic` Python SDK는 API 키(`sk-ant-api03-*`) 전용 → Extra Usage 사용 불가
- OAuth 토큰을 SDK에 넣는 것은 토큰 탈취로 밴 사유
- **해법**: `claude -p` (CLI 파이프 모드)로 subprocess 호출 → CLI가 OAuth 인증 처리 → Extra Usage에서 차감
- OpenClaw도 동일 방식 사용 — `claude` CLI를 백엔드로 호출, Anthropic이 이 사용법을 공식 허용

**근거 출처**:
- Claude Code 공식 문서: `claude -p` (pipe mode), `--output-format json`, `--json-schema`, `--tools ""`, `--no-session-persistence`
- OpenClaw OAuth 문서: "Anthropic staff told us this usage is allowed again"
- OpenClaw→CLI 마이그레이션 가이드: "Anthropic changed billing so third-party API apps draw from 'extra usage' credits, not plan limits"

**크레딧 기한**: 수령 기한 4월 17일, 사용 기한은 수령 후 90일 (7월 중순). 이미 수령 완료.

**서드파티 정의**: Anthropic이 만들지 않은 모든 프로그램 = 서드파티. 우리 `extract_knowledge.py`도 서드파티. `claude` CLI를 백엔드로 사용하는 외부 프로그램이라는 점에서 OpenClaw과 동일.

#### extract_knowledge.py 구현

플랜모드로 설계 후 구현.

**호출 방식**:
```bash
claude -p --output-format json --model sonnet --tools "" \
  --no-session-persistence --system-prompt "짧은 한 줄" \
  --json-schema '<스키마>' --max-budget-usd 0.50
```
- stdin으로 Prompt A 전체 본문 + 트랜스크립트 전달
- `--tools ""`: 도구 비활성화 (순수 텍스트 완성만)
- `--json-schema`: 서버사이드 구조화 출력 검증 → `structured_output` 필드로 반환

**핵심 기능**:
1. 순차 처리 (시간순) — canonical entity list가 누적되어야 entity 일관성 확보
2. Entity 누적 — `dict[str, str]` 메모리 유지, 후속 세션 프롬프트에 반영
3. Source quote 검증 — 추출 후 `source_quote in flat_text` 확인, grounding rate 계산
4. 대형 세션 청크 분할 — flat_text > 500K 문자 시 "User: " 경계에서 분할
5. Resume 지원 — 이미 추출된 세션 자동 스킵
6. 파일럿 모드 — `--pilot 3`으로 소량 테스트 가능
7. dry-run — 실제 호출 없이 처리 대상 확인

**검증**: ruff 통과, mypy --strict 통과, --dry-run 정상 동작 (90세션 로드, 시간순 정렬, 소형 세션 1개 자동 스킵)

**파일**: `knowledge-extraction/scripts/extract_knowledge.py`

#### extract_knowledge.py 파일럿 결과 + 문제 발견

7세션 파일럿 실행. 평균 grounding rate 90.9% (마크다운 strip 적용 후). 추출 품질 자체는 의미 있는 fact들을 잡아내지만, 정성적 검수에서 두 가지 근본 문제 발견:

**문제 1: Predicate 불일치**
- 같은 개념이 세션마다 다른 predicate로 추출됨
  - 03-25: `uses_mcp_tool_count → 2개`
  - 04-01: `has_mcp_tools → 4개`
- 원인: 각 세션이 독립적으로 추출되어 이전 fact를 모름
- 영향: 서버의 supersede가 동작하지 않음 (다른 predicate = 다른 사실로 인식)

**문제 2: 대형 세션 source_quote fabrication**
- 1926턴 세션을 통째로 넣으니 모델이 source_quote를 정확히 복사하지 못함
- 원문에 없는 문장을 조합해서 만들어냄 (fabrication)
- 예: 원문 `"프로젝트: 펀드메신저 v2"` → 추출 quote `"나는 지금 펀드메신저 프로젝트를 진행 중이야"` (없는 문장)
- grounding rate 50%의 원인

**근본 원인**: 독립 추출은 실사용과 다르다. 실사용에서는 AI 클라이언트가 서버 상태를 알고 있는 채로 3-5턴마다 증분 저장. 독립 추출은 이 피드백 루프를 완전히 우회.

**사용자 핵심 지적**: "사용자가 처음부터 자비스를 쓰고 있었던 것처럼 느낄 수 있어야 한다" — 이건 온보딩이다. 독립 추출→나중 import는 이 요구를 충족하지 못함.

#### seed_jarvis.py — 실사용 시뮬레이션 방식으로 재설계

extract_knowledge.py의 독립 추출 방식을 버리고, 실사용 흐름을 그대로 재현하는 `seed_jarvis.py` 작성.

**핵심 변경: 턴 그룹 단위 증분 처리**
```
세션 N의 각 턴 그룹 (5턴):
  1. recall_memory → 기존 fact 가져오기
  2. 기존 fact를 프롬프트에 포함 (existing_facts 섹션)
  3. claude -p로 추출 (작은 컨텍스트 → 정확한 source_quote)
  4. store_memory API → 서버가 entity resolution + predicate resolution + NLI + supersede 처리
  5. 다음 턴 그룹
```

**이전 방식(extract_knowledge.py)과 차이**:
- 세션 통째로 → 5턴씩 증분
- 독립 추출 → recall→extract→store 루프
- 프롬프트에 entity만 → entity + existing facts
- 나중에 import → 추출 즉시 서버 저장

**초기 결과** (1926턴 세션 1, 진행 중):
- grounding rate: 대부분 100% (이전 50% → 100%)
- supersede: 매 그룹마다 발생 — 변경 추적이 실사용처럼 동작
- 청크 사이즈 문제 없음 — 5턴이라 타임아웃 불가

**프로덕션 온보딩 고려**: 턴 그룹마다 개별 호출이라 느림. 프로덕션에서는 Batch API로 전환 필요.

**파일**: `knowledge-extraction/scripts/seed_jarvis.py`

#### 8세션 시딩 완료 + recall 검증 결과

**시딩 완료 결과**:
- 8세션 (전부 fundmessenger, 03-14~03-16)
- 1,469 entities, 1,807 facts, 759 relations, 347 supersedes
- 비용 $21.06

**recall 검증** — MCP recall_memory로 직접 테스트:

1. "펀드메신저를 왜 만들게 되었는지" → 뉴스/AI 파이프라인 fact에 편향. 프로젝트 개요 수준 정보가 상위에 안 올라옴
2. 쿼리를 바꿔서 "동아리 관리", "멤버 가입", "기술 스택" 등으로 구체적으로 물어보니 다양한 기능 파악 가능
3. 최종 종합: 펀드메신저가 "대학 투자동아리용 SaaS 플랫폼"이고 동아리 관리/종목관리/AI 뉴스/종토방/메시징/게시판/마이페이지 등을 포함한다는 전체 그림 파악 가능
4. supersede 이력 확인: `뉴스 작성자 페르소나 implementation_status`가 4단계 변경 이력 추적됨
5. 사용자 결정 추적: `동아리별 관심종목 rejected_by_user` 등

**발견된 문제**:
- recall이 한 번에 편향된 결과 반환 → AI가 여러 번 recall해야 전체 파악 가능. recall 결과에 카테고리 메타 정보 포함하면 개선 가능
- "댐퍼" (API 키 로테이션) 관련 fact 없음 → 시딩 범위(8세션) 밖의 세션에 존재. 71개 전체 시딩 필요
- 시딩 스크립트가 서버 밖에서 동작 — Path B + 보완 파이프라인으로 서버 내부에서 처리하는 게 맞는 구조

**설계 vs 구현 갭 전수 조사 결과** (04-16 구현 시도 후 업데이트):

| 항목 | 상태 | 비고 |
|------|------|------|
| Defense-in-depth (Stop hook, CLAUDE.md, bootstrap, readOnlyHint) | **미구현** | AI가 자발적으로 자비스를 안 부름 — 이 세션에서 직접 체험 |
| Path B: Episode 업로드 엔드포인트 | **구현됨** | `POST /upload-transcript` 동작. 단, YAKE/GLiNER 경량 추출은 미구현 |
| Path B: 백그라운드 gap processing | **결함** | asyncio.create_task 동시 폭발 → 작업 큐 + 단일 워커로 재구현 필요 |
| Path C (transcript_path POST, 미정리 세션 감지) | **미구현** | |
| 보완 파이프라인 API 연결 | **부분** | analyze-gaps 엔드포인트 존재. gap_extraction.py claude -p 전환됨. 큐 구조 미완 |
| temporal 필드 처리 | **미구현** | schemas.py에 필드 있지만 store.py에서 무시 |
| soft decay | **미구현** | recall에서 오래된 fact 순위 안 내려감 |
| Entity merge 중간 티어 (0.85 로그, 0.78 리뷰) | **미구현** | 0.92만 auto-merge |
| 서버 로깅 | **구현됨** | main.py 설정 + store.py/recall.py 로그 추가 |
| initialize_memory Stage 2-4 (프로필, 미처리 경고, 프라이밍) | **미구현** | 최근 10건만 반환, 프라이밍 부족 |
| NLI 동작 확인 | **미확인** | supersede 347건은 predicate 기반. NLI auto-supersede 발동 여부 로그로 확인 불가 (로그 추가 후 미테스트) |
| Auth/OAuth | **미구현** (의도적) | 배포 시 활성화 예정 |

**방향 전환**: 시딩 스크립트 방식(서버 밖) → Path B + 보완 파이프라인(서버 안)으로 온보딩 구현. 이러면 시딩 스크립트 불필요, 일반 사용자 온보딩도 같은 흐름. claude -p + sonnet 사용.

#### Path B + 보완 파이프라인 구현 시도 (04-16)

**구현한 것**:
- `POST /api/v1/memory/upload-transcript` — Episode 저장 + 백그라운드 gap processing 트리거
- `POST /api/v1/memory/analyze-gaps` — gap detection 엔드포인트
- `gap_extraction.py` — anthropic SDK → claude -p subprocess 전환
- `main.py` 로깅 설정, `store.py`/`recall.py` 로그 추가
- `schemas.py` 새 엔드포인트용 스키마 (UploadTranscript, AnalyzeGaps, ExtractGaps)
- ruff + mypy --strict 통과

**심각한 구조적 결함 — 다음 세션이 반드시 수정해야 함**:

1. **백그라운드 태스크 동시 폭발**: upload-transcript가 호출될 때마다 `asyncio.create_task`로 백그라운드 gap processing을 즉시 시작함. 66개 세션을 업로드하면 66개 태스크가 동시에 생성되어 서버 과부하. embedding 생성 + gap detection + claude -p extraction이 66개 동시 실행.

2. **해결 방향 (미구현)**: 작업 큐 + 단일 워커 패턴 필요.
   - Episode 테이블에 `processing_status` 컬럼 추가 (pending → processing → done)
   - 업로드는 Episode 저장 + status="pending" 설정 후 즉시 응답
   - 서버 시작 시 백그라운드 워커 1개가 루프 돌면서 pending episode를 하나씩 꺼내 처리
   - 이러면 업로드는 항상 빠르고, 추출은 순차 처리되어 서버 안정

3. **현재 코드 상태**: `memory.py`에서 백그라운드 트리거를 주석 처리한 상태 (`asyncio.create_task` 제거됨). 즉 업로드만 되고 gap processing은 안 돌아감. **이걸 큐 방식으로 재구현해야 함**.

4. **subprocess가 이벤트루프 블로킹**: `gap_extraction.py`에서 `subprocess.run`을 `asyncio.to_thread`로 감쌌지만, 이것도 다수 동시 실행 시 thread pool 고갈 가능. 큐 방식으로 하나씩 처리하면 이 문제도 해소.

**다음 세션 인수인계 — 해야 할 일**:

1. Episode 테이블에 `processing_status` 컬럼 추가 (alembic migration)
2. 백그라운드 워커 구현 (서버 lifespan에서 시작, pending episode 순차 처리)
3. upload-transcript에서 status="pending"만 설정, 워커가 알아서 처리
4. DB 정리 후 펀드메신저+펀드메세지 73세션 업로드
5. 워커가 순차 gap processing 완료 대기
6. recall로 품질 검증

**현재 DB 상태**: personal workspace에 이전 시딩 데이터 일부 남아있을 수 있음 (35개 에피소드 + 관련 fact). 깨끗하게 시작하려면 workspace 삭제 후 재생성.

**서버 프로세스**: 포트 8002에서 Python uvicorn이 돌고 있을 수 있음. `netstat -ano | grep 8002`로 확인 후 `taskkill //PID {pid} //F`로 정리.

#### 사용자 수정/지적 기록

- AI 에이전트에 조사 위임 후 검증 없이 전달 → hallucination 섞인 정보가 계속 영향. 직접 검색+공식 문서 확인이 필수
- "밴당하면 50만원 손해" — OAuth 토큰 추출 테스트 제안은 위험한 제안이었음
- "오픈클로는 오픈소스인데 코드를 보면 되잖아" — 추측 대신 실제 구현 확인이 정답
- "왜 자꾸 Batch API를 대안으로 넣느냐" — 이미 결정된 사항을 반복 제시하지 말 것
- "Claude Code에서 기본사용량 차감이 확실한데 왜 Extra Usage라고 하냐" — CLI 일반 사용은 기본 사용량, 서드파티 사용분만 Extra Usage
- "왜 실사용처럼 해야한다고 합의했는데 세션 통째로 넣었냐" — 비용/시간 걱정으로 편한 쪽으로 타협한 실수. 인풋 총량 동일하고 오히려 대형 세션 타임아웃이 문제
- "이건 온보딩이다" — 시딩 품질은 JARVIS의 첫인상. 사용자가 처음부터 쓰고 있었던 것처럼 느껴야 함
- "자꾸 컨텍스트 컴팩트 하자고 하지 마" — 33% 사용 중인데 불필요한 중단 제안. 작업 연속성 유지
- "왜 66개 백그라운드 태스크가 동시에 도는 구조를 만들었냐" — 작업 큐 없이 asyncio.create_task로 즉시 실행은 프로덕션에서 쓸 수 없는 구조
- "트리거를 빼면 프로덕션에서 누가 트리거하냐" — 트리거 제거가 아니라 큐 방식으로 전환이 답
- "의심스러운 지점이 생기면 추측만 하지 말고 규명하라" — 로그 확인, DB 조회, 코드 추적으로 원인을 확정한 뒤 보고
- "결정된 사항을 다시 흔들지 마" — claude -p로 결정했는데 "API 크레딧으로 할 건지 결정 필요" 다시 언급, 소넷으로 하기로 했는데 하이쿠 제안

## 2026-04-16

### 세션 3 (노트북): recall 품질 문제 + "맥락 조립" 아이디어

#### 검색 품질 문제 인식

데스크탑에서 8세션 시딩 후 recall 검증한 결과, "펀드메신저를 왜 만들게 되었는지" → AI 뉴스 파이프라인 fact에만 편향. 프로젝트 전체 개요가 상위에 안 올라옴. 여러 번 다른 각도로 recall해야 전체 파악 가능.

사용자: "추천 알고리즘이랑 비슷한 거 아닌가?" → diversity-relevance 트레이드오프.

#### AI의 단정 문제 — recall 결과를 그대로 믿어버림

사용자의 핵심 관찰: AI는 반환받은 결과가 전부라고 단정한다. "이상한데? 더 찾아봐야겠다"라는 판단을 하지 않음.

구체적 예:
- "펀드메신저가 뭐야?" → recall이 뉴스 관련 fact 10개 반환 → AI: "펀드메신저는 AI 뉴스 플랫폼입니다"
- 이름만 봐도 "펀드+메신저"인데 AI는 의심 없이 답변
- 학습 컷오프 모델이 "qwen 3.5는 없습니다"라고 단정짓는 것과 같은 패턴

**근본 원인**: AI에게 "더 파고들어줄 것을 기도"하는 구조 자체가 잘못됨. AI는 고집이 세고, 한 번 받은 결과를 전부라고 믿음.

#### "맥락 조립기" 아이디어 — recall의 역할 재정의

**현재**: recall = 검색엔진. 쿼리 → 점수 순 flat list 반환 → AI가 알아서 판단
**제안**: recall = 맥락 조립기. 쿼리 → 서버가 답변에 필요한 가지 조합을 미리 구성 → AI는 조합을 종합만 하면 됨

메커니즘:
1. 쿼리에서 관련 entity 식별
2. entity의 relation을 따라 연결된 entity들 탐색
3. 각 가지(하위 토픽)별로 대표 fact 선별
4. **구조화된 조합**을 반환: "이 질문에 답하려면 이 가지들을 알아야 합니다"

예시 반환:
```
펀드메신저 (root entity)
├── 동아리관리 (15 facts) — 대표: "멤버 가입/탈퇴, 역할 관리"
├── 종목관리 (8 facts) — 대표: "관심종목 CRUD, 실시간 시세"
├── AI뉴스파이프라인 (20 facts) — 대표: "뉴스 요약 + 페르소나 작성"
├── 메시징/종토방 (12 facts) — 대표: "실시간 채팅, 종목별 토론"
├── 기술스택 (6 facts) — 대표: "Next.js + FastAPI + PostgreSQL"
└── 마이페이지 (4 facts) — 대표: "프로필, 포트폴리오 현황"
```

**현재 데이터 구조가 이미 이걸 지원함**: Entity + EntityRelation + KnowledgeFact. hybrid_graph_search가 graph walk을 하는데 결과를 flat list로 뭉개는 게 문제. 그래프의 구조 정보를 살려서 반환하면 됨.

**추천 시스템과의 관계**: 검색 다양성 문제(MMR, entity-level diversification 등)와 겹치지만, 본질은 다름. 추천은 "뭐 볼까" 탐색이고 recall은 "이 질문에 대한 맥락 조립". 다양성이 항상 좋은 게 아니라, **질문의 범위에 맞는 구조를 반환**하는 게 핵심.

#### 아이디어 정교화 — "조합 점수" (시작점/체인이 아님)

초기에 "시작점에서 유사도 체인을 타고 간다"로 오해했으나 사용자가 교정:

**핵심**: 자비스 내 **모든 파편**을 대상으로, 가능한 부분집합 중 "이 질문에 가장 좋은 답변을 만들 수 있는 조합"에 점수를 매겨서 최고점 조합을 반환. 시작점도 체인도 카테고리도 없음. **조합 자체가 단위**.

이건 추천 시스템의 bundle recommendation / set function optimization과 같은 문제 — NP-hard. 파편 N개면 2^N 조합. brute force 불가능. **효율적 근사가 핵심 연구 과제**.

**열린 질문**:
- 조합의 "점수"를 어떻게 정의하는가 — 질문과의 관련도? 조합 내 파편 간 상호보완성? 커버리지?
- 효율적 근사 방법 — greedy submodular? beam search? embedding space에서의 클러스터링?
- 파편 수가 만 단위 이상일 때 실시간 recall latency 제약 하에서 가능한가
- 좁은 질문("PostgreSQL 왜?")과 넓은 질문("펀드메신저가 뭐야?")에 같은 메커니즘이 적용되는가
- 기존 연구에서 이 문제를 어떻게 풀고 있는가 — 리서치 필요

#### Greedy 근사 아이디어 — 앵커 파편에서 시작하는 조합 구축

사용자 아이디어: 질문에 대해 가장 높은 점수를 받는 파편 1개를 먼저 확정("앵커"). 그러면 반환 조합에 앵커가 반드시 포함되므로 탐색 공간이 2^N → 2^(N-1)로 반감. 여기서 더 나아가면:

1. 질문 → 앵커 파편 1개 확정 (개별 점수 1위)
2. 앵커와 조합 점수가 가장 높은 파편 1개 추가
3. 현재 조합과 가장 높은 점수의 다음 파편 추가
4. 반복 → 점수 증가가 임계값 이하면 종료

이러면 매 스텝이 N개 후보만 비교 → O(N×K). 10,000 파편에서 10개 조합이면 100,000번 비교. **실시간 가능**.

이 구조는 submodular greedy 알고리즘과 동일 — 최적해의 (1-1/e) ≈ 63% 보장이 증명되어 있음.

**사용자 판단**: 깊은 리서치가 필요한 주제. 지금 당장 구현이 아니라 연구 과제로 진행.

---

## 2026-04-16

### 세션 1: 전수 감사 — recall 완전 고장 + 코드 버그 7건 발견

#### 경위

이전 세션(788d6a7c, 5140줄) JSONL을 역순으로 읽어 맥락 파악. 이전 AI가 작성한 연구노트 요약을 검증 없이 신뢰하는 실수를 인지한 뒤, recall 품질 검증으로 진입.

#### recall 완전 고장 발견 — 원인 규명

recall_memory를 테스트하니 **모든 결과가 score 1.000**, 쿼리와 무관한 fact가 상위에 올라옴. 원인 추적 결과:

**버그 1: seed_array를 string으로 만듦 (recall.py:45)**
```python
# broken
seed_array = "{" + ",".join(str(s) for s in seed_ids) + "}"
```
asyncpg는 uuid[] 파라미터에 Python list를 기대하는데 `"{uuid1,uuid2}"` 문자열을 넘김. → hybrid_graph_search SQL 함수 호출 자체가 실패.

**버그 2: except Exception이 traceback을 삼킴 (recall.py:232)**
```python
except Exception:
    logger.warning("Hybrid search unavailable...")  # traceback 없음
```
에러 내용이 로그에 안 남아서 원인 파악 불가능했음. `logger.exception`으로 변경하여 처음으로 실제 에러 확인.

**버그 3: hybrid 실패 후 트랜잭션 abort 상태에서 fallback 실행**
hybrid가 PostgresSyntaxError로 실패하면 트랜잭션이 abort됨. 이 상태에서 fallback ILIKE 쿼리가 실행되면 `InFailedSQLTransactionError`로 fallback까지 죽음 → 500 Internal Server Error. `await db.rollback()` 추가로 수정.

**버그 4: HNSW 인덱스 리빌드 안 됨**
대량 임베딩 삽입 후 HNSW 인덱스가 깨져서 벡터 검색이 2개만 반환 (sequential scan하면 10개 나옴). `REINDEX INDEX ix_embedding_vector_hnsw`로 수정. 자동화 미구현.

수정 후 hybrid search가 정상 동작 확인: score 0.0135~0.0164 범위로 차별화, 쿼리별 관련 fact가 상위에 올라옴.

#### 절대문서 vs 코드 전수 감사 — CRITICAL/HIGH/MEDIUM 이슈

코드 파일 전부 읽고 절대문서와 1:1 대조. 이후 DB에서 실제 데이터를 조회하여 "OK"로 판단한 항목들도 재검증.

**CRITICAL (검색/데이터 근본 결함)**

| # | 이슈 | 원인 위치 | 영향 |
|---|------|----------|------|
| C1 | Relations 0개 — 그래프 탐색 전체 무력화 | gap_extraction.py 프롬프트에 relation 추출 없음, worker.py가 StoreMemoryRequest에 relations 안 넘김, MCP 도구에 relations 파라미터 없음 | hybrid search의 graph_facts CTE가 항상 빈 결과 |
| C2 | Entity merge 0.85 티어 미동작 | store.py:186-188 — 로그만 찍고 새 엔티티 생성. 0.92도 사후 비교 안 함 | 1262개 엔티티 중 415쌍 중복 (cosine>0.85), 45쌍은 cosine>0.92 |
| C3 | fact의 82% (919/1118 활성)가 low_trust | worker.py:92 `transcript[:10000]` 잘림 → quote 검증이 잘린 텍스트에서 실행 → 10K 이후 위치의 quote 전부 MISS | source_quote 검증 시스템 사실상 무효화 |

**HIGH (설계와 다르게 동작)**

| # | 이슈 | 원인 위치 | 영향 |
|---|------|----------|------|
| H1 | covered_turn_indices 항상 빈 set | worker.py:42 `covered_turn_indices=set()` 하드코딩 | 모든 에피소드를 100% 미커버로 간주 |
| H2 | FTS에 엔티티명 미포함 | hybrid_graph_search SQL의 fts_facts CTE: `kf.object_value &@~ p_query_text OR kf.source_quote &@~` — `e.name` 없음 | "펀드메신저"로 검색해도 엔티티명 매칭 안 됨 |
| H3 | E5 임베딩 prefix 오용 | embedding.py:39 — 저장할 때도 `query:` prefix 사용. E5 모델은 저장용 `passage:`, 검색용 `query:` 분리 필요 | 벡터 검색 품질 최적 아님 |
| H4 | HNSW 리빌드 자동화 없음 | 대량 삽입 후 수동 REINDEX 필요. 워커 배치 처리 후 자동 실행 로직 없음 | 벡터 검색 결과 누락 가능 |
| H5 | MCP store_memory에 relations 파라미터 없음 | mcp_adapter.py:214-222 — REST API에는 있지만 MCP 도구 인자에 없음 | AI 클라이언트가 relation을 보낼 방법 없음 |
| H6 | NLI 오탐 가능성 | DB에서 cross-predicate supersede 5건 확인. "uses_workflow → commit_rate"는 의미적 모순 아닌데 supersede됨 | 정상 fact가 잘못 supersede될 수 있음 |

**MEDIUM (기능 부재/미완)**

| # | 이슈 | 비고 |
|---|------|------|
| M1 | soft decay 미구현 | recall에서 오래된 fact 순위 안 내려감 |
| M2 | initialize_memory Stage 2-4 미구현 | 최근 10건만 반환, importance 필터/미처리 경고/프라이밍 없음 |
| M3 | readOnlyHint 미구현 | recall/initialize에 설정 안 됨 |
| M4 | defense-in-depth 전체 미구현 | Stop hook, CLAUDE.md 템플릿, bootstrap priming 전부 없음 |
| M5 | Fragment keywords 빈약 | `[entity_name, predicate]` 두 개뿐. YAKE/GLiNER 경량 추출 미구현 |
| M6 | Auth/OAuth 비활성 | 의도적 — 배포 시 활성화 |

#### 데이터 품질 실측 (personal workspace)

| 지표 | 값 | 평가 |
|------|---|------|
| 에피소드 | 121개 (전부 done) | OK — 원본 보존 |
| 엔티티 | 1262개 | 과다 — 415쌍 중복 |
| 활성 fact | 1118개 (grounded 199, low_trust 919) | 82% low_trust — 쓰레기 |
| Relations | 0개 | 전멸 |
| Fragments | 1496개 (전부 fact 연결) | 구조 OK, keywords 빈약 |
| Embeddings | 3696개 (fact 2072, entity 1504, episode 120) | 있지만 prefix 오용 |
| 에피소드 >10K | 56/121 (46.3%) | 이 에피소드의 fact가 전부 low_trust |

#### 수정 방향 — 길 B (DB 밀고 재처리) 합의

- 에피소드 원본은 보존, 추출 결과(facts/entities/fragments/embeddings/relations)만 삭제
- 코드 버그 전부 수정 후 worker가 121 에피소드 재처리
- 사용량 제한(현재 12% 잔여) 때문에 즉시 실행 불가 — 사용량 회복 후 실행

#### 수정 필요 코드 목록 (다음 세션 인수인계)

| 파일 | 수정 내용 | 관련 이슈 |
|------|----------|----------|
| recall.py:45 | seed_array string→SQL 리터럴 삽입 | **수정 완료** |
| recall.py:232 | logger.warning→logger.exception | **수정 완료** |
| recall.py:234 | await db.rollback() 추가 | **수정 완료** |
| worker.py:92 | `transcript[:10000]` 제거 — 에피소드 전체 전달 | C3 |
| worker.py:89-109 | StoreMemoryRequest에 relations 추가 | C1 |
| worker.py:42 | covered_turn_indices에 실제 커버된 턴 전달 | H1 |
| gap_extraction.py:46-69 | 추출 프롬프트를 리서치 #1 Prompt A로 교체 (entities+facts+relations). 현재는 리서치 #4 blind extract 기반으로 facts만 추출 | C1 |
| gap_extraction.py 전체 | Haiku extraction → Sonnet reconciliation 2단계로 전환 (리서치 #4 설계). 현재 Sonnet 1단계 | 추출 품질 |
| store.py:186-188 | 0.85 구간에서 merge 실행 (로그만→실제 병합) | C2 |
| mcp_adapter.py:214-222 | store_memory에 relations 파라미터 추가 | H5 |
| embedding.py:39 | 저장 시 `passage:` prefix 사용 | H3 |
| store.py:663 | 저장용 임베딩에 `passage:` prefix | H3 |
| hybrid_graph_search SQL | fts_facts CTE에 e.name 추가 | H2 |
| worker.py 배치 완료 후 | REINDEX HNSW 자동화 | H4 |

#### 사용자 수정/지적 기록

- "저 세션에서 AI가 요약해놓은거 긁어온것처럼 보이는데" — 이전 AI 연구노트를 검증 없이 신뢰한 것 지적. 맞았음
- "니가 OK라고 한것들이 정말 문제없다고 확신할수있는지?" — 코드 읽기만으로 OK 판단한 것 재검증 요구. 실제 DB 조회하니 82% low_trust, 415쌍 중복 발견
- "원본 데이터 품질이 떨어져도 해결할 수 있어야 하는 거 아니야?" — 맞음. 절대문서 설계 기준으로 Path B + gap pipeline + quote 검증이 그 품질 보장 장치. 코드 버그 때문에 안 되는 거지 원본 문제 아님
- "이거 어디에 기록해놓지 않으면 잊지 않을까" — 연구노트에 전수 감사 결과 기록 (이 항목)
- "리서치에서 이렇게 해야 좋다고 나와있는데 왜 안 따라가고 있지?" — gap_extraction.py가 리서치 #1 Prompt A(entities+facts+relations)가 아니라 리서치 #4 Prompt A(facts만)를 기반으로 구현됨. relations 빠뜨림. Haiku+Sonnet 2단계도 리서치 #4 설계인데 구현 시 Sonnet 1단계로 축소됨. 의도적 변경이 아니라 구현 시 누락. **리서치 결과를 그대로 따라가면 됨.**
- **자비스의 궁극적 품질 기준**: "데스크탑 트랜스크립트 전부를 자비스에 넣으면, 아무것도 모르는 새 세션에서 AI가 recall만으로 타고타고 가서 세컨드브레인/아르고스/자비스 등 프로젝트의 리서치 노트 전체를 재구성할 수 있어야 한다." 이건 단순 fact 검색이 아니라 — 프로젝트 맥락, 의사결정 이유, 실패/수정 이력, 아이디어 흐름까지 recall로 복원 가능해야 한다는 의미. 시딩 = 온보딩이고, 온보딩 품질의 최종 검증 기준이 이것.

### Phase A~C 코드 수정 + Phase D 검증 실행

#### Phase A~C 수정 (별도 세션에서 실행, 이 세션에서 플랜 작성 + 검증)

- **Phase A**: recall 검색 경로 — H2(FTS에 e.name 추가), H3(E5 passage:/query: 분리), H4(HNSW 리빌드 자동화), recall.py 기존 수정 검증
- **Phase B**: store 데이터 경로 — C1(relations 추출 + 전달 + MCP 파라미터), C3(transcript 잘림 제거), H1(covered_turn_indices 주석), 추출 프롬프트를 리서치 #1 Prompt A로 교체, Haiku+Sonnet 2단계 전환
- **Phase C**: entity 품질 — C2(0.85 merge 실행 + aliases + 동기 name_embedding), H6(NLI cosine threshold 0.40→0.55 + 로깅 강화)

#### Phase D 검증 결과 — DB 밀고 6 에피소드 재처리

DB를 전부 밀고(에피소드 포함), 6개 에피소드를 새로 업로드:
- brain_096a9aa0 (111K, 자비스 구현)
- brain_69275bf1 (291K, 자비스 대형)
- brain_3a455274 (115K, 아르고스)
- brain_74c972f7 (19K, 세컨드브레인)
- fundmessenger_dc97204b (399K, 펀드메신저 대형)
- fundmessenger_5cedef29 (103K, 펀드메신저 중형)

**최초 결과 (6 에피소드, gaps[:20] 하드코딩 상태)**:

| 지표 | 수정 전 (121ep) | Phase D 최초 (6ep) |
|------|----------------|-------------------|
| Grounded 비율 | 20.8% | **95.5%** |
| Relations | 0개 | **27개** |
| Entity 중복 (>0.92) | 45쌍 | **0쌍** |
| Entity 중복 (>0.85) | 415쌍 | **0쌍** |
| Hybrid search | 100% 실패 | **정상 동작** |

**하지만 펀드메신저 관련 fact가 0개**. 399K + 103K 에피소드에서 fact가 각각 4개, 10개만 추출됨.

#### gaps[:20] 하드코딩 발견 — 리서치 설계 미준수

**원인**: worker.py:63의 `gaps[:20]`이 gap detection 결과에서 상위 20개 턴만 추출에 전달. 이건 리서치 #4의 설계에 없는 하드코딩. 리서치 #4의 decision logic은 recommendation에 따라 "full_extract"/"gap_fill"/"skip"으로 분기하고, 추출 턴 수를 비율로 조절하라고 되어 있음.

**문제의 본질**: gap pipeline은 "AI가 이미 store_memory로 대부분 커버한 후, 빠진 부분만 보완"하는 설계. 그런데 Path B(upload-transcript) 에피소드는 store_memory 호출이 0회 → 전체가 미커버 → 모든 user 턴이 "갭" → 그 중 20개만 추출 = 대부분 버려짐. **보완용 설계를 온보딩에 그대로 쓴 것이 문제.**

**추가 문제**: gap_extraction.py의 extract_from_gaps()가 모든 gap turns를 하나의 프롬프트로 합쳐서 claude -p에 보내는 구조. 20개 제한을 제거하면 수백 개 턴이 하나의 프롬프트에 → 컨텍스트 윈도우 초과/타임아웃.

**수정 (이 세션에서 직접 실행)**:
1. worker.py: `gaps[:20]` → `gaps.gaps` (제한 제거). 리서치 #4의 Stage 1-4 progressive filtering이 이미 volume control 역할.
2. gap_extraction.py: extract_from_gaps()에 청크 분할 추가. `max_chars_per_chunk=30000` 기준으로 gap turns를 분할, 각 청크를 독립적으로 Haiku에 전달, 결과 merge.

**재처리 결과 (103K 펀드메신저 에피소드)**:

| 지표 | gaps[:20] 상태 | 청크 분할 후 |
|------|--------------|------------|
| Facts | 10개 | **16개** (+60%) |
| Relations | 4개 | **12개** (+200%) |
| Grounded | 10/10 | **16/16 (100%)** |

**남은 과제**: 103K에서 fact 16개는 여전히 적을 수 있음. gap_detection의 Stage 1-4 필터링이 user 턴 중 substantive한 것만 10개로 걸러내고, 그 10개를 2청크로 나눠서 추출한 결과. gap_detection의 턴 파싱(plain text "User: " 패턴)이 전처리된 transcript 포맷을 제대로 파싱하지 못할 가능성도 있음 — 추후 조사 필요.

#### 사용자 수정/지적 기록

- "왜 상위 20개 턴만 보내는거?" — gaps[:20] 하드코딩의 근거를 물어봄. 리서치에 없는 임의 제한이었음 확인
- "미커버 턴이 200개여도 20개, 2000개여도 20개, 200000개여도 20개? 이게 맞나?" — 어떤 시나리오에서도 하드코딩이 말이 안 됨을 지적
- "이게 진짜 데이터의 양의 문제일까?" — 펀드메신저 fact 0개를 "데이터 양 부족"으로 넘기려 한 것에 대한 반론. 실제로는 코드 문제(gaps[:20])였음
- "리콜 결과가 완벽하지 않다고 했는데 사용자 누구야에 프로젝트 팩트가 섞여나온다 이게 데이터의 문제인가?" — recall 랭킹 품질 문제를 데이터 양으로 돌리지 말고 원인을 규명하라는 지적
- **assistant 턴 무시 문제 발견**: gap_detection Stage 1이 user 턴만 추출하고 assistant 턴(전체의 88%)을 전부 버림. 의사결정, 구현 내용, 발견된 문제가 assistant 턴에 집중되어 있어 대부분의 지식을 놓침. 리서치 #4의 "skip assistant turns" 전제가 실제 AI 코딩 세션 + 일상 대화 양쪽에서 맞지 않음. → **딥리서치 완료**: `2026-04-17-assistant-turn-extraction-filter.md`. Mem0(user-only) 방식이 아니라 Graphiti(speaker 대칭) 방식 채택. pair-level 추출(user+assistant 묶음) + 5-layer LLM-free 기계적 필터 설계.
- **사용자 발화의 암묵적 AI 참조 문제**: "그 방법은 별로였어"에서 "그 방법"이 AI의 이전 발화를 가리킴. user 턴만 추출하면 coreference가 해결 안 되어 fact가 무의미해짐. 대화 쌍(user+assistant)을 함께 봐야 의미 있는 추출 가능.

#### 자비스 프레이밍 — 작업 맥락의 외장 메모리 (사용자 방향성 논의)

Phase D 검증 중 "클로드 앱에서는 transcript가 로컬에 없는데 온보딩이 가능한가?"라는 질문에서 시작된 논의. 자비스의 정체성과 한계를 어떻게 프레이밍할지에 대한 사용자의 방향성.

**자비스의 본질 — "오늘 똥쌌어"를 저장하라고 만든 게 아니다**

자비스는 세컨드브레인 프로젝트의 핵심 엔진이다. 지금은 캡스톤 과제이기 때문에 MCP라는 배포 형식을 택했지만, 자비스의 본질은 **어떤 작업에 대한 맥락을 외장화하고, 어디서든 접근 가능한 맥락 서버를 통해 세션/기기/AI를 넘어서 보존되게 하는 것**이다. 세컨드브레인에서는 이걸 팀 온보딩에 활용할 수 있게 하는 것까지가 의의.

물론 사용자가 건강 케어를 목적으로 "오늘 똥 쌌다, 상태가 어땠다"를 기록하고 싶다면 명시적으로 자비스에 저장을 요청하면 된다. 하지만 자비스가 자동으로 축적하도록 설계된 지식은 **작업에서 생긴 의사결정, 맥락, 지식**이다.

**hookless 클라이언트(클로드 앱 등) — 앱에서도 가능하다, 단 방식이 다르다**

클로드 앱에는 transcript가 로컬에 없고, hooks도 없다. 하지만 사용자가 대화를 나눈 세션에서 "지금까지 대화 자비스에 기록해줘"라고 하면, **그 AI는 현재 세션의 맥락을 가지고 있으므로** store_memory를 호출해서 핵심 내용을 저장할 수 있다. 자동이 아니라 사용자가 요청해야 한다는 차이가 있지만, 아예 불가능한 건 아니다. 이건 절대문서의 "사용자 명시 저장은 실패가 아닌 기대되는 인터랙션 패턴"(아이디어 #16)과 같은 맥락.

**3티어 포지셔닝 — 결함이 아니라 티어 차이**

| 환경 | 자동화 수준 | 품질 |
|------|-----------|------|
| 코딩 에이전트 (Claude Code, Codex, Gemini CLI) | transcript + hooks → 자동 | 최고. **권장 환경** |
| 앱 (Claude, ChatGPT) | AI 자발적 호출 + 사용자 명시 요청 | 기본. 핵심은 커버 가능 |
| 세컨드브레인 (미래) | API 직접 호출, UI 직접 조작 | 도구 호출 의존 0% |

이렇게 프레이밍하면 "hookless 클라이언트에서 캡처율이 40-60%"가 결함이 아니라 **티어 차이**가 된다. "앱에서도 돼요, 근데 코딩 에이전트에서 쓰면 훨씬 좋아요"가 자연스러운 메시지.

**세컨드브레인 전환 시 도구 호출 의존 문제가 소멸하는 이유**

MCP에서는 AI의 자발적 도구 호출에 의존한다 — 이게 지금까지 가장 큰 설계 도전이었다 (3경로 캡처, defense-in-depth, Stop hook 등 전부 이 문제를 풀기 위한 것). 하지만 세컨드브레인에서 웹 앱이나 자동화 스크립트가 기계적으로 API를 호출하면, AI의 자발성에 의존하는 구조 자체가 사라진다. 자비스의 설계(store/recall이 HTTP API)가 이미 이걸 지원하는 구조이므로 추가 아키텍처 변경 없이 전환 가능.

**캡스톤 시연 전략**: "코딩 에이전트에서 시연"이 정당화됨. "앱에서도 기본 동작"은 보너스. 세컨드브레인 비전은 미래 확장 슬라이드에서 보여주면 됨.

---

## 2026-04-17

### 세션 1: assistant 턴 필터 설계 + 구현

#### 세션 인수 + 문서 전수 읽기

이전 세션 인수인계를 받고 절대문서, 두 신규 리서치(assistant 턴 추출 필터, 맥락 조립), 연구노트 04-16을 순서대로 전부 읽음. 첫 응답에서 핸드오프 프롬프트 요약을 그대로 복붙하고 "뭘 하고 싶으세요?"로 넘긴 것에 대해 사용자가 "문서를 제대로 읽은 게 맞냐" 지적. 실제로는 research-notes.md 35K 토큰 중 1K줄만 읽고 나머지 생략했음을 자인. 나머지 읽고 실제 이해한 내용으로 재응답.

사용자 지적: "문서들을 제대로 읽은게 맞음?" — 복붙 요약은 이해가 아니라 앵무새. 제대로 읽고 자기 관점으로 소화한 것을 말해야 함.

#### 우선순위 논의 — assistant 턴 필터 vs 맥락 조립

초기에 "assistant 턴 필터 먼저, 맥락 조립은 데이터 적어서 나중에"라고 추천. 사용자 반박:

- "검증 도구(recall)가 고장난 상태로 재처리 결과를 판단하는 게 의미 있냐"
- "맥락 조립 리서치가 '소규모 데이터도 처음부터 돌리면 됨'이라고 명시했는데 왜 데이터 핑계로 미루냐"

인정: 제 추천은 리서치 설계를 무시한 자의적 판단이었음. 두 리서치는 독립적(입력 쪽/출력 쪽)이고 둘 다 재처리 전에 들어가야 검증이 의미 있음.

#### 맥락 조립 리서치 설명 논의

사용자 요청으로 맥락 조립 리서치 내용을 "자세히" 설명. 처음에는 간략히 답해서 사용자가 "자세히라는 말은" 다시 지적. 두 번째 설명에서 Stage 1/Stage 2 구조, MMR + community-aware + adaptive K, 80% solution의 각 구성 요소, MCP 응답 포맷 변경 등을 완전히 풀어 설명. assistant 턴 필터도 동일 수준으로 자세히 재설명 (Mem0 vs Graphiti 철학 차이, 5-layer 기계적 필터, pair-level 추출 근거 등).

사용자 지적: 구체성 수준을 맞추지 않은 것. "간략히"가 아니라 "상대가 내 이해 수준을 알 수 있을 만큼 구체적으로" 답해야 함.

#### "자비스는 AI가 쓰는 위키" 관점 논의

사용자 질문: "a라는 파편이 30턴 뒤에 a'로 나오면 엮이나?" "같은 세션의 한국어/영어 표기가 엮이나?"

답변을 만들면서 재확인한 것:

1. 자비스 내부 연결 메커니즘(entity resolution, predicate resolution, supersede, NLI, graph search) 전부 "거리 무관"으로 설계됨. 30턴 떨어져도, 다른 세션이어도 같은 entity로 해소되면 연결.
2. pair-level 추출은 "연결" 문제가 아니라 "추출 품질" 문제. 턴 단독으로는 coreference 해결 불가 → pair로 묶어야 LLM이 의미 있는 fact 생성.
3. "포스트그래 이전 어떻게 했음?" 같은 한국어 축약은 별칭 사전(Stage 1)이 사전 정의해야 해소. 임베딩 + fuzzy로 완전 자동화 불가.

#### 자비스 내부 저장 구조 재확인

사용자: "우리 내부의 파편들이 어떤식으로 저장되는지 알 수 없다. 글을 의미를 가진 파편들로 나눠서..."

설명:
- 구조화된 그래프 (Entity + KnowledgeFact + EntityRelation): 명시적 노드+엣지. "JARVIS → uses_db → PostgreSQL" 같은 triple.
- 자연어 파편 (Fragment + Embedding): 의미 공간의 점. 시맨틱 검색용.
- 두 저장소는 같은 기억을 다른 축으로 인덱싱. recall은 3-way(벡터+FTS+그래프)로 둘 다 활용.

#### assistant 턴 필터 플랜모드 프롬프트 작성 및 검증

플랜모드 세션에 전달할 프롬프트 작성. 사용자가 구현 이전 세션들이 반복한 실수들(relations 누락, gaps[:20] 하드코딩, except 에러 삼킴, 코드 읽기만으로 OK 판정) 명시적으로 금지 조항으로 포함하게 함.

플랜모드가 작성한 플랜 검토 시 발견한 2건:

1. Layer 1 thinking 블록 regex가 단일 줄만 잡음(`^\[thinking\].*$`). 실제 전처리된 transcript의 thinking은 멀티라인. 해법 섹션은 멀티라인 분석했는데 코드에 반영 안 됨.
2. Layer 2 regex가 축약형(`r"...|..."` 중간 `|...`로 끝). 구현 세션이 자의적으로 완성할 위험.

수정 지시: "리서치 문서 2026-04-17-assistant-turn-extraction-filter.md를 읽고 플랜의 '추가 2/3/4' 코드가 리서치와 일치하는지 대조할 것. 특히 thinking 멀티라인, regex 전문 일치 여부." 이 지시가 효과 있어서 플랜모드가 `_strip_thinking_blocks()` 상태 머신 함수 작성 + regex 전문 복사 반영.

#### 구현 결과 검증

구현 완료 후 실측:

- 전처리된 brain_096a9aa0 (111K, 423 턴) 대상
- parse_transcript_turns: 423 턴 (user 42, assistant 381)
- assemble_pairs: 37 pair 조립
- 비교:

| 지표 | OLD (user only) | NEW (pair-level) |
|------|----------------|------------------|
| 추출 단위 수 | 38 turn | 37 pair |
| 총 글자수 | 16,479 | 92,983 |
| 콘텐츠 배율 | 1x | **5.64x** |

- Layer 1 효과: [thinking] 17개 → 0개, tool breadcrumb 168개 → 0개
- Layer 3 signal_boost 분포: boost=0(23), boost=1(8), boost=2(4), boost=3(2) — decision/discovery/correction 마커가 priority 상위로 자동 승격

#### 사용자 수정/지적 기록

- "문서를 제대로 읽은게 맞음?" — 복붙 요약은 이해가 아님
- "자세히라는 말은" — 구체성 수준 맞추기
- "recall이 진짜 고장인가?" — 추측 전에 실측으로 확인. 맥락 조립이 필수인지는 데이터로 판단
- "플랜모드를 사용하라니까" — 내가 적은 플랜을 플랜모드 세션에 전달해야 한다는 의미. 프롬프트 작성자와 구현자 분리
- "너가 직접 플랜모드 진입해서 플랜 작성해줘" — ExitPlanMode 도구로 플랜을 직접 제출할 수도 있으나 플랜 본문 자체가 중요하므로 텍스트로 구성

---

## 2026-04-18

### 세션 1: 맥락 조립 구현 + DB 정리 + 재처리

#### 맥락 조립 플랜모드 프롬프트 작성

이전 세션의 assistant 턴 필터 구현 완료 + 검증을 이어받아 맥락 조립(MMR) 플랜 작성.

현재 상태 조사: recall.py의 `_hybrid_search_sql`이 `request.limit`을 그대로 SQL 함수 `p_match_count`에 전달 → Stage 1 후보 수 = 최종 반환 수. MMR이 다양화할 여지 없음. 이걸 Stage 1 pool=100으로 고정 + Stage 2에서 MMR 10개 선택하는 2단계 구조로 변경 필요.

리서치의 80% solution 기준:
- Stage 1: 기존 hybrid search 그대로 (pool=100)
- Stage 2: community-aware MMR (λ=0.6, community bonus 0.05)
- Adaptive K: τ=0.1, K_min=3, K_max=20
- Leiden 알고리즘 오프라인 (배치 완료 시 REINDEX와 같은 타이밍)
- 응답 포맷 확장: structural_summary + coverage metadata + pagination_token

플랜모드 반환 검토에서 발견한 6가지 중 5개가 구현 세부 결정(sim_1 정규화 방식, structural_summary 생성, pagination_token 형식, adaptive K 비교 척도, embedding 없는 fact 처리). 자의적 판단 금지를 위해 플랜에 명시적 결정 포함 요청. 모두 반영 완료 후 구현 세션 인계.

#### 구현 완료 후 실행 순서

1. leidenalg + python-igraph 설치
2. 마이그레이션 `d4e5f6a7b8c9_add_entity_community_id` 적용
3. Leiden 수동 실행: 817 엔티티 → 516 커뮤니티 (상위 10개가 190 엔티티, 나머지는 파편)
4. 서버 재시작 (새 코드 로드)

여기서 중요한 교훈: 마이그레이션과 코드 수정이 끝나도 **서버를 재시작 안 하면 이전 코드가 메모리에 그대로 남음**. 이번 세션 후반에 이 문제가 또 발생(아래 참조).

#### 6 에피소드 재처리 (assistant 턴 필터 적용)

personal 워크스페이스의 Phase D 추출 결과 정리 후 재처리:

- DELETE entity_relations/fragments/knowledge_facts/entities/embeddings WHERE workspace_id = personal
- UPDATE episodes SET processing_status = 'pending'
- 워커가 자동으로 6 에피소드 순차 처리 (~3시간)

결과:

| 지표 | Phase D (OLD, user-only) | NEW (pair-level) | 배율 |
|------|-------------------------|------------------|------|
| Entities | 78 | 817 | 10.5x |
| Facts | 75 | 789 | 10.5x |
| Fragments | 75 | 789 | 10.5x |
| Relations | 35 | 370 | 10.6x |
| Embeddings | 181 | 1,741 | 9.6x |
| Grounded | 95.5% | **100%** | - |

assistant 턴 콘텐츠 포함만으로 추출량 10배. 콘텐츠 양 증가(5.64x)보다 추출 결과 증가(10.5x)가 큰 이유: assistant 턴의 콘텐츠 밀도가 user 턴보다 훨씬 높음 — 결정/발견/수정이 assistant에 집중됐다는 리서치 가정 실측 확인.

#### recall 품질 실측 (MMR 적용 상태) — 문제 발견

5개 쿼리로 검증:
1. "펀드메신저가 뭐야?" → 뉴스/SecondBrain 섞인 편향 결과
2. "자비스에서 recall이 왜 고장났었어?" → 무관 fact 상위
3. "아르고스 OOS 실패 원인?" → 일부 관련, 전반적으로 혼란
4. "세컨드브레인 사업계획서?" → 완전 무관
5. "자비스 프로젝트 전체 현황" → Argos가 상위 점유 (틀린 프로젝트)

MMR 자체는 동작(score range 2배 확장, community 3~5개 대표). 하지만 관련성이 낮음.

#### 사용자 지적: 데이터 검증 먼저

사용자: "너가 낸 결론이 정말 사실인가도 중요함. 트랜스크립트 자체가 질문에 답할 수 있었는가?"

에피소드 내용 재확인:
- d85634dc (19K): 세컨드브레인 사업계획서 **맥락 제공 논의** (사업계획서 자체 아님)
- 423cf0f5 (103K): 펀드메신저 **NewsPipelineService 기술부채 분석**
- b30db8e5 (111K): 자비스 폴더 문서 읽기
- 19c68c44 (115K): 아르고스 신규 개발 플랜
- 48688c49 (291K): 아르고스에 대한 질문
- b40755e7 (399K): 펀드메신저 전체 기술부채 분석

제가 던진 5개 쿼리 중 3개(펀드메신저가 뭐야 / recall 왜 고장 / 아르고스 OOS)는 **에피소드에 답이 없는 질문**이었음. 결론을 내기 전 데이터 검증 빠뜨림.

사용자 정정 후 재구성한 쿼리:
1. 펀드메신저 NewsPipelineService의 기술부채 문제는?
2. 아르고스를 왜 개발하려고 하는가?
3. 자비스 구현에서 무엇부터 해야 하는가?
4. 세컨드브레인 사업계획서에 필요한 정보 구조?
5. 자비스 프로젝트 전체 현황

답이 존재하는 질문들로 재테스트 결과: 여전히 관련 fact가 top에 안 올라옴. MMR 문제 아닌 게 명확해짐.

#### Stage 1 진단

hybrid_graph_search에 직접 쿼리를 던져서 pool 100개 안에 관련 fact가 있는지 확인:

- Query 1 "NewsPipelineService 기술부채": NewsPipelineService 관련 2개 있지만 rank 22, 34 (MMR이 top 10에서 절대 못 뽑음)
- Query 3 "자비스 구현": JARVIS 관련 fact **0개**
- Query 4 "세컨드브레인 사업계획서": SecondBrain 관련 1개뿐

즉 MMR이 좋아도 Stage 1 pool에 관련 fact가 못 들어오면 의미 없음. **Stage 1 recall 품질이 근본 문제**.

#### 크로스링구얼 논의 (사용자 통찰)

사용자: "자비스는 AI가 쓰는 위키. MCP 사용자가 AI니까 AI가 영어로 쿼리를 보내면 되지 않나?"

이 통찰 실측: 같은 쿼리를 영어로 던져봄 (e.g., "fundmessenger NewsPipelineService technical debt issues"). 여전히 무관한 fact 상위. `SecondBrain.must_remove_technical_jargon = pgvector, GraphRAG, ...` 같은 긴 object_value가 기술용어 grab-bag으로 여러 기술 쿼리의 top 점유.

즉 크로스링구얼 가설 기각 — 영어로 바꿔도 품질 나쁨. 더 깊은 문제가 있음.

#### 문서-코드 정합성 조사 — 거대한 갭 발견

사용자 요청으로 절대문서 vs 코드 대조:

| 설계(문서) | 실제 구현 |
|-----------|----------|
| Fragment = 300자 자연어, rich keywords, importance | `{entity} {predicate} {object}` triple 복사본, keywords 2개, importance 0.7/0.4 하드코딩 |
| Fragment 임베딩 → 시맨틱 검색 | embeddings 테이블 source_type='fragment' **0건** |
| `final_score = rrf_score × importance × e^(-λ × days)` | 순수 RRF, decay/importance **적용 0** |
| NFKC 정규화 + 별칭 사전 (Stage 1) | entity_resolution에만, recall 쿼리 전처리 **없음** |
| aliases 컬럼 활용 | 0.85 merge에서만 추가 (크로스링구얼 cosine 0.41이라 발동 불가) + recall에서 사용 **0** |

**중요**: 설계가 틀린 게 아니라 설계의 절반만 구현됨. aliases/Fragment/decay가 각각 "슬롯"만 있고 채우는 로직이 없거나, 채워도 읽는 로직이 없음.

#### PGroonga FTS 자연어 쿼리 실패 발견

실측:
- "NewsPipelineService 기술부채" → FTS **0건**
- "자비스 구현" → FTS **0건**
- "자비스" 단독 → 1건
- "JARVIS" 단독 → 6건

PGroonga `&@~` 연산자는 pgroonga 쿼리 문법 모드. 여러 단어가 들어오면 암묵적 AND로 처리되어 거의 매칭 실패. 자연어 쿼리 그대로 보내면 FTS 죽음.

#### Recall 품질 수복 플랜 작성 및 구현

절대문서의 설계대로 메워야 할 갭이 명확함. 리서치 불필요, 문서 따라 구현. 9파일 수정하는 플랜 작성:

**Phase 1** (쿼리 전처리):
- `query_preprocessing.py` 신규: NFKC + 파티클 제거 + 별칭 확장 + OR 쿼리 구성
- `entity_resolution.py`: CROSS_LINGUAL_ALIASES 추가 (자비스↔JARVIS 4쌍)
- `recall.py`: preprocess_query 통합, SQL에 p_fts_query 분리 전달
- Migration `e5f6a7b8c9d0`: SQL 시그니처 11파라미터 (DROP + CREATE), vector_facts를 fragment 기반으로 전환

**Phase 2** (Fragment 제대로):
- `store.py`: Fragment content = source_quote (≥10자), keywords 20개 cap
- Fragment 임베딩 자동 생성 + 기존 789개 백필

**Phase 3** (Soft decay):
- Migration `f6a7b8c9d0e1`: knowledge_facts.last_accessed_at 컬럼 + SQL 함수 재생성 (last_accessed_at 반환)
- `context_assembly.py`: `_compute_final_scores` + 반감기 사전 (preference=120/decision=90/fact=60/procedure=30)
- recall에서 선택된 fact last_accessed_at = NOW() 갱신

**Phase 4** (정리):
- worker.py에 orphan embedding 정리 훅 추가

플랜모드 검토에서 발견한 4건 수정 지시 (Migration DROP 누락, last_accessed_at 반환 누락, _extract_keywords private, normalize_name 주석). 모두 반영 후 구현 인계.

#### 구현 완료 후 실측 — 서버 재시작 누락 사고

구현 완료됐다고 해서 5개 쿼리 실행. 모든 쿼리의 score가 **정확히 1.0000**. 곧바로 이상 감지.

원인 규명:
- score 1.0은 `_fallback_search`의 하드코딩 값
- 즉 hybrid_graph_search가 실패해서 ILIKE로 떨어짐
- 서버 로그 확인: `UndefinedFunctionError: function hybrid_graph_search(uuid, unknown, vector, uuid[], unknown, numeric, numeric, numeric, integer, unknown) does not exist` (10 파라미터 시그니처)
- DB 함수는 11 파라미터(p_fts_query 포함)로 migration 되어 있음
- 서버 프로세스는 PID 7680, 시작 시간이 마이그레이션/코드 수정 **이전**

해결: 서버 재시작 → 새 recall.py 로드 → 정상 동작.

교훈: **마이그레이션/코드 수정 후 서버 재시작은 필수 체크리스트 항목.** 이번이 두 번째 발생(이전 assistant 턴 필터 구현 때도 동일).

#### 서버 재시작 후 실측 — 극적 개선

| Query | Before (Phase 1-4 이전) | After (Phase 1-4) |
|-------|------------------------|------------------|
| 1: NewsPipelineService 기술부채 | 0 top 10 진입 | **4개 진입** (has_transaction_boundary_issue 등) |
| 2: 아르고스 왜 개발 | Argos 일부, 섞임 | Argos/Binance/Kovacevic #1~5 독점 |
| 3: 자비스 구현 시작점 | JARVIS 0개 | JARVIS.has_mvp_completion_timeline #5 등 |
| 4: 세컨드브레인 사업계획서 | 비즈니스 fact 0 | 심사위원/매출/마진/86% 마진 #1~5 |
| 5: 자비스 전체 현황 | Argos가 #1 (틀린 프로젝트) | JARVIS #1, #3, #8, #9 |

Score range도 0.015~0.017 → 0.02~0.034로 2배 확장. communities_represented 3~6개 (다양성).

#### Fragment content 백필 논의

신규 fragment만 source_quote로 저장되고 기존 789개는 여전히 triple 형식. 임베딩도 triple 기반으로 생성됨.

사용자 질문: "추출한 거 지우고 다시 넣느냐?"

답: 아님. SQL UPDATE 한 번으로 충분. `UPDATE fragments f SET content = kf.source_quote FROM knowledge_facts kf WHERE f.source_fact_id = kf.id AND kf.source_quote IS NOT NULL AND length(trim(kf.source_quote)) >= 10`. 그 다음 fragment 임베딩 삭제 + 백필 함수 실행. claude -p 크레딧 0원.

실행: 781 fragment content 업데이트, 789 embedding 재생성.

#### 백필 후 재실측 — 결과 혼재

- 일부 쿼리 개선 (Q4의 `chosen_as_yechuangpae_item` 새로 top 진입, Q5의 JARVIS 추가 증가)
- 일부 쿼리 약간 이동 (Q4에서 이전에 top이었던 매출/마진 fact 탈락)
- 종합: 어느 쪽이 확실히 더 좋은지 실측으로는 판단 어려움

원인 해석:
- Triple 임베딩(이전): "entity predicate object" 토큰을 인코딩 — 구조적 predicate 쿼리에 강함
- source_quote 임베딩(현재): 원문 맥락을 인코딩 — 자연어 쿼리에 강함

AI가 사용자 표현 그대로 쿼리를 보낼 가능성이 높다면 source_quote가 유리. AI가 구조화된 개념으로 쿼리 재작성한다면 triple이 유리. 실사용 패턴 관찰 필요.

#### 비전 명료화

사용자: "내가 원하는 방향은 명확함. 모든 대화를 DB에 올린다. AI 클라이언트가 빠르고 정확하게 필요한 정보만 검색할 수 있게 한다. 토큰 절감. 어디서든 어떤 AI든 접근."

이 비전에서 자비스의 정체성 = **"AI 전용 위키(효율적 검색 DB)"**. "AI가 스마트하게 대답 재구성" 같은 복잡한 프레이밍 불필요.

현재 구현이 이 비전을 얼마나 달성:
- 빠른 검색: Stage 1 + MMR 100ms 이하 ✅
- 정확한 검색: Phase 1-4 후 대폭 개선 ✅
- 필요한 것만: adaptive K (top K 3~20) ✅
- 컨텍스트 절감: 응답 크기 바운드됨 ✅
- 어디서든/어떤 AI든: MCP 표준 프로토콜 (OAuth 미활성이라 로컬만) ⚠️
- 모든 대화 자동 축적: Path B 일회성 업로드만 (Stop 훅 미구현) ⚠️

#### 지형도(topic map) 도구 아이디어

사용자 제안: "바로 질문 던지지 말고 먼저 지도 반환받고 그 후 필요한 것만 세부 쿼리."

흐름:
```
User 질문 → AI → explore_topic(쿼리) → 지형도 (엔티티/커뮤니티/predicate/시간범위)
                                    ↓
                         AI가 "이것만 필요"라고 판단 → recall_memory(구체 쿼리)
```

이 패턴 = hierarchical retrieval / GraphRAG community summary / two-stage RAG. 학술적으로 검증됨. 현재 인프라(community_id, entity 그래프, valid_from)가 다 있어서 구현 부담 작음.

구현 방향: 새 MCP 도구 `jarvis_explore_topic`. 쿼리 → 상위 엔티티 + 커뮤니티 분포 + 주요 predicate + 시간 범위 반환. fact 세부 없음. 토큰 절감 효과 직접적.

주의할 데이터 특성: 현재 personal 워크스페이스는 516 커뮤니티/817 엔티티로 파편화 심함. 커뮤니티 단순 그룹핑만으로는 지형도가 overwhelming할 수 있음. 플랜에서 4가지 대응 옵션 제시.

플랜모드 프롬프트 작성 완료. 구현은 다음 세션으로 미룸.

#### 사용자 수정/지적 기록

- "결론을 내기 전 데이터 검증" — 재처리 후 관련성 낮은 결과를 MMR 문제라고 판단했으나, 쿼리 자체가 에피소드에 답 없는 경우였음. 먼저 데이터 확인.
- "크로스링구얼 리서치 필요?" — 영어 쿼리 테스트 후 품질 여전히 나쁨 확인. 진짜 문제는 더 깊은 곳(Stage 1 FTS 자연어 실패)에 있음. 리서치 불필요로 결론.
- "이미 청사진이 있는 거 아니야?" — 설계-코드 갭 발견 후 "어떻게 다뤄야 하나" 묻자 사용자 지적. 문서가 이미 답 갖고 있음. 새 리서치 없이 구현하면 됨.
- "서버 재시작 누락" — 마이그레이션/코드 수정 시 체크리스트 필요. 두 번째 발생.
- "데이터 탓 전에 코드 의심" — 04-16부터 반복. 이번에도 Stage 1 품질 문제를 "데이터 양 부족"으로 넘기려 함.

### 세션 2: 지형도 구현 + 품질 진단 + multi-project 리서치 + 비전 재정립

#### 지형도(explore_topic) 구현 + 검증

플랜모드 2라운드(초안 → 6건 수정 지시 → 최종)로 플랜 확정. 열린 결정 3건(엔티티 정렬 키, workspace_fact_count 포함, private rename)을 전부 명시적으로 결정 — "완벽한 플랜"으로. 구현자에게 자의 판단 여지 제거.

구현 완료 후 실측:
- 5개 쿼리 모두 정상 응답
- 응답 크기: recall_memory(10~12KB) 대비 explore_topic(2.5~2.6KB) — **22~27% 크기**. 목표 30~50% 대비 초과 달성
- distinct_communities 6~14개, isolated 2~10개

하지만 랭킹 품질은 MVP 수준. 관찰된 문제:
1. `max_rrf desc` 우선 정렬 — pool에 fact 1~2개만 있어도 그 중 rrf가 높으면 상위. "자비스 전체 현황"에서 OAuth 2.1(pool=1)이 JARVIS(pool=2)보다 #1
2. 크로스 프로젝트 엔티티 혼재 — "자비스 구현" 쿼리에 fundmessenger/Argos가 상위 등장

사용자 지적: "6 세션이 전부인 사용자도 가능. 자비스 물어봤는데 펀드메신저가 올라오면 어캄?" — 데이터 규모 문제가 아니라 구조적 결함.

#### 쿼리 토큰 매칭의 근본 오류 발견

사용자: "자비스 구현 어디까지 했는지라는 말이 원문에 있을 리가 없잖아"

현재 `query_preprocessing`이 쿼리 전체를 토큰화해서 "자비스 OR JARVIS OR 구현 OR 어디까지 OR 했는지"로 OR 매칭. 저장된 원문에 "어디까지" "했는지" 같은 의문 어미가 있을 리 없는데도 매칭 시도 중.

이건 **자연어 쿼리 파싱에 대한 근본 오해**:
- 의문 어미("어디까지", "왜", "언제")는 질문 구조일 뿐 검색어가 아님
- 쿼리의 본질은 **앵커(무엇에 대해) + 의도(어떤 정보)**이지 단어 자체가 아님
- 저장된 fact는 chunked triple이지 사용자 문장 원문이 아님

Phase 1 구현 당시 PGroonga `&@~`가 AND 기본이라 자연어 쿼리에서 0건 매칭 → OR로 바꿨는데, pendulum을 반대 극단으로 밀어버린 꼴.

#### MCP 계약 인식 — "AI가 자비스에 질문한다"

사용자: "사용자가 AI 클라이언트에게 질문하고 AI 클라이언트는 사용자의 질문에 대답할 수 있게 자비스에 질문을 한다는 걸 명심해야함."

이 구조적 인식이 쿼리 처리 철학을 바꿈:
- 자비스가 받는 쿼리는 **AI가 이미 가공한 것**, 사용자 원문이 아님
- "어디까지" 같은 의문 어미가 자비스에 올 리 없음 (AI가 제거하고 보냄)
- 자비스는 **"AI가 보낼 법한 쿼리" 기준으로 최적화**하면 됨
- 사용자 자연어 이해는 AI 클라이언트 책임

즉 자비스는 natural language processor가 아니라 **structured retrieval API**. MCP tool description이 AI 행동의 실제 계약.

#### Multi-project retrieval 리서치 결과

9개 production 시스템(Mem0, Zep/Graphiti, Letta, Cognee, Mem.ai, Glean, NotebookLM, Reflect, Obsidian Copilot) + 학술(MES-RAG, NodeRAG, HippoRAG 2, Leiden 2019, pgvector 0.8) 조사.

**핵심 결론**:
- 현재 실패는 데이터 규모·Leiden 파편화 때문이 아니라 **쿼리 시점에 scope를 강제하는 구조적 필터 부재** 때문
- Leiden singleton 51%는 증상, 원인은 그래프 sparsity (평균 차수 1.39)
- 7/9 시스템이 "명시적 project_id 태깅 + WHERE 하드 필터" 패턴

**리서치 권고 (A+B 조합)**:
- A. Entity-anchored retrieval (Aho-Corasick 별칭 사전)
- B. Project partitioning (project_id 컬럼 NOT NULL)
- 둘 다 벡터/FTS/그래프 세 경로에 hard filter 적용

**리서치가 배제한 것**:
- Microsoft GraphRAG global search (LLM 필수)
- LightRAG (NQ/PopQA에서 vanilla RAG보다도 나쁘다고 HippoRAG 2 비판)
- LLM-based query routers (LlamaIndex SelfQueryRetriever)
- Letta의 agent-as-scope (특정 에이전트 종속)
- 자동 project 추론 (소규모 데이터에서 학습 불가)
- 서버 cross-encoder rerank 기본 (ARM64에서 200~400ms 부담)

**기술 전제조건**:
- pgvector 0.8의 `hnsw.iterative_scan='relaxed_order'` — 과거 버전에선 post-filter로 결과 떨어짐
- HippoRAG PPR damping=0.5 (웹 기준 0.85 아님, 소규모 KG에서 노이즈)
- `ahocorasick_rs` Rust 구현 (<1ms, GIL-free)

#### 리서치 결과 재평가 — project_id에 대한 의심

리서치가 명시적 project_id 강력 권고했지만 사용자가 검증:

"근데 그게... 맞나?"

재검토 결과 의심 지점:
1. **"외장 뇌/AI 위키" 비전과 충돌** — 위키는 폴더가 아니라 하이퍼링크로 연결된 그래프. project 파티션은 이 성격과 어긋남
2. **프로젝트 경계 실제로는 모호** — 원고 집필, 일상 대화, 자비스↔세컨드브레인 cross-reference 등을 어느 프로젝트로?
3. **"JARVIS.is_core_engine_for=SecondBrain" 문제** — 이 fact는 어느 프로젝트? 둘 중 하나로 강제하면 다른 쪽 쿼리에서 안 나옴
4. **리서치의 코딩 use case 편향** — 인용한 7개 시스템 대부분이 "사용자/조직/워크스페이스 격리"이지 "프로젝트 파티션"이 아님 (Mem0 user_id, Zep group_id, Letta agent_id)
5. **리서치 스스로 인정한 한계** — 앵커 없는 broad 쿼리는 project_id 해도 해결 안 됨. 부분 해결

**자비스 workspace 개념이 이미 "사용자/조직" 격리 역할**. 그 안에서 project로 더 쪼개는 건 overkill.

#### 대안 — 엔티티 앵커링만 강하게

프로젝트 태깅 없이도 가능:
- Aho-Corasick으로 "자비스" → JARVIS entity 매칭
- Stage 1 검색을 JARVIS entity + 1~2홉 이웃으로 제한
- 벡터/FTS 후보 중 이 엔티티 집합에 포함되는 것만 유지
- 외부는 자연히 배제 (project_id 필터 없이도)

**그래프 구조 자체가 자연스러운 scope**. 별도 태깅 없음. cross-reference("JARVIS is_core_engine_for SecondBrain")는 관계 타고 넘어가서 자연스레 포함 가능.

#### 핵심 프레임 전환 — "검색 시스템 → 항해 가능한 지식 그래프"

사용자 제안: "AI 클라이언트가 자비스에 접속해서 원하는 것을 찾아낸다 이런 생각으로 가면 좀 더 명확하게 방향이 정해지지 않을까"

이 관점이 설계 철학을 완전히 바꿈:

**기존(암묵)**: 쿼리 → JARVIS가 정답 가까운 거 찾아줌 → AI가 답변 조립
**새**: AI가 JARVIS에 접속 → 지형도 스캔 → 엔티티 타고 탐색 → 필요한 거 수집 → 답변 조립

위키 사용 패턴과 동일:
1. 검색창 (explore_topic)
2. 문서 안 하이퍼링크 (엔티티 탐색)
3. 관련 항목 (related_entities)
4. 편집 이력 (supersede history)

이 프레임이 알려주는 설계 원칙:
1. **도구 수는 늘어도 된다** — 각 항해 단위별로 분리하면 혼동 없음. 4개 고정 원칙 완화
2. **응답 구조가 다음 이동을 암시** — related_entities에 relation type + fact_count 포함
3. **서버는 그래프 항해 primitive만 제공** — 자연어 이해는 AI 책임
4. **엔티티 앵커링이 이 철학의 핵심 pillar** — 그래프가 scope, 프로젝트 태그 불필요

#### 결정된 방향성

**Phase 1 (즉시)** — 리서치 결과 재해석:
- Entity-anchored retrieval만 (project_id 태깅 배제)
- Aho-Corasick 별칭 사전 + 하드 필터 (단 project_id 아닌 entity_id 기반)
- pgvector 0.8 업그레이드 (HNSW post-filter 문제 회피)
- recall_memory 응답의 related_entities 강화 (relation type + fact_count)

**Phase 2 (중기)**:
- `entity_detail` 도구 추가 (특정 엔티티 깊이 탐색)
- `follow_relation` 도구 추가 (관계 경로)
- 그래프 densification (cosine>0.8 동의어 엣지로 평균 차수 4 목표)
- Shallow PPR (damping=0.5, 2~3 iter, degree-normalized)
- 비-LLM query classifier (broad query 경로 분기)

**Phase 3 (장기)**:
- GLiNER auto alias expansion (ingestion only, 쿼리 hot path는 Aho-Corasick만)
- Hierarchical Leiden (multi-level 커뮤니티)

**배제 유지**:
- 서버 측 LLM rerank (사용자 제약)
- project_id 명시적 태깅 (비전 충돌, workspace가 이미 역할)
- 자동 project 추론 (소규모 데이터 불가)

#### 사용자 수정/지적 기록 (세션 2)

- "이게 이상하잖아" — "자비스 어디까지 했는지"를 토큰 매칭으로 검색한다는 발상 자체의 모순 지적. 의문 어미는 검색어가 아님
- "AI 클라이언트가 자비스에 질문한다" — MCP 계약의 본질 환기. 자비스는 자연어 parser가 아니라 structured API
- "project_id... 맞나?" — 리서치 강력 권고에 대한 건전한 의심. 자비스 비전(외장 뇌/AI 위키)과 rigid 파티션의 충돌 포착. workspace가 이미 격리 역할 수행
- "AI가 접속해서 찾아낸다" — 검색 시스템 → 항해 가능한 지식 그래프 프레임 전환. 설계 철학의 중심 axis 변경
- "어차피 더 궁금하다면 전달받은 지형도로 더 살펴보지 않을까?" — 항해 패턴이 자연스러운 여러 번 호출을 유도, AI agency 존중

이 세션은 "완벽한 플랜 → 실측 → 문제 발견 → 리서치 → 리서치 결과 의심 → 비전 재정립"의 긴 수정 사이클. 리서치 결과를 맹목 수용하지 않고 비전 기준으로 재평가한 것이 핵심 decision point.

---

## 아이디어 인덱스

| # | 아이디어 | 발생 시점 | 기존 연구 |
|---|---------|----------|----------|
| 1 | 클라이언트 구조화 + 서버 검증 (LLM 비용 0) | 03-26 | Zep retrieval path 영감, 완전 LLM-free는 없음 |
| 2 | 트리플 트리거 (토픽전환 + 5턴 폴백 + 이벤트) | 03-31 | "when you learn something new" 프레이밍이 고정 간격보다 우수 |
| 3 | source_quote grounding (fabrication 98.9% 제거) | 03-31 | AGREE 프레임워크 기반 |
| 4 | 3경로 캡처 (Stop훅 + Episode자동 + 세션복구) | 04-02 | Memento 비교에서 도출, AI 도구 호출 의존 탈피 |
| 5 | 이중 저장소 (Fragment + KnowledgeFact) | 04-02 | Graphiti 3-tier에서 영감 |
| 6 | 계층적 모순 탐지 (predicate→NLI→entailment) | 04-02 | nli-deberta-v3-xsmall 22M params, 87.77% 정확도 |
| 7 | initialize_memory → AI가 hooks 자동 설정 (도구 호출 의존 탈피) | 04-14 | Memento도 미해결. Claude Code 파일시스템 접근을 활용한 우회 |
| 8 | Stop 훅 decision:block으로 store_memory 결정론적 강제 | 04-14 | 딥리서치에서 발견. 코딩 에이전트 85-95% 신뢰성 |
| 9 | transcript_path로 훅에서 전체 대화 접근 가능 | 04-14 | 모든 Claude Code 훅 이벤트에 전달됨 |
| 10 | bootstrap 패턴 (initialize_memory 응답에 행동 프라이밍) | 04-14 | hookless 클라이언트의 유일한 강화 메커니즘 |
| 11 | 듀얼 패스 아키텍처 (훅 강화 + instruction 기반) | 04-14 | 코딩 에이전트와 앱 클라이언트 모두 커버 |
| 12 | defense-in-depth 레이어 스태킹 (10%→80%) | 04-14 | 단일 레이어는 불충분, 스태킹이 핵심 |
| 13 | N턴마다 넛지 패턴 (매 턴 아닌 3턴마다 block) | 04-14 | 무한루프 방지 + 오버헤드 감소 |
| 14 | Tool Search 지연 로딩 대응 (CLAUDE.md가 의도 생성) | 04-14 | v2.1.9+ 도구 설명이 컨텍스트에 없을 수 있음 |
| 15 | 도구 네임스페이스 (jarvis_ 접두사) | 04-14 | 멀티 MCP 서버 환경에서 충돌 방지 |
| 16 | 사용자 명시 저장은 실패가 아닌 기대 패턴 | 04-14 | hookless 클라이언트 40-60% 천장의 보완 |
| 17 | transcript_path → unreflected 세션 결정론적 감지 | 04-14 | 훅이 transcript 올리면 서버가 미정리 세션 감지 가능. Path C 결정론화 |
| 18 | 보완 파이프라인 — 서버 LLM으로 미처리 구간만 갭 채우기 | 04-15 | "비용 0" → "비용 최소화". 클라이언트 80% + 서버 보완 20%. Memento/Zep 대비 1/5 비용 |
| 19 | Transcript 시딩 — 기존 대화로 초기 지식 베이스 구축 | 04-15 | 91세션 813MB 중 유효 텍스트 4.3MB. Sonnet $5-7. 콜드 스타트 해결 |
| 20 | 토픽 기반 정보 군집 — "오늘 할 거" → 컨텍스트 자동 조립 | 04-15 | Entity + 연결된 사실/Fragment가 군집. 세컨드브레인 미래 비전 |
| 21 | AI 클라이언트가 곧 어시스턴트 레이어 — 별도 레이어 불필요 | 04-15 | recall 결과를 AI가 종합/제안하는 것 자체가 "자동 조립 + 제안" |
| 22 | 독립 추출 + 사후 병합이 순차 처리보다 우수 | 04-15 | context rot으로 누적 state 10K+ 시 품질 하락. #1과 #3 리서치 충돌 → #3이 이김 |
| 23 | source_quote 사후 검증이 프롬프트 최적화보다 ROI 높음 | 04-15 | Mem0 97.8% 쓰레기가 검증 없이 발생. 3중 보장: 프롬프트+사후검증+거부 |
| 24 | refinement ≠ contradiction — refines edge로 별도 처리 | 04-15 | "MCP 서버"→"MCP+HTTP"→"4도구"는 모순 아닌 점진적 구체화 |
| 25 | 대형 세션 자동 분할 (3000턴, user 경계) | 04-15 | git 업로드 + 추출 윈도우 동시 해결. 토픽 분할은 LLM 필요해 전처리에서 과함 |
| 26 | `claude -p` CLI 파이프 모드로 Extra Usage 과금 활용 | 04-15 | anthropic SDK 불가 → CLI subprocess 호출로 우회. OpenClaw과 동일 방식. 밴 위험 0 |
| 27 | `--json-schema`로 구조화 출력 보장 | 04-15 | 서버사이드 constrained decoding. `structured_output` 필드로 파싱된 JSON 직접 반환 |
| 28 | canonical entity list 누적으로 세션 간 일관성 | 04-15 | 순차 처리 시 이전 세션의 entity를 프롬프트에 포함. 독립 추출이지만 entity naming은 일관 |
| 29 | 턴 그룹 단위 증분 시딩 = 실사용 시뮬레이션 | 04-15 | 5턴씩 recall→extract→store. source_quote 정확도 50%→100%, predicate 일관성 확보, supersede 동작 |
| 30 | existing_facts를 프롬프트에 포함하면 predicate 재사용 유도 | 04-15 | entity만 넘기면 predicate 제각각. fact까지 보여주면 모델이 동일 predicate 재사용 |
| 31 | 프로덕션 온보딩은 Batch API 전환 필요 | 04-15 | 턴 그룹마다 개별 호출은 느림. Batch API면 한번에 제출 + 50% 할인 |
| 32 | recall 결과에 카테고리 메타 정보 포함 | 04-16 | "entity에 대해 총 N개 fact 중 상위 10개, 카테고리: 동아리관리(15), AI(20)..." → AI가 넓게 탐색 가능 |
| 33 | 온보딩 = Path B + 보완 파이프라인 (서버 내부) | 04-16 | 시딩 스크립트(서버 밖)가 아니라 Episode 업로드 → 서버가 보완 추출. 일반 사용자와 동일 흐름 |
| 34 | 작업 큐 + 단일 워커로 백그라운드 처리 | 04-16 | Episode.processing_status 컬럼 + lifespan 워커. asyncio.create_task 동시 폭발 방지 |
| 35 | recall = "맥락 조립기" — 파편 조합 점수 최적화로 최적 부분집합 반환 | 04-16 | AI가 결과를 단정하는 문제 해결. flat list가 아닌 조합 단위. 2^N 조합 중 최고점 근사 = NP-hard → 효율적 근사가 핵심 연구 과제. bundle recommendation과 동일 문제 구조 |
| 36 | pair-level 추출 단위 — user+assistant 인접쌍 | 04-17 | Graphiti speaker-symmetric 모델. coreference ~95% 해결. turn-level의 "그 방법" 참조 실패 문제 해결 |
| 37 | 5-layer 기계적 필터 (LLM-free) | 04-17 | Stage 1 block/sentence/signal/pair assembly/decision tree. Claude scaffolding(sycophancy/preamble) 90%+ 제거. downstream Haiku+Sonnet이 semantic 필터 역할 |
| 38 | community-aware MMR with Leiden | 04-18 | 오프라인 Leiden (배치 완료 훅에서) → community_id 태깅. MMR에서 unrepresented community에 +0.05 bonus. 파편화 심하면 embedding fallback이라 손해 없음 |
| 39 | adaptive K — marginal gain ratio τ=0.1 | 04-18 | 좁은 질문 K≈3-5, 넓은 질문 K≈12-18 자동 조절. K_min=3, K_max=20. 쿼리 분류 불필요 |
| 40 | PreprocessedQuery — NFKC + 파티클 제거 + 별칭 확장 + OR 쿼리 | 04-18 | 저장 측 normalization과 대칭. "의/는/이/가" 한국어 파티클 스트립. PGroonga `&@~`가 AND 기본이라 OR 명시 필요 |
| 41 | Fragment content = source_quote | 04-18 | 추출된 triple(entity/predicate/object)은 응답 데이터에 이미 포함. Fragment 임베딩은 응답에 없는 축(원문 대화 맥락)을 인덱싱해야 의미 있음. triple 중복 인덱싱보다 자연어가 쿼리 표현과 가까움 |
| 42 | CROSS_LINGUAL_ALIASES 사전 | 04-18 | 자비스↔JARVIS, 세컨드브레인↔SecondBrain 등. 임베딩 cosine 0.41이라 entity resolution threshold 0.75에 못 미침 → 수동 사전 필수. 실측 크로스링구얼 쌍만 (투기적 확장 금지) |
| 43 | Soft decay 구현 — last_accessed_at + 반감기 사전 | 04-18 | final_score = rrf × importance × 0.5^(days/half_life). preference=120/decision=90/fact=60/procedure=30. recall 시 선택된 fact만 last_accessed_at 갱신 |
| 44 | 지형도(topic map) 도구 — 2단계 탐색 | 04-18 | hierarchical retrieval / GraphRAG community summary 패턴. 1단계 엔티티/커뮤니티 맵 → 2단계 세부 recall. 토큰 절감 직접적. 학술 검증됨 |
| 45 | "자비스 = AI 전용 위키" 비전 명료화 | 04-18 | AI 클라이언트가 빠르고 정확히 필요한 정보만 검색 → 토큰/컨텍스트 절감. 복잡한 "AI 재구성/모순 감지" 프레이밍 불필요. cross-device/cross-AI가 강점 |
| 46 | Fragment 임베딩은 자연어 축 인덱싱 (응답은 structured) | 04-18 | Fragment.content는 AI에게 노출 안 됨 — 검색 매칭에만 쓰임. AI는 결과로 entity/predicate/source_quote 다 받음. 따라서 Fragment는 "응답 데이터에 없는 축"을 인덱싱해야 가치 있음 |
| 47 | "검색 시스템 → 항해 가능한 지식 그래프" 프레임 전환 | 04-18 | AI가 JARVIS에 접속해 탐색. 위키 패턴(검색→하이퍼링크→관련 항목→이력). 한 번에 정답 찾는 게 아니라 여러 번 navigate. 4-tool 고정 완화, primitive 도구 추가 가능 |
| 48 | Entity 그래프가 자연스러운 scope (project_id 불필요) | 04-18 | 7/9 production 시스템이 project 파티션이라는 리서치 결과를 비전 기준 재평가. workspace가 이미 사용자/조직 격리 역할. 프로젝트로 더 쪼개면 "외장 뇌" 비전 깨짐. cross-reference("JARVIS is_core_engine_for SecondBrain")가 자연스럽게 작동하려면 그래프 그대로여야 |
| 49 | Aho-Corasick 별칭 매칭으로 entity anchor 추출 | 04-18 | Rust 구현 ahocorasick_rs, <1ms, GIL-free. CROSS_LINGUAL_ALIASES 테이블을 자동 확장 가능. 쿼리 hot path에 LLM 없이도 anchor 잡음 |
| 50 | 의문 어미는 stopword처럼 버림 (쿼리 token 매칭의 허구) | 04-18 | "어디까지" "했는지" "왜" "언제" 같은 질문 구조는 검색어 아님. 저장된 fact 원문에 있을 리 없음. AI 클라이언트가 자비스로 보내기 전에 제거한다는 전제, 혹은 query_preprocessing에서 추가 |
| 51 | MCP 계약 = AI용 structured API (사용자 자연어 parser 아님) | 04-18 | 자비스가 받는 쿼리는 AI가 이미 가공한 것. 사용자 원문이 아님. 자연어 이해는 AI 책임. 자비스는 entity + 관련어 기반 retrieval primitive 제공. Tool description이 AI 행동의 실제 계약 |
| 52 | 그래프 sparsity가 커뮤니티 파편화 원인 (Leiden 탓 아님) | 04-18 | 817 노드 / 567 엣지 = 평균 차수 1.39 = percolation threshold 근처. Leiden 알고리즘을 고칠 게 아니라 그래프를 densify (cosine>0.8 동의어 엣지 추가). 목표 평균 차수 ≥4 |
| 53 | pgvector 0.8 iterative_scan이 WHERE + HNSW 조합의 전제 | 04-18 | 과거 버전에선 `WHERE project_id=?` 필터가 HNSW 후 post-filter라 ef_search 후보 중 소수만 남음. 0.8의 `hnsw.iterative_scan='relaxed_order'`로 이터러티브 확장해 해결 |
| 54 | HippoRAG PPR damping=0.5 (웹 기준 0.85 아님) | 04-18 | 소규모 KG에서 높은 damping은 walker가 seed에서 멀어져 noise. Power iteration도 shallow (2~3회). degree-normalized로 hub bias 제거. JARVIS 같은 수백~천 노드 규모에 맞는 세팅 |

## 실패/수정 기록

| 시도 | 결과 | 수정 |
|------|------|------|
| 구 코드 (로컬 Docker 개인 비서) | 스코프 불일치, 팀플 퀄리티 안 나옴 | 전량 폐기 → 클라우드 컨텍스트 서버로 재정의 |
| Oracle Cloud Always Free | ARM64 호환 문제 + 가용성 | GCP 무료 크레딧으로 전환 |
| MCP 도구 호출에만 의존 | AI가 안 부름 (미래지향 행동 = 체계적 무시) | 3경로 캡처로 전환 |
| 3경로 캡처 Path A (Stop 훅) | 사용자 수동 설정 필요 + Claude Code 전용 = UX 목표 충돌 | initialize_memory 응답으로 AI가 hooks 자동 설정 → 사용자 부담 0 |
| KnowledgeFact 단일 저장소 | 시맨틱 검색에 약함 | Fragment 추가 → 이중 저장소 |
| confidence score 포함 | LLM이 87%에 최고 확신, 정답/오답 차이 0.6~5.4% | 제거 (노이즈) |
| "훅으로 도구 호출 강제 불가" 분석 | Stop decision:block 패턴 모름, transcript_path 모름 | 딥리서치로 정정 — 둘 다 가능 |
| "Memento SKILL.md 1000줄" | 실제로 존재하지 않음 | scrypster/memento ~30줄 CLAUDE.md가 전부 |
| "서버 LLM 비용 0"이 절대 원칙 | unknown unknowns로 20% 누락 가능 | "비용 최소화"로 전환 — 미처리 구간만 서버 LLM 보완 |
| "어시스턴트 레이어 별도 필요" 분석 | AI 클라이언트가 이미 그 역할 | recall 결과 종합/제안 = 기존 AI 기능. 메타적 시야 부족이었음 |
| AI 에이전트에 과금 조사 위임 | hallucination 섞인 정보 반복, "OAuth 2월에 차단됨" 등 허위 정보 | 직접 공식 문서 확인 + 오픈소스 코드 확인이 정답. 에이전트 결과 무조건 검증 필수 |
| "claude -p는 기본 사용량에서 차감" 반복 주장 | 서드파티 사용분은 Extra Usage에서 차감 (마이그레이션 가이드 명시) | OpenClaw 오픈소스 코드 + 공식 발언으로 확인 |
| OAuth 토큰 추출해서 SDK 테스트 제안 | 토큰 탈취 행위 = 밴 사유 | 절대 하면 안 됨. CLI 정상 사용이 유일한 안전 경로 |
| extract_knowledge.py 독립 추출 방식 | predicate 불일치 + source_quote fabrication + 변경 추적 불가 | seed_jarvis.py로 재설계: 턴 그룹 단위 recall→extract→store 루프 |
| 세션 통째로 추출 (실사용과 다른 방식) | 대형 세션 타임아웃 + grounding 50% | 5턴 단위 증분으로 전환. 합의한 설계를 구현에 반영하지 않은 실수 |
| 시딩을 서버 밖 스크립트로 구현 | 서버 기능 우회, 일반 사용자 온보딩과 다른 흐름 | Path B + 보완 파이프라인을 서버에 구현하면 시딩 = 온보딩 = 같은 코드 |
| 이미 결정된 사항(claude -p) 또 흔듦 | "API 크레딧으로 할 건지 결정 필요" — 불필요한 혼란 | 결정된 건 다시 꺼내지 말 것 |
| 보완 파이프라인에 하이쿠 제안 | 소넷으로 잘 되고 있는데 성능 테스트 없이 모델 변경 제안 | 동작하는 걸 건드리지 말 것 |
| upload-transcript에서 asyncio.create_task 즉시 실행 | 66개 동시 실행 → 서버 과부하 → 타임아웃 | 작업 큐 + 단일 워커 패턴 필수 |
| 문제 원인 추측만 하고 규명 안 함 (반복) | "블로킹일 수 있어요", "큰 트랜스크립트라서 그런 것 같아요" | 서버 로그, DB 조회, 프로세스 확인으로 원인 확정 후 보고 |
| recall hybrid search 완전 고장 (04-16 발견) | seed_array string 전달 + except가 에러 삼킴 + rollback 없음 → 100% ILIKE fallback, score 전부 1.0 | 3건 수정: SQL 리터럴 삽입, logger.exception, rollback 추가 |
| HNSW 인덱스 깨짐 (04-16 발견) | 대량 임베딩 삽입 후 REINDEX 안 해서 벡터 검색 2/2072만 반환 | 수동 REINDEX로 복구. 자동화 미구현 |
| worker.py transcript[:10000] 잘림 (04-16 발견) | quote 검증이 잘린 텍스트에서 실행 → 46.3% 에피소드의 fact가 전부 low_trust | 에피소드 전체 전달로 수정 필요 |
| entity merge 0.85 구간 새 엔티티 생성 (04-16 발견) | 로그만 찍고 merge 안 함. 0.92도 사후 비교 미실행 → 415쌍 중복 | merge 로직 + 사후 dedup 배치 필요 |
| gap_extraction에 relation 추출 없음 (04-16 발견) | 프롬프트가 entity+fact만 요청. worker도 relations 안 넘김 → relations 0개 | 프롬프트 + worker + MCP 도구 전부 수정 필요 |
| E5 임베딩 prefix 오용 (04-16 발견) | 저장/검색 모두 `query:` prefix → 비대칭 검색 품질 저하 | 저장 시 `passage:` prefix로 변경 필요 |
| AI가 "OK" 판정한 항목 실제 검증 시 결함 다수 (04-16) | 코드 읽기만으로 OK 판정 → DB 실측하니 82% low_trust, 415쌍 중복 | "OK"는 "코드 구조가 맞아 보인다" 수준. 실행 검증 필수 |
| gaps[:20] 하드코딩 (04-16 Phase D에서 발견) | 리서치 #4에 없는 임의 제한. 399K 에피소드에서 fact 4개만 추출. 보완용 설계를 온보딩에 그대로 적용한 것 | 제한 제거 + 청크 분할 추가. 동일 에피소드 fact 10→16개, relation 4→12개 |
| recall 결과 문제를 "데이터 양 부족"으로 넘기려 함 (04-16) | 펀드메신저 fact 0개를 "6개 에피소드라 부족"으로 설명. 실제로는 gaps[:20] 코드 문제 | 결과가 이상하면 데이터 탓 전에 코드를 먼저 의심할 것 |
| 문서 핸드오프 요약을 앵무새 복붙 (04-17) | "뭐 하고 싶으세요?"로 넘기며 이해 없음 드러남 | 문서는 본인 말로 소화한 내용을 답해야 함. 복붙은 읽은 것 아님 |
| research-notes.md 생략 (04-17) | 35K 토큰이라 1K줄만 읽고 "전부 읽었다" 주장 | 파일이 크면 offset/limit으로 분할 읽기. "안 읽었다"고 솔직히 말하고 계속 읽기 |
| "assistant 필터 먼저, MMR 나중" 자의 판단 (04-17) | 리서치가 "소규모 데이터도 처음부터 돌리면 됨" 명시했는데 데이터 핑계로 미룸 | 리서치 설계를 이유 없이 바꾸지 말 것. 두 리서치는 독립 축(입력/출력)이라 순서 상관없음 |
| recall 품질 문제를 데이터 탓으로 넘기려 함 (04-18) | 내가 던진 5개 쿼리 중 3개가 에피소드에 답 없는 질문이었음. 결론 전 데이터 검증 빠뜨림 | 쿼리가 트랜스크립트와 정렬되는지 먼저 확인. 답 없는 질문으로는 recall 품질 판단 불가 |
| 크로스링구얼 리서치가 필요하다고 가정 (04-18) | 영어 쿼리로 바꿔서 테스트해보니 여전히 품질 나쁨. Stage 1 FTS 자체 문제 | 리서치 제안 전 더 깊이 파고들 것. 가설 기각을 위한 실측 먼저 |
| Fragment 이중 저장소 설계 절반만 구현 (04-18 발견) | content가 triple 복사 + 임베딩 0개. 설계 의도(300자 자연어 시맨틱)와 무관 | Fragment.content = source_quote로 교체 + 임베딩 백필 789건 |
| importance/soft decay 공식 미구현 (04-18 발견) | 절대문서 Section 8에 `rrf × importance × e^(-λ × days)` 명시인데 recall은 순수 RRF만 | last_accessed_at 컬럼 추가 + context_assembly에서 final_score 계산 |
| aliases 컬럼 죽은 코드 (04-18 발견) | 0.85 merge 구간에서만 추가 (크로스링구얼 cosine 0.41이라 발동 불가) + recall에서 사용 0 | CROSS_LINGUAL_ALIASES 수동 사전 + query_preprocessing에서 적극 활용 |
| 쿼리 전처리 없음 (04-18 발견) | 저장은 NFKC/정규화/별칭 적용, 쿼리는 raw 문자열 그대로 FTS에 | query_preprocessing.py 신규로 대칭 맞춤. 저장/검색 같은 정규화 적용 |
| PGroonga `&@~` 자연어 쿼리 실패 (04-18 발견) | 다중 단어는 AND 기본 → "자비스 구현에서..." 같은 자연어 쿼리에서 0건 매칭 | 쿼리 전처리에서 OR 명시 조합. "JARVIS OR 구현" 형태로 변환 |
| orphan embedding 129건 방치 (04-18) | 이전 entity 정리 시 embeddings 함께 삭제 안 함 | worker.py 배치 훅에 cleanup 추가 (orphan DELETE) |
| 서버 재시작 누락 → score 1.0 fallback (04-18, 2회차) | 마이그레이션 완료 후 기존 서버 프로세스가 이전 recall.py 메모리에 유지. DB 함수 시그니처 불일치로 UndefinedFunctionError → ILIKE fallback | 마이그레이션/코드 수정 후 체크리스트에 "서버 재시작" 필수 |
| MMR 효과 없다고 판단할 뻔 (04-18) | top 결과가 여전히 무관한 걸 보고 "MMR이 안 듣나?" 의심. 실제로는 Stage 1 pool에 관련 fact가 못 들어오는 게 원인 | Stage 1 진단 우선 (`rank=22, 34`로 깊이 있음 확인). MMR 탓 전에 입력 pool 검증 |
| 플랜 재도입되는 실수 재확인 (04-18) | 플랜모드가 초기 플랜에서 gaps[:20] 반복 재도입. "이미 수정된 것"이라는 맥락이 플랜 안에 없음 | 플랜에 "이전에 수정된 버그" 섹션 필수. 구현자는 현재 코드를 읽고 "아직 수정 안 됐다"고 오해할 수 있음 |
| "어디까지" 같은 의문 어미를 검색어로 취급 (04-18 인식) | query_preprocessing이 쿼리 전체를 OR로 매칭. 저장 fact에 있을 리 없는 의문 어미를 literal 매칭 시도 | 의문 어미 stopword 확장 + AI가 MCP 호출 전 질문 어미 제거 전제 |
| 리서치 권고 맹목 수용할 뻔 — project_id (04-18) | 9개 production 중 7개 명시적 태깅이라는 데이터만 보고 1번(적극 도입) 가려 함. 비전(외장 뇌/AI 위키) 관점 재평가에서 rigid 파티션 부적합 발견 | 리서치 결과를 비전 기준으로 다시 거르기. 프로덕션 평균이 아니라 "이 프로젝트 비전에 맞는가" 질문 |
| Leiden 파편화를 retrieval 품질의 원인으로 오진단 (04-18 리서치 이전) | "커뮤니티가 깨져서 검색 나쁘다"로 가정. 실제는 그래프 sparsity(평균 차수 1.39)가 근본, 파편화는 증상 | 인과 사슬 먼저 확인. 증상과 원인 구분 |
| 토큰 매칭으로 자연어 쿼리 처리 시도 (04-18 인식) | Phase 1에서 PGroonga AND 0건 → OR로 바꾸며 반대 극단으로. 저장 원문에 없는 질문 어미까지 매칭 시도 | 쿼리의 본질은 앵커+의도. 토큰 자체가 아님. 엔티티 앵커링 + narrowing 분리 |
| 자비스를 natural language parser로 설계할 뻔 (04-18) | "어떻게 한국어 질문을 해석할까" 고민. 하지만 MCP 계약상 AI가 쿼리 가공해서 보냄 | 자비스는 structured retrieval API. NL 이해는 AI 책임. Tool description이 계약 |
