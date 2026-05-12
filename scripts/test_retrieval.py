"""Test P3 retrieval endpoints: timeline, subject-feed, subject-tree."""

import json
import urllib.request

WS = "5f7aa78b-48d5-41e0-8ab3-45fb4a6ea550"
BASE = "http://127.0.0.1:8008/api/v1/memory"


def post(path, payload):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


print("=== /subject-tree ===")
r = post("/subject-tree", {"workspace_id": WS})
print(f"  total_subjects: {r['total_subjects']}")
for root in r["roots"]:
    print(f"    [{root['turn_count']}x] {root['name']}  children={len(root['children'])}")

print("\n=== /timeline — 최근 5개 turns ===")
r = post("/timeline", {"workspace_id": WS, "descending": True, "limit": 5})
print(f"  total_turns: {r['total_turns']}, has_more: {r['has_more']}")
for t in r["turns"]:
    text = t["text"][:80].replace("\n", " ")
    print(f"    [{t['timestamp'][:10]} {t['role']}] {text} (subjects={len(t['subjects'])})")

print("\n=== /subject-feed — Argos 주제 turns ===")
argos_id = None
for root in post("/subject-tree", {"workspace_id": WS})["roots"]:
    if root["name"] == "Argos":
        argos_id = root["subject_id"]
        break
if argos_id:
    r = post("/subject-feed", {"workspace_id": WS, "subject_id": argos_id, "limit": 5})
    print(f"  subject: {r['subject_name']}, total_turns: {r['total_turns']}")
    for t in r["turns"]:
        text = t["text"][:80].replace("\n", " ")
        print(f"    [{t['timestamp'][:10]} {t['role']}] {text}")

print("\n=== /timeline 날짜 범위 (2026-04-01 ~ 2026-04-02) ===")
r = post("/timeline", {
    "workspace_id": WS,
    "date_from": "2026-04-01T00:00:00Z",
    "date_to": "2026-04-02T00:00:00Z",
    "limit": 5,
})
print(f"  total_turns in range: {r['total_turns']}")
for t in r["turns"][:3]:
    text = t["text"][:80].replace("\n", " ")
    print(f"    [{t['timestamp'][:19]}] {t['role']}: {text}")
