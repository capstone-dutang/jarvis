"""Heuristic subject classification — engine validation, not production AI.

Two passes:
1. cwd-based top-level subjects: each episode's working directory (basename)
   becomes a top-level subject. All turns in that episode link to it.
2. Text keyword matching: scan turn text for known important entity names
   (manually curated list); link to those subjects too.

Used to seed turn_subjects M:N links at scale ($0 cost, no AI), so we can
verify timeline / subject-feed / subject-tree behavior end-to-end.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault(
    "JARVIS_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@localhost:5440/jarvis",
)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jarvis.core.entity_resolution import normalize_name
from jarvis.models.tables import Entity, EntityType, Workspace

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("classify")


# Curated set of cross-mentioned subjects (text keyword matching pass).
# Keys are canonical subject names; values are case-insensitive substring patterns.
KEYWORD_SUBJECTS = {
    "JARVIS": [r"\bJARVIS\b", r"자비스", r"\bjarvis\b"],
    "Argos": [r"\bArgos\b", r"\bARGOS\b", r"아르고스", r"argos-crypto", r"\bargos\b"],
    "SecondBrain": [r"\bSecondBrain\b", r"\bsecond[\s-]?brain\b", r"세컨드브레인", r"세컨드 브레인"],
    "fundmessenger": [r"\bfund[\s-]?messenger\b", r"펀드메신저", r"펀드메"],
    "Claude Code": [r"\bclaude[\s-]?code\b", r"클로드[\s-]?코드"],
    "Maverix": [r"\bMaverix\b", r"마버릭스"],
    "예창패": [r"예창패"],
    "캡스톤": [r"캡스톤", r"capstone"],
}
# Pre-compile patterns
KEYWORD_RES = {
    name: [re.compile(p, re.IGNORECASE) for p in patterns]
    for name, patterns in KEYWORD_SUBJECTS.items()
}


def cwd_to_subject(cwd: str | None) -> str | None:
    """Extract canonical subject name from cwd path.

    Examples:
        F:\\brain\\jarvis → jarvis
        F:/brain/argos-crypto → argos-crypto
        C:\\Users\\lhhh0\\OneDrive\\Desktop\\자료구조 → 자료구조
    """
    if not cwd:
        return None
    # Normalize separators
    cwd = cwd.replace("\\", "/").rstrip("/")
    basename = cwd.split("/")[-1] if "/" in cwd else cwd
    if not basename or len(basename) < 2:
        return None
    # Skip drive letters / single chars
    if len(basename) == 2 and basename[1] == ":":
        return None
    return basename


async def get_or_create_subject(
    db: AsyncSession,
    workspace_id, name: str,
    cache: dict,
) -> "uuid.UUID":
    """Return subject_id from cache, or fetch/create."""
    import uuid
    normalized = normalize_name(name)
    if normalized in cache:
        return cache[normalized]
    result = await db.execute(
        select(Entity).where(
            Entity.workspace_id == workspace_id,
            Entity.name_normalized == normalized,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        cache[normalized] = existing.id
        return existing.id
    ent = Entity(
        workspace_id=workspace_id,
        name=name,
        name_normalized=normalized,
        entity_type=EntityType.concept,
        parent_id=None,
    )
    db.add(ent)
    await db.flush()
    cache[normalized] = ent.id
    return ent.id


async def main(workspace_name: str, batch_size: int = 200):
    engine = create_async_engine(os.environ["JARVIS_DATABASE_URL"])
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as db:
        ws_q = await db.execute(select(Workspace).where(Workspace.name == workspace_name))
        ws = ws_q.scalar_one_or_none()
        if not ws:
            logger.error("Workspace %s not found", workspace_name)
            return
        ws_id = ws.id
        logger.info("Workspace: %s (%s)", ws.name, ws_id)

    # Pass 1: cwd-based classification (per-episode)
    t0 = time.time()
    cache: dict = {}
    stats = Counter()

    async with SessionLocal() as db:
        # Load episodes + cwd from metadata, count turns per episode
        ep_rows = await db.execute(text("""
            SELECT
                e.id,
                e.metadata->>'cwd' AS cwd,
                COUNT(t.id) AS turn_count
            FROM episodes e
            LEFT JOIN turns t ON t.episode_id = e.id
            WHERE e.workspace_id = :ws
            GROUP BY e.id, e.metadata->>'cwd'
        """), {"ws": str(ws_id)})
        episodes = ep_rows.fetchall()
        logger.info("Pass 1 (cwd → subject): %d episodes", len(episodes))

        # Group episodes by cwd-derived subject name
        ep_by_subj: dict[str, list] = defaultdict(list)
        for ep_id, cwd, turn_count in episodes:
            subj_name = cwd_to_subject(cwd)
            if not subj_name:
                stats["episode_no_cwd"] += 1
                continue
            ep_by_subj[subj_name].append(ep_id)

        # Create subjects + link turns per subject (one query per subject)
        for subj_name, ep_ids in ep_by_subj.items():
            sid = await get_or_create_subject(db, ws_id, subj_name, cache)
            stats["episode_cwd_classified"] += len(ep_ids)
            id_array = "ARRAY[" + ",".join(f"'{e}'::uuid" for e in ep_ids) + "]"
            res = await db.execute(text(f"""
                INSERT INTO turn_subjects (turn_id, subject_id, workspace_id)
                SELECT t.id, :sid, :ws
                FROM turns t
                WHERE t.workspace_id = :ws
                  AND t.episode_id = ANY({id_array})
                ON CONFLICT (turn_id, subject_id) DO NOTHING
                RETURNING turn_id
            """), {"sid": str(sid), "ws": str(ws_id)})
            cnt = len(res.fetchall())
            stats["pass1_links"] += cnt
            logger.info("  '%s': %d episodes → %d turn links", subj_name, len(ep_ids), cnt)

        await db.commit()

    # Pass 2: text keyword matching
    logger.info("\nPass 2 (text keyword): %d patterns", len(KEYWORD_RES))
    for kw_name in KEYWORD_RES:
        async with SessionLocal() as db:
            kw_subj_id = await get_or_create_subject(db, ws_id, kw_name, cache)
            await db.commit()
        # Build SQL with regex OR
        pattern_sql = "|".join(
            re.sub(r"\\b", r"\\m", p.pattern.replace("\\", "\\\\"))
            for p in KEYWORD_RES[kw_name]
        )
        async with SessionLocal() as db:
            res = await db.execute(text(f"""
                INSERT INTO turn_subjects (turn_id, subject_id, workspace_id)
                SELECT t.id, :sid, :ws
                FROM turns t
                WHERE t.workspace_id = :ws
                  AND t.text ~* :pat
                ON CONFLICT (turn_id, subject_id) DO NOTHING
                RETURNING turn_id
            """), {"sid": str(kw_subj_id), "ws": str(ws_id), "pat": pattern_sql})
            cnt = len(res.fetchall())
            await db.commit()
        logger.info("  '%s': %d turn links", kw_name, cnt)
        stats[f"pass2_{kw_name}"] = cnt

    await engine.dispose()
    logger.info("\n=== Done in %.1fs ===", time.time() - t0)
    for k, v in stats.items():
        logger.info("  %s: %d", k, v)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-name", default="vision-test")
    args = parser.parse_args()
    asyncio.run(main(args.workspace_name))
