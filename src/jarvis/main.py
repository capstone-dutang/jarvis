"""FastAPI application entry point."""

import contextlib
import logging
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from jarvis.api.v1.memory import router as memory_router
from jarvis.api.v1.users import members_router
from jarvis.api.v1.users import router as users_router
from jarvis.api.v1.workspaces import router as workspace_router
from jarvis.mcp_adapter import mcp
from jarvis.middleware.rate_limit import RateLimitMiddleware


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    async with mcp.session_manager.run():
        yield


# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

app = FastAPI(
    title="JARVIS",
    description="Cloud context server for AI memory",
    version="0.1.0",
    lifespan=lifespan,
)

# Rate limiting (120/min reads, 30/min writes)
app.add_middleware(RateLimitMiddleware)

# REST API routes
app.include_router(memory_router, prefix="/api/v1")
app.include_router(workspace_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(members_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# MCP endpoint — mounted AFTER REST routes so /health etc. are not shadowed
# SDK's streamable_http_app() has internal route at /mcp
# Mount at root so final path = /mcp
mcp_app = mcp.streamable_http_app()
app.mount("", mcp_app)
