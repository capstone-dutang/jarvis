"""Reflect workflow: (date × subject) summaries.

Day/week/month zoom views are built by combining these summaries.
The AI client generates the summary text (using its own context); the server
only stores and reads (no server LLM, per JARVIS principles).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


async def save_summaries(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    summaries: list[dict[str, Any]],
) -> int:
    """Upsert (workspace, subject, date) summaries.

    Each item: {subject_id, date (YYYY-MM-DD), summary, turn_count}.
    Returns count of upserted rows.
    """
    if not summaries:
        return 0

    upserted = 0
    for s in summaries:
        raw_dt = s["date"]
        dt_obj = raw_dt if isinstance(raw_dt, date) else _parse_date(raw_dt)
        await db.execute(
            text("""
                INSERT INTO daily_subject_summaries
                    (workspace_id, subject_id, date, summary,
                     turn_count, unique_turn_count, updated_at)
                VALUES (:ws, :sid, :dt, :sum, :tc, :utc, now())
                ON CONFLICT (workspace_id, subject_id, date) DO UPDATE
                SET summary = EXCLUDED.summary,
                    turn_count = EXCLUDED.turn_count,
                    unique_turn_count = EXCLUDED.unique_turn_count,
                    updated_at = now()
            """),
            {
                "ws": str(workspace_id),
                "sid": str(s["subject_id"]),
                "dt": dt_obj,
                "sum": s["summary"],
                "tc": int(s.get("turn_count", 0)),
                # When caller does not supply unique_turn_count, default to
                # turn_count — keeps legacy callers correct on leaf subjects
                # (no descendants ⇒ unique == total).
                "utc": int(s.get("unique_turn_count", s.get("turn_count", 0))),
            },
        )
        upserted += 1
    return upserted


async def get_summaries(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    date_from: date | None = None,
    date_to: date | None = None,
    subject_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Read summaries in date range, joined with subject name."""
    clauses = ["dss.workspace_id = :ws"]
    params: dict[str, Any] = {"ws": str(workspace_id)}
    if date_from is not None:
        clauses.append("dss.date >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        clauses.append("dss.date < :date_to")
        params["date_to"] = date_to
    if subject_id is not None:
        clauses.append("dss.subject_id = :sid")
        params["sid"] = str(subject_id)
    where_sql = " AND ".join(clauses)

    rows = await db.execute(
        text(f"""
            SELECT dss.id, dss.subject_id, e.name, dss.date,
                   dss.summary, dss.turn_count, dss.unique_turn_count
            FROM daily_subject_summaries dss
            JOIN entities e ON e.id = dss.subject_id
            WHERE {where_sql}
            ORDER BY dss.date DESC, e.name ASC
        """),
        params,
    )
    return [
        {
            "summary_id": r[0],
            "subject_id": r[1],
            "subject_name": r[2],
            "date": r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3]),
            "summary": r[4],
            "turn_count": int(r[5]),
            # NULL ⇒ legacy row pre-P8; fall back to turn_count so callers
            # never see None. Backfill migration sets this for existing rows.
            "unique_turn_count": int(r[6] if r[6] is not None else r[5]),
        }
        for r in rows.fetchall()
    ]


async def get_pending_reflects(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Find (date, subject) pairs that have turns but no summary yet.

    Date is derived from turn.timestamp::date.
    """
    clauses = ["t.workspace_id = :ws"]
    params: dict[str, Any] = {"ws": str(workspace_id)}
    if date_from is not None:
        clauses.append("t.timestamp::date >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        clauses.append("t.timestamp::date < :date_to")
        params["date_to"] = date_to
    where_sql = " AND ".join(clauses)

    rows = await db.execute(
        text(f"""
            WITH turn_subject_dates AS (
                SELECT
                    t.timestamp::date AS dt,
                    ts.subject_id,
                    COUNT(*) AS turn_count
                FROM turns t
                JOIN turn_subjects ts ON ts.turn_id = t.id
                WHERE {where_sql}
                GROUP BY t.timestamp::date, ts.subject_id
            )
            SELECT tsd.dt, tsd.subject_id, e.name, tsd.turn_count
            FROM turn_subject_dates tsd
            JOIN entities e ON e.id = tsd.subject_id
            LEFT JOIN daily_subject_summaries dss
                ON dss.workspace_id = :ws
               AND dss.subject_id = tsd.subject_id
               AND dss.date = tsd.dt
            WHERE dss.id IS NULL
            ORDER BY tsd.dt DESC, e.name ASC
        """),
        params,
    )
    return [
        {
            "date": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
            "subject_id": r[1],
            "subject_name": r[2],
            "turn_count": int(r[3]),
        }
        for r in rows.fetchall()
    ]
