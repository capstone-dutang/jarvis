# 작업 이력
> 최신이 위, 시간순 기록.

---

## 2026-03-26 | JARVIS 컨텍스트 서버 기준 문서 작성
- **유형**: 문서화 / 방향 정리
- **요청**: JARVIS 프로젝트 안에 새로운 문서를 생성하고, 최신 방향을 매우 자세하게 정리
- **수정**:
  - `docs/jarvis/context-server-spec.md` 신규 작성
  - JARVIS를 workflow 엔진이 아닌 클라우드 기반 컨텍스트 서버로 재정의
  - JARVIS와 SecondBrain의 관계를 코어 엔진과 제품 레이어 관점으로 재정리
  - 계정, workspace, contributor, raw event, memory item, relation, evidence 중심 구조 제안
  - CLI / MCP / API / retrieval / 캡스톤 범위 / 리스크까지 포함한 기준 문서 작성
- **영향 파일**:
  - `docs/jarvis/context-server-spec.md`
  - `memory/session-state.md`
  - `memory/work-log.md`
  - `memory/decisions.md`
- **상태**: 완료

## 03-01 | 프론트엔드 JARVIS UI 구현
- **유형**: 기능 구현
- **내용**: React 앱을 JARVIS AI 비서 UI로 전면 재구성
- **생성/수정 파일**:
  - `src/App.tsx` — BrowserRouter 기반 라우팅 (Layout + 3개 페이지)
  - `src/components/Sidebar.tsx` — 로고, 네비게이션 (홈/업무관리/검색), 상태 표시
  - `src/components/Layout.tsx` — 사이드바 + Outlet 구조
  - `src/pages/Home.tsx` — 텍스트 입력창 (Ctrl+Enter 전송), 최근 기록 3개 mock
  - `src/pages/Workflows.tsx` — 업무 카드 목록, 토글, 새 업무 추가 모달
  - `src/pages/Search.tsx` — 검색창, 결과 없음/있음 상태, mock 검색 로직
- **디자인**: 다크 테마 (#0a0a0f), 파란색 액센트 (blue-400/500), 터미널+AI 느낌

---

## 2026-02-27 | 프로젝트 경계 설정
- **유형**: 설정
- **내용**: SecondBrain ↔ JARVIS 경계 정의, 메모리 시스템 초기화
- **결정**: JARVIS = 캡스톤 스코프, SecondBrain = 사업화 스코프 (사용자 단독)
