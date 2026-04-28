# 01_agent module/context/code/compact/prompt.py
"""Summarization prompts for context compression."""


def get_compact_prompt(custom_instructions: str = None) -> str:
    """
    Full compaction system prompt.
    """
    base = """You are an AI assistant summarizing a conversation.

Create a concise summary capturing:
- Main topic or task
- Key decisions made
- Important code snippets, file paths, technical details
- Problems identified and solutions
- Open questions or next steps

Format with sections:
## Topic
[1-2 sentences]

## Key Decisions
[Bulleted list]

## Technical Details
[Code snippets, file paths]

## Next Steps
[Any remaining work]"""

    if custom_instructions:
        return f"{base}\n\nCustom instructions:\n{custom_instructions}"
    return base


def get_partial_compact_prompt(direction: str = "from") -> str:
    """Partial compaction prompt."""
    target = "AFTER" if direction == "from" else "BEFORE"
    return f"""Summarize messages {target} the selected point:
- Main topic
- Key decisions
- Remaining work"""


def get_compact_summary_message(summary: str, suppress_follow_up: bool = False) -> str:
    """Format summary as user message."""
    msg = f"[Compacted Summary]\n\n{summary}"
    if suppress_follow_up:
        msg += "\n\n(Note: Follow-up questions suppressed)"
    return msg