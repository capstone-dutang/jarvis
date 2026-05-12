"""Test P4 reflect endpoints: pending-reflects, save-summaries, summaries."""

import json
import urllib.request

WS = "5f7aa78b-48d5-41e0-8ab3-45fb4a6ea550"
BASE = "http://127.0.0.1:8010/api/v1/memory"


def post(path, payload):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


print("=== 1. /pending-reflects — 요약 필요한 (date, subject) 쌍 ===")
r = post("/pending-reflects", {"workspace_id": WS})
print(f"  total pending: {len(r['pending'])}")
for p in r["pending"][:5]:
    print(f"    {p['date']}  {p['subject_name']}  ({p['turn_count']} turns)")

print("\n=== 2. /save-summaries — Argos 요약 2건 저장 ===")
# Pick first two pending items for argos
argos_items = [p for p in r["pending"] if p["subject_name"] == "Argos"][:2]
summaries = [
    {
        "subject_id": p["subject_id"],
        "date": p["date"],
        "summary": f"[{p['date']}] Argos 관련 {p['turn_count']}개 턴 — 임시 요약 (테스트)",
        "turn_count": p["turn_count"],
    }
    for p in argos_items
]
r2 = post("/save-summaries", {"workspace_id": WS, "summaries": summaries})
print(f"  upserted: {r2['upserted']}")

print("\n=== 3. /summaries — 저장된 요약 조회 ===")
r3 = post("/summaries", {"workspace_id": WS})
print(f"  total: {r3['total']}")
for s in r3["summaries"]:
    print(f"    {s['date']} [{s['subject_name']}] turns={s['turn_count']}: {s['summary'][:60]}")

print("\n=== 4. /pending-reflects 재호출 (방금 저장한 건 빠져야 함) ===")
r4 = post("/pending-reflects", {"workspace_id": WS})
print(f"  total pending: {len(r4['pending'])} (was {len(r['pending'])})")

print("\n=== 5. /save-summaries 같은 (subject, date) 다시 (upsert 확인) ===")
r5 = post("/save-summaries", {
    "workspace_id": WS,
    "summaries": [{
        "subject_id": argos_items[0]["subject_id"],
        "date": argos_items[0]["date"],
        "summary": "[수정됨] Argos — 두 번째 호출로 갱신",
        "turn_count": 999,
    }],
})
print(f"  upserted: {r5['upserted']}")
r6 = post("/summaries", {"workspace_id": WS, "subject_id": argos_items[0]["subject_id"]})
print(f"  Argos summaries: {r6['total']}")
for s in r6["summaries"][:2]:
    print(f"    {s['date']} turns={s['turn_count']}: {s['summary'][:60]}")
