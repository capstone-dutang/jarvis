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

    # Total count for has_more / pagination — match visible filter
    total_q = await db.execute(
        text(f"""
            SELECT COUNT(*) FROM turns t
            WHERE {where_sql}
              AND (t.cleaned_text_v2 IS NOT NULL OR t.cleanup_metadata IS NULL)
        """),
        params,
    )
    total = int(total_q.scalar() or 0)

    rows_q = await db.execute(
        text(f"""
            SELECT
                t.id, t.episode_id, t.sequence, t.role, t.text,
                COALESCE(t.cleaned_text_v2, t.cleaned_text) AS cleaned_text,
                t.timestamp,
                COALESCE(
                    (SELECT array_agg(ts.subject_id)
                     FROM turn_subjects ts WHERE ts.turn_id = t.id),
                    ARRAY[]::uuid[]
                ) AS subject_ids
            FROM turns t
            WHERE {where_sql}
              AND (t.cleaned_text_v2 IS NOT NULL OR t.cleanup_metadata IS NULL)
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
            "cleaned_text": r[5],
            "timestamp": r[6],
            "subjects": list(r[7] or []),
        }
        for r in rows_q.fetchall()
    ]
    return turns, total


async def get_date_buckets(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    subject_id: uuid.UUID | None = None,
) -> tuple[list[dict], int]:
    """Return per-day turn count for the full workspace history. Light — no turn payload."""
    params: dict[str, Any] = {"ws": str(workspace_id)}
    if subject_id is None:
        sql = """
            SELECT DATE(timestamp AT TIME ZONE 'UTC') AS d, COUNT(*) AS n
            FROM turns
            WHERE workspace_id = :ws
            GROUP BY 1
            ORDER BY 1
        """
    else:
        subj_ids = await _resolve_subject_with_descendants(db, workspace_id, subject_id, True)
        id_array = "ARRAY[" + ",".join(f"'{sid}'::uuid" for sid in subj_ids) + "]"
        sql = f"""
            SELECT DATE(t.timestamp AT TIME ZONE 'UTC') AS d, COUNT(DISTINCT t.id) AS n
            FROM turns t
            JOIN turn_subjects ts ON ts.turn_id = t.id
            WHERE t.workspace_id = :ws AND ts.subject_id = ANY({id_array})
            GROUP BY 1
            ORDER BY 1
        """
    rows = await db.execute(text(sql), params)
    buckets = [{"date": r[0].isoformat(), "count": int(r[1])} for r in rows.fetchall()]
    total = sum(b["count"] for b in buckets)
    return buckets, total


async def get_entity_page(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    entity_id: uuid.UUID,
    fact_limit: int = 30,
    relation_limit: int = 24,
    episode_limit: int = 10,
) -> dict | None:
    """Wiki-style entity page payload — entity + facts + bidirectional relations + recent episodes."""
    ws = str(workspace_id)
    eid = str(entity_id)

    # Entity basic
    e_row = await db.execute(
        text("""
            SELECT name, entity_type::text, parent_id, aliases, wiki_article
            FROM entities WHERE id = :eid AND workspace_id = :ws
        """),
        {"eid": eid, "ws": ws},
    )
    e = e_row.fetchone()
    if not e:
        return None
    name, entity_type, parent_id, aliases_json, wiki_article = e
    aliases_list: list[str] = []
    if aliases_json:
        try:
            aliases_list = list(aliases_json) if isinstance(aliases_json, list) else []
        except Exception:
            aliases_list = []

    # Parent
    parent = None
    if parent_id:
        p = await db.execute(
            text("SELECT id, name FROM entities WHERE id = :p AND workspace_id = :ws"),
            {"p": str(parent_id), "ws": ws},
        )
        pr = p.fetchone()
        if pr:
            parent = {"entity_id": pr[0], "name": pr[1]}

    # Children
    c_rows = await db.execute(
        text("""
            SELECT e.id, e.name
            FROM entities e
            WHERE e.parent_id = :eid AND e.workspace_id = :ws
            ORDER BY (SELECT COUNT(*) FROM turn_subjects ts WHERE ts.subject_id = e.id) DESC
        """),
        {"eid": eid, "ws": ws},
    )
    children = [{"entity_id": r[0], "name": r[1]} for r in c_rows.fetchall()]

    # Facts (recent first, both active and superseded)
    f_rows = await db.execute(
        text("""
            SELECT id, predicate, object_value, source_episode_id, source_quote,
                   valid_from, superseded_at, trust_level::text
            FROM knowledge_facts
            WHERE entity_id = :eid AND workspace_id = :ws
            ORDER BY valid_from DESC
            LIMIT :lim
        """),
        {"eid": eid, "ws": ws, "lim": fact_limit},
    )
    facts = [
        {
            "fact_id": r[0],
            "predicate": r[1],
            "object_value": r[2],
            "source_episode_id": r[3],
            "source_quote": r[4] or "",
            "valid_from": r[5],
            "superseded_at": r[6],
            "trust_level": r[7],
        }
        for r in f_rows.fetchall()
    ]

    # Bidirectional relations
    r_rows = await db.execute(
        text("""
            SELECT er.id, er.relation_type::text, er.from_entity_id, er.to_entity_id,
                   er.weight, er.valid_from,
                   CASE WHEN er.from_entity_id = :eid THEN 'out' ELSE 'in' END AS dir,
                   eo.name AS other_name, eo.id AS other_id
            FROM entity_relations er
            JOIN entities eo ON eo.id = CASE WHEN er.from_entity_id = :eid THEN er.to_entity_id ELSE er.from_entity_id END
            WHERE (er.from_entity_id = :eid OR er.to_entity_id = :eid) AND er.workspace_id = :ws
            ORDER BY er.weight DESC, er.valid_from DESC
            LIMIT :lim
        """),
        {"eid": eid, "ws": ws, "lim": relation_limit},
    )
    relations = [
        {
            "relation_id": r[0],
            "relation_type": r[1],
            "other_entity_id": r[8],
            "other_entity_name": r[7],
            "direction": r[6],
            "weight": float(r[4]),
            "valid_from": r[5],
        }
        for r in r_rows.fetchall()
    ]

    # Recent episodes via turn_subjects (entity appeared in which episodes)
    ep_rows = await db.execute(
        text("""
            SELECT e.id, DATE(MIN(t.timestamp) AT TIME ZONE 'UTC') AS d,
                   e.summary, COUNT(t.id) AS turn_n, e.human_summary
            FROM turn_subjects ts
            JOIN turns t ON t.id = ts.turn_id
            JOIN episodes e ON e.id = t.episode_id
            WHERE ts.subject_id = :eid AND ts.workspace_id = :ws
              AND (e.metadata->>'deleted' IS DISTINCT FROM 'true')
            GROUP BY e.id, e.summary, e.human_summary
            ORDER BY d DESC NULLS LAST
            LIMIT :lim
        """),
        {"eid": eid, "ws": ws, "lim": episode_limit},
    )
    recent_episodes = [
        {
            "episode_id": r[0],
            "date": r[1].isoformat() if r[1] else "",
            # 사람용 요약(human_summary) 우선, 없으면 AI summary fallback.
            "summary": (r[4] or r[2] or "")[:400],
            "turn_count": int(r[3]),
        }
        for r in ep_rows.fetchall()
    ]

    return {
        "entity_id": entity_id,
        "name": name,
        "entity_type": entity_type,
        "parent": parent,
        "children": children,
        "aliases": aliases_list,
        "wiki_article": wiki_article,
        "facts": facts,
        "relations": relations,
        "recent_episodes": recent_episodes,
    }


async def get_dashboard(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    recent_days: int = 7,
    recent_limit: int = 10,
    on_this_day_limit: int = 5,
    top_entities_limit: int = 10,
) -> dict:
    """Home dashboard payload — single call serving the 4-block home.

    Combines workspace-wide counts, latest daily summaries (last N days),
    "on this day" history (same month·day in past years), and the top
    entities by knowledge_fact count.

    The first/last date pair uses MIN/MAX(turns.timestamp) when turns are
    present; otherwise it falls back to MIN/MAX(episodes.created_at). Today
    is read from PostgreSQL's clock so the dashboard agrees with the same
    "today" the server-side ingest pipeline sees.
    """
    ws = str(workspace_id)

    # ── Stats ── workspace-wide counts in one round trip
    stats_row = await db.execute(
        text(
            """
            SELECT
                (SELECT COUNT(*) FROM episodes WHERE workspace_id = :ws
                    AND (metadata->>'deleted' IS DISTINCT FROM 'true')) AS episode_count,
                (SELECT COUNT(*) FROM turns WHERE workspace_id = :ws) AS turn_count,
                (SELECT COUNT(*) FROM entities WHERE workspace_id = :ws) AS entity_count,
                (SELECT COUNT(*) FROM knowledge_facts
                    WHERE workspace_id = :ws AND superseded_at IS NULL) AS fact_count,
                (SELECT DATE(MIN(timestamp) AT TIME ZONE 'UTC') FROM turns WHERE workspace_id = :ws) AS turns_first,
                (SELECT DATE(MAX(timestamp) AT TIME ZONE 'UTC') FROM turns WHERE workspace_id = :ws) AS turns_last,
                (SELECT DATE(MIN(created_at) AT TIME ZONE 'UTC') FROM episodes WHERE workspace_id = :ws
                    AND (metadata->>'deleted' IS DISTINCT FROM 'true')) AS ep_first,
                (SELECT DATE(MAX(created_at) AT TIME ZONE 'UTC') FROM episodes WHERE workspace_id = :ws
                    AND (metadata->>'deleted' IS DISTINCT FROM 'true')) AS ep_last
            """
        ),
        {"ws": ws},
    )
    sr = stats_row.fetchone()
    if sr is None:
        sr = (0, 0, 0, 0, None, None, None, None)
    first_d = sr[4] or sr[6]
    last_d = sr[5] or sr[7]
    stats = {
        "episode_count": int(sr[0] or 0),
        "turn_count": int(sr[1] or 0),
        "entity_count": int(sr[2] or 0),
        "fact_count": int(sr[3] or 0),
        "first_date": first_d.isoformat() if first_d else None,
        "last_date": last_d.isoformat() if last_d else None,
    }

    # ── Recent daily summaries (last N days) ──
    # asyncpg refuses str→interval; use make_interval(days := :n) so the
    # parameter stays a plain integer.
    # P8 — only surface *top-level* subjects (parent_id IS NULL) so the
    # home card always shows the project-level paragraph (JARVIS / Argos),
    # never the sub-subject leaves that share the same day. The sub-rows
    # are still reachable via the per-subject feed when the user drills in.
    recent_rows = await db.execute(
        text(
            """
            SELECT dss.date, dss.subject_id, e.name,
                   dss.summary, dss.turn_count, dss.unique_turn_count
            FROM daily_subject_summaries dss
            JOIN entities e ON e.id = dss.subject_id
            WHERE dss.workspace_id = :ws
              AND e.parent_id IS NULL
              AND dss.date >= (CURRENT_DATE - make_interval(days => :days))::date
            ORDER BY dss.date DESC, e.name ASC
            LIMIT :lim
            """
        ),
        {"ws": ws, "days": int(recent_days), "lim": recent_limit},
    )
    recent_summaries = [
        {
            "date": r[0].isoformat(),
            "subject_id": r[1],
            "subject_name": r[2],
            "summary": r[3] or "",
            "turn_count": int(r[4] or 0),
            "unique_turn_count": int(r[5] if r[5] is not None else (r[4] or 0)),
        }
        for r in recent_rows.fetchall()
    ]

    # ── On This Day — same MM-DD in any past year (excludes today) ──
    on_this_day_rows = await db.execute(
        text(
            """
            SELECT e.id,
                   DATE(e.created_at AT TIME ZONE 'UTC') AS d,
                   e.summary,
                   (CURRENT_DATE - DATE(e.created_at AT TIME ZONE 'UTC')) AS days_ago
            FROM episodes e
            WHERE e.workspace_id = :ws
              AND (e.metadata->>'deleted' IS DISTINCT FROM 'true')
              AND EXTRACT(MONTH FROM e.created_at AT TIME ZONE 'UTC') = EXTRACT(MONTH FROM CURRENT_DATE)
              AND EXTRACT(DAY FROM e.created_at AT TIME ZONE 'UTC') = EXTRACT(DAY FROM CURRENT_DATE)
              AND DATE(e.created_at AT TIME ZONE 'UTC') < CURRENT_DATE
            ORDER BY d DESC
            LIMIT :lim
            """
        ),
        {"ws": ws, "lim": on_this_day_limit},
    )
    on_this_day = [
        {
            "date": r[1].isoformat(),
            "episode_id": r[0],
            "summary": (r[2] or "")[:300],
            "days_ago": int(r[3] or 0),
        }
        for r in on_this_day_rows.fetchall()
    ]

    # ── Top entities by active fact count, with relation degree ──
    # P6: exclude entity_type='episode_topic' (session-label anchors). They
    # belong on subject-feed / date-bucket UI, not on the wiki dashboard.
    top_rows = await db.execute(
        text(
            """
            SELECT e.id, e.name,
                   COALESCE(fc.cnt, 0) AS fact_count,
                   COALESCE(rc.cnt, 0) AS relation_count
            FROM entities e
            LEFT JOIN (
                SELECT entity_id, COUNT(*) AS cnt
                FROM knowledge_facts
                WHERE workspace_id = :ws AND superseded_at IS NULL
                GROUP BY entity_id
            ) fc ON fc.entity_id = e.id
            LEFT JOIN (
                SELECT eid, COUNT(*) AS cnt FROM (
                    SELECT from_entity_id AS eid FROM entity_relations
                        WHERE workspace_id = :ws AND valid_to IS NULL
                    UNION ALL
                    SELECT to_entity_id AS eid FROM entity_relations
                        WHERE workspace_id = :ws AND valid_to IS NULL
                ) er
                GROUP BY eid
            ) rc ON rc.eid = e.id
            WHERE e.workspace_id = :ws
              AND e.entity_type != 'episode_topic'
              AND COALESCE(fc.cnt, 0) > 0
            ORDER BY fact_count DESC, relation_count DESC, e.name ASC
            LIMIT :lim
            """
        ),
        {"ws": ws, "lim": top_entities_limit},
    )
    top_entities = [
        {
            "entity_id": r[0],
            "name": r[1],
            "fact_count": int(r[2] or 0),
            "relation_count": int(r[3] or 0),
        }
        for r in top_rows.fetchall()
    ]

    return {
        "stats": stats,
        "recent_summaries": recent_summaries,
        "on_this_day": on_this_day,
        "top_entities": top_entities,
    }


async def get_entity_index(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[dict]:
    """Wiki index — list every entity grouped by entity_type.

    Excludes `episode_topic` (session-label anchors) for the same reason
    `get_dashboard.top_entities` does: they aren't real concepts and
    pollute the wiki overview. Within each group, entities are sorted by
    active fact_count desc, then relation_count desc, then name asc.

    `last_seen_date` is the most recent `valid_from` across the entity's
    active facts; NULL when the entity has no facts (rare — usually means
    parent-only or alias-only).
    """
    ws = str(workspace_id)
    rows = await db.execute(
        text(
            """
            WITH fc AS (
                SELECT entity_id,
                       COUNT(*) AS cnt,
                       MAX(valid_from) AS last_seen
                FROM knowledge_facts
                WHERE workspace_id = :ws AND superseded_at IS NULL
                GROUP BY entity_id
            ),
            rc AS (
                SELECT eid, COUNT(*) AS cnt FROM (
                    SELECT from_entity_id AS eid FROM entity_relations
                        WHERE workspace_id = :ws AND valid_to IS NULL
                    UNION ALL
                    SELECT to_entity_id AS eid FROM entity_relations
                        WHERE workspace_id = :ws AND valid_to IS NULL
                ) er
                GROUP BY eid
            )
            SELECT e.id, e.name, e.entity_type::text,
                   e.parent_id, p.name AS parent_name,
                   COALESCE(fc.cnt, 0) AS fact_count,
                   COALESCE(rc.cnt, 0) AS relation_count,
                   DATE(fc.last_seen AT TIME ZONE 'UTC') AS last_seen_date
            FROM entities e
            LEFT JOIN entities p ON p.id = e.parent_id AND p.workspace_id = :ws
            LEFT JOIN fc ON fc.entity_id = e.id
            LEFT JOIN rc ON rc.eid = e.id
            WHERE e.workspace_id = :ws
              AND e.entity_type != 'episode_topic'
            ORDER BY e.entity_type::text ASC,
                     COALESCE(fc.cnt, 0) DESC,
                     COALESCE(rc.cnt, 0) DESC,
                     e.name ASC
            """
        ),
        {"ws": ws},
    )
    groups: dict[str, list[dict]] = {}
    for r in rows.fetchall():
        et = r[2] or "other"
        groups.setdefault(et, []).append(
            {
                "entity_id": r[0],
                "name": r[1],
                "parent_id": r[3],
                "parent_name": r[4],
                "fact_count": int(r[5] or 0),
                "relation_count": int(r[6] or 0),
                "last_seen_date": r[7].isoformat() if r[7] else None,
            }
        )
    # Deterministic group order: highest aggregate fact_count first, then name.
    ordered = sorted(
        groups.items(),
        key=lambda kv: (-sum(e["fact_count"] for e in kv[1]), kv[0]),
    )
    return [{"entity_type": et, "entities": ents} for et, ents in ordered]


async def get_entity_graph(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    limit: int = 100,
    min_rel_cnt: int = 1,
    entity_types: list[str] | None = None,
    include_isolates: bool = False,
) -> dict:
    """Hub-cut entity-relation graph for the workspace's D3 force view.

    Returns top-N hub entities (ordered by relation_count desc) plus every
    edge whose endpoints both survive the cut — no orphan edges. Same
    `episode_topic` exclusion as `get_entity_index`. `has_wiki` is a boolean
    projection of `wiki_article IS NOT NULL` so we don't ship the full
    article text in the index payload.

    `include_isolates` overrides `min_rel_cnt` and admits orphan nodes too —
    used by the "고립 노드 포함" toggle when debugging the index.
    """
    ws = str(workspace_id)
    # Effective min_rel_cnt — include_isolates dominates the threshold so the
    # SQL stays single-pathed (no separate branch for "0 or more").
    effective_min = 0 if include_isolates else int(min_rel_cnt)
    # Total entity count (pre-LIMIT) so the UI can show "N개 중 M개 표시".
    type_filter_sql = ""
    params: dict[str, Any] = {"ws": ws, "lim": int(limit), "minr": effective_min}
    if entity_types:
        # Bind each type individually for safety; SQLAlchemy `text` doesn't
        # expand list parameters automatically in this codebase.
        placeholders = []
        for i, t in enumerate(entity_types):
            key = f"et{i}"
            params[key] = t
            placeholders.append(f":{key}")
        type_filter_sql = f" AND e.entity_type::text IN ({', '.join(placeholders)})"

    total_row = await db.execute(
        text(
            f"""
            SELECT COUNT(*) FROM entities e
            WHERE e.workspace_id = :ws
              AND e.entity_type != 'episode_topic'
              {type_filter_sql}
            """
        ),
        params,
    )
    total_entities = int(total_row.scalar() or 0)

    # Hub-cut nodes (ranked by undirected relation count, ties broken by name).
    node_rows = await db.execute(
        text(
            f"""
            WITH rc AS (
                SELECT eid, COUNT(*) AS cnt FROM (
                    SELECT from_entity_id AS eid FROM entity_relations
                        WHERE workspace_id = :ws AND valid_to IS NULL
                    UNION ALL
                    SELECT to_entity_id AS eid FROM entity_relations
                        WHERE workspace_id = :ws AND valid_to IS NULL
                ) er
                GROUP BY eid
            )
            SELECT e.id, e.name, e.entity_type::text,
                   COALESCE(rc.cnt, 0) AS rel_cnt,
                   (e.wiki_article IS NOT NULL) AS has_wiki
            FROM entities e
            LEFT JOIN rc ON rc.eid = e.id
            WHERE e.workspace_id = :ws
              AND e.entity_type != 'episode_topic'
              {type_filter_sql}
              AND COALESCE(rc.cnt, 0) >= :minr
            ORDER BY rel_cnt DESC, e.name ASC
            LIMIT :lim
            """
        ),
        params,
    )
    nodes: list[dict] = []
    node_id_set: set[str] = set()
    for r in node_rows.fetchall():
        nid = str(r[0])
        node_id_set.add(nid)
        nodes.append(
            {
                "id": r[0],
                "name": r[1],
                "entity_type": r[2] or "other",
                "rel_cnt": int(r[3] or 0),
                "has_wiki": bool(r[4]),
            }
        )

    # Edges restricted to nodes that survived the cut. Group + COUNT so we
    # collapse duplicate (from, to, type) triples into `weight`.
    edges: list[dict] = []
    if node_id_set:
        edge_rows = await db.execute(
            text(
                """
                SELECT from_entity_id, to_entity_id, relation_type::text,
                       COUNT(*) AS weight
                FROM entity_relations
                WHERE workspace_id = :ws
                  AND valid_to IS NULL
                  AND from_entity_id = ANY(:ids ::uuid[])
                  AND to_entity_id   = ANY(:ids ::uuid[])
                GROUP BY from_entity_id, to_entity_id, relation_type
                """
            ),
            {"ws": ws, "ids": list(node_id_set)},
        )
        for r in edge_rows.fetchall():
            edges.append(
                {
                    "from_id": r[0],
                    "to_id": r[1],
                    "relation_type": r[2] or "related_to",
                    "weight": int(r[3] or 1),
                }
            )

    return {
        "workspace_id": workspace_id,
        "total_entities": total_entities,
        "returned_nodes": len(nodes),
        "nodes": nodes,
        "edges": edges,
    }


async def get_on_this_day(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    month: int | None = None,
    day: int | None = None,
    limit: int = 10,
) -> tuple[int, int, list[dict]]:
    """Episodes whose created_at hits the given month·day in any past year.

    Defaults to today (PostgreSQL CURRENT_DATE) when month/day aren't passed.
    Excludes today's date itself so the widget only surfaces *past* echoes.
    Returns (resolved_month, resolved_day, matches).
    """
    ws = str(workspace_id)
    params: dict[str, Any] = {"ws": ws, "lim": int(limit)}

    if month is None or day is None:
        # Resolve "today" via Postgres so we agree with server-side ingest clock.
        today_row = await db.execute(
            text(
                "SELECT EXTRACT(MONTH FROM CURRENT_DATE)::int AS m, "
                "EXTRACT(DAY FROM CURRENT_DATE)::int AS d"
            )
        )
        tr = today_row.fetchone()
        resolved_m = int(month if month is not None else (tr[0] if tr else 1))
        resolved_d = int(day if day is not None else (tr[1] if tr else 1))
    else:
        resolved_m = int(month)
        resolved_d = int(day)
    params["m"] = resolved_m
    params["d"] = resolved_d

    rows = await db.execute(
        text(
            """
            SELECT e.id,
                   DATE(e.created_at AT TIME ZONE 'UTC') AS d,
                   EXTRACT(YEAR FROM e.created_at AT TIME ZONE 'UTC')::int AS y,
                   (CURRENT_DATE - DATE(e.created_at AT TIME ZONE 'UTC')) AS days_ago,
                   e.summary,
                   (SELECT COUNT(*) FROM turns t WHERE t.episode_id = e.id) AS turn_count
            FROM episodes e
            WHERE e.workspace_id = :ws
              AND (e.metadata->>'deleted' IS DISTINCT FROM 'true')
              AND EXTRACT(MONTH FROM e.created_at AT TIME ZONE 'UTC') = :m
              AND EXTRACT(DAY FROM e.created_at AT TIME ZONE 'UTC') = :d
              AND DATE(e.created_at AT TIME ZONE 'UTC') < CURRENT_DATE
            ORDER BY d DESC
            LIMIT :lim
            """
        ),
        params,
    )
    matches = [
        {
            "episode_id": r[0],
            "date": r[1].isoformat() if r[1] else "",
            "year": int(r[2] or 0),
            "days_ago": int(r[3] or 0),
            "summary": (r[4] or "")[:300],
            "turn_count": int(r[5] or 0),
        }
        for r in rows.fetchall()
    ]
    return resolved_m, resolved_d, matches


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
              AND (t.cleaned_text_v2 IS NOT NULL OR t.cleanup_metadata IS NULL)
        """),
        params,
    )
    total = int(total_q.scalar() or 0)

    rows_q = await db.execute(
        text(f"""
            SELECT DISTINCT
                t.id, t.episode_id, t.sequence, t.role, t.text,
                COALESCE(t.cleaned_text_v2, t.cleaned_text) AS cleaned_text,
                t.timestamp,
                (SELECT array_agg(ts2.subject_id)
                 FROM turn_subjects ts2 WHERE ts2.turn_id = t.id) AS subject_ids
            FROM turns t
            JOIN turn_subjects ts ON ts.turn_id = t.id
            WHERE {where_sql}
              AND (t.cleaned_text_v2 IS NOT NULL OR t.cleanup_metadata IS NULL)
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
            "cleaned_text": r[5],
            "timestamp": r[6],
            "subjects": list(r[7] or []),
        }
        for r in rows_q.fetchall()
    ]
    return subject_name, turns, total


async def get_subject_tree(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> tuple[list[dict], int]:
    """Build hierarchical subject tree from entities.parent_id.

    Only entities with at least one turn_subjects link are included
    (excludes knowledge_facts-only entities like 'user', 'assistant').
    """
    rows = await db.execute(
        text("""
            SELECT
                e.id, e.name, e.parent_id,
                tc.cnt AS turn_count,
                e.entity_type::text AS entity_type
            FROM entities e
            JOIN (
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
            "entity_type": r[4],
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
