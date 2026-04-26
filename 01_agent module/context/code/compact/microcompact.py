# 01_agent module/context/code/compact/microcompact.py
"""Tier 1: Microcompact - Pre-request compression.

Lightweight compression before each API request.
Clears old tool results based on time gap.
"""

import time
import datetime
from typing import List, Set, Union, Optional

# Tools that can be microcompacted
COMPACTABLE_TOOLS: Set[str] = {
    "read_file",
    "edit_file",
    "write_file",
    "bash",
    "grep",
    "glob",
    "WebSearch",
    "WebFetch",
}

TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"

DEFAULT_TIME_BASED_CONFIG = {
    "enabled": True,
    "gap_threshold_minutes": 1,
    "keep_recent": 3,
}


def get_time_based_config() -> dict:
    """Get time-based microcompact config."""
    return DEFAULT_TIME_BASED_CONFIG.copy()


def collect_compactable_tool_ids(messages: List[Union[dict, object]]) -> List[str]:
    """
    Collect tool_use IDs that can be compacted.

    Args:
        messages: List of messages

    Returns:
        List of compactable tool IDs in encounter order
    """
    ids: List[str] = []
    for msg in messages:
        content = msg.get("content", []) if isinstance(msg, dict) else getattr(msg, "content", [])
        if not isinstance(content, list):
            continue

        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "")
                if name in COMPACTABLE_TOOLS:
                    tool_id = block.get("id", "")
                    if tool_id:
                        ids.append(tool_id)
    return ids


def evaluate_time_based_trigger(
    messages: List[Union[dict, object]],
    query_source: str = None
) -> Optional[dict]:
    """
    Check if time-based trigger should fire.

    Returns gap_minutes if trigger fires, None otherwise.

    Args:
        messages: List of messages
        query_source: Query source identifier

    Returns:
        {gap_minutes, config} if triggered, None otherwise
    """
    config = get_time_based_config()

    if not config.get("enabled", False):
        return None

    # Require explicit main-thread source
    if query_source and not query_source.startswith("repl_main_thread"):
        return None

    # Find last assistant message
    last_assistant = None
    for msg in reversed(messages):
        if isinstance(msg, dict):
            if msg.get("role") == "assistant" or msg.get("type") == "assistant":
                last_assistant = msg
                break
        elif hasattr(msg, "role"):
            if msg.role == "assistant":
                last_assistant = msg
                break

    if not last_assistant:
        return None

    # Get timestamp
    timestamp = last_assistant.get("timestamp", "") if isinstance(last_assistant, dict) else getattr(last_assistant, "timestamp", "")

    if not timestamp:
        return None

    try:
        if isinstance(timestamp, str):
            last_time = datetime.datetime.fromisoformat(timestamp).timestamp()
        else:
            return None
    except (ValueError, TypeError, OSError):
        return None

    current_time = time.time()
    gap_minutes = (current_time - last_time) / 60

    if gap_minutes < config.get("gap_threshold_minutes", 60):
        return None

    return {"gap_minutes": gap_minutes, "config": config}


def maybe_time_based_microcompact(
    messages: List[Union[dict, object]],
    query_source: str = None
) -> Optional[List[Union[dict, object]]]:
    """
    Execute time-based microcompact if trigger fires.

    Clears old tool results when user returns after a gap.
    Keeps the most recent N results.

    Args:
        messages: List of messages
        query_source: Query source identifier

    Returns:
        Messages with cleared tool results, or None if trigger didn't fire
    """
    trigger = evaluate_time_based_trigger(messages, query_source)
    if not trigger:
        return None

    config = trigger["config"]
    keep_recent = max(1, config.get("keep_recent", 5))

    compactable_ids = collect_compactable_tool_ids(messages)

    # Keep at least 1
    keep_set = set(compactable_ids[-keep_recent:])
    clear_set = set(id for id in compactable_ids if id not in keep_set)

    if not clear_set:
        return None

    def clear_block(block: dict) -> dict:
        if block.get("type") != "tool_result":
            return block
        tool_use_id = block.get("tool_use_id", "")
        if tool_use_id not in clear_set:
            return block
        if block.get("content") == TIME_BASED_MC_CLEARED_MESSAGE:
            return block
        return {**block, "content": TIME_BASED_MC_CLEARED_MESSAGE}

    result = []
    for msg in messages:
        content = msg.get("content", []) if isinstance(msg, dict) else getattr(msg, "content", [])
        if isinstance(content, list):
            new_content = [clear_block(b) if isinstance(b, dict) else b for b in content]
            if isinstance(msg, dict):
                result.append({**msg, "content": new_content})
            else:
                # Create a shallow copy of the message (immutable pattern)
                new_msg = object.__new__(type(msg))
                new_msg.__dict__.update(msg.__dict__.copy())
                new_msg.content = new_content
                result.append(new_msg)
        else:
            result.append(msg)

    return result


def microcompact_messages(
    messages: List[Union[dict, object]],
    query_source: str = None
) -> List[Union[dict, object]]:
    """
    Main entry point for microcompact.

    Runs time-based microcompact before each API request.

    Args:
        messages: List of messages
        query_source: Query source identifier

    Returns:
        Messages with potentially cleared tool results
    """
    result = maybe_time_based_microcompact(messages, query_source)
    return result if result is not None else messages