"""User CRUD + WorkspaceMember management."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.auth.oauth import hash_password
from jarvis.db import get_session
from jarvis.models.tables import User, Workspace, WorkspaceMember, WorkspaceRole
from jarvis.schemas import MemberInvite, UserCreate, UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_session),
) -> UserResponse:
    # Check for duplicate email
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=body.email,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name, created_at=user.created_at)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> UserResponse:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name, created_at=user.created_at)


# ── WorkspaceMember management ──


members_router = APIRouter(prefix="/workspaces/{workspace_id}/members", tags=["members"])


@members_router.post("", status_code=201)
async def invite_member(
    workspace_id: uuid.UUID,
    body: MemberInvite,
    db: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    # Verify workspace exists
    ws_result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    if not ws_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Find user by email
    user_result = await db.execute(select(User).where(User.email == body.email))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already a member
    existing = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Already a member")

    try:
        role = WorkspaceRole(body.role)
    except ValueError:
        role = WorkspaceRole.contributor

    member = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=user.id,
        role=role,
    )
    db.add(member)
    await db.commit()
    return {"status": "invited", "user": body.email, "role": role.value}


@members_router.get("")
async def list_members(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, str]]:
    result = await db.execute(
        select(WorkspaceMember, User)
        .join(User, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == workspace_id)
    )
    rows = result.all()
    return [
        {
            "user_id": str(member.user_id),
            "email": user.email,
            "display_name": user.display_name,
            "role": member.role.value,
        }
        for member, user in rows
    ]
