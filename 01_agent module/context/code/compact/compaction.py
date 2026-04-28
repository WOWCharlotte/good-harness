# 01_agent module/context/code/compact/compaction.py
"""Tier 3: Full Conversation Compaction.

LLM API-based conversation summarization.
Most powerful compression but requires API call.
"""

import dataclasses
from pathlib import Path
from typing import List, Union, Optional
from .prompt import get_compact_prompt, get_compact_summary_message
from .token_estimation import rough_token_count_estimation_for_messages, rough_token_count_estimation
from .models import Message

POST_COMPACT_MAX_FILES = 5
POST_COMPACT_TOKEN_BUDGET = 50000

# File state cache for tracking read files
_file_state_cache: dict[str, dict] = {}


def track_file_read(file_path: str, content: str = "", checksum: str = ""):
    """Track a file that has been read, for cache invalidation after compaction."""
    _file_state_cache[file_path] = {
        "content": content,
        "checksum": checksum,
        "read_count": _file_state_cache.get(file_path, {}).get("read_count", 0) + 1,
    }


def clear_file_state_cache():
    """Clear the file state cache after compaction."""
    global _file_state_cache
    _file_state_cache = {}


def get_tracked_files() -> dict[str, dict]:
    """Get the current file state cache for building attachments."""
    return dict(_file_state_cache)



def _get_recent_files_from_messages(
    messages: list,
    max_files: int = POST_COMPACT_MAX_FILES,
    max_tokens: int = POST_COMPACT_TOKEN_BUDGET
) -> list[dict]:
    """
    Extract recently read/modified files from messages.

    Args:
        messages: Messages to extract from
        max_files: Maximum number of files to return
        max_tokens: Maximum total token budget

    Returns:
        List of file info dicts with path, content, and token_count
    """
    file_reads = []
    seen_paths = set()

    for msg in reversed(messages):
        content = msg.get("content", []) if isinstance(msg, dict) else getattr(msg, "content", [])
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue

            tool_use_id = block.get("tool_use_id", "")
            if not tool_use_id:
                continue

            result_content = block.get("content", "")
            if isinstance(result_content, list):
                text_parts = []
                for part in result_content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                result_text = "\n".join(text_parts)
            else:
                result_text = str(result_content)

            result_tokens = rough_token_count_estimation(result_text)

            for prev_block in reversed(content[:content.index(block)]):
                if not isinstance(prev_block, dict):
                    continue
                if prev_block.get("type") != "tool_use":
                    continue
                if prev_block.get("id", "") != tool_use_id:
                    continue

                name = prev_block.get("name", "")
                if name == "read_file":
                    inp = prev_block.get("input", {})
                    path = inp.get("path", "")
                    if path and path not in seen_paths:
                        seen_paths.add(path)
                        file_reads.append({
                            "path": path,
                            "content": result_text,
                            "tool": "read_file",
                            "token_count": result_tokens,
                        })
                break

    file_reads.reverse()

    selected = []
    total_tokens = 0
    for f in file_reads:
        if len(selected) >= max_files:
            break
        if total_tokens + f["token_count"] > max_tokens:
            continue
        selected.append(f)
        total_tokens += f["token_count"]

    return selected


def _build_post_compact_attachment(
    messages: list,
    cwd: str | Path = None
) -> str:
    """
    Build post-compaction attachment content.

    Includes:
    - Recent files (up to 5 or 50k tokens)
    - Plan files
    - Skill content
    - MCP tool descriptions
    - Agent list

    Args:
        messages: Original messages before compaction
        cwd: Current working directory

    Returns:
        Formatted attachment string
    """
    if cwd is None:
        cwd = Path.cwd() / ".harness"
    else:
        cwd = Path(cwd)

    parts = []

    recent_files = _get_recent_files_from_messages(messages)
    if recent_files:
        parts.append("# Recent Files")
        for f in recent_files:
            path = f["path"]
            content = f["content"]
            parts.append(f"## {path}")
            parts.append(f"```\n{content[:2000]}{'...' if len(content) > 2000 else ''}\n```")
        parts.append("")

    plan_files = ["PLAN.md", "plan.md"]
    for pf in plan_files:
        pf_path = cwd / pf
        if pf_path.exists():
            try:
                content = pf_path.read_text(encoding="utf-8")
                parts.append(f"# Plan File ({pf})")
                parts.append(content[:3000])
                parts.append("")
                break
            except Exception:
                pass

    skills_dir = cwd / "skills"
    if skills_dir.exists():
        skill_files = list(skills_dir.rglob("SKILL.md"))[:5]
        if skill_files:
            parts.append("# Skills")
            for sf in skill_files:
                try:
                    content = sf.read_text(encoding="utf-8")
                    name = sf.stem
                    parts.append(f"## {name}")
                    parts.append(content[:1500])
                    parts.append("")
                except Exception:
                    pass


    agent_files = ["workspace/AGENTS.md", "workspace/CLAUDE.md"]
    for af in agent_files:
        af_path = cwd / af
        if af_path.exists():
            try:
                content = af_path.read_text(encoding="utf-8")
                parts.append(f"# Agent Config ({af})")
                parts.append(content[:2000])
                parts.append("")
                break
            except Exception:
                pass

    return "\n".join(parts) if parts else ""

COMPACT_SYSTEM_PROMPT = """Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly.

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages: 
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response. 

There may be additional summarization instructions provided in the included context. If so, remember to follow these instructions when creating the above summary. Examples of instructions include:
<example>
## Compact Instructions
When summarizing the conversation focus on typescript code changes and also remember the mistakes you made and how you fixed them.
</example>

<example>
# Summary instructions
When you are using compact - please focus on test output and code changes. Include file reads verbatim.
</example>
"""


def _content_to_dict(content) -> list | str:
    """Convert content blocks to JSON-serializable format."""
    if isinstance(content, list):
        result = []
        for block in content:
            if isinstance(block, dict):
                result.append(block)
            elif dataclasses.is_dataclass(block):
                result.append(dataclasses.asdict(block))
            elif hasattr(block, "model_dump"):
                result.append(block.model_dump())
            else:
                result.append(str(block))
        return result
    return content


def _message_to_api_format(msg: Union[dict, Message]) -> dict:
    """Convert Message or dict to API format {role, content}."""
    if isinstance(msg, Message):
        return {"role": msg.role, "content": _content_to_dict(msg.content)}
    return {"role": msg.get("role", ""), "content": msg.get("content", "")}


def _is_user_message(msg: Union[dict, Message]) -> bool:
    """Check if a message is a user message (role=user, not tool result)."""
    if isinstance(msg, dict):
        role = msg.get("role", "")
        content = msg.get("content", [])
    else:
        role = getattr(msg, "role", "")
        content = getattr(msg, "content", [])

    if role != "user":
        return False

    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return False
    return True


def _get_recent_user_messages(messages: list, keep_count: int = 5) -> list:
    """
    Get the most recent N user messages with their associated tool exchanges.

    Args:
        messages: Messages to extract from
        keep_count: Number of recent user messages to preserve

    Returns:
        List of recent user messages with surrounding context
    """
    user_msg_indices = []
    for i, msg in enumerate(messages):
        if _is_user_message(msg):
            user_msg_indices.append(i)

    if not user_msg_indices:
        return []

    recent_indices = user_msg_indices[-keep_count:]
    start_idx = recent_indices[0]

    return messages[start_idx:]


def _normalize_content(content) -> list:
    """Convert content blocks to dict format for safe processing."""
    if isinstance(content, list):
        return [
            block.model_dump() if hasattr(block, "model_dump") else block
            for block in content
        ]
    return content


def strip_images_from_messages(messages: List[Union[dict, object]]) -> List[Union[dict, object]]:
    """
    Strip image blocks before compaction.

    Images are not needed for summarization and can cause
    the API call to hit prompt-too-long limit.

    Args:
        messages: Input messages

    Returns:
        Messages with images replaced by [image] markers
    """
    result = []
    for msg in messages:
        content = msg.get("content", []) if isinstance(msg, dict) else getattr(msg, "content", [])
        if not isinstance(content, list):
            result.append(msg)
            continue

        has_media = False
        new_content = []
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            block_type = block.get("type", "")
            if block_type in ("image", "document"):
                has_media = True
                new_content.append({"type": "text", "text": f"[{block_type}]"})
            else:
                new_content.append(block)

        if not has_media:
            result.append(msg)
        else:
            if isinstance(msg, dict):
                result.append({**msg, "content": new_content})
            else:
                msg.content = new_content
                result.append(msg)

    return result


def create_boundary_message(
    _boundary_type: str = "auto",
    pre_token_count: int = 0
) -> Message:
    """Create compact boundary marker as Message object."""
    return Message(
        role="user",
        content=f"[Compacted ~{pre_token_count} tokens]",
    )


async def compact_conversation(
    messages: List[Union[dict, object]],
    llm_client=None,
    model_id: str = "minimax-m2.7",
    custom_instructions: str = None,
    cwd: str | Path = None
) -> List[Message]:
    """
    Full conversation compaction.

    1. Strip images (avoid compression request itself exceeding limit)
    2. Generate summary via model (user intent, key concepts, files/code,
       errors/fixes, problem solving, user messages, pending tasks,
       current work, optional next step)
    3. Clear file state cache
    4. Create compact boundary marker
    5. Generate summary message
    6. Create post-compact attachment (recent files, plan, skills, MCP, agents)
    7. Re-inject attachment

    Args:
        messages: Messages to compact
        llm_client: Optional LLM client (uses mock if None)
        custom_instructions: Optional user instructions
        cwd: Current working directory for attachment building

    Returns:
        Compacted messages: [boundary, summary, attachment]
    """
    if not messages:
        return messages

    pre_token_count = rough_token_count_estimation_for_messages(messages)

    # Normalize: convert any content blocks to dict format before processing
    messages = [
        {**msg, "content": _normalize_content(msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", []))}
        if isinstance(msg, dict) else msg
        for msg in messages
    ]

    # Step 1: Strip images
    stripped = strip_images_from_messages(messages)

    # Step 2: Generate summary from all messages
    if llm_client is None:
        summary = _generate_mock_summary(stripped) if stripped else "No context to summarize."
    else:
        summary_messages = [_message_to_api_format(m) for m in stripped]
        if not summary_messages:
            summary = "No context to summarize."
        else:
            prompt = get_compact_prompt(custom_instructions)
            response = llm_client.messages.create(
                model=model_id,
                system=COMPACT_SYSTEM_PROMPT,
                messages=[
                    *summary_messages,
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4096,
            )
            summary = ""
            for block in response.content:
                if hasattr(block, "text"):
                    summary += block.text
                elif isinstance(block, dict) and block.get("type") == "text":
                    summary += block.get("text", "")
            summary = summary.strip()

    # TODO:Step 3: Clear file state cache
    clear_file_state_cache()

    # Step 4: Create boundary marker
    boundary = create_boundary_message("auto", pre_token_count)

    # Step 5: Generate summary message
    summary_msg = Message(
        role="user",
        content=get_compact_summary_message(summary),
    )

    # Step 6: Build post-compact attachment
    attachment_content = _build_post_compact_attachment(messages, cwd)
    attachment_msg = Message(
        role="user",
        content=f"[Post-Compact Attachment]\n\n{attachment_content}",
    ) if attachment_content else None

    # Step 7: Return compacted messages
    # Structure: [boundary, summary, attachment?, recent_user_messages...]
    recent = _get_recent_user_messages(messages, keep_count=5)
    result = [boundary, summary_msg]
    if attachment_msg:
        result.append(attachment_msg)
    result.extend(recent)
    return result


def _generate_mock_summary(messages: List[Union[dict, object]]) -> str:
    """Generate a mock summary for testing."""
    msg_count = len(messages)
    token_count = rough_token_count_estimation_for_messages(messages)

    return f"""[Summary of {msg_count} messages (~{token_count} tokens)]

## Topic
Technical discussion involving code implementation and context compression.

## Key Decisions
- Discussed implementation approach for compression pipeline
- Four-tier compression architecture selected

## Technical Details
- Tier 1: Microcompact (pre-request, zero cost)
- Tier 2: Session Memory (structured facts, zero cost)
- Tier 3: Full Compaction (LLM summarization)
- Tier 4: API Context Management

## Next Steps
- Complete compression pipeline implementation
- Integrate with runtime
- Test and validate

This summary preserves critical context while reducing token count."""


def should_trigger_full_compaction(
    messages: List[Union[dict, object]],
    threshold: int = 150000
) -> bool:
    """
    Check if full compaction should be triggered.

    Args:
        messages: Messages to check
        threshold: Token threshold

    Returns:
        True if compaction should be triggered
    """
    token_count = rough_token_count_estimation_for_messages(messages)
    return token_count >= threshold