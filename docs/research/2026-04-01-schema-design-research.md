# MCP tool schema design determines AI memory quality

> 연구 일자: 2026-04-01
> 성격: store_memory 스키마 설계 + AI 추출 품질 리서치
> 상태: 활성 (절대문서 반영 필요)

**핵심 발견:**
1. enum 제약이 카테고리 hallucination을 완전 제거 (정확도 44% 향상)
2. source_quote가 서버 LLM 없이 검증할 수 있는 유일한 강력한 수단
3. confidence score는 쓸모없다 (정답/오답 간 차이 0.6~5.4%)
4. 2~3 레벨 이상 중첩하면 품질 급락, 30필드 넘으면 후반 필드 품질 저하
5. 프로덕션 메모리 시스템(Graphiti, Mem0, 공식서버)은 예상보다 단순한 스키마 사용

---

## 프로바이더별 스키마 엔진 차이

| 기능 | 안전 | 위험 |
|------|------|------|
| string, number, boolean, array, object | ✅ 전부 | — |
| enum on strings | ✅ 전부 | — |
| required array | ✅ 전부 | — |
| description on fields | ✅ 전부 | — |
| 중첩 2레벨 | ✅ 전부 | — |
| anyOf / union | ⚠️ Gemini 제한 | 복잡한 union |
| default 값 | ❌ Gemini 거부 | 항상 회피 |
| $ref / recursive | ❌ 불일치 | 항상 회피 |
| minimum/maximum/pattern | ❌ strict mode에서 제거됨 | description에 넣기 |

## confidence score는 버린다

GPT-4는 응답의 87%에 최고 확신도를 부여 (오답 포함).
정답/오답 간 확신도 차이: 0.6~5.4% (JMIR 2025).
→ 숫자 confidence 대신 `"stated" | "implied" | "uncertain"` 범주형 사용.

## source_quote가 핵심

서버 LLM 없이 추출 품질을 검증할 수 있는 유일한 강력한 수단.
원본 대화에서 substring/fuzzy match → 매칭 안 되면 fabrication 의심.
AGREE 프레임워크: grounding으로 fabrication 98.9% 제거.

## 권장 스키마

```json
{
  "name": "store_memory",
  "description": "Store structured knowledge extracted from the current conversation. Extract ONLY entities and facts explicitly stated or clearly implied. Do NOT fabricate relationships between entities that merely co-occur. Include source_quote when possible — this is used for verification.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "entities": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "name": {"type": "string"},
            "entity_type": {
              "type": "string",
              "enum": ["person", "organization", "location", "event", "concept", "product", "preference", "procedure", "other"]
            },
            "source_quote": {"type": "string"}
          },
          "required": ["name", "entity_type", "source_quote"]
        }
      },
      "facts": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "subject": {"type": "string"},
            "predicate": {"type": "string"},
            "object": {"type": "string"},
            "temporal": {"type": "string"},
            "source_quote": {"type": "string"}
          },
          "required": ["subject", "predicate", "object", "temporal", "source_quote"]
        }
      },
      "conversation_summary": {"type": "string"}
    },
    "required": ["entities", "facts", "conversation_summary"]
  }
}
```

설계 원칙:
- 전 필드 required, 없으면 빈 문자열 (프로바이더 호환)
- 최대 2레벨 중첩, 15개 미만 속성
- entity_type은 enum (hallucination 제거)
- predicate는 free-form + description에서 가이드
- source_quote 필수 (서버 검증용)
- confidence score 없음 (노이즈)
- temporal은 문자열 ("last Tuesday" 그대로, 서버가 파싱)
