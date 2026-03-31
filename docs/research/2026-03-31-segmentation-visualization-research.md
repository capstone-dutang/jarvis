# Building JARVIS: practical engineering for bitemporal AI memory and graph visualization

> 연구 일자: 2026-03-31
> 성격: 대화 분할 전략 + 지식 그래프 시각화 UX 리서치
> 상태: 활성 (절대문서 반영 필요)

**핵심 발견:**
1. 모든 프로덕션 AI 기억 시스템은 자동 분할을 안 함 — 호출자/LLM에게 위임
2. 최적 retrieval 단위는 256-512 토큰 = 3-5턴과 일치
3. 토픽 전환 감지: 슬라이딩 윈도우 cosine similarity, threshold 0.55-0.65
4. 그래프 UI: 절대 전체 그래프를 기본 뷰로 보여주지 말 것. 검색 우선 + 점진적 확장이 유일하게 작동하는 패턴
5. React Flow 한계: ~500 노드에서 버벅임, 1000+ 불가 → expand/collapse로 50-150개만 렌더링

---

## 대화 분할 — 기존 시스템 분석

- **Graphiti (Zep)**: 호출자가 전달하는 단위가 곧 episode. 내부 분할 로직 없음. EPISODE_WINDOW_LEN=3은 추출 시 참조하는 이전 에피소드 수.
- **MemGPT/Letta**: LLM이 스스로 archival_memory_insert 도구로 저장 결정. 규칙 없음, 순수 에이전트 자율.
- **ChatGPT Memory**: 6개 프로파일링 레이어를 비동기 배치 생성, 시스템 프롬프트에 주입. 실시간 벡터 검색 없음.
- **Claude Memory**: MEMORY.md 플레인 마크다운을 200K 컨텍스트에 통째로 로드. DB 없음.
- **Mem0**: 매 턴마다 2단계 LLM 파이프라인(추출→비교→ADD/UPDATE/DELETE). 91% 지연 감소, 90% 토큰 절약.

**핵심**: 어떤 시스템도 원시 대화를 그대로 1차 기억으로 저장하지 않음 (Graphiti의 episode layer만 예외, ground truth용).

---

## 최적 분할 전략: 트리플 트리거

```python
class ConversationSegmenter:
    def __init__(self, similarity_threshold=0.6, max_turns=5, min_turns=2):
        self.model = SentenceTransformer("BAAI/bge-m3")
        self.threshold = similarity_threshold
        self.max_turns = max_turns
        self.min_turns = min_turns

    def should_store(self, messages, latest_message) -> bool:
        turn_count = len(messages)
        # Trigger 1: 토픽 전환 (임베딩 유사도 하락)
        if turn_count >= self.min_turns and self._topic_shifted(messages):
            return True
        # Trigger 2: 고정 간격 폴백
        if turn_count >= self.max_turns:
            return True
        # Trigger 3: 중요 이벤트 (키워드 휴리스틱)
        if self._is_significant_event(latest_message):
            return True
        return False
```

- FloTorch 벤치마크: 512 토큰 + recursive character splitting → 69% 답변 정확도 (최고)
- 토픽 전환 감지: 슬라이딩 윈도우 cosine similarity, threshold 0.55-0.65 (모델 의존)
- 한국어+영어 혼합: BAAI/bge-m3 (568M, 1024dim) 권장, 크로스링구얼 cosine 0.78-0.94

---

## MCP 도구 설명 설계

"when you learn something new" 프레이밍이 "every N turns" 보다 품질 높음. 고정 간격은 안전망.

권장 도구 설명:
- WHAT과 WHEN을 동시 명시
- 추출 카테고리 나열 (identity, preferences, decisions, corrections, goals)
- "self-contained statement" 형식 강제
- DO NOT 리스트 포함 (인사, 잡담, 이미 저장된 것, AI 응답)

---

## 그래프 시각화 — 검증된 패턴

### 절대 하면 안 되는 것
- 전체 그래프를 기본 뷰로 보여주기 ("hairball" 문제)
- 인간 단기기억은 7±2개 — 50-100 노드 이상 해석 불가

### 작동하는 패턴
- **검색 우선**: 검색바가 진입점, 그래프는 탐색 도구
- **점진적 확장**: 1-2홉 이웃만 표시, 클릭으로 확장
- **TheBrain 패턴**: 활성 노드의 이웃만 표시, 포커스 변경 시 애니메이션 전환
- **액셔너블 노드**: 클릭 → 사실 목록 → 근거 → 원본 대화로 이동

### React Flow 성능 한계

| 규모 | 상태 |
|------|------|
| ~100 단순 노드 | 최적화 불필요 |
| ~100-200 커스텀 노드 | React.memo() 필수 |
| ~500 노드 | 버벅임 보고 |
| 1000+ | 불가 (Canvas/WebGL 필요) |

→ expand/collapse로 **50-150 가시 노드** 유지하면 React Flow 최적

### 필수 최적화
- React.memo() on ALL custom components
- nodeTypes을 컴포넌트 외부 정의
- onlyRenderVisibleElements={true}
- Zustand 상태 관리 (Redux/Context 아닌)
- 레이아웃: dagre (기본) → ELKjs (고급)

### 시간축 시각화
- superseded 사실: 30% 투명도 + dashed edge + gray 색상
- 시간 슬라이더: 하단에 valid_time 필터
- "show history" 토글로 superseded 사실 오버레이

### 5000+ 엔티티 시
- 그래프 탐색을 서버 사이드 API로 이전
- 타입별 combo node (줌아웃 시 그룹)
- 적응형 LOD (줌 레벨별 상세도)
- cursor-based 페이지네이션 (이웃 50+ 시 10개씩 로드)
