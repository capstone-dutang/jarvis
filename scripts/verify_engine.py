"""End-to-end engine validation against vision-test workspace.

Checks:
  1. /upload-status — totals consistent
  2. /subject-tree — hierarchy renders
  3. /subjects (top-level) — chips list with counts
  4. /timeline by date — daily call works
  5. /subject-feed for JARVIS/Argos — proper isolation
  6. /timeline + subject filter via /subject-feed date_from/to
  7. Cross-subject sanity — turn appearing in multiple subjects (M:N working)
"""

import json
import urllib.request
from collections import Counter

WS = "5f7aa78b-48d5-41e0-8ab3-45fb4a6ea550"
BASE = "http://127.0.0.1:8011/api/v1/memory"


def post(path, payload):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


print("="*70)
print("1. /upload-status")
print("="*70)
r = post("/upload-status", {"workspace_id": WS})
for k, v in r.items():
    print(f"  {k}: {v}")

print("\n" + "="*70)
print("2. /subject-tree — 상위 + 하위 구조")
print("="*70)
r = post("/subject-tree", {"workspace_id": WS})
print(f"  total_subjects: {r['total_subjects']}")
print(f"  root count: {len(r['roots'])}")
# Sort by turn count desc
sorted_roots = sorted(r["roots"], key=lambda x: -x["turn_count"])
for root in sorted_roots[:15]:
    print(f"    [{root['turn_count']:>6}x] {root['name']}  (children={len(root['children'])})")

print("\n" + "="*70)
print("3. /subjects (top-level chips)")
print("="*70)
r = post("/subjects", {"workspace_id": WS, "top_level_only": True})
print(f"  total: {r['total']}")
for s in sorted(r["subjects"], key=lambda x: -x["turn_count"])[:15]:
    print(f"    [{s['turn_count']:>6}x] {s['name']}")

print("\n" + "="*70)
print("4. /timeline — 특정 일자 (2026-04-19)")
print("="*70)
r = post("/timeline", {
    "workspace_id": WS,
    "date_from": "2026-04-19T00:00:00Z",
    "date_to": "2026-04-20T00:00:00Z",
    "limit": 10,
})
print(f"  total_turns in 2026-04-19: {r['total_turns']}")
for t in r["turns"][:5]:
    text = t["text"][:80].replace("\n", " ")
    print(f"    [{t['timestamp'][11:19]} {t['role']}] {text}")

print("\n" + "="*70)
print("5. /subject-feed — JARVIS 주제만")
print("="*70)
subjects = post("/subjects", {"workspace_id": WS, "top_level_only": True})["subjects"]
jarvis_id = next((s["subject_id"] for s in subjects if s["name"] == "JARVIS"), None)
if jarvis_id:
    r = post("/subject-feed", {
        "workspace_id": WS, "subject_id": jarvis_id,
        "include_descendants": True, "limit": 5,
    })
    print(f"  total_turns for JARVIS: {r['total_turns']}")
    for t in r["turns"][:5]:
        text = t["text"][:80].replace("\n", " ")
        print(f"    [{t['timestamp'][:10]} {t['role']}] {text}")
        # Verify JARVIS keyword actually appears
        has_kw = any(k.lower() in t["text"].lower() for k in ["jarvis", "자비스"])
        print(f"      contains JARVIS/자비스: {has_kw}")

print("\n" + "="*70)
print("6. /subject-feed + 일자 결합 (Argos + 2026-04-19)")
print("="*70)
argos_id = next((s["subject_id"] for s in subjects if s["name"] == "Argos"), None)
if argos_id:
    r = post("/subject-feed", {
        "workspace_id": WS, "subject_id": argos_id,
        "date_from": "2026-04-19T00:00:00Z",
        "date_to": "2026-04-20T00:00:00Z",
        "limit": 5,
    })
    print(f"  total Argos turns 2026-04-19: {r['total_turns']}")
    for t in r["turns"][:5]:
        text = t["text"][:80].replace("\n", " ")
        print(f"    {t['timestamp'][11:19]}: {text}")

print("\n" + "="*70)
print("7. 크로스 주제 (M:N 작동 확인)")
print("="*70)
print("  같은 턴이 여러 주제에 속하는지 — 통계로 확인")
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
        # Distribution of subjects-per-turn
        r = await db.execute(text("""
            SELECT subj_count, COUNT(*) AS n_turns
            FROM (
                SELECT t.id, COUNT(ts.subject_id) AS subj_count
                FROM turns t
                LEFT JOIN turn_subjects ts ON ts.turn_id = t.id
                WHERE t.workspace_id = :ws
                GROUP BY t.id
            ) sub
            GROUP BY subj_count
            ORDER BY subj_count
        """), {"ws": WS})
        print("  주제 수별 turn 분포:")
        for row in r.fetchall():
            print(f"    {row[0]} subjects: {row[1]:>6} turns")

        # Top crossover example
        r2 = await db.execute(text("""
            SELECT t.id::text, LEFT(t.text, 100), array_agg(e.name) AS subjects
            FROM turns t
            JOIN turn_subjects ts ON ts.turn_id = t.id
            JOIN entities e ON e.id = ts.subject_id
            WHERE t.workspace_id = :ws
            GROUP BY t.id, t.text
            HAVING COUNT(*) >= 3
            LIMIT 3
        """), {"ws": WS})
        print("\n  3+ 주제 동시 소속 예시:")
        for row in r2.fetchall():
            txt = row[1].replace("\n", " ")[:80]
            print(f"    [{', '.join(row[2])}] {txt}")
    await engine.dispose()

asyncio.run(check_crossover())
