# 현재 세션 상태
> 마지막 업데이트: 03-26 (2026)

## 현재 작업 목록 (2026-03-26 사용자 요청)
- [x] JARVIS 최신 방향을 반영한 기준 문서 작성
- [x] `docs/jarvis/context-server-spec.md` 신규 생성
- [x] 프로젝트 메모리 문서에 작업 내용 반영

## 프로젝트 방향 대전환 (03-25)
- 기존: JARVIS = 워크플로우 실행 엔진 / 개인 비서 앱
- **확정**: JARVIS = 개인 맥락 서버 (클라우드) + MCP/CLI 인터페이스
- 서버에 LLM 없음 — 임베딩 모델만 필요
- 클라이언트(Claude Code, Cursor, GPT 등)가 AI 역할
- JARVIS는 데이터 레이어 (기억 저장/검색/관계 추적)
- 사용자 계정 시스템 → 기여자 추가 → 자연스럽게 팀 맥락 공유
- SecondBrain = JARVIS + 협업 UX + Web UI (별도 프로젝트 아님, 확장)

## 현재 단계
- 방향 전환 확정, 기술 설계 시작 전
- 기존 프론트엔드/백엔드 코드는 리셋 필요 (워크플로우 기반이었으므로)
- 최신 기준 문서 작성 완료: `docs/jarvis/context-server-spec.md`
- 이후 단계: API / 데이터모델 / CLI / MCP 세부 명세 분리 필요

## 기술 스택 (유지)
- Backend: FastAPI (Python)
- DB: PostgreSQL + pgvector + FTS
- 임베딩: bge-m3 또는 API 기반
- 인프라: Docker, 클라우드 배포 (CloudType → 추후 검토)
- 인터페이스: MCP 서버 + CLI

## 레퍼런스
- SuperMemory ASMR: 아토믹 메모리 블록, Update/Extend/Derive 관계, 이중 시간축
- GSD: 컨텍스트 엔지니어링, 구조화된 문서 > 채팅 히스토리
- brain 레포 TECH_SPEC: 승인 기반 구조, 노드+엣지 그래프, 하이브리드 검색
