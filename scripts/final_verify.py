"""Final end-to-end engine verification after smart classification."""

import json
import urllib.request

WS = "5f7aa78b-48d5-41e0-8ab3-45fb4a6ea550"
BASE = "http://127.0.0.1:8013/api/v1/memory"


def post(path, payload):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


print("="*70)
print("1. /upload-status")
print("="*70)
r = post("/upload-status", {"workspace_id": WS})
for k, v in r.items():
    print(f"  {k}: {v}")

print("\n" + "="*70)
print("2. /subjects (top-level only)")
print("="*70)
r = post("/subjects", {"workspace_id": WS, "top_level_only": True})
print(f"  total: {r['total']}")
for s in sorted(r["subjects"], key=lambda x: -x["turn_count"]):
    print(f"    [{s['turn_count']:>6}x] {s['name']}")

print("\n" + "="*70)
print("3. /subject-tree — 계층")
print("="*70)
r = post("/subject-tree", {"workspace_id": WS})
print(f"  total_subjects: {r['total_subjects']}")
for root in sorted(r["roots"], key=lambda x: -x["turn_count"]):
    print(f"    [{root['turn_count']:>6}x] {root['name']}")
    for child in sorted(root["children"], key=lambda x: -x["turn_count"]):
        print(f"        └ [{child['turn_count']:>5}x] {child['name']}")

print("\n" + "="*70)
print("4. /timeline — 2026-04-19 일별 호출")
print("="*70)
r = post("/timeline", {
    "workspace_id": WS,
    "date_from": "2026-04-19T00:00:00Z",
    "date_to": "2026-04-20T00:00:00Z",
    "limit": 3,
})
print(f"  total_turns: {r['total_turns']}")
for t in r["turns"][:3]:
    text = t["text"][:90].replace("\n", " ")
    print(f"    [{t['timestamp'][11:19]} {t['role']}] {text}")

print("\n" + "="*70)
print("5. /subject-feed — JARVIS 주제만 (최근 5건)")
print("="*70)
subjects = post("/subjects", {"workspace_id": WS, "top_level_only": True})["subjects"]
jarvis_id = next((s["subject_id"] for s in subjects if s["name"] == "JARVIS"), None)
if jarvis_id:
    r = post("/subject-feed", {
        "workspace_id": WS, "subject_id": jarvis_id,
        "include_descendants": True, "limit": 5,
    })
    print(f"  total JARVIS turns: {r['total_turns']}")
    for t in r["turns"][:3]:
        text = t["text"][:90].replace("\n", " ")
        print(f"    [{t['timestamp'][:10]} {t['role']}] {text}")

print("\n" + "="*70)
print("6. /subject-feed — JARVIS + 특정 일자 (2026-04-19)")
print("="*70)
if jarvis_id:
    r = post("/subject-feed", {
        "workspace_id": WS, "subject_id": jarvis_id,
        "date_from": "2026-04-19T00:00:00Z",
        "date_to": "2026-04-20T00:00:00Z",
        "limit": 3,
    })
    print(f"  JARVIS 2026-04-19 turns: {r['total_turns']}")
    for t in r["turns"][:3]:
        text = t["text"][:90].replace("\n", " ")
        print(f"    [{t['timestamp'][11:19]} {t['role']}] {text}")

print("\n" + "="*70)
print("7. 크로스 주제 M:N 통계")
print("="*70)
import asyncio, os
os.environ.setdefault("JARVIS_DATABASE_URL", "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis")
import sys
sys.path.insert(0, "src")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


async def check_crossover():
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as db:
        r = await db.execute(text("""
            SELECT subj_count, COUNT(*) AS n_turns
            FROM (
                SELECT t.id, COUNT(DISTINCT ts.subject_id) AS subj_count
                FROM turns t
                LEFT JOIN turn_subjects ts ON ts.turn_id = t.id
                WHERE t.workspace_id = :ws
                GROUP BY t.id
            ) sub
            GROUP BY subj_count ORDER BY subj_count
        """), {"ws": WS})
        for row in r.fetchall():
            print(f"  {row[0]} subjects: {row[1]:>7} turns")
    await engine.dispose()

asyncio.run(check_crossover())
