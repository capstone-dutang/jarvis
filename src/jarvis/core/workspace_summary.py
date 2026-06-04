"""Rich workspace summary — list workspaces with activity + top subjects.

Powers the workspace picker / dashboard so the UI shows for each ws:
  - status, description (curated label)
  - episode_count, turn_count, last_activity
  - top 3 subjects (concept-type entities by turn_subjects volume)

Single SQL pass per workspace via correlated sub-selects — N is the number
of workspaces (currently ~9), so this is cheap and avoids N+1 round trips
from the API layer.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def list_workspaces_rich(
    db: AsyncSession,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    """List workspaces with description + activity stats + top subjects.

    Sort order: active first, then by last_activity DESC (nulls last).
    """
    status_filter = "" if include_hidden else "WHERE w.status = 'active'"
    rows = await db.execute(
        text(f"""
            SELECT
                w.id,
                w.name,
                w.status,
                w.description,
                w.created_at,
                (SELECT COUNT(*) FROM episodes e WHERE e.workspace_id = w.id
                    AND (e.metadata->>'deleted' IS DISTINCT FROM 'true'))
                    AS episode_count,
                (SELECT COUNT(*) FROM turns t WHERE t.workspace_id = w.id)
                    AS turn_count,
                (SELECT MAX(t.timestamp) FROM turns t WHERE t.workspace_id = w.id)
                    AS last_activity,
                (SELECT array_agg(name ORDER BY tc DESC)
                 FROM (
                    SELECT e.name, COUNT(ts.turn_id) AS tc
                    FROM entities e
                    JOIN turn_subjects ts ON ts.subject_id = e.id
                    WHERE e.workspace_id = w.id
                      AND e.parent_id IS NULL
                      AND e.entity_type = 'concept'
                    GROUP BY e.name
                    ORDER BY tc DESC
                    LIMIT 3
                 ) sub) AS top_subjects
            FROM workspaces w
            {status_filter}
            ORDER BY (w.status = 'active') DESC,
                     last_activity DESC NULLS LAST,
                     w.name ASC
        """)
    )
    out: list[dict[str, Any]] = []
    for row in rows.mappings():
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "status": row["status"],
                "description": row["description"],
                "created_at": row["created_at"],
                "episode_count": int(row["episode_count"] or 0),
                "turn_count": int(row["turn_count"] or 0),
                "last_activity": row["last_activity"],
                "top_subjects": list(row["top_subjects"] or []),
            }
        )
    return out
