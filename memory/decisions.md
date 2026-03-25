# 기술적 결정 이력

## 2026-03-25 | JARVIS 방향 대전환 — 개인 맥락 서버
- **Before**: JARVIS = 워크플로우 실행 엔진 + 개인 비서 앱 (로컬 Docker)
- **After**: JARVIS = 클라우드 기반 개인 맥락 서버 + MCP/CLI 인터페이스
- **이유**:
  - 캡스톤에서 여러 기능 누더기보다 "기억"이라는 하나를 깊게 밀어붙이는 게 낫다
  - 실제 pain point: 세션/환경별로 맥락이 갇힘 (데스크톱↔노트북, 세션 A↔B)
  - memory/ 폴더 방식은 수작업 패치일 뿐
  - MCP/CLI 인터페이스면 서버에 LLM 불필요 → 서버 비용 최소화
  - 기여자 추가만으로 팀 맥락 공유 가능 → SecondBrain으로 자연 확장
- **영향**: 기존 워크플로우 기반 프론트엔드/백엔드 코드 리셋 필요

## 2026-03-26 | 최신 기준 문서 채택
- **문서**: `docs/jarvis/context-server-spec.md`
- **결정**:
  - JARVIS 최신 방향은 위 문서를 기준으로 해석한다
  - 기존 `planning.md`의 workflow 엔진 정의는 역사적 배경 문서로 취급한다
  - 이후 세부 설계는 `context-server-spec.md`를 부모 문서로 두고 분리한다
- **이유**:
  - 방향 전환은 확정됐지만 단일 기준 문서가 없어 다음 구현 단계에서 혼선이 생길 수 있다
  - 캡스톤 범위, 제품 한 줄 정의, 아키텍처 원칙, 데이터 모델의 중심 개념을 먼저 고정할 필요가 있다

## 2026-03-25 | JARVIS ↔ SecondBrain 관계 재정의
- **Before**: JARVIS = SecondBrain의 내부 기능 모듈 (분리된 별개 프로젝트)
- **After**: JARVIS = SecondBrain의 코어 그 자체 (같은 것의 다른 단계)
  - JARVIS (캡스톤) = 코어 맥락 서버 + MCP/CLI
  - SecondBrain (졸업 후) = JARVIS + 협업 강화 + Web UI
- **이유**: 억지로 분리할 필요 없음. 계정 시스템에 기여자 추가만으로 팀 공유 가능

## 2026-03-25 | 서버 아키텍처: LLM 없는 데이터 레이어
- **Before**: 서버에 Ollama + Qwen 로컬 LLM 탑재
- **After**: 서버에 LLM 없음. 임베딩 모델만 사용.
- **이유**:
  - MCP/CLI로 연결하면 클라이언트(Claude/GPT/Gemini)가 AI 역할
  - 서버는 저장/검색/관계 추적만 담당
  - 모델 종속 없음, 서버 비용 최소화

## 2026-02-27 | SecondBrain ↔ JARVIS 경계 정의 (폐기됨)
- ~~JARVIS는 SecondBrain의 내부 기능 모듈~~
- → 2026-03-25 결정으로 대체됨
