"""Smart project-aware classification — replaces dumb basename heuristic.

Rules (in order of priority):
1. fundmessenger/fundmessage project (any cwd containing these tokens) →
   subject 'fundmessenger' + sub-subject from path component (backend/frontend/...)
2. brain/jarvis/* → 'JARVIS' + sub-subject from path
3. claude/worktrees/* (brain worktrees) → 'JARVIS' (these are jarvis worktrees)
4. Other specific cwds:
   - newsdesk → 'newsdesk'
   - 자료구조, 정보보안*, wsper, blissful-festive-darwin etc. — top-level by basename
5. f:\brain (no sub-path) — content peek:
   - title or first turn mentions 자비스/JARVIS → 'JARVIS'
   - mentions 아르고스/Argos → 'Argos'
   - mentions 캡스톤 → '캡스톤'
   - else → no subject (orphan; manual review)
6. Trivial (turn_count < 3) → skip

CLI: python scripts/smart_classify.py [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter
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
API = os.environ.get("JARVIS_API_URL", "http://127.0.0.1:8012/api/v1/memory")


# Patterns are evaluated in order. First match wins.
# Each rule returns: list of subjects with parent chain. None = use content peek.
def rule_match(cwd: str | None, title: str | None, first_text: str | None) -> list[str] | None:
    """Return subject chains (each as 'A>B>C' string). Empty = no classification. None = needs content peek."""
    cwd = (cwd or "").replace("\\", "/").lower()

    # 1. fundmessenger / fundmessage variants
    if "/fundmessenger" in cwd or "/fundmessage" in cwd or cwd.endswith("/fundmessenger") or cwd.endswith("/fundmessage"):
        # Extract sub-path after fundmessenger/fundmessage
        m = re.search(r"/(fundmessenger|fundmessage)(.*)", cwd)
        if m:
            tail = m.group(2).strip("/")
            if not tail:
                return ["fundmessenger"]
            # First component of tail
            sub = tail.split("/")[0]
            sub_map = {
                "backend": "fundmessenger 백엔드",
                "frontend": "fundmessenger 프론트엔드",
                "prototype": "fundmessenger 프로토타입",
                "test-ai": "fundmessenger AI 테스트",
                "e2e": "fundmessenger E2E",
                "docs": "fundmessenger 문서",
                "spring-backend": "fundmessenger 백엔드",
                "src": "fundmessenger 백엔드",  # ambiguous; default to backend
            }
            child = sub_map.get(sub)
            if child:
                return [f"fundmessenger>{child}"]
            return ["fundmessenger"]

    # 2. brain/jarvis/* — explicit jarvis subdir
    if "/brain/jarvis" in cwd:
        m = re.search(r"/brain/jarvis(.*)", cwd)
        tail = m.group(1).strip("/") if m else ""
        if tail:
            first = tail.split("/")[0]
            sub_map = {
                "frontend": "JARVIS 프론트엔드",
                "backend": "JARVIS 백엔드",
                "docs": "JARVIS 문서",
                "src": "JARVIS 백엔드",
                "scripts": "JARVIS 스크립트",
                "alembic": "JARVIS 백엔드",
            }
            child = sub_map.get(first)
            if child:
                return [f"JARVIS>{child}"]
        return ["JARVIS"]

    # 3. brain worktrees — JARVIS work
    if "/brain/.claude/worktrees" in cwd or "/brain--claude-worktrees" in cwd:
        return ["JARVIS"]

    # 4a. newsdesk
    if "/newsdesk" in cwd:
        return ["newsdesk"]

    # 4b. 자료구조 / 정보보안 / wsper / 기타 학과 / 사이드
    for kw in ["자료구조", "정보보안", "wsper", "blissful-festive-darwin", "unruffled-golick"]:
        if kw.lower() in cwd:
            if kw == "unruffled-golick":
                return ["JARVIS"]  # brain worktree
            return [kw]

    # 5. f:\brain (no specific sub) — content peek
    if cwd in ("f:/brain", "f:/brain/"):
        text_sample = (title or "") + " " + (first_text or "")
        text_lower = text_sample.lower()
        # Cross-language keyword match
        if re.search(r"자비스|\bjarvis\b", text_lower, re.IGNORECASE):
            return ["JARVIS"]
        if re.search(r"아르고스|\bargos\b|argos-crypto", text_lower, re.IGNORECASE):
            return ["Argos"]
        if "캡스톤" in text_lower or "capstone" in text_lower:
            return ["캡스톤"]
        if "secondbrain" in text_lower or "세컨드브레인" in text_lower or "세컨드 브레인" in text_lower:
            return ["SecondBrain"]
        if "예창패" in text_lower:
            return ["예창패"]
        # Default: no clear subject for brain root work
        return []

    # 6. Last-ditch: extract basename
    if cwd:
        bn = cwd.rstrip("/").split("/")[-1]
        if bn and len(bn) >= 3 and not bn.isascii() or (bn.isalpha() and len(bn) > 4):
            return [bn]

    return []  # no classification


def post(path, payload):
    req = urllib.request.Request(
        f"{API}{path}", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


async def _get_or_create_subject_db(
    db: AsyncSession, name: str, parent_id, subject_cache: dict,
) -> str:
    """Direct DB get_or_create — bypasses API rate limit. Returns subject_id (str)."""
    norm = name.lower().strip()
    if norm in subject_cache:
        return subject_cache[norm]
    # Check DB
    r = await db.execute(text("""
        SELECT id::text FROM entities
        WHERE workspace_id = :ws AND name_normalized = :norm
    """), {"ws": WS, "norm": norm})
    row = r.fetchone()
    if row:
        subject_cache[norm] = row[0]
        return row[0]
    # Insert (must generate UUID; Python default uuid4 only applies via ORM)
    import uuid as _uuid
    new_id = str(_uuid.uuid4())
    await db.execute(text("""
        INSERT INTO entities (id, workspace_id, name, name_normalized, entity_type, parent_id)
        VALUES (:id, :ws, :name, :norm, 'concept', :pid)
    """), {"id": new_id, "ws": WS, "name": name, "norm": norm, "pid": parent_id})
    subject_cache[norm] = new_id
    return new_id


async def classify_episode(db: AsyncSession, episode_id: str, subject_cache: dict, dry_run: bool = False) -> tuple[str, list[str]]:
    """Classify one episode. Returns (result_tag, subjects)."""
    r = await db.execute(text("""
        SELECT e.metadata->>'cwd', e.metadata->>'ai_title',
               (SELECT t.text FROM turns t WHERE t.episode_id = e.id ORDER BY t.sequence LIMIT 1) AS first_text,
               (SELECT COUNT(*) FROM turns t WHERE t.episode_id = e.id) AS tc
        FROM episodes e WHERE e.id = :eid
    """), {"eid": episode_id})
    row = r.fetchone()
    if not row:
        return "not_found", []
    cwd, title, first_text, tc = row[0], row[1], row[2], row[3]

    if tc is None or tc < 3:
        return "skip_trivial", []

    subjects = rule_match(cwd, title, first_text)
    if subjects is None:
        return "needs_review", []
    if not subjects:
        return "no_subject", []

    if dry_run:
        return "ok_dry", subjects

    # Resolve subject chain → get/create each subject in chain
    seen = set()
    resolved_subject_ids: list[str] = []
    for spec in subjects:
        parts = [p.strip() for p in spec.split(">") if p.strip()]
        parent_id = None
        for name in parts:
            if name in seen:
                # Already resolved earlier in this episode — recover from cache
                parent_id = subject_cache.get(name.lower().strip())
                continue
            seen.add(name)
            sid = await _get_or_create_subject_db(db, name, parent_id, subject_cache)
            resolved_subject_ids.append(sid)
            parent_id = sid

    # Bulk insert turn_subjects via SQL with ON CONFLICT
    subj_array = "ARRAY[" + ",".join(f"'{s}'::uuid" for s in resolved_subject_ids) + "]"
    await db.execute(text(f"""
        INSERT INTO turn_subjects (turn_id, subject_id, workspace_id)
        SELECT t.id, s.subject_id, :ws
        FROM turns t
        CROSS JOIN UNNEST({subj_array}) AS s(subject_id)
        WHERE t.episode_id = :eid
        ON CONFLICT (turn_id, subject_id) DO NOTHING
    """), {"ws": WS, "eid": episode_id})

    return "ok", subjects


async def main(limit: int | None = None, dry_run: bool = False):
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as db:
        # Get unclassified episodes
        r = await db.execute(text("""
            SELECT e.id::text, COUNT(t.id) AS tc
            FROM episodes e
            LEFT JOIN turns t ON t.episode_id = e.id
            WHERE e.workspace_id = :ws
            GROUP BY e.id
            HAVING NOT EXISTS (
                SELECT 1 FROM turn_subjects ts
                JOIN turns t2 ON t2.id = ts.turn_id
                WHERE t2.episode_id = e.id
            )
            ORDER BY tc DESC NULLS LAST
        """), {"ws": WS})
        episode_ids = [(row[0], row[1]) for row in r.fetchall()]

    if limit:
        episode_ids = episode_ids[:limit]
    print(f"Classifying {len(episode_ids)} episodes (dry_run={dry_run})...")

    t0 = time.time()
    tags = Counter()
    subj_freq = Counter()
    needs_review_ids = []

    subject_cache: dict = {}
    for i, (eid, tc) in enumerate(episode_ids):
        async with SessionLocal() as db:
            try:
                tag, subjects = await classify_episode(db, eid, subject_cache, dry_run)
                await db.commit()
            except Exception as exc:
                tag = "error"
                subjects = []
                if i < 5 or i % 200 == 0:
                    print(f"  ERROR {eid}: {str(exc)[:200]}")
        tags[tag] += 1
        for s in subjects:
            top = s.split(">")[0]
            subj_freq[top] += 1
        if tag == "needs_review":
            needs_review_ids.append(eid)
        if (i + 1) % 200 == 0 or i + 1 == len(episode_ids):
            elapsed = time.time() - t0
            print(f"  [{i+1}/{len(episode_ids)}] {dict(tags)} ({elapsed:.1f}s)")

    await engine.dispose()

    print(f"\n=== Summary ({time.time() - t0:.1f}s) ===")
    for tag, n in tags.most_common():
        print(f"  {tag}: {n}")
    print(f"\nTop subjects (top-level only):")
    for name, n in subj_freq.most_common(15):
        print(f"  [{n:>4}x] {name}")
    if needs_review_ids:
        print(f"\nNeeds manual review: {len(needs_review_ids)}")
        for eid in needs_review_ids[:10]:
            print(f"  {eid}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(main(args.limit, args.dry_run))
