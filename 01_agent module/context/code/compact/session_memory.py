# 01_agent module/context/code/compact/session_memory.py
"""Tier 2: Session Memory Compaction.

Uses pre-extracted structured facts instead of LLM summarization.
Zero API cost, leverages already-extracted memory.
"""

from pathlib import Path
from typing import List, Set, Optional, Union
import datetime

from .models import SessionMemory
from .token_estimation import rough_token_count_estimation_for_messages

# Extraction thresholds
EXTRACTION_INIT_THRESHOLD = 20000  # Tokens to trigger initial extraction
EXTRACTION_UPDATE_THRESHOLD = 5000  # Tokens + 10 tool calls for update
EXTRACTION_MIN_TOOL_CALLS = 10  # Minimum tool calls for update trigger
MAX_SECTION_TOKENS = 4096  # Max tokens per section in extraction

# Compaction config
DEFAULT_SM_CONFIG = {
    "min_tokens": 10000,
    "min_text_block_messages": 5,
    "max_tokens": 40000,
}

# Extraction prompt for LLM-based memory extraction
EXTRACTION_PROMPT = """Based on the user conversation above (EXCLUDING this note-taking instruction message as well as system prompt, claude.md entries, or any past session summaries), update the session notes file.

The file {notesPath} has already been read for you. Here are its current contents:
<current_notes_content>
{currentNotes}
</current_notes_content>

Your ONLY task is to use the Edit tool to update the notes file, then stop. You can make multiple edits (update every section as needed) - make all Edit tool calls in parallel in a single message. Do not call any other tools.

CRITICAL RULES FOR EDITING:
- The file must maintain its exact structure with all sections, headers, and italic descriptions intact
-- NEVER modify, delete, or add section headers (the lines starting with '#' like # Task specification)
-- NEVER modify or delete the italic _section description_ lines (these are the lines in italics immediately following each header - they start and end with underscores)
-- The italic _section descriptions_ are TEMPLATE INSTRUCTIONS that must be preserved exactly as-is - they guide what content belongs in each section
-- ONLY update the actual content that appears BELOW the italic _section descriptions_ within each existing section
-- Do NOT add any new sections, summaries, or information outside the existing structure
- Do NOT reference this note-taking process or instructions anywhere in the notes
- It's OK to skip updating a section if there are no substantial new insights to add. Do not add filler content like "No info yet", just leave sections blank/unedited if appropriate.
- Write DETAILED, INFO-DENSE content for each section - include specifics like file paths, function names, error messages, exact commands, technical details, etc.
- For "Key results", include the complete, exact output the user requested (e.g., full table, full answer, etc.)
- Do not include information that's already in the CLAUDE.md files included in the context
- Keep each section under ~{MAX_SECTION_TOKENS} tokens/words - if a section is approaching this limit, condense it by cycling out less important details while preserving the most critical information
- Focus on actionable, specific information that would help someone understand or recreate the work discussed in the conversation
- IMPORTANT: Always update "Current State" to reflect the most recent work - this is critical for continuity after compaction

Use the Edit tool with file_path: {notesPath}

STRUCTURE PRESERVATION REMINDER:
Each section has TWO parts that must be preserved exactly as they appear in the current file:
1. The section header (line starting with #)
2. The italic description line (the _italicized text_ immediately after the header - this is a template instruction)

You ONLY update the actual content that comes AFTER these two preserved lines. The italic description lines starting and ending with underscores are part of the template structure, NOT content to be edited or removed.

REMEMBER: Use the Edit tool in parallel and stop. Do not continue after the edits. Only include insights from the actual user conversation, never from these note-taking instructions. Do not delete or change section headers or italic _section descriptions_.

OUTPUT FORMAT:
Your final response (after Edit tool calls) must be a markdown string. The output MUST follow this exact structure:

# Session Memory: {session_id}

## Title
_5-10 word descriptive title of the session_

## Current State
_What is currently being worked on, current progress/status_

## Task Specification
_What the user asked to accomplish, specific requirements_

## Implementation Notes
_Key technical decisions, code patterns, file paths, function names_

## Important Context
_Critical info to preserve: error fixes, user preferences, workarounds_

## Next Steps
_Concrete next steps or remaining work_

"""


def get_sm_config() -> dict:
    """Get session memory compaction config."""
    return DEFAULT_SM_CONFIG.copy()


def _get_memory_dir() -> Path:
    """Get or create the memory directory."""
    memory_dir = Path.cwd() / ".harness" / "data" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    return memory_dir


def _get_memory_file_path(session_id: str) -> Path:
    """Get path for session memory file."""
    return _get_memory_dir() / f"{session_id}.md"


def load_session_memory(session_id: str) -> Optional[SessionMemory]:
    """
    Load session memory from file.

    Args:
        session_id: Session identifier

    Returns:
        SessionMemory object or None if not found/empty
    """
    file_path = _get_memory_file_path(session_id)
    if not file_path.exists():
        return None

    try:
        content = file_path.read_text(encoding="utf-8")
        if not content or len(content.strip()) < 50:
            return None
        return SessionMemory.from_markdown(session_id, content)
    except (OSError, ValueError):
        return None


def save_session_memory(memory: SessionMemory) -> bool:
    """
    Save session memory to file.

    Args:
        memory: SessionMemory object to save

    Returns:
        True on success, False on failure
    """
    try:
        file_path = _get_memory_file_path(memory.session_id)
        file_path.write_text(memory.to_markdown(), encoding="utf-8")
        return True
    except OSError:
        return False


def is_session_memory_empty(session_id: str) -> bool:
    """
    Check if session memory file exists and has content.

    Args:
        session_id: Session identifier

    Returns:
        True if no memory file or content is essentially empty
    """
    memory = load_session_memory(session_id)
    if memory is None:
        return True

    empty_fields = ["title", "current_state", "task_specification",
                    "implementation_notes", "important_context", "next_steps"]
    if all(getattr(memory, f) in ("", "N/A", None) for f in empty_fields):
        return True

    return False


def delete_session_memory(session_id: str) -> bool:
    """Delete session memory file."""
    try:
        file_path = _get_memory_file_path(session_id)
        if file_path.exists():
            file_path.unlink()
        return True
    except OSError:
        return False


def has_text_blocks(message: Union[dict, object]) -> bool:
    """Check if message has text content."""
    content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")

    if isinstance(content, str):
        return len(content) > 0
    if isinstance(content, list):
        return any(
            (isinstance(b, dict) and b.get("type") == "text") for b in content
        )
    return False


def get_tool_result_ids(message: Union[dict, object]) -> List[str]:
    """Get tool_result IDs from message."""
    content = message.get("content", []) if isinstance(message, dict) else getattr(message, "content", [])
    if not isinstance(content, list):
        return []
    return [
        block.get("tool_use_id", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]


def has_tool_use_with_ids(message: Union[dict, object], tool_ids: Set[str]) -> bool:
    """Check if message has tool_use with given IDs."""
    content = message.get("content", []) if isinstance(message, dict) else getattr(message, "content", [])
    if not isinstance(content, list):
        return False
    return any(
        block.get("type") == "tool_use" and block.get("id", "") in tool_ids
        for block in content
    )


def adjust_index_to_preserve_pairs(
    messages: List[Union[dict, object]],
    start_index: int
) -> int:
    """Adjust index to not split tool_use/tool_result pairs."""
    if start_index <= 0 or start_index >= len(messages):
        return start_index

    adjusted = start_index

    all_result_ids: List[str] = []
    for i in range(start_index, len(messages)):
        all_result_ids.extend(get_tool_result_ids(messages[i]))

    if all_result_ids:
        needed = set(all_result_ids)
        for i in range(adjusted - 1, -1, -1):
            if not needed:
                break
            msg = messages[i]
            if has_tool_use_with_ids(msg, needed):
                adjusted = i
                content = msg.get("content", []) if isinstance(msg, dict) else getattr(msg, "content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            needed.discard(block.get("id", ""))

    return adjusted


def _is_compact_boundary(message: Union[dict, object]) -> bool:
    """Check if message is a compact boundary."""
    content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
    return content == "compact_boundary" if isinstance(content, str) else False


def _estimate_tokens(message: Union[dict, object]) -> int:
    """Estimate tokens for a message."""
    content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
    if isinstance(content, str):
        return (len(content) // 4) + 1
    if isinstance(content, list):
        return sum((len(str(b)) // 4) + 1 for b in content)
    return 0


def calculate_messages_to_keep_index(
    messages: List[Union[dict, object]],
    last_summarized_index: int
) -> int:
    """Calculate starting index for messages to keep."""
    if not messages:
        return 0

    config = get_sm_config()
    start_index = last_summarized_index + 1 if last_summarized_index >= 0 else len(messages)

    total_tokens = sum(_estimate_tokens(messages[i]) for i in range(start_index, len(messages)))
    text_count = sum(1 for i in range(start_index, len(messages)) if has_text_blocks(messages[i]))

    if total_tokens >= config.get("max_tokens", 40000):
        return adjust_index_to_preserve_pairs(messages, start_index)

    if total_tokens >= config.get("min_tokens", 10000) and text_count >= config.get("min_text_block_messages", 5):
        return adjust_index_to_preserve_pairs(messages, start_index)

    floor = 0
    for i in range(len(messages) - 1, -1, -1):
        if _is_compact_boundary(messages[i]):
            floor = i + 1
            break

    for i in range(start_index - 1, max(floor, 0) - 1, -1):
        total_tokens += _estimate_tokens(messages[i])
        if has_text_blocks(messages[i]):
            text_count += 1
        start_index = i

        if total_tokens >= config.get("max_tokens", 40000):
            break
        if total_tokens >= config.get("min_tokens", 10000) and text_count >= config.get("min_text_block_messages", 5):
            break

    return adjust_index_to_preserve_pairs(messages, start_index)


def _count_tool_calls_since_index(messages: List[Union[dict, object]], since_index: int) -> int:
    """Count tool calls since the given index."""
    tool_calls = 0
    for i in range(since_index + 1, len(messages)):
        content = messages[i].get("content", []) if isinstance(messages[i], dict) else getattr(messages[i], "content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_calls += 1
    return tool_calls


def _get_tokens_since_index(messages: List[Union[dict, object]], since_index: int) -> int:
    """Calculate tokens since the given index."""
    return sum(_estimate_tokens(messages[i]) for i in range(since_index + 1, len(messages)))


def should_trigger_extraction(
    messages: List[Union[dict, object]],
    session_id: str,
    is_initial: bool = False
) -> bool:
    """
    Check if session memory extraction should be triggered.

    Args:
        messages: All messages in conversation
        session_id: Current session ID
        is_initial: True if this is first extraction (vs update)

    Returns:
        True if extraction should be triggered
    """
    token_count = rough_token_count_estimation_for_messages(messages)

    if is_initial:
        return token_count >= EXTRACTION_INIT_THRESHOLD

    existing_memory = load_session_memory(session_id)
    if not existing_memory or not existing_memory.last_summarized_message_id:
        return token_count >= EXTRACTION_INIT_THRESHOLD

    last_idx = -1
    for i, msg in enumerate(messages):
        msg_id = msg.get("id", "") if isinstance(msg, dict) else getattr(msg, "id", "")
        if msg_id == existing_memory.last_summarized_message_id:
            last_idx = i
            break

    if last_idx < 0:
        return token_count >= EXTRACTION_INIT_THRESHOLD

    recent_tokens = _get_tokens_since_index(messages, last_idx)
    recent_tool_calls = _count_tool_calls_since_index(messages, last_idx)

    return (recent_tokens >= EXTRACTION_UPDATE_THRESHOLD and recent_tool_calls >= EXTRACTION_MIN_TOOL_CALLS) or \
           token_count >= EXTRACTION_INIT_THRESHOLD * 1.5


def _content_to_str(content) -> str:
    """Convert content blocks to string."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                parts.append(f"[tool: {block.get('name', 'unknown')}]")
            elif block.get("type") == "tool_result":
                parts.append("[tool result]")
        elif hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts)


def _mock_extract_session_memory(session_id: str, messages: List[Union[dict, object]]) -> SessionMemory:
    """Generate mock session memory for testing."""
    msg_count = len(messages)
    token_count = rough_token_count_estimation_for_messages(messages)

    return SessionMemory(
        session_id=session_id,
        title=f"Session {session_id[:8]}" if session_id else "Mock Session",
        current_state="Mock extraction for testing purposes",
        task_specification=f"Processed {msg_count} messages (~{token_count} tokens)",
        implementation_notes="This is mock data - implement with real LLM for production",
        important_context="Mock session memory for testing Tier 2 compaction",
        next_steps="Integrate real LLM extraction",
        last_summarized_message_id=messages[-1].get("id", "") if messages else None,
        created_at=datetime.datetime.now().isoformat(),
        updated_at=datetime.datetime.now().isoformat(),
    )


async def extract_session_memory(
    messages: List[Union[dict, object]],
    session_id: str,
    llm_client=None,
    model_id: str = "minimax-m2.7"
) -> Optional[SessionMemory]:
    """
    Extract structured session memory via LLM.

    Args:
        messages: Messages to extract from
        session_id: Session identifier
        llm_client: LLM client (uses mock if None)
        model_id: Model to use

    Returns:
        SessionMemory object or None on failure
    """
    if llm_client is None:
        return _mock_extract_session_memory(session_id, messages)

    from .compaction import strip_images_from_messages
    stripped = strip_images_from_messages(messages)

    # Format prompt with runtime variables
    notes_path = str(_get_memory_file_path(session_id))
    existing_memory = load_session_memory(session_id)
    current_notes = existing_memory.to_markdown() if existing_memory else ""

    # Get last message ID for the output format
    last_msg_id = ""
    if messages:
        last_msg = messages[-1]
        last_msg_id = last_msg.get("id", "") if isinstance(last_msg, dict) else getattr(last_msg, "id", "")

    timestamp = datetime.datetime.now().isoformat()

    formatted_prompt = EXTRACTION_PROMPT.format(
        notesPath=notes_path,
        currentNotes=current_notes,
        MAX_SECTION_TOKENS=MAX_SECTION_TOKENS,
        session_id=session_id,
        message_id=last_msg_id,
        timestamp=timestamp,
    )

    api_messages = []
    for msg in stripped:
        if isinstance(msg, dict):
            role = msg.get("role", "")
            content = msg.get("content", "")
        else:
            role = getattr(msg, "role", "")
            content = getattr(msg, "content", "")

        if isinstance(content, list):
            content = _content_to_str(content)
        api_messages.append({"role": role, "content": content})
    api_messages.append({"role":"user","content":formatted_prompt})

    # Tools for extraction agent
    extraction_tools = [
        {
            "name": "read_file",
            "description": "Read file contents.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "edit_file",
            "description": "Write content to a file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["path", "content"]
            }
        },
    ]

    max_tool_failures = 3
    tool_failures = 0

    try:
        response = llm_client.messages.create(
            model=model_id,
            system="Based on the user conversation above (EXCLUDING this note-taking instruction message as well as system prompt, claude.md entries, or any past session summaries), update the session notes file.",
            messages=api_messages,
            tools=extraction_tools,
            max_tokens=4096,
        )

        # Handle tool use loop
        while response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input

                    try:
                        if tool_name == "read_file":
                            path = tool_input.get("path", "")
                            limit = tool_input.get("limit")
                            result = _safe_read_file(path, limit)
                        elif tool_name == "edit_file":
                            path = tool_input.get("path", "")
                            content = tool_input.get("content", "")
                            result = _safe_edit_file(path, content)
                        else:
                            result = f"Unknown tool: {tool_name}"
                    except Exception as e:
                        result = f"[Error: {type(e).__name__}] {e}"
                        tool_failures += 1

                    if tool_failures >= max_tool_failures:
                        return None

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            api_messages.append({"role": "assistant", "content": response.content})
            api_messages.append({"role": "user", "content": tool_results})

            response = llm_client.messages.create(
                model=model_id,
                system="Based on the user conversation above (EXCLUDING this note-taking instruction message as well as system prompt, claude.md entries, or any past session summaries), update the session notes file.",
                messages=api_messages,
                tools=extraction_tools,
                max_tokens=4096,
            )

        response_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                response_text += block.text
            elif isinstance(block, dict) and block.get("type") == "text":
                response_text += block.get("text", "")

        memory = SessionMemory.from_markdown(session_id, response_text)
        memory.session_id = session_id
        memory.created_at = datetime.datetime.now().isoformat()
        memory.updated_at = datetime.datetime.now().isoformat()

        if messages:
            last_msg = messages[-1]
            memory.last_summarized_message_id = last_msg.get("id", "") if isinstance(last_msg, dict) else getattr(last_msg, "id", "")

        return memory

    except Exception as e:
        print(e)
        return None


def _safe_read_file(path: str, limit: int = None) -> str:
    """Safely read a file for extraction agent."""
    try:
        from pathlib import Path
        cwd = Path.cwd()
        # Resolve path relative to cwd
        if not path.startswith("/"):
            file_path = (cwd / path).resolve()
        else:
            file_path = Path(path)

        text = file_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"[Error reading {path}: {e}]"


def _safe_edit_file(path: str, content: str) -> str:
    """Safely write a file for extraction agent."""
    try:
        from pathlib import Path
        cwd = Path.cwd()
        # Resolve path relative to cwd
        if not path.startswith("/"):
            file_path = (cwd / path).resolve()
        else:
            file_path = Path(path)

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"[Error writing {path}: {e}]"


def get_last_summarized_id(session_id: str) -> Optional[str]:
    """Get last summarized message ID from file."""
    memory = load_session_memory(session_id)
    return memory.last_summarized_message_id if memory else None


def set_last_summarized_id(session_id: str, message_id: str):
    """Update last summarized message ID in file."""
    memory = load_session_memory(session_id)
    if memory:
        memory.last_summarized_message_id = message_id
        memory.updated_at = datetime.datetime.now().isoformat()
        save_session_memory(memory)


def try_session_memory_compaction(
    messages: List[Union[dict, object]],
    session_id: str,
) -> Optional[List[Union[dict, object]]]:
    """
    Try to compact using session memory (Tier 2).

    Args:
        messages: All messages
        session_id: Session identifier for file lookup

    Returns:
        Compacted messages or None
    """
    memory = load_session_memory(session_id)
    if not memory:
        return None

    if is_session_memory_empty(session_id):
        return None

    last_summarized_id = memory.last_summarized_message_id

    if last_summarized_id:
        last_index = -1
        for i, msg in enumerate(messages):
            msg_id = msg.get("id", "") if isinstance(msg, dict) else getattr(msg, "id", "")
            if msg_id == last_summarized_id:
                last_index = i
                break
        if last_index == -1:
            last_index = len(messages) - 1
    else:
        last_index = len(messages) - 1

    start_index = calculate_messages_to_keep_index(messages, last_index)

    result = [msg for msg in messages[start_index:] if not _is_compact_boundary(msg)]

    summary_parts = []
    if memory.title:
        summary_parts.append(f"## Session: {memory.title}")
    if memory.current_state:
        summary_parts.append(f"## Current State\n{memory.current_state}")
    if memory.task_specification:
        summary_parts.append(f"## Task\n{memory.task_specification}")
    if memory.implementation_notes:
        summary_parts.append(f"## Implementation\n{memory.implementation_notes}")
    if memory.important_context:
        summary_parts.append(f"## Context\n{memory.important_context}")
    if memory.next_steps:
        summary_parts.append(f"## Next Steps\n{memory.next_steps}")

    summary_content = "\n\n".join(summary_parts) if summary_parts else "[Session Memory]"

    summary_msg = {
        "role": "user",
        "content": f"[Session Memory Summary]\n\n{summary_content}",
    }
    result.insert(0, summary_msg)

    return result
