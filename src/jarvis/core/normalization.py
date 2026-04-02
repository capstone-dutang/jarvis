"""Conversation normalization: provider-specific → canonical format.

Based on: JARVIS_DEFINITIVE.md Section 16 + research/cloud-context-server Section 4

Handles 8 edge cases:
1. Gemini role "model" → "assistant"
2. Claude tool_call inside content → separate tool_calls field
3. OpenAI tool arguments JSON string → parsed object
4. OpenAI role "tool" → "tool_result"
5. Gemini functionCall (camelCase) → tool_calls
6. content flat string → [{"type": "text", "text": "..."}]
7. system prompt location → separate system_prompt field
8. Claude thinking / OpenAI reasoning → {"type": "thinking"}
"""

import json
import logging
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ContentPart(BaseModel):
    type: str  # "text", "thinking", "image", "tool_use", "tool_result"
    text: str = ""


class ToolCallInfo(BaseModel):
    id: str = ""
    name: str = ""
    arguments: dict[str, Any] = {}


class CanonicalMessage(BaseModel):
    role: str  # "user", "assistant", "tool_result", "system"
    content: list[ContentPart] = []
    tool_calls: list[ToolCallInfo] = []


class NormalizedTranscript(BaseModel):
    system_prompt: str = ""
    messages: list[CanonicalMessage] = []


def normalize_transcript(provider: str, raw: str) -> NormalizedTranscript:
    """Normalize raw conversation transcript to canonical format.

    The raw input is expected to be a JSON string containing messages.
    If it's plain text (not JSON), wrap it as a single user message.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Plain text — wrap as single user message
        return NormalizedTranscript(
            messages=[
                CanonicalMessage(
                    role="user",
                    content=[ContentPart(type="text", text=raw)],
                )
            ]
        )

    if provider in ("openai", "chatgpt"):
        return _normalize_openai(data)
    elif provider in ("anthropic", "claude"):
        return _normalize_claude(data)
    elif provider in ("google", "gemini"):
        return _normalize_gemini(data)
    else:
        return _normalize_generic(data)


def _normalize_openai(data: Any) -> NormalizedTranscript:
    """Normalize OpenAI format.

    Edge cases:
    - content is flat string → wrap in array
    - tool_calls arguments are JSON strings → parse
    - role "tool" → "tool_result"
    - role "system" → extract to system_prompt
    - reasoning_content → thinking block
    """
    messages_raw = data if isinstance(data, list) else data.get("messages", [])
    system_prompt = ""
    messages: list[CanonicalMessage] = []

    for msg in messages_raw:
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "user")

        # Extract system prompt
        if role == "system":
            system_prompt = msg.get("content", "")
            continue

        # Map role
        if role == "tool":
            role = "tool_result"

        # Normalize content
        content_raw = msg.get("content", "")
        parts: list[ContentPart] = []
        if isinstance(content_raw, str):
            if content_raw:
                parts.append(ContentPart(type="text", text=content_raw))
        elif isinstance(content_raw, list):
            for item in content_raw:
                if isinstance(item, dict):
                    parts.append(ContentPart(type=item.get("type", "text"), text=item.get("text", "")))

        # Reasoning → thinking
        reasoning = msg.get("reasoning_content") or msg.get("reasoning")
        if reasoning:
            parts.insert(0, ContentPart(type="thinking", text=str(reasoning)))

        # Tool calls
        tool_calls: list[ToolCallInfo] = []
        for tc in msg.get("tool_calls", []):
            args = tc.get("arguments", tc.get("function", {}).get("arguments", "{}"))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}
            tool_calls.append(
                ToolCallInfo(
                    id=tc.get("id", ""),
                    name=tc.get("function", {}).get("name", tc.get("name", "")),
                    arguments=args,
                )
            )

        messages.append(CanonicalMessage(role=role, content=parts, tool_calls=tool_calls))

    return NormalizedTranscript(system_prompt=system_prompt, messages=messages)


def _normalize_claude(data: Any) -> NormalizedTranscript:
    """Normalize Claude/Anthropic format.

    Edge cases:
    - tool_use blocks inside content array → extract to tool_calls
    - thinking blocks → ContentPart(type="thinking")
    - system is top-level parameter, not a message
    """
    system_prompt = ""
    if isinstance(data, dict):
        system_prompt = data.get("system", "")
        messages_raw = data.get("messages", [])
    else:
        messages_raw = data

    messages: list[CanonicalMessage] = []

    for msg in messages_raw:
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "user")
        content_raw = msg.get("content", "")
        parts: list[ContentPart] = []
        tool_calls: list[ToolCallInfo] = []

        if isinstance(content_raw, str):
            parts.append(ContentPart(type="text", text=content_raw))
        elif isinstance(content_raw, list):
            for block in content_raw:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "text")

                if block_type == "tool_use":
                    # Extract to tool_calls field
                    tool_calls.append(
                        ToolCallInfo(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            arguments=block.get("input", {}),
                        )
                    )
                elif block_type == "thinking":
                    parts.append(ContentPart(type="thinking", text=str(block.get("thinking", block.get("text", "")))))
                else:
                    parts.append(ContentPart(type=block_type, text=block.get("text", "")))

        messages.append(CanonicalMessage(role=role, content=parts, tool_calls=tool_calls))

    return NormalizedTranscript(system_prompt=system_prompt, messages=messages)


def _normalize_gemini(data: Any) -> NormalizedTranscript:
    """Normalize Gemini/Google format.

    Edge cases:
    - role "model" → "assistant"
    - functionCall (camelCase) → tool_calls
    - systemInstruction → system_prompt
    - parts array → content parts
    """
    system_prompt = ""
    if isinstance(data, dict):
        sys_instr = data.get("systemInstruction", data.get("system_instruction", {}))
        if isinstance(sys_instr, dict):
            sys_parts = sys_instr.get("parts", [])
            system_prompt = " ".join(p.get("text", "") for p in sys_parts if isinstance(p, dict))
        elif isinstance(sys_instr, str):
            system_prompt = sys_instr
        messages_raw = data.get("contents", data.get("messages", [])) or []
    else:
        messages_raw = data if isinstance(data, list) else []

    messages: list[CanonicalMessage] = []

    for msg in messages_raw:
        if not isinstance(msg, dict):
            continue

        # Map role
        role = msg.get("role", "user")
        if role == "model":
            role = "assistant"

        parts_raw = msg.get("parts", [])
        parts: list[ContentPart] = []
        tool_calls: list[ToolCallInfo] = []

        for part in parts_raw:
            if not isinstance(part, dict):
                continue

            if "text" in part:
                parts.append(ContentPart(type="text", text=part["text"]))
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append(
                    ToolCallInfo(
                        id=f"gemini-{id(fc)}",  # Synthetic ID
                        name=fc.get("name", ""),
                        arguments=fc.get("args", {}),
                    )
                )
            elif "functionResponse" in part:
                fr = part["functionResponse"]
                parts.append(ContentPart(type="tool_result", text=json.dumps(fr.get("response", {}))))

        messages.append(CanonicalMessage(role=role, content=parts, tool_calls=tool_calls))

    return NormalizedTranscript(system_prompt=system_prompt, messages=messages)


def _normalize_generic(data: Any) -> NormalizedTranscript:
    """Fallback: try to extract messages from any format."""
    if isinstance(data, list):
        messages = []
        for msg in data:
            if isinstance(msg, dict) and "role" in msg:
                content = msg.get("content", "")
                parts = [ContentPart(type="text", text=content)] if isinstance(content, str) else []
                messages.append(CanonicalMessage(role=msg["role"], content=parts))
        return NormalizedTranscript(messages=messages)
    return NormalizedTranscript(
        messages=[CanonicalMessage(role="user", content=[ContentPart(type="text", text=str(data))])]
    )
