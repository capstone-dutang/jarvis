"""Rate limiting middleware.

Based on: research/2026-03-31-mcp-server-implementation-research.md line 228
- 120/min for reads (recall, initialize)
- 30/min for writes (store)

Returns AI-friendly 429 messages per research lines 237-245.
"""

import time
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

# Token bucket per client IP
_buckets: dict[str, list[float]] = defaultdict(list)

READ_LIMIT = 120  # per minute
WRITE_LIMIT = 30  # per minute
WINDOW = 60  # seconds


def _check_rate(key: str, limit: int) -> tuple[bool, int]:
    """Check if request is within rate limit. Returns (allowed, remaining)."""
    now = time.time()
    window_start = now - WINDOW
    _buckets[key] = [t for t in _buckets[key] if t > window_start]

    if len(_buckets[key]) >= limit:
        wait = int(_buckets[key][0] - window_start) + 1
        return False, wait

    _buckets[key].append(now)
    return True, limit - len(_buckets[key])


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Only rate-limit API and MCP endpoints
        path = request.url.path
        if not (path.startswith("/api/") or path.startswith("/mcp")):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        # Determine if this is a read or write operation
        is_write = request.method == "POST" and ("store" in path)
        limit = WRITE_LIMIT if is_write else READ_LIMIT
        bucket_key = f"{client_ip}:{'write' if is_write else 'read'}"

        allowed, remaining_or_wait = _check_rate(bucket_key, limit)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": (
                        f"Rate limit exceeded. "
                        f"Limit is {limit} requests per minute for {'writes' if is_write else 'reads'}. "
                        f"Wait {remaining_or_wait} seconds before retrying."
                    )
                },
            )

        response = await call_next(request)
        return response
