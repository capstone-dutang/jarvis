"""Manual classification of 5 sample episodes using the new rules.

Demonstrates the AI-driven flow:
  1. AI reads episode metadata + first turns
  2. AI decides subject(s), with parent relationships
  3. POST /classify-turns to save

For these 5 samples, classification done in this script (mimicking AI judgment).
Final scale-up will use Agent tool for parallelism.
"""

import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis",
)

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

WS = "5f7aa78b-48d5-41e0-8ab3-45fb4a6ea550"
BASE = "http://127.0.0.1:8012/api/v1/memory"


# AI classification results (from my analysis of episode content)
# (episode_id, [list of subjects with optional parent])
CLASSIFICATIONS = [
    {
        "episode_id": "424b2660-1061-4464-9606-d5b2fe1a2aa8",
        "label": "fundmessenger backend - Cerebras API 테스트",
        "subjects": [
            {"name": "fundmessenger", "parent": None},
            {"name": "fundmessenger 백엔드", "parent": "fundmessenger"},
        ],
    },
    {
        "episode_id": "d13c8b07-faac-4ab0-b975-1d1b2a5ebf14",
        "label": "brain의 JARVIS 세션 이어받기",
        "subjects": [
            {"name": "JARVIS", "parent": None},
        ],
    },
    {
        "episode_id": "fc565f2b-b043-4574-b609-5b0d16862736",
        "label": "JARVIS 프론트엔드 전문가 작업",
        "subjects": [
            {"name": "JARVIS", "parent": None},
            {"name": "JARVIS 프론트엔드", "parent": "JARVIS"},
        ],
    },
    {
        "episode_id": "41a0f892-20cb-4f2a-bfec-eeebae1ebd2c",
        "label": "자료구조 과제",
        "subjects": [
            {"name": "자료구조", "parent": None},
        ],
    },
    {
        "episode_id": "79eb98c9-6ba1-4fd9-86b9-6829ebb9a734",
        "label": "newsdesk — 그래프 시각화 리서치",
        "subjects": [
            {"name": "newsdesk", "parent": None},
        ],
    },
]


def post(path, payload):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


async def get_turn_ids(episode_id: str) -> list[str]:
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as db:
        r = await db.execute(
            text("SELECT id::text FROM turns WHERE episode_id = :eid ORDER BY sequence"),
            {"eid": episode_id},
        )
        ids = [row[0] for row in r.fetchall()]
    await engine.dispose()
    return ids


async def main():
    # Maintain subject_id cache across classifications (so parents resolve)
    # First pass: create top-level subjects via /classify-turns with empty turn_ids
    # Then resolve parent_id and create children
    # Simplest: process sequentially, looking up existing each time

    for cls in CLASSIFICATIONS:
        ep_id = cls["episode_id"]
        label = cls["label"]
        print(f"\n=== {label} ===")
        print(f"  ep_id: {ep_id}")

        turn_ids = await get_turn_ids(ep_id)
        print(f"  turn_count: {len(turn_ids)}")

        subj_resp = post("/subjects", {"workspace_id": WS, "top_level_only": False})
        existing_by_name = {s["name"]: s["subject_id"] for s in subj_resp["subjects"]}

        existing_links = []
        new_subjects = []
        for spec in cls["subjects"]:
            name = spec["name"]
            parent_name = spec["parent"]
            if name in existing_by_name:
                existing_links.append({
                    "subject_id": existing_by_name[name],
                    "turn_ids": turn_ids,
                })
            else:
                item = {"name": name, "turn_ids": turn_ids}
                if parent_name:
                    if parent_name in existing_by_name:
                        item["parent_id"] = existing_by_name[parent_name]
                    else:
                        item["parent_name"] = parent_name
                new_subjects.append(item)

        result = post("/classify-turns", {
            "workspace_id": WS,
            "existing_links": existing_links,
            "new_subjects": new_subjects,
        })
        print(f"  result: created_subjects={result['created_subjects']}, "
              f"linked_turns={result['linked_turns']}, skipped={result['skipped_duplicate_links']}")

    # Final subject tree
    print("\n=== 최종 주제 트리 ===")
    tree = post("/subject-tree", {"workspace_id": WS})
    for root in sorted(tree["roots"], key=lambda x: -x["turn_count"]):
        print(f"  [{root['turn_count']:>5}x] {root['name']}")
        for child in sorted(root["children"], key=lambda x: -x["turn_count"]):
            print(f"    └ [{child['turn_count']:>5}x] {child['name']}")


asyncio.run(main())
