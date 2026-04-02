"""Memory endpoints: store, recall, initialize."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.recall import recall_memory
from jarvis.core.store import store_memory
from jarvis.db import get_session
from jarvis.models.tables import Entity, KnowledgeFact, Workspace
from jarvis.schemas import (
    InitializeMemoryRequest,
    InitializeMemoryResponse,
    RecallMemoryRequest,
    RecallMemoryResponse,
    StoreMemoryRequest,
    StoreMemoryResponse,
)

router = APIRouter(prefix="/memory", tags=["memory"])

PROTOCOL = (
    "Memory protocol:\n"
    "- Call store_memory when you learn a new fact about the user, "
    "their preferences, goals, technical stack, or decisions.\n"
    "- Call store_memory when the user corrects or updates previously known information.\n"
    "- Call store_memory if 5+ substantive exchanges have passed without storing.\n"
    "- Each memory should be a self-contained statement.\n"
    "- Do NOT store greetings, small talk, or your own responses."
)


@router.post("/store", response_model=StoreMemoryResponse)
async def api_store_memory(
    request: StoreMemoryRequest,
    db: AsyncSession = Depends(get_session),
) -> StoreMemoryResponse:
    return await store_memory(db, request)


@router.post("/recall", response_model=RecallMemoryResponse)
async def api_recall_memory(
    request: RecallMemoryRequest,
    db: AsyncSession = Depends(get_session),
) -> RecallMemoryResponse:
    return await recall_memory(db, request)


@router.post("/initialize", response_model=InitializeMemoryResponse)
async def api_initialize_memory(
    request: InitializeMemoryRequest,
    db: AsyncSession = Depends(get_session),
) -> InitializeMemoryResponse:
    """Initialize memory: return workspace recent context + protocol."""
    # Get workspace name
    ws_result = await db.execute(select(Workspace).where(Workspace.id == request.workspace_id))
    ws = ws_result.scalar_one_or_none()
    workspace_name = ws.name if ws else "unknown"

    # Get recent active facts (top 10)
    fact_result = await db.execute(
        select(KnowledgeFact, Entity.name)
        .join(Entity, KnowledgeFact.entity_id == Entity.id)
        .where(
            KnowledgeFact.workspace_id == request.workspace_id,
            KnowledgeFact.superseded_at.is_(None),
        )
        .order_by(KnowledgeFact.recorded_at.desc())
        .limit(10)
    )
    rows = fact_result.all()

    recent_summary = ""
    if rows:
        lines = [f"- {name} {fact.predicate} {fact.object_value}" for fact, name in rows]
        recent_summary = "\n".join(lines)

    return InitializeMemoryResponse(
        workspace_name=workspace_name,
        recent_summary=recent_summary,
        protocol=PROTOCOL,
    )
