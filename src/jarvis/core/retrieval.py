"""Retrieval API — timeline, subject feed, subject tree.

The new vision: AI client (or web UI) calls these to render:
- Day/week/month view: timeline filtered by date range
- Subject page: feed of turns linked to a subject (and descendants)
- Sidebar tree: hierarchical subject structure
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_timeline(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    date_from: Any = None,
    date_to: Any = None,
    descending: bool = True,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return turns in [date_from, date_to) ordered by timestamp.

    Includes linked subject_ids per turn (array_agg).
    """
    order = "DESC" if descending else "ASC"

    where_clauses = ["t.workspace_id = :ws"]
    params: dict[str, Any] = {"ws": str(workspace_id), "lim": limit, "off": offset}
    if date_from is not None:
        where_clauses.append("t.timestamp >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        where_clauses.append("t.timestamp < :date_to")
        params["date_to"] = date_to
    where_sql = " AND ".join(where_clauses)

    # Total count for has_more / pagination
    total_q = await db.execute(
        text(f"SELECT COUNT(*) FROM turns t WHERE {where_sql}"),
        params,
    )
    total = int(total_q.scalar() or 0)

    rows_q = await db.execute(
        text(f"""
            SELECT
                t.id, t.episode_id, t.sequence, t.role, t.text, t.timestamp,
                COALESCE(
                    (SELECT array_agg(ts.subject_id)
                     FROM turn_subjects ts WHERE ts.turn_id = t.id),
                    ARRAY[]::uuid[]
                ) AS subject_ids
            FROM turns t
            WHERE {where_sql}
            ORDER BY t.timestamp {order}
            LIMIT :lim OFFSET :off
        """),
        params,
    )
    turns = [
        {
            "turn_id": r[0],
            "episode_id": r[1],
            "sequence": r[2],
            "role": r[3],
            "text": r[4],
            "timestamp": r[5],
            "subjects": list(r[6] or []),
        }
        for r in rows_q.fetchall()
    ]
    return turns, total


async def _resolve_subject_with_descendants(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    subject_id: uuid.UUID,
    include_descendants: bool,
) -> list[uuid.UUID]:
    """Return subject_id + all descendants (via recursive CTE on parent_id)."""
    if not include_descendants:
        return [subject_id]
    rows = await db.execute(
        text("""
            WITH RECURSIVE subject_tree AS (
                SELECT id FROM entities
                WHERE id = :root AND workspace_id = :ws
                UNION ALL
                SELECT e.id FROM entities e
                JOIN subject_tree st ON e.parent_id = st.id
                WHERE e.workspace_id = :ws
            )
            SELECT id FROM subject_tree
        """),
        {"root": str(subject_id), "ws": str(workspace_id)},
    )
    return [r[0] for r in rows.fetchall()]


async def get_subject_feed(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    subject_id: uuid.UUID,
    include_descendants: bool = True,
    date_from: Any = None,
    date_to: Any = None,
    descending: bool = True,
    limit: int = 500,
    offset: int = 0,
) -> tuple[str, list[dict], int]:
    """Turns linked to subject (and descendants), ordered by time. Returns (subject_name, turns, total)."""

    # Get subject name first
    name_q = await db.execute(
        text("SELECT name FROM entities WHERE id = :id AND workspace_id = :ws"),
        {"id": str(subject_id), "ws": str(workspace_id)},
    )
    name_row = name_q.fetchone()
    if not name_row:
        return "", [], 0
    subject_name = name_row[0]

    subj_ids = await _resolve_subject_with_descendants(db, workspace_id, subject_id, include_descendants)
    if not subj_ids:
        return subject_name, [], 0

    order = "DESC" if descending else "ASC"
    id_array = "ARRAY[" + ",".join(f"'{sid}'::uuid" for sid in subj_ids) + "]"

    where_clauses = [
        "t.workspace_id = :ws",
        f"ts.subject_id = ANY({id_array})",
    ]
    params: dict[str, Any] = {"ws": str(workspace_id), "lim": limit, "off": offset}
    if date_from is not None:
        where_clauses.append("t.timestamp >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        where_clauses.append("t.timestamp < :date_to")
        params["date_to"] = date_to
    where_sql = " AND ".join(where_clauses)

    total_q = await db.execute(
        text(f"""
            SELECT COUNT(DISTINCT t.id)
            FROM turns t
            JOIN turn_subjects ts ON ts.turn_id = t.id
            WHERE {where_sql}
        """),
        params,
    )
    total = int(total_q.scalar() or 0)

    rows_q = await db.execute(
        text(f"""
            SELECT DISTINCT
                t.id, t.episode_id, t.sequence, t.role, t.text, t.timestamp,
                (SELECT array_agg(ts2.subject_id)
                 FROM turn_subjects ts2 WHERE ts2.turn_id = t.id) AS subject_ids
            FROM turns t
            JOIN turn_subjects ts ON ts.turn_id = t.id
            WHERE {where_sql}
            ORDER BY t.timestamp {order}
            LIMIT :lim OFFSET :off
        """),
        params,
    )
    turns = [
        {
            "turn_id": r[0],
            "episode_id": r[1],
            "sequence": r[2],
            "role": r[3],
            "text": r[4],
            "timestamp": r[5],
            "subjects": list(r[6] or []),
        }
        for r in rows_q.fetchall()
    ]
    return subject_name, turns, total


async def get_subject_tree(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> tuple[list[dict], int]:
    """Build hierarchical subject tree from entities.parent_id."""
    rows = await db.execute(
        text("""
            SELECT
                e.id, e.name, e.parent_id,
                COALESCE(tc.cnt, 0) AS turn_count
            FROM entities e
            LEFT JOIN (
                SELECT subject_id, COUNT(*) AS cnt
                FROM turn_subjects
                WHERE workspace_id = :ws
                GROUP BY subject_id
            ) tc ON tc.subject_id = e.id
            WHERE e.workspace_id = :ws
            ORDER BY e.name
        """),
        {"ws": str(workspace_id)},
    )
    flat = [
        {
            "subject_id": r[0],
            "name": r[1],
            "parent_id": r[2],
            "turn_count": int(r[3]),
            "children": [],
        }
        for r in rows.fetchall()
    ]
    by_id = {n["subject_id"]: n for n in flat}
    roots: list[dict] = []
    for n in flat:
        if n["parent_id"] is None:
            roots.append(n)
        else:
            parent = by_id.get(n["parent_id"])
            if parent is not None:
                parent["children"].append(n)
            else:
                # Orphaned (parent deleted?) → treat as root
                roots.append(n)
    return roots, len(flat)
