import argparse
import asyncio
import dataclasses
import os
import shutil
import subprocess
from pathlib import Path
from typing import Union

from anthropic import Anthropic
from dotenv import load_dotenv
from prompt import (
    SOUL_TEMPLATE,
    USER_TEMPLATE,
    BOOTSTRAP_TEMPLATE,
    MEMORY_INDEX_TEMPLATE,
    build_runtime_system_prompt
)
from session import SessionManager

# Compression pipeline imports
from compact.microcompact import microcompact_messages
from compact.token_estimation import rough_token_count_estimation_for_messages
from compact.compaction import compact_conversation
from compact.session_memory import (
    try_session_memory_compaction,
    should_trigger_extraction,
    extract_session_memory,
    load_session_memory,
    save_session_memory,
    is_session_memory_empty,
    EXTRACTION_INIT_THRESHOLD,
)
from compact.models import Message,ToolResultBlock

if os.path.exists(".env"):
    load_dotenv(".env", override=True)
else:
    load_dotenv(override=True)

WORKDIR = Path(os.getcwd()+"/.harness")


def _content_to_dict(content) -> list | str:
    """Convert Anthropic content blocks to JSON-serializable format."""
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


def _response_to_message(role: str, content) -> Message:
    """Convert API response content blocks to Message dataclass."""
    blocks = _content_to_dict(content)
    return Message(role=role, content=blocks)


# Default values from environment
DEFAULT_API_KEY = os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
DEFAULT_BASE_URL = os.getenv("ANTHROPIC_BASE_URL") or os.getenv("OPENAI_BASE_URL") or None
DEFAULT_MODEL = os.environ.get("MODEL_ID", "claude-sonnet-4-20250514")


def parse_args():
    parser = argparse.ArgumentParser(description="Agent runtime with Claude API")
    parser.add_argument("--api-key", type=str, default=DEFAULT_API_KEY,
                        help="API key for the provider")
    parser.add_argument("--base-url", type=str, default=DEFAULT_BASE_URL,
                        help="Base URL for API (optional)")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL,
                        help="Model ID to use")
    parser.add_argument("--agent-type",type=str, choices=["agent", "ai-coder"],default="agent",
                        help="Agent type for system prompt (default: 'agent')")
    return parser.parse_args()


args = parse_args()
MODEL = args.model
AGENT_TYPE = args.agent_type

if args.base_url:
    client = Anthropic(base_url=args.base_url, api_key=args.api_key or None)
else:
    client = Anthropic(api_key=args.api_key or None)



def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: int = None) -> str:
    try:
        text = safe_path(path).read_text()
        lines = text.splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


# -- The dispatch map: {tool_name: handler} --
TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}

TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
]

SYSTEM = None  # Will be set in main()

MAX_OUTPUT_LEN = 4000  # 截断阈值
TRUNCATED_SUFFIX = "\n... (output truncated)"
MAX_ERRORS = 3

# Compaction configuration
COMPACTION_THRESHOLD = 150000  # Tokens
MICROCOMPACT_GAP_MINUTES = 60


class CompactionState:
    """Track compaction state across sessions."""
    def __init__(self):
        self.last_compact_time = 0
        self.compact_count = 0
        self.last_assistant_timestamp = 0


_compaction_state = CompactionState()


def _safe_call(handler, **kwargs):
    """安全调用工具，捕获异常并返回错误信息。"""
    try:
        return handler(**kwargs)
    except Exception as e:
        return f"[Error: {type(e).__name__}] {e}"


def _truncate_output(output: str, limit: int = MAX_OUTPUT_LEN) -> str:
    """截断输出，保留开头和结尾的关键信息。"""
    if len(output) <= limit:
        return output
    head = limit // 2
    tail = limit - head
    return output[:head] + TRUNCATED_SUFFIX + output[-tail:]


def agent_loop(messages: list, session_id: str):
    while True:
        # BEFORE API CALL: Apply microcompact (Tier 1)
        messages = microcompact_messages(messages, query_source="repl_main_thread")

        token_count = rough_token_count_estimation_for_messages(messages)

        # Check if higher-tier compaction is needed
        if token_count >= COMPACTION_THRESHOLD:
            print(f"[Compaction: ~{token_count} tokens, threshold {COMPACTION_THRESHOLD}]")
            # Try session memory first (Tier 2, zero cost)
            compacted = try_session_memory_compaction(messages, session_id)
            if compacted is None:
                # Full compaction via LLM (Tier 3)
                compacted = asyncio.run(compact_conversation(messages, client,MODEL))
            if compacted:
                messages = compacted
                _compaction_state.compact_count += 1
                print(f"[Compaction complete: {len(messages)} messages]")

        api_messages = [_message_to_api_format(m) for m in messages]
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=api_messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append(_response_to_message("assistant", response.content))
        if response.stop_reason != "tool_use":
            return messages
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)
                output = _safe_call(handler, **block.input) if handler else f"Unknown tool: {block.name}"
                output = _truncate_output(output)
                print(f"> {block.name}:")
                print(output[:200])
                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=output))
        messages.append(Message(role="user", content=tool_results))


if __name__ == "__main__":
    # Create .harness directory structure
    harness_dir = WORKDIR
    dirs_to_create = [
        harness_dir / "workspace",
        harness_dir / "skills",
        harness_dir / "data" / "memory",
        harness_dir / "data" / "session",
    ]
    for d in dirs_to_create:
        d.mkdir(parents=True, exist_ok=True)

    # Create default files if they don't exist
    default_files = {
        harness_dir / "workspace" / "SOUL.md": f"{SOUL_TEMPLATE}",
        harness_dir / "workspace" / "USER.md": f"{USER_TEMPLATE}",
        harness_dir / "workspace" / "BOOTSTRAP.md": f"{BOOTSTRAP_TEMPLATE}",
        harness_dir / "workspace" / "HEARTBEAT.md": "## HEARTBEAT.md - Agent Status\n\n",
        harness_dir / "workspace" / "AGENTS.md": "## AGENTS.md - Agents \n\n",
        harness_dir / "workspace" / "CLAUDE.md": "## CLAUDE.md - CLAUDE Agent \n\n",
        harness_dir / "workspace" / "MEMORY.md": f"{MEMORY_INDEX_TEMPLATE}",
    }
    for fp, default_content in default_files.items():
        if not fp.exists():
            fp.write_text(default_content)

    # Copy template skills to .harness/skills if not already present
    template_skills = Path.cwd() / "template"
    skills_dest = harness_dir / "skills"
    if template_skills.exists():
        for item in template_skills.iterdir():
            dest = skills_dest / item.name
            if not dest.exists():
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

    # Build system prompt before session manager
    SYSTEM = build_runtime_system_prompt(agent_type=AGENT_TYPE, cwd=WORKDIR)
    # Initialize session manager
    session_manager = SessionManager(harness_dir / "data" / "session", model_id=MODEL, system_prompt=SYSTEM)
    session_id, history = session_manager.load_session()
    print(f"[Session: {session_id}]")
    print(f"[history: {len(history)}]")

    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break

        cmd = query.strip().lower()

        # Session management commands
        if cmd in ("q", "exit", ""):
            session_manager.save_session(history)
            break
        if cmd == "new":
            session_manager.save_session(history)
            session_id, history = session_manager.create_session(), []
            print(f"[New session: {session_id}]")
            continue
        if cmd == "sessions":
            sessions = session_manager.list_sessions()
            print(f"[Sessions ({len(sessions)}):]")
            for s in sessions[:10]:
                print(f"  {s['id']} - {s['updated_at']} - system_prompt: {s.get('system_prompt', 'N/A')[:50]}...")
            continue
        if cmd.startswith("load "):
            target_id = cmd[5:].strip()
            session_manager.save_session(history)
            session_id, history = session_manager.load_session(target_id)
            print(f"[Loaded: {session_id}]")
            continue
        if cmd.startswith("delete "):
            target_id = cmd[7:].strip()
            session_manager.delete_session(target_id)
            print(f"[Deleted: {target_id}]")
            continue
        if cmd == "compact":
            # Manual compaction trigger
            compacted = asyncio.run(compact_conversation(history,client,MODEL))
            if compacted:
                print(f"[Manual compact: {len(history)} messages]")
                history = compacted
                session_manager.save_session(history)
            continue
        if cmd == "status":
            # Show compression status
            token_count = rough_token_count_estimation_for_messages(history)
            print(f"[Messages: {len(history)}]")
            print(f"[Tokens: ~{token_count}]")
            print(f"[Compactions: {_compaction_state.compact_count}]")
            continue
        if cmd == "memory":
            # Show session memory
            memory = load_session_memory(session_id)
            if memory:
                print(f"[Session Memory: {memory.title}]")
                print(memory.to_markdown())
            else:
                print("[No session memory found]")
            continue
        if cmd == "extract":
            # Manual trigger extraction
            memory = asyncio.run(extract_session_memory(history, session_id, client, MODEL))
            if memory:
                save_session_memory(memory)
                print(f"[Extracted: {memory.title}]")
            else:
                print("[Extraction failed]")
            continue

        history.append(Message(role="user", content=query))
        history = agent_loop(history, session_id)
        session_manager.save_session(history)

        # Session memory extraction (between turns)
        token_count = rough_token_count_estimation_for_messages(history)
        is_initial = is_session_memory_empty(session_id)
        if should_trigger_extraction(history, session_id, is_initial=is_initial):
            print(f"[Extracting session memory: ~{token_count} tokens]")
            memory = asyncio.run(extract_session_memory(history, session_id, client, MODEL))
            if memory:
                save_session_memory(memory)
                print(f"[Session memory saved: {memory.title}]")

        last = history[-1]
        if isinstance(last, Message):
            content = last.content
        else:
            content = last.get("content") if isinstance(last, dict) else ""

        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    print(block.get("text", ""))
                elif hasattr(block, "text") and block.type == "text":
                    print(block.text)
        print()