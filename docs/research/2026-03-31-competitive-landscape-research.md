# MCP memory servers: a crowded field with one clear gap

> 연구 일자: 2026-03-31
> 성격: 경쟁 환경 분석 — MCP 메모리 서버 30+ 분석
> 상태: 활성

**핵심 발견:** JARVIS의 조합(클라이언트 구조화 + 서버 LLM 없음 + bitemporal + 멀티프로바이더 워크스페이스)은 현재 시장에 없다.

---

## 4개 아키텍처 캠프

### Camp 1: LLM-free 단순 저장소
- **공식 server-memory**: JSONL 파일, substring 검색, 임베딩 없음, 시간 없음. 주간 44K 다운로드.
- 포크들 (AIM, memory-mcp 등): SQLite 확장, 여전히 임베딩/시간 없음.
- "클라이언트 구조화" 패턴을 검증하지만, 검색 품질 한계.

### Camp 2: 임베딩 있는 LLM-free 서버
- **doobidoo/mcp-memory-service** (~1,500 stars): ONNX 로컬 임베딩, SQLite-vec, 5ms 검색, OAuth, D3.js 그래프. 가장 활발한 커뮤니티 서버. 단일 temporal (recorded_at만).
- Puliczek/mcp-memory: Cloudflare Workers AI bge-m3.
- mkreyman/mcp-memory-keeper: Claude Code 특화 체크포인트.

### Camp 3: 서버 LLM 있는 상용 플랫폼
- **Zep/Graphiti**: 4-6 LLM 호출/에피소드. 유일한 bitemporal. Neo4j/FalkorDB. 커뮤니티 에디션 중단.
- **Mem0** ($24M Series A, 51K stars): GPT-4.1-nano. 그래프 기능은 $249/월.
- **Letta/MemGPT**: 에이전트가 자체 메모리 편집.
- **Cognee**: 비정형→지식그래프 ECL 파이프라인.
- **Supermemory**: LongMemEval ~85%.
- **Hindsight**: LongMemEval 91.4%.

### Camp 4: 기존 도구를 메모리로 활용
- Notion MCP, Obsidian MCP (24+개), 파일시스템 기반.

---

## JARVIS의 경쟁 우위 (검증됨)

| 차별점 | 현존 경쟁자 수 |
|--------|-------------|
| 클라이언트 구조화 + 임베딩 + 지식그래프 | **0** (공식 서버는 임베딩 없음, 상용은 서버 LLM) |
| Bitemporal + 서버 LLM 없음 | **0** (bitemporal은 Graphiti뿐, 그것도 LLM 필수) |
| 멀티프로바이더 워크스페이스 (1st class) | **0** (기술적 가능하지만 설계 중심으로 하는 곳 없음) |
| 엔티티 해소 without 서버 LLM | **0** (Graphiti의 3-tier 중 3번째가 LLM) |

---

## 전략적 리스크 3가지

1. **공식 MCP 서버가 진화**: 임베딩+시간 추가하면 직접 경쟁.
2. **Graphiti 포크**: 누군가 서버 LLM 제거하고 클라이언트 위임으로 바꿀 수 있음.
3. **플랫폼 네이티브 메모리 발전**: Claude Auto Dream, Gemini context fusion 등.

---

## 비교표

| | 저장소 | 검색 | 서버 LLM | 시간 모델 | 엔티티 중복제거 |
|---|---|---|---|---|---|
| 공식 server-memory | JSONL | substring | 없음 | 없음 | 없음 |
| doobidoo/mcp-memory | SQLite-vec | semantic(ONNX) | 없음(옵션 Groq) | 단일(recorded_at) | 통합만 |
| **Zep/Graphiti** | Neo4j | hybrid | **4-6 호출/ep** | **bitemporal** | **3-tier(LLM)** |
| **Mem0** | Qdrant+graph | semantic | **GPT-4.1-nano** | decay | 기본 |
| Letta | PostgreSQL | vector | **에이전트 추론** | 자체 편집 | 없음 |
| **JARVIS** | **PG+pgvector+PGroonga** | **hybrid 3-way RRF** | **없음** | **bitemporal** | **3단계(LLM 없음)** |
