"""MCP tool definitions for JARVIS. 4 tools: manage_workspace, initialize_memory, store_memory, recall_memory.

Based on: research/2026-03-31-mcp-server-implementation-research.md
- Tool descriptions: lines 204-215 ("Use this when..." pattern)
- Error handling: lines 237-245 (prescriptive ToolError messages)
- Response limit: definitive doc Section 9 (25,000 tokens)
"""

import uuid

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from jarvis.core.recall import recall_memory as _recall
from jarvis.core.store import store_memory as _store
from jarvis.db import async_session_factory
from jarvis.models.tables import Entity, KnowledgeFact, Workspace
from jarvis.schemas import (
    EntityHint,
    FactHint,
    RecallMemoryRequest,
    StoreMemoryRequest,
)

# Max response size: ~25,000 tokens ≈ 100,000 chars (definitive doc Section 9)
MAX_RESPONSE_CHARS = 100_000

# TODO: Re-enable OAuth after MCP logic verification
# auth_server_provider=JarvisOAuthProvider(),
# auth=AuthSettings(issuer_url=..., required_scopes=["mcp:tools"]),
mcp = FastMCP(
    "JARVIS Memory",
    stateless_http=True,
    instructions=(
        "JARVIS is a cloud memory server. Use manage_workspace to create or switch workspaces. "
        "Use initialize_memory at the start of each conversation to load context. "
        "Use store_memory whenever you learn new facts. Use recall_memory to retrieve past context."
    ),
)


async def _resolve_workspace(workspace: str) -> uuid.UUID:
    """Resolve workspace name or UUID string to UUID.

    If it looks like a UUID, use it directly.
    Otherwise, look up by name (case-insensitive).
    """
    # Try UUID first
    try:
        return uuid.UUID(workspace)
    except ValueError:
        pass

    # Look up by name
    async with async_session_factory() as db:
        result = await db.execute(select(Workspace).where(Workspace.name.ilike(workspace)))
        ws = result.scalar_one_or_none()
        if ws:
            return ws.id

    raise ValueError(f"Workspace '{workspace}' not found. Use manage_workspace to create one.")


# ── Tool 1: Workspace Management ──


@mcp.tool(name="jarvis_manage_workspace")
async def manage_workspace(
    action: str,
    name: str = "",
    new_name: str = "",
) -> str:
    """Manage workspaces — create, list, switch, or rename.

    Use this when: the user wants to create a new workspace, see their workspaces,
    switch to a different workspace, or rename one.

    Args:
        action: One of "list", "create", "switch", "rename"
        name: Workspace name (for create, switch, rename)
        new_name: New name (only for rename action)
    """
    async with async_session_factory() as db:
        if action == "list":
            result = await db.execute(select(Workspace).order_by(Workspace.created_at.desc()))
            workspaces = result.scalars().all()
            if not workspaces:
                return "No workspaces yet. Create one with manage_workspace(action='create', name='my-project')"
            lines = [f"- {ws.name}" for ws in workspaces]
            return "Workspaces:\n" + "\n".join(lines)

        elif action == "create":
            if not name:
                return "Failed: name is required. Example: manage_workspace(action='create', name='my-project')"
            # Check duplicate
            existing = await db.execute(select(Workspace).where(Workspace.name.ilike(name)))
            if existing.scalar_one_or_none():
                return f"Workspace '{name}' already exists. Use manage_workspace(action='switch', name='{name}') to switch to it."
            ws = Workspace(name=name)
            db.add(ws)
            await db.commit()
            return f"Created workspace '{name}'. All memory tools will now use this workspace."

        elif action == "switch":
            if not name:
                return "Failed: name is required. Use manage_workspace(action='list') to see available workspaces."
            try:
                ws_id = await _resolve_workspace(name)
                result = await db.execute(select(Workspace).where(Workspace.id == ws_id))
                found_ws = result.scalar_one_or_none()
                if found_ws:
                    return f"Switched to workspace '{found_ws.name}'. Use initialize_memory(workspace='{found_ws.name}') to load context."
                return f"Workspace '{name}' not found."
            except ValueError as e:
                return str(e)

        elif action == "rename":
            if not name or not new_name:
                return "Failed: both name and new_name are required."
            try:
                ws_id = await _resolve_workspace(name)
                result = await db.execute(select(Workspace).where(Workspace.id == ws_id))
                found_ws = result.scalar_one_or_none()
                if found_ws:
                    found_ws.name = new_name
                    await db.commit()
                    return f"Renamed workspace '{name}' → '{new_name}'."
                return f"Workspace '{name}' not found."
            except ValueError as e:
                return str(e)

        else:
            return f"Unknown action '{action}'. Use one of: list, create, switch, rename."


# ── Tool 2: Initialize Memory ──


@mcp.tool(name="jarvis_initialize_memory")
async def initialize_memory(workspace: str = "") -> str:
    """Initialize memory session. Call this at the start of every conversation.

    Returns workspace context summary and memory protocol instructions.
    If no workspace is specified, shows available workspaces.

    Args:
        workspace: Workspace name (or UUID). Leave empty to list workspaces.
    """
    if not workspace:
        # List workspaces
        async with async_session_factory() as db:
            result = await db.execute(select(Workspace).order_by(Workspace.created_at.desc()))
            workspaces = result.scalars().all()
            if not workspaces:
                return "No workspaces found. Create one first:\nmanage_workspace(action='create', name='my-project')"
            lines = [f"- {ws.name}" for ws in workspaces]
            return (
                "Available workspaces:\n" + "\n".join(lines) + "\n\n"
                "Call initialize_memory(workspace='name') to load a workspace."
            )

    try:
        ws_id = await _resolve_workspace(workspace)
    except ValueError as e:
        return str(e)

    recent_summary = ""
    workspace_name = workspace
    try:
        async with async_session_factory() as db:
            # Get workspace name
            ws_result = await db.execute(select(Workspace).where(Workspace.id == ws_id))
            ws = ws_result.scalar_one_or_none()
            if ws:
                workspace_name = ws.name

            result = await db.execute(
                select(KnowledgeFact, Entity.name)
                .join(Entity, KnowledgeFact.entity_id == Entity.id)
                .where(
                    KnowledgeFact.workspace_id == ws_id,
                    KnowledgeFact.superseded_at.is_(None),
                )
                .order_by(KnowledgeFact.recorded_at.desc())
                .limit(10)
            )
            rows = result.all()
            if rows:
                lines = [f"- {name} {fact.predicate} {fact.object_value}" for fact, name in rows]
                recent_summary = "Recent context:\n" + "\n".join(lines)
    except Exception:
        pass

    protocol = (
        f"Active workspace: {workspace_name}\n\n"
        "Memory protocol:\n"
        f"- Use workspace='{workspace_name}' in all store_memory and recall_memory calls.\n"
        "- Call store_memory when you learn a new fact — preferences, "
        "background, goals, technical stack, decisions.\n"
        "- Call store_memory when the user corrects or updates previously known info.\n"
        "- Call store_memory if 5+ substantive exchanges pass without storing.\n"
        "- Each memory should be a self-contained statement.\n"
        "- Do NOT store: greetings, small talk, info already stored, or your own responses."
    )

    if recent_summary:
        return f"{recent_summary}\n\n{protocol}"
    return protocol


# ── Tool 3: Store Memory ──


@mcp.tool(name="jarvis_store_memory")
async def store_memory(
    workspace: str,
    provider: str,
    conversation_transcript: str,
    entities: list[dict[str, str]],
    facts: list[dict[str, str]],
    conversation_summary: str = "",
) -> str:
    """Store structured knowledge extracted from the current conversation.

    Use this when you: (1) learn a new fact about the user — preferences,
    background, goals, technical stack; (2) the user makes a decision or states
    a preference; (3) the user corrects or updates previously known information;
    (4) a meaningful topic concludes with actionable insights.
    Do NOT store: greetings, small talk, information already stored, or your
    own responses. Also call this if 5+ substantive exchanges have passed
    without storing. Each memory should be a self-contained statement.

    Args:
        workspace: Workspace name (or UUID)
        provider: AI provider (openai, anthropic, google, manual)
        conversation_transcript: Raw conversation text
        entities: List of {name, entity_type, source_quote}
        facts: List of {subject, predicate, object, temporal, source_quote}
        conversation_summary: Brief summary of the conversation segment
    """
    if len(conversation_transcript) > 50000:
        return (
            "Failed to store memory: transcript too long. "
            f"Try again with content under 50000 characters. "
            f"Current length: {len(conversation_transcript)}"
        )

    try:
        ws_id = await _resolve_workspace(workspace)

        request = StoreMemoryRequest(
            workspace_id=ws_id,
            provider=provider,
            conversation_transcript=conversation_transcript,
            entities=[EntityHint(**e) for e in entities],
            facts=[FactHint(**f) for f in facts],
            conversation_summary=conversation_summary,
        )

        async with async_session_factory() as db:
            result = await _store(db, request)

        return (
            f"Stored {len(result.facts_stored)} facts, "
            f"resolved {result.entities_resolved} entities, "
            f"created {result.entities_created} new entities. "
            f"Episode: {result.episode_id}"
        )
    except Exception as e:
        return (
            f"Failed to store memory: {e}. "
            "Check that workspace exists and all required fields are present. "
            "Required entity fields: name, entity_type, source_quote. "
            "Required fact fields: subject, predicate, object, source_quote."
        )


# ── Tool 4: Recall Memory ──


@mcp.tool(name="jarvis_recall_memory")
async def recall_memory(
    workspace: str,
    query: str,
    limit: int = 10,
) -> str:
    """Recall relevant memories by semantic search.

    Use this when you need context from past conversations — at the start
    of a new session, when the user references past decisions, or when you
    need background information to give a better answer.

    Args:
        workspace: Workspace name (or UUID)
        query: Natural language query describing what you want to recall
        limit: Maximum number of results (1-50)
    """
    try:
        ws_id = await _resolve_workspace(workspace)

        request = RecallMemoryRequest(
            workspace_id=ws_id,
            query=query,
            limit=min(limit, 50),
        )

        async with async_session_factory() as db:
            result = await _recall(db, request)

        if not result.results:
            return "No relevant memories found."

        lines: list[str] = []
        for r in result.results:
            grounded_tag = " [grounded]" if r.grounded else " [low_trust]"
            lines.append(f"- {r.entity} {r.predicate} {r.object_value}{grounded_tag} (since {r.valid_from.date()})")
            if r.history:
                for h in r.history:
                    lines.append(f"  (was: {h.object_value}, until {h.superseded_at})")

        response = "\n".join(lines)

        if len(response) > MAX_RESPONSE_CHARS:
            response = (
                response[:MAX_RESPONSE_CHARS] + "\n\n[Results truncated. Narrow your query for more specific results.]"
            )

        return response
    except Exception as e:
        return f"Failed to recall memory: {e}. Check that workspace exists. Try a shorter or more specific query."
