# JARVIS

AI 클라이언트들이 공유하는 클라우드 기억 서버.

세션이 바뀌어도, 기기가 바뀌어도, AI가 바뀌어도 작업 맥락이 이어진다.

## 핵심 아이디어

**클라이언트가 구조화하고, 서버가 검증한다.**

기존 AI 기억 시스템(Zep, LangMem, Letta, OpenAI Memory)은 서버에서 LLM을 돌려 대화를 분석한다. JARVIS는 대화 중인 AI 클라이언트가 이미 맥락을 알고 있다는 점을 활용하여, 서버에 LLM 추론 없이 검증/저장/검색만 수행한다.

## 기술 스택

| 레이어 | 기술 |
|--------|------|
| API 서버 | Python + FastAPI |
| MCP | mcp Python SDK (Streamable HTTP) |
| 인증 | OAuth 2.1 (SDK 내장 provider) |
| DB | PostgreSQL 16 + pgvector + PGroonga |
| 임베딩 | multilingual-e5-small-ko (ONNX int8, 로컬) |
| 엔티티 해소 | RapidFuzz + 임베딩 cosine + 별칭 사전 |
| 그래프 탐색 | PostgreSQL Recursive CTE |
| 프론트 | React + React Flow |
| 인프라 | Oracle Cloud Always Free (ARM 4 OCPU, 24GB) |

## 문서

- [절대문서 (JARVIS_DEFINITIVE.md)](docs/JARVIS_DEFINITIVE.md) — 설계의 단일 기준 문서
- [리서치](docs/research/) — 7개 기술 리서치
