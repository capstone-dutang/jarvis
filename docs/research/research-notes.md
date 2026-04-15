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
