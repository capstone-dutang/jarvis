"""MCP tool definitions for JARVIS. 4 tools: manage_workspace, initialize_memory, store_memory, recall_memory.

Based on: research/2026-03-31-mcp-server-implementation-research.md
- Tool descriptions: lines 204-215 ("Use this when..." pattern)
- Error handling: lines 237-245 (prescriptive ToolError messages)
- Response limit: definitive doc Section 9 (25,000 tokens)
"""

import uuid

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from jarvis.core.passage_search import search_passages as _search_passages
from jarvis.core.recall import recall_memory as _recall
from jarvis.core.store import store_memory as _store
from jarvis.core.topic_map import build_topic_map as _build_topic_map
from jarvis.db import async_session_factory
from jarvis.models.tables import Entity, KnowledgeFact, Workspace
from jarvis.schemas import (
    EntityHint,
    FactHint,
    RecallMemoryRequest,
    RelationHint,
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
        "Use store_memory whenever you learn new facts. "
        "For unfamiliar topics, call explore_topic first to map the landscape, "
        "then call recall_memory with a narrower query for specific details."
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
    relations: list[dict[str, str]] | None = None,
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
        relations: List of {from_entity, to_entity, relation_type, source_quote}
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
            relations=[RelationHint(**r) for r in (relations or [])],
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

        # Build metadata header (v1: structural_summary + coverage)
        header_lines: list[str] = []
        if result.structural_summary:
            header_lines.append(result.structural_summary)
        header = "\n".join(header_lines)

        lines: list[str] = []
        if result.anchor_matched:
            lines.append("[anchor-matched: query resolved to specific entities]")
        else:
            lines.append("[broad search: no anchor entity matched]")

        for r in result.results:
            grounded_tag = " [grounded]" if r.grounded else " [low_trust]"
            lines.append(f"- {r.entity} {r.predicate} {r.object_value}{grounded_tag} (since {r.valid_from.date()})")
            if r.related_entities:
                rel_repr = ", ".join(
                    f"{rel.name}[{rel.relation_type}, {rel.fact_count}f]"
                    for rel in r.related_entities[:5]
                )
                lines.append(f"  related: {rel_repr}")
            if r.history:
                for h in r.history:
                    lines.append(f"  (was: {h.object_value}, until {h.superseded_at})")

        body = "\n".join(lines)
        footer = ""
        if result.pagination_token == "more_available":
            footer = "\n\n[More facts available — narrow query for specifics.]"

        response = f"{header}\n\n{body}{footer}" if header else f"{body}{footer}"

        if len(response) > MAX_RESPONSE_CHARS:
            response = (
                response[:MAX_RESPONSE_CHARS] + "\n\n[Results truncated. Narrow your query for more specific results.]"
            )

        return response
    except Exception as e:
        return f"Failed to recall memory: {e}. Check that workspace exists. Try a shorter or more specific query."


# ── Tool 5: Explore Topic (structural map, no fact details) ──


@mcp.tool(name="jarvis_explore_topic")
async def explore_topic(workspace: str, query: str) -> str:
    """Get a structural map of a topic before diving into details.

    Use this when: you need to explore a topic you're unfamiliar with — it
    returns entities, community tags, and predicate distribution without fact
    details, so you can scan the landscape cheaply. Then call recall_memory
    with a narrower query for the specific entities/predicates you care about.

    Do NOT use this when: you already know the entity name and want its facts
    (call recall_memory directly), or for trivial lookups.

    Args:
        workspace: Workspace name (or UUID)
        query: Natural language query describing the topic to explore
    """
    try:
        ws_id = await _resolve_workspace(workspace)

        async with async_session_factory() as db:
            topic_map = await _build_topic_map(db, ws_id, query)

        if topic_map.total_candidates == 0:
            return f"No entities found for topic '{query}'. Try a broader query."

        lines: list[str] = []
        expanded_repr = ", ".join(topic_map.expanded_terms) if topic_map.expanded_terms else query
        lines.append(f'Topic map for "{query}" (expanded: {expanded_repr})')
        lines.append(
            f"Pool: {topic_map.total_candidates} candidates, "
            f"{topic_map.distinct_communities} distinct communities, "
            f"{topic_map.isolated_entity_count} entities isolated"
        )

        lines.append(f"\nTop entities ({len(topic_map.entities)}):")
        for e in topic_map.entities:
            comm = f"comm={e.community_id}" if e.community_id is not None else "comm=none"
            lines.append(
                f"  - {e.name} [{e.entity_type}, {comm}] "
                f"pool_facts={e.fact_count_in_pool}, ws_facts={e.workspace_fact_count}, "
                f"out_degree={e.out_degree}"
            )

        if topic_map.top_predicates:
            pred_repr = ", ".join(f"{p} ({c})" for p, c in topic_map.top_predicates)
            lines.append(f"\nTop predicates in pool:\n  {pred_repr}")

        if topic_map.time_range_start and topic_map.time_range_end:
            lines.append(
                f"\nTime range: {topic_map.time_range_start:%Y-%m-%d %H:%M} "
                f"→ {topic_map.time_range_end:%Y-%m-%d %H:%M}"
            )
        lines.append(f"Edges between top entities: {topic_map.edge_count}")

        lines.append(
            "\nNext: call recall_memory with a specific entity or predicate for details."
        )

        response = "\n".join(lines)
        if len(response) > MAX_RESPONSE_CHARS:
            response = response[:MAX_RESPONSE_CHARS] + "\n\n[Truncated.]"
        return response
    except Exception as e:
        return f"Failed to explore topic: {e}. Check that workspace exists."


# ── Tool 6: Search Passages (narrative/episodic layer) ──


@mcp.tool(name="jarvis_search_passages")
async def search_passages_tool(workspace: str, query: str, limit: int = 10) -> str:
    """Semantic search over raw conversation passages — bypasses anchor filter.

    Use this when: recall_memory returns structured facts but you need the
    "why/decision/reasoning" behind them. Returns natural-language passages
    ranked by semantic similarity with links back to episode and fact.

    Do NOT use this as a default — recall_memory is cheaper and returns
    structured facts. Fall back to this when recall misses narrative context.

    Args:
        workspace: Workspace name (or UUID)
        query: Natural language query for the reasoning/decision you want
        limit: Max passages to return (1-50)
    """
    try:
        ws_id = await _resolve_workspace(workspace)
        async with async_session_factory() as db:
            hits = await _search_passages(db, ws_id, query, limit=limit)
        if not hits:
            return f"No passages found for '{query}'."

        lines = [f"Passages for '{query}' (ranked by semantic similarity):"]
        for i, h in enumerate(hits, 1):
            link = ""
            if h.entity_name and h.predicate:
                link = f" [{h.entity_name} {h.predicate}]"
            ep_short = str(h.episode_id)[:8]
            content = h.content if len(h.content) <= 400 else h.content[:400] + "..."
            lines.append(f"\n{i}. sim={h.similarity:.3f}{link} (ep={ep_short})")
            lines.append(f"   {content}")

        response = "\n".join(lines)
        if len(response) > MAX_RESPONSE_CHARS:
            response = response[:MAX_RESPONSE_CHARS] + "\n\n[Truncated.]"
        return response
    except Exception as e:
        return f"Failed to search passages: {e}."
