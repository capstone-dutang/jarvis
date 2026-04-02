# Three crises threatening MCP-based AI memory — and a path through them

> 연구 일자: 2026-04-02
> 성격: Memento MCP 비교 + AI 도구 호출 위기 + 기억 아키텍처 심층 분석
> 상태: 활성 (절대문서 반영 필요 — 아키텍처 변경 수준)

**핵심 발견:** MCP 서버는 AI가 도구를 호출하지 않으면 대화를 볼 수조차 없다. 이 문제는 프로토콜 수준에서 해결 불가능. Claude Code hooks의 prompt 타입이 유일한 해법.

---

## 문제 1: AI가 메모리 도구를 안 부른다

MCP 서버는 "방음실에 갇혀서 누가 문을 열어주길 기다리는" 구조.
AI는 memory tool을 "미래 지향적 행동"으로 인식 → 즉각적 보상 없음 → 체계적으로 무시.

### 해결 방법 신뢰도 순위
1. **Claude Code hooks (결정론적, ~100%)** — AI 의지와 무관하게 시스템 이벤트에 발동
2. **시스템 프롬프트 (70-85%)** — "Recall-Act-Memorize" 인지 아키텍처
3. **MCP instructions (60-80%)** — GPT-5-Mini 20%→80% 개선, 모델별 편차 큼
4. **도구 설명 최적화 (보조 역할)** — 단독으로는 불충분

### prompt 타입 훅 — 핵심 돌파구
```json
{
  "hooks": {
    "Stop": [{
      "hooks": [{
        "type": "prompt",
        "prompt": "Before ending, extract key facts, decisions, and user preferences from this conversation. For each extracted fact, call store_memory with entity, predicate, object, and confidence.",
        "timeout": 30
      }]
    }]
  }
}
```
- 세션의 기존 LLM을 사용 (추가 인프라 0)
- 결정론적으로 발동 (AI 의지 불필요)
- Memento의 Gemini CLI 의존 제거

---

## 문제 2: 세션 종료 시 자동 추출 (서버 LLM 없이)

### 4단계 방어 아키텍처
- **Tier 1 (즉시, 0비용)**: 원본 대화 저장 + YAKE 키워드 추출. 크래시에도 생존.
- **Tier 2 (휴리스틱, ~100ms)**: GLiNER(100MB ONNX) NER + KeyBERT. LLM 없이 80-90% 품질.
- **Tier 3 (클라이언트 LLM, 최고 품질)**: prompt 타입 Stop 훅으로 AI가 직접 추출.
- **Tier 4 (다음 세션 복구)**: SessionStart에서 미처리 세션 감지 → AI에게 처리 요청.

---

## 문제 3: 기억 시스템 강도 비교

### 데이터 모델
- **Fragment (Memento)**: 시맨틱 검색에 강함, 자연어 300자 텍스트
- **KnowledgeFact (JARVIS)**: 구조적 쿼리에 강함, entity+predicate+object
- **정답: 둘 다** — Graphiti/Zep도 3-tier (episode + entity + community)

### 망각/생명주기
- JARVIS의 bitemporal supersede: 감사 추적 완벽, but 50K+ 시 검색 품질 12% 저하
- Memento의 importance decay: 저장 55%에서 82.1% 핵심 사실 유지 (Mem0의 78.4%보다 우수)
- **최적 조합**: bitemporal 유지 (삭제 안 함) + soft decay를 검색 점수에 적용 + hot/cold 파티셔닝

### 모순 탐지
- JARVIS: same entity+predicate → supersede (결정론적, O(1))
- Memento: pgvector → NLI(mDeBERTa) → Gemini (3단계)
- **권장 모델**: `cross-encoder/nli-deberta-v3-xsmall` — 22M params, 87.77% 정확도, 28-50ms CPU
- **조합**: predicate supersede (1차) → NLI top-5 유사 기억 대조 (2차) → entailment으로 중복제거

---

## 최종 권장 아키텍처

### 이중 저장소
- text fragment (시맨틱 검색용) + structured KnowledgeFact (구조적 쿼리용)
- 새 기억마다 양쪽 모두 저장

### 3경로 캡처
- Path A: prompt 타입 Stop 훅 → 클라이언트 LLM이 추출 (주력)
- Path B: 원본 대화 즉시 저장 + YAKE/GLiNER (안전망)
- Path C: 다음 세션 시작 시 미처리 감지 → 복구 (최후 폴백)

### 점진적 세션 시작
- Stage 1: 정적 앵커 (~100 토큰, 항상 로드)
- Stage 2: 핵심 프로필 (valid_to IS NULL, importance > 0.9, ~300 토큰)
- Stage 3: 첫 메시지 기반 시맨틱 검색 (~1000 토큰)

### 하이브리드 생명주기
- bitemporal 유지 (감사 추적)
- soft decay on 검색 점수 (유형별 반감기)
- 50K+ 시 hot/cold 파티셔닝

### 계층적 모순 탐지
- predicate supersede (결정론적) → NLI xsmall (~28ms) → entailment 중복제거 → 모호한 경우만 LLM
