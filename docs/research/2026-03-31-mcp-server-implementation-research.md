# Building JARVIS: a production MCP server for AI memory

> 연구 일자: 2026-03-31
> 성격: MCP 서버 구현 + AI 도구 호출 신뢰성 리서치
> 상태: 활성 (절대문서 반영 필요)

**The official `mcp` Python SDK (v1.26.0) and standalone FastMCP (v3.1.1) both support Streamable HTTP transport and can power a production remote MCP server on FastAPI, but they differ significantly in scope, OAuth handling, and deployment ergonomics.** Building a reliable AI memory system through MCP requires solving two fundamentally different problems: the infrastructure challenge of running a secure, authenticated remote server, and the behavioral challenge of getting AI clients to actually call your memory tools consistently. This report covers battle-tested patterns for both, drawn from real deployments in 2025–2026. The critical finding throughout: ChatGPT and Claude behave very differently as MCP clients, tool invocation is unreliable by default, and the MCP spec still has significant gaps in rate limiting and response size handling that you must solve at the application layer.

---

## The Python SDK landscape: three libraries, one clear winner per use case

Three Python libraries compete for MCP server development. Each serves a distinct purpose, and choosing wrong wastes weeks.

The **official `mcp` SDK** (v1.26.0, ~22.3k GitHub stars) is maintained by Anthropic's modelcontextprotocol team. It implements the full MCP specification including Streamable HTTP transport (introduced in **v1.8.0, May 2025**), built-in OAuth server/resource-server utilities, and both high-level (`FastMCP`) and low-level (`Server`) APIs. The v2 SDK is in pre-alpha on the main branch and will rename `FastMCP` to `MCPServer`. The high-level API lives at `from mcp.server.fastmcp import FastMCP` — this is actually FastMCP 1.0 code that was incorporated into the SDK in 2024.

**FastMCP standalone** (v3.1.1, ~22.8k stars, by Jeremiah Lowin/PrefectHQ) diverged from the official SDK after the 1.0 incorporation. It wraps the official `mcp` package but adds substantially more: server composition via Providers/Transforms, `FastMCP.from_fastapi()` for converting existing FastAPI apps, CLI tools (`fastmcp discover`, `fastmcp call`), OpenAPI Provider for auto-generating tools from API specs, Code Mode (v3.1) with BM25 search for scaling tool counts, and MCP Apps for interactive UIs. Import path: `from fastmcp import FastMCP`. Streamable HTTP arrived in **v2.3**.

**fastapi-mcp** (v0.4.0, ~11.5k stars, by Tadata) is a bridge library that auto-generates MCP tools from existing FastAPI route definitions. It is not for building MCP-first servers — it wraps your existing REST endpoints. Still in alpha, with known bugs around SSE transport and route mounting.

| Aspect | `mcp` v1.26.0 | `fastmcp` v3.1.1 | `fastapi-mcp` v0.4.0 |
|---|---|---|---|
| Best for | Spec compliance, simple servers | Full-featured MCP apps | Exposing existing FastAPI |
| Streamable HTTP | ✅ since v1.8.0 | ✅ since v2.3 | ✅ since v0.4.0 |
| OAuth server built-in | ✅ `OAuthAuthorizationServerProvider` | ✅ `OAuthProvider` | Via FastAPI `Depends()` |
| FastAPI mounting | Manual Starlette mount | `mcp.http_app()` + mount | Native (`mcp.mount_http()`) |
| Server composition | ❌ | ✅ Providers/Transforms | ❌ |
| License | MIT | Apache-2.0 | MIT |

**For JARVIS, use the official `mcp` SDK** if you want minimal dependencies and guaranteed spec compliance with direct control over OAuth. Use **FastMCP standalone** if you want richer tooling, CLI debugging, and future flexibility with composition. Both produce equivalent Streamable HTTP servers.

---

## FastAPI + MCP integration: the ASGI mounting pattern

The MCP SDK produces a Starlette/ASGI sub-application that mounts directly into FastAPI. Your REST API endpoints and MCP protocol handler coexist in one process, sharing database connections and service layers.

The official SDK exposes `mcp.streamable_http_app()` which returns an ASGI app:

```python
import contextlib
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

# Create MCP server
mcp = FastMCP("JARVIS Memory", stateless_http=True)

@mcp.tool()
def store_memory(content: str, tags: list[str] = []) -> str:
    """Store a conversation memory. Use this when the user shares 
    preferences, facts about themselves, project context, or decisions."""
    # ... store to PostgreSQL + pgvector
    return f"Memory stored with {len(tags)} tags"

@mcp.tool()
def recall_memory(query: str, limit: int = 5) -> str:
    """Recall relevant memories by semantic search. Use this at the start 
    of conversations or when context from past conversations would help."""
    # ... vector similarity search
    return "Retrieved memories..."

# Mount into FastAPI
mcp_app = mcp.streamable_http_app()

@contextlib.asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield

app = FastAPI(title="JARVIS API", lifespan=lifespan)

@app.get("/api/health")
def health():
    return {"status": "ok"}

app.mount("/mcp", mcp_app)
```

With FastMCP standalone, the pattern is nearly identical but uses `mcp.http_app(path="/")`:

```python
from fastapi import FastAPI
from fastmcp import FastMCP

mcp = FastMCP("JARVIS Memory")
mcp_app = mcp.http_app(path="/")
app = FastAPI(lifespan=mcp_app.lifespan)  # Critical: share lifespan
app.mount("/mcp", mcp_app)
```

**The critical gotcha**: you **must** pass the MCP app's lifespan to FastAPI. Without it, the session manager never initializes, and every request silently fails. This is the single most common deployment error reported on GitHub. Also avoid layering FastAPI's `CORSMiddleware` on the MCP sub-app — the SDK already handles CORS for OAuth routes, and doubling up causes 404 errors on `/.well-known` endpoints. Mount REST and MCP as separate sub-apps with independent middleware.

For production on Oracle Cloud ARM, set `stateless_http=True` to enable horizontal scaling behind a load balancer. Each request creates fresh state, eliminating the need for sticky sessions. The trade-off is losing multi-request stateful tool interactions, but for a memory server this is acceptable since each `store_memory` and `recall_memory` call is independent.

---

## OAuth 2.1: the spec shifted, and self-hosting is now optional

The MCP authorization model changed significantly in the **2025-06-18 spec revision**. The MCP server is now explicitly an **OAuth Resource Server (RS)**, while the **Authorization Server (AS)** is a separate entity. This means you have two deployment options, and the choice matters enormously.

**Option 1: External IdP as Authorization Server** (recommended for production). Use Auth0, Keycloak, or any OIDC-compliant provider as your AS. Your MCP server only validates tokens:

```python
from pydantic import AnyHttpUrl
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
import jwt
import httpx

class JWTTokenVerifier(TokenVerifier):
    def __init__(self, jwks_url: str, issuer: str, audience: str):
        self.jwks_url = jwks_url
        self.issuer = issuer
        self.audience = audience
    
    async def verify_token(self, token: str) -> AccessToken | None:
        async with httpx.AsyncClient() as client:
            jwks = (await client.get(self.jwks_url)).json()
        try:
            payload = jwt.decode(token, jwks, algorithms=["RS256"],
                                 audience=self.audience, issuer=self.issuer)
            return AccessToken(
                token=token, client_id=payload["azp"],
                scopes=payload.get("scope", "").split(),
                expires_at=payload["exp"]
            )
        except jwt.InvalidTokenError:
            return None

mcp = FastMCP(
    "JARVIS Memory",
    token_verifier=JWTTokenVerifier(
        jwks_url="https://auth.example.com/.well-known/jwks.json",
        issuer="https://auth.example.com",
        audience="https://jarvis.example.com"
    ),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl("https://auth.example.com"),
        resource_server_url=AnyHttpUrl("https://jarvis.example.com"),
        required_scopes=["mcp:tools"],
    ),
)
```

**Option 2: Self-hosted AS** using the SDK's `OAuthAuthorizationServerProvider`. The SDK provides a protocol class you implement, and it automatically wires up all required endpoints:

```python
from mcp.server.auth.provider import OAuthAuthorizationServerProvider
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

class JarvisOAuthProvider(OAuthAuthorizationServerProvider):
    async def get_client(self, client_id: str):
        return await db.get_oauth_client(client_id)
    
    async def register_client(self, client_info):
        return await db.store_oauth_client(client_info)
    
    async def authorize(self, client, params):
        code = generate_auth_code(client, params)
        await db.store_auth_code(code, client.client_id, 
                                  params.code_challenge)
        return f"{params.redirect_uri}?code={code}&state={params.state}"
    
    async def exchange_authorization_code(self, client, auth_code):
        return OAuthToken(access_token=..., refresh_token=..., 
                         expires_in=3600, token_type="Bearer")

mcp = FastMCP(
    "JARVIS Memory",
    auth_server_provider=JarvisOAuthProvider(),
    auth=AuthSettings(
        issuer_url="https://jarvis.example.com",
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["mcp:tools", "mcp:read"],
            default_scopes=["mcp:tools"],
        ),
        required_scopes=["mcp:tools"],
    ),
)
```

**Critical gotcha for ChatGPT**: it registers a new client via DCR for every session, potentially creating thousands of client records. Implement rate limiting on `/register` and periodic cleanup. ChatGPT's redirect URI is `https://chatgpt.com/connector_platform_oauth_redirect` — redirect URI mismatches are the number-one cause of OAuth failures.

---

## ChatGPT vs Claude vs Gemini: three clients, three different worlds

**Transport and authentication** diverge sharply. ChatGPT is remote-only, supports both SSE and Streamable HTTP, and strictly requires OAuth 2.1 with Dynamic Client Registration — bearer tokens alone are rejected. Claude supports both local (stdio) and remote servers, handles authentication through its own connector flow, and also requires OAuth 2.1 for remote connections. Gemini added MCP support at Google I/O (May 2025), supports both local and remote via the Gemini SDK, but currently only accesses `tools/list` — resources and prompts are not supported.

**Tool-calling behavior** is the most consequential difference. A confirmed finding: **Claude calls MCP tools automatically when relevant context might be needed, while ChatGPT only calls tools when the user explicitly asks** (e.g., "remember this" or "recall my preferences"). Same MCP server, same tool definitions — fundamentally different invocation patterns.

**Error visibility** is broken on ChatGPT. ChatGPT does not properly receive error details from MCP tool calls. The model cannot see the error content, preventing self-correction. Claude handles tool errors correctly and can retry based on error messages.

**Reasoning model compatibility**: ChatGPT's reasoning models (o4-mini, o4-mini-high) do **not execute MCP tools** at all. Only non-reasoning models (GPT-4o, GPT-4.1, GPT-4.5) work with MCP tools.

---

## Making AI actually call your memory tools

**Tool count has a dramatic, measurable effect on accuracy.** Benchmarks show near-perfect tool selection with ~10 tools, declining accuracy at ~20, a critical threshold at ~30 where descriptions start overlapping, and virtual failure at 100+ tools. JARVIS should expose **2–3 tools maximum**.

**Tool descriptions must follow the "Use this when…" pattern** with explicit negative cases:

```python
# GOOD — explicit triggers, negative cases, action-oriented
@mcp.tool()
def store_memory(content: str, tags: list[str] = []) -> str:
    """Use this when the user shares personal preferences, project context, 
    technical decisions, or facts about themselves or their work. Store the 
    information as a memory for future conversations. Do NOT use this for 
    transient questions or small talk. Call this every time you learn 
    something new about the user."""
```

**Tool naming matters more than expected.** Renaming tools with action verbs like "unlock" or "initialize" triggered setup→execute patterns in LLMs.

**The `instructions` field is unreliable.** Many MCP hosts ignore it entirely. The proven workaround is implementing a dedicated initialization tool with an imperative name like `initialize_memory_system` that returns operational rules.

**For detecting missed tool calls**: the MCP server has **no visibility** into conversations where tools weren't called. The protocol only sends messages to the server when the client explicitly invokes `tools/call`.

---

## Session management, rate limiting, and response size

For JARVIS, use `stateless_http=True` — each tool call is independent, no session persistence needed.

**Rate limiting has zero protocol support.** Implement as FastAPI middleware. Recommended starting limits: **120/minute** for reads, **30/minute** for writes.

**Large tool responses**: Claude Code enforces a **25,000 token** limit per MCP tool response. Implement server-side truncation with cursor-based pagination for `recall_memory`.

---

## Error handling for AI self-correction

**Protocol errors** (JSON-RPC error codes) are discarded — the LLM never sees them. **Tool execution errors** (`isError: true`) are injected into the LLM's context. Write error messages for an AI reader:

```python
raise ToolError(
    f"Failed to store memory: {e}. "
    f"Try again with content under 5000 characters. "
    f"Current content length: {len(content)}"
)
```

The AI does not automatically retry — it reads the error message and decides based on content. Making error messages prescriptive significantly improves retry success rates.
