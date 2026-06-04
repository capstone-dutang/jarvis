"""Reusable helper — ingest one jsonl + classify with given subjects."""

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from cleanup_preserve import parse_and_clean


def main():
    p = argparse.ArgumentParser()
    p.add_argument("jsonl")
    p.add_argument("--workspace-id", default="71a0ddee-a88c-4ca3-978a-ee5c61e5ed63")
    p.add_argument("--api", default="http://127.0.0.1:8015/api/v1/memory")
    p.add_argument("--summary", required=True)
    p.add_argument("--keywords", required=True, help="comma-separated")
    p.add_argument("--subjects", required=True, help="subject chains: 'A;B>A;C>A>B' (top;child>parent;...)")
    args = p.parse_args()

    cleaned, metadata, raw = parse_and_clean(Path(args.jsonl))
    print(f"  cleaned: {len(cleaned)} turns")

    # Ingest
    body = {
        "workspace_id": args.workspace_id, "provider": metadata.get("entrypoint") or "claude-code",
        "source_session_id": metadata.get("session_id", ""), "source_path": str(args.jsonl),
        "title": metadata.get("title", ""),
        "summary": args.summary, "keywords": [k.strip() for k in args.keywords.split(",")],
        "turns": cleaned, "raw_content": raw,
        "metadata": {"cwd": metadata.get("cwd", ""), "git_branch": metadata.get("git_branch", ""),
                     "ai_title": metadata.get("title", ""), "ingested_via": "autonomous_batch"},
    }
    req = urllib.request.Request(args.api + "/ingest-transcript",
        data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        r = json.loads(resp.read().decode())
    print(f"  ingest: ep={r['episode_id']}, turns={r['turn_count']}")
    episode_id = r["episode_id"]

    # Get existing subjects
    r2 = urllib.request.urlopen(urllib.request.Request(
        args.api + "/subjects",
        data=json.dumps({"workspace_id": args.workspace_id, "top_level_only": False}).encode(),
        headers={"Content-Type": "application/json"}), timeout=30)
    existing = {s["name"]: s["subject_id"] for s in json.loads(r2.read().decode())["subjects"]}

    # Get turn_ids
    os.environ.setdefault("JARVIS_DATABASE_URL", "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis")
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    async def get_ids(eid):
        e = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
        SL = sessionmaker(e, class_=AsyncSession, expire_on_commit=False)
        async with SL() as db:
            rr = await db.execute(sql_text("SELECT id::text FROM turns WHERE episode_id = :eid"), {"eid": eid})
            return [x[0] for x in rr.fetchall()]
    turn_ids = asyncio.run(get_ids(episode_id))

    # Parse subjects spec: 'A;B>A;C>A>B'
    existing_links, new_subjects = [], []
    for spec in args.subjects.split(";"):
        spec = spec.strip()
        if not spec:
            continue
        parts = [x.strip() for x in spec.split(">") if x.strip()]
        # spec written as 'leaf>parent>...', flip to top-down
        parts_top_down = parts[::-1]
        last_parent = None
        for name in parts_top_down:
            if name in existing:
                existing_links.append({"subject_id": existing[name], "turn_ids": turn_ids})
            else:
                item = {"name": name, "turn_ids": turn_ids}
                if last_parent:
                    if last_parent in existing:
                        item["parent_id"] = existing[last_parent]
                    else:
                        item["parent_name"] = last_parent
                new_subjects.append(item)
            last_parent = name

    r3 = urllib.request.urlopen(urllib.request.Request(
        args.api + "/classify-turns",
        data=json.dumps({"workspace_id": args.workspace_id,
                         "existing_links": existing_links, "new_subjects": new_subjects}).encode(),
        headers={"Content-Type": "application/json"}), timeout=30)
    print(f"  classify: {json.loads(r3.read().decode())}")


if __name__ == "__main__":
    main()
