# 01_agent module/context/code/compact/token_estimation.py
"""Token estimation utilities for context compression."""

from typing import Union, List

IMAGE_MAX_TOKEN_SIZE = 2000
BYTES_PER_TOKEN = 4


def rough_token_count_estimation(text: str) -> int:
    """
    Estimate tokens using ~4 chars per token.

    Args:
        text: Input text string

    Returns:
        Estimated token count
    """
    if not text:
        return 0
    return (len(text) // BYTES_PER_TOKEN) + 1


def estimate_message_tokens(message: Union[dict, object]) -> int:
    """
    Estimate token count for a message.

    Handles both dict format (from runtime.py) and Message dataclass.

    Args:
        message: Message dict or Message object

    Returns:
        Estimated token count, padded by 4/3 for conservatism
    """
    if isinstance(message, dict):
        content = message.get("content", "")
    else:
        content = getattr(message, "content", "")

    if isinstance(content, str):
        return rough_token_count_estimation(content)

    if not isinstance(content, list):
        return 0

    total = 0
    for block in content:
        if not isinstance(block, dict):
            if hasattr(block, "model_dump"):
                block = block.model_dump()
            else:
                block = {"type": str(type(block).__name__)}
        block_type = block.get("type", "")

        if block_type == "text":
            text = block if isinstance(block, str) else block.get("text", "")
            total += rough_token_count_estimation(text)
        elif block_type in ("image", "document"):
            total += IMAGE_MAX_TOKEN_SIZE
        elif block_type == "tool_result":
            result_content = block.get("content", "") if isinstance(block, dict) else ""
            if isinstance(result_content, str):
                total += rough_token_count_estimation(result_content)
            elif isinstance(result_content, list):
                for item in result_content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        total += rough_token_count_estimation(item.get("text", ""))

    return int(total * (4 / 3)) + 1


def rough_token_count_estimation_for_messages(messages: List[Union[dict, object]]) -> int:
    """
    Sum token estimates for a message list.

    Args:
        messages: List of messages

    Returns:
        Total estimated token count
    """
    return sum(estimate_message_tokens(msg) for msg in messages)