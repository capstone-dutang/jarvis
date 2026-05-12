"""Quick test of /subjects + /classify-turns endpoints."""

import json
import os
import urllib.request

os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis",
)

import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

WS = "5f7aa78b-48d5-41e0-8ab3-45fb4a6ea550"
BASE = "http://127.0.0.1:8007/api/v1/memory"


def post(path, payload):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


async def get_turn_ids():
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as db:
        result = await db.execute(
            text("SELECT id::text FROM turns WHERE workspace_id = :ws LIMIT 5"),
            {"ws": WS},
        )
        ids = [r[0] for r in result.fetchall()]
    await engine.dispose()
    return ids


async def main():
    print("=== 1. /subjects (초기) ===")
    r = post("/subjects", {"workspace_id": WS, "top_level_only": True})
    print(f"  total: {r['total']}")

    print("\n=== 2. turn_ids 5개 가져오기 ===")
    turn_ids = await get_turn_ids()
    print(f"  ids: {turn_ids}")

    print("\n=== 3. /classify-turns — 새 주제 2개 생성 + 링크 ===")
    r = post("/classify-turns", {
        "workspace_id": WS,
        "existing_links": [],
        "new_subjects": [
            {"name": "자비스", "parent_id": None, "turn_ids": turn_ids[:2]},
            {"name": "Argos", "parent_id": None, "turn_ids": turn_ids[2:]},
        ],
    })
    print(f"  {r}")

    print("\n=== 4. /subjects (생성 후) ===")
    r = post("/subjects", {"workspace_id": WS, "top_level_only": True})
    print(f"  total: {r['total']}")
    for s in r["subjects"]:
        print(f"    [{s['turn_count']}x] {s['name']} (id={s['subject_id'][:8]})")

    print("\n=== 5. /classify-turns — 같은 링크 재시도 (중복 스킵 확인) ===")
    r = post("/classify-turns", {
        "workspace_id": WS,
        "existing_links": [
            {"subject_id": post("/subjects", {"workspace_id": WS, "top_level_only": True})["subjects"][0]["subject_id"],
             "turn_ids": turn_ids[:2]},
        ],
        "new_subjects": [],
    })
    print(f"  {r}")

    print("\n=== 6. upload-status 최종 ===")
    r = post("/upload-status", {"workspace_id": WS})
    print(f"  {r}")


if __name__ == "__main__":
    asyncio.run(main())
