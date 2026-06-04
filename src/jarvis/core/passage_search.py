"""search_passages: pure fragment semantic search, no anchor filter.

Use case: "why/decision/reason" queries where the relevant passage may live
outside the anchor's 2-hop neighborhood. The anchor-filtered recall_memory
can miss these by design; this tool bypasses the filter entirely and ranks
fragments by pure pgvector cosine similarity.

Each result includes the fragment content, its source episode_id, and the
linked fact_id if any — so the AI client can follow up with recall_memory
on specific entities/facts it discovered.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class PassageHit:
    fragment_id: uuid.UUID
    content: str
    similarity: float
    episode_id: uuid.UUID
    fact_id: uuid.UUID | None
    entity_name: str | None
    predicate: str | None
    created_at: datetime
    # NULL until R1's cleaning pipeline lands. SELECT uses to_jsonb so this
    # works even before the column exists on fragments — graceful degradation.
    cleaned_content: str | None = None


async def search_passages(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    query: str,
    limit: int = 10,
) -> list[PassageHit]:
    """Pure fragment semantic search, ordered by cosine similarity.

    Skips anchor filtering and RRF. Returns raw passages with linkage info.
    """
    from jarvis.core.embedding import embed_text
    from jarvis.core.query_preprocessing import preprocess_query

    pq = preprocess_query(query)
    vec = embed_text(pq.normalized)
    if not vec:
        return []

    # to_jsonb(f)->>'cleaned_content' returns NULL when the column does not
    # exist yet (R1 hasn't added it) and returns the value once it does — so
    # this query stays correct across R1's migration without a deploy ordering
    # constraint.
    result = await db.execute(
        text("""
            SELECT
                f.id AS fragment_id,
                f.content,
                1 - (emb.vector <=> cast(:vec as vector)) AS similarity,
                f.source_episode_id,
                f.source_fact_id,
                e.name AS entity_name,
                kf.predicate,
                f.created_at,
                to_jsonb(f) ->> 'cleaned_content' AS cleaned_content
            FROM embeddings emb
            JOIN fragments f ON f.id = emb.source_id
            LEFT JOIN knowledge_facts kf ON kf.id = f.source_fact_id
            LEFT JOIN entities e ON e.id = kf.entity_id
            WHERE emb.workspace_id = :ws
              AND emb.source_type = 'fragment'
            ORDER BY emb.vector <=> cast(:vec as vector)
            LIMIT :lim
        """),
        {"ws": str(workspace_id), "vec": str(vec), "lim": limit},
    )
    rows = result.fetchall()
    return [
        PassageHit(
            fragment_id=row[0],
            content=row[1],
            similarity=float(row[2]),
            episode_id=row[3],
            fact_id=row[4],
            entity_name=row[5],
            predicate=row[6],
            created_at=row[7],
            cleaned_content=row[8],
        )
        for row in rows
    ]
