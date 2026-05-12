"""Q1-Q3 regression: verify AI can reconstruct 왜/결정/맥락 via recall after today's changes.

Uses `personal` workspace (original seeded data, untouched today).
Runs each Q through recall_memory + shows top facts.
Manual eyeball: does the top fact contain the expected reconstruction keyword?
"""

from __future__ import annotations

import json
import sys
import urllib.request

WS = "2d92735f-c858-4398-b4dd-d28423208e17"
URL = "http://127.0.0.1:8005/api/v1/memory"

QUESTIONS = [
    ("Q1", "예창패 아이템으로 SecondBrain과 Argos 중 무엇을 선택했고 왜인가",
     ["예창패", "SecondBrain", "Argos"]),
    ("Q2", "펀드메신저 커뮤니티 2400명을 SecondBrain B2B 진입에 어떻게 활용하는가",
     ["펀드메신저", "2400", "B2B", "커뮤니티", "인터뷰"]),
    ("Q3", "아르고스의 strength 모델을 왜 폐기했는가",
     ["strength", "폐기", "삭제", "redesign"]),
]


def post(path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{URL}{path}", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


for q_id, question, expect_kw in QUESTIONS:
    print(f"\n{'='*60}\n{q_id}: {question}\n{'='*60}")
    print(f"Expected keywords: {expect_kw}")

    # 2-stage: search_passages → get_episode_excerpt
    try:
        r = post("/search-passages", {"workspace_id": WS, "query": question, "limit": 5})
        passages = r.get("results", [])
        print(f"\n--- search_passages top {len(passages)} ---")
        for i, p in enumerate(passages):
            content = p["content"][:200]
            print(f"  [{i+1}] sim={p['similarity']:.3f}  {content}")

        if not passages:
            print("RESULT: FAIL (no passages)")
            continue

        # Use top passage's episode for excerpt
        top_eid = passages[0].get("episode_id")
        if top_eid:
            ex = post("/episode-excerpt", {
                "workspace_id": WS, "episode_id": top_eid,
                "query": question, "mode": "relevant", "max_chars": 1500,
            })
            excerpt = ex.get("excerpt", "")
            print(f"\n--- get_episode_excerpt ({len(excerpt)}자, mode={ex.get('mode')}) ---")
            print(excerpt[:800])

            combined = " ".join(p["content"] for p in passages) + " " + excerpt
            hits = [kw for kw in expect_kw if kw.lower() in combined.lower()]
            print(f"\nKeyword hits: {hits} / {expect_kw}")
            print(f"RESULT: {'PASS' if len(hits) >= len(expect_kw)//2 + 1 else 'WEAK' if hits else 'FAIL'}")
    except Exception as exc:
        print(f"ERROR: {exc}")
