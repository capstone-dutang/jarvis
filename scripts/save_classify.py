"""Save classification for an episode via /classify-turns API.

Usage:
    python scripts/save_classify.py <episode_id> \\
        --subject "JARVIS" \\
        --subject "JARVIS>UI"

  '>' separates child from parent. E.g.:
    --subject "fundmessenger"  → top-level
    --subject "fundmessenger>backend"  → child of fundmessenger
    --subject "JARVIS>UI>버튼"  → grand-child (recursive)

Multi-subject support: pass --subject multiple times.

Skip flag for trivial episodes:
    python scripts/save_classify.py <ep_id> --skip
  Skips classification entirely.
"""

import argparse
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
os.environ.setdefault("JARVIS_API_URL", "http://127.0.0.1:8012/api/v1/memory")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

WS = "5f7aa78b-48d5-41e0-8ab3-45fb4a6ea550"


def post(path, payload):
    req = urllib.request.Request(
        f"{os.environ['JARVIS_API_URL']}{path}",
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


async def main(episode_id: str, subject_specs: list[str], skip: bool):
    if skip:
        print(f"SKIPPED {episode_id}")
        return

    if not subject_specs:
        print(f"NO_SUBJECTS {episode_id}")
        return

    turn_ids = await get_turn_ids(episode_id)
    if not turn_ids:
        print(f"NO_TURNS {episode_id}")
        return

    # Build subject hierarchy from "A>B>C" specs
    # Each spec: a chain of canonical names from root to leaf
    # All chain members get linked to the turns (M:N to all ancestors)
    subj_resp = post("/subjects", {"workspace_id": WS, "top_level_only": False})
    existing_by_norm = {}
    for s in subj_resp["subjects"]:
        # crude normalization mirroring server's normalize_name
        existing_by_norm[s["name"].lower().strip()] = (s["subject_id"], s["name"])

    existing_links = []
    new_subjects = []
    seen_specs = set()

    for spec in subject_specs:
        parts = [p.strip() for p in spec.split(">") if p.strip()]
        if not parts:
            continue
        last_parent = None  # name of parent in the chain
        for i, name in enumerate(parts):
            if name in seen_specs:
                # already processed this exact node — skip re-add
                last_parent = name
                continue
            seen_specs.add(name)
            norm = name.lower().strip()
            if norm in existing_by_norm:
                subj_id, _ = existing_by_norm[norm]
                existing_links.append({
                    "subject_id": subj_id,
                    "turn_ids": turn_ids,
                })
            else:
                item = {"name": name, "turn_ids": turn_ids}
                if last_parent:
                    pn = last_parent.lower().strip()
                    if pn in existing_by_norm:
                        item["parent_id"] = existing_by_norm[pn][0]
                    else:
                        item["parent_name"] = last_parent
                new_subjects.append(item)
            last_parent = name

    result = post("/classify-turns", {
        "workspace_id": WS,
        "existing_links": existing_links,
        "new_subjects": new_subjects,
    })
    print(f"OK {episode_id} created={result['created_subjects']} linked={result['linked_turns']} skipped={result['skipped_duplicate_links']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("episode_id")
    p.add_argument("--subject", action="append", default=[], dest="subjects")
    p.add_argument("--skip", action="store_true")
    args = p.parse_args()
    asyncio.run(main(args.episode_id, args.subjects, args.skip))
