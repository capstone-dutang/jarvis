"""Workspace CRUD endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db import get_session
from jarvis.models.tables import Workspace
from jarvis.schemas import WorkspaceCreate, WorkspaceResponse

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    body: WorkspaceCreate,
    db: AsyncSession = Depends(get_session),
) -> WorkspaceResponse:
    ws = Workspace(name=body.name)
    db.add(ws)
    await db.commit()
    await db.refresh(ws)
    return WorkspaceResponse(
        id=ws.id, name=ws.name, created_at=ws.created_at, status=ws.status
    )


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    include_hidden: bool = False,
    db: AsyncSession = Depends(get_session),
) -> list[WorkspaceResponse]:
    """List workspaces. By default only active ones are returned.

    Pass include_hidden=true to also include hidden/archived workspaces
    (admin / debug view).
    """
    stmt = select(Workspace).order_by(Workspace.name.asc())
    if not include_hidden:
        stmt = stmt.where(Workspace.status == "active")
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        WorkspaceResponse(id=w.id, name=w.name, created_at=w.created_at, status=w.status)
        for w in rows
    ]


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> WorkspaceResponse:
    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return WorkspaceResponse(
        id=ws.id, name=ws.name, created_at=ws.created_at, status=ws.status
    )
