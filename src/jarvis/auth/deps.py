"""Authentication dependencies for FastAPI REST API routes.

Note: MCP endpoints use SDK's built-in OAuth via auth_server_provider.
This module is for REST API authentication only.
"""

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.auth.oauth import JarvisOAuthProvider
from jarvis.db import get_session
from jarvis.models.tables import User

_oauth_provider = JarvisOAuthProvider()


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> User:
    """Extract and verify Bearer token, return authenticated user."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = auth_header[7:]
    access_token = await _oauth_provider.load_access_token(token)
    if not access_token:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # For now, look up user by a simple mapping
    # In production, the access token would contain user_id in claims
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="No user found")

    return user
