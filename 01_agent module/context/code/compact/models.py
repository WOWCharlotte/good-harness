# 01_agent module/context/code/compact/models.py
from dataclasses import dataclass, field
from typing import Union, Optional, List
import datetime
import uuid



# Tool name constants
FILE_READ_TOOL_NAME = "read_file"
FILE_EDIT_TOOL_NAME = "edit_file"
FILE_WRITE_TOOL_NAME = "write_file"
GREP_TOOL_NAME = "grep"
GLOB_TOOL_NAME = "glob"
WEB_SEARCH_TOOL_NAME = "WebSearch"
WEB_FETCH_TOOL_NAME = "WebFetch"

SHELL_TOOL_NAMES = {"bash"}

@dataclass
class ToolResultBlock:
    type: str = "tool_result"
    tool_use_id: str = ""
    content: Union[str, list] = ""


@dataclass
class Message:
    """
    核心消息数据类。

    用于表示 Agent 与用户、工具之间的交互消息。
    content 字段可以是字符串（简单消息）或列表（复杂消息，包含工具调用等）。
    """
    content: Union[str, list]  # 消息内容
    role: str = ""  # 角色："user", "assistant", "system", "tool"
    id: str = ""  # 消息的唯一标识符
    timestamp: str = ""  # ISO 格式时间戳

    def __post_init__(self):
        # 如果没有提供 id，自动生成一个 UUID
        if not self.id:
            self.id = str(uuid.uuid4())
        # 如果没有提供时间戳，自动使用当前时间
        if not self.timestamp:
            self.timestamp = datetime.datetime.now().isoformat()

    def is_assistant(self) -> bool:
        """判断是否是对话助手的消息"""
        return self.role == "assistant"

    def is_user(self) -> bool:
        """判断是否是人类用户的消息"""
        return self.role == "user"


@dataclass
class CompactMetadata:
    boundary_type: str = ""  # "auto" or "manual"
    pre_compact_token_count: int = 0
    last_message_uuid: str = ""


@dataclass
class CompactionResult:
    messages: list
    boundary_marker: Optional[Message] = None
    summary: str = ""
    tokens_saved: int = 0


@dataclass
class SessionMemory:
    """Structured session memory for Tier 2 compaction."""
    session_id: str
    title: str = ""
    current_state: str = ""
    task_specification: str = ""
    implementation_notes: str = ""
    important_context: str = ""
    next_steps: str = ""
    last_summarized_message_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def to_markdown(self) -> str:
        """Serialize to markdown format for file storage."""
        sections = [
            f"# Session Memory: {self.session_id}",
            f"## Title\n{self.title or 'N/A'}",
            f"## Current State\n{self.current_state or 'N/A'}",
            f"## Task Specification\n{self.task_specification or 'N/A'}",
            f"## Implementation Notes\n{self.implementation_notes or 'N/A'}",
            f"## Important Context\n{self.important_context or 'N/A'}",
            f"## Next Steps\n{self.next_steps or 'N/A'}",
            f"---\nlast_summarized_message_id: {self.last_summarized_message_id or 'None'}",
            f"created_at: {self.created_at}",
            f"updated_at: {self.updated_at}",
        ]
        return "\n\n".join(sections)

    @classmethod
    def from_markdown(cls, session_id: str, content: str) -> "SessionMemory":
        """Parse from markdown format."""
        sm = cls(session_id=session_id)
        current_section = None
        section_content = []
        in_metadata = False

        section_mapping = {
            "Title": "title",
            "Current State": "current_state",
            "Task Specification": "task_specification",
            "Implementation Notes": "implementation_notes",
            "Important Context": "important_context",
            "Next Steps": "next_steps",
        }

        for line in content.splitlines():
            stripped = line.strip()
            # Skip empty lines
            if not stripped:
                continue
            # Track metadata section start
            if stripped == "---":
                in_metadata = True
                continue
            # Section headers
            if stripped.startswith("## "):
                if current_section and section_content:
                    setattr(sm, current_section, "\n".join(section_content).strip())
                header = stripped[3:].strip()
                current_section = section_mapping.get(header)
                section_content = []
                continue
            # In metadata section, only parse last_summarized_message_id
            # created_at and updated_at are set programmatically
            if in_metadata:
                if ":" in line:
                    key, _, value = line.partition(":")
                    key = key.strip()
                    value = value.strip()
                    if key == "last_summarized_message_id":
                        sm.last_summarized_message_id = value if value and value != "None" else None
                continue
            # Section content
            if current_section is not None:
                section_content.append(line)

        if current_section and section_content:
            setattr(sm, current_section, "\n".join(section_content).strip())

        return sm


# Helper functions
def is_content_array(content: Union[str, list]) -> bool:
    return isinstance(content, list)


def get_text_from_message(message: Union[dict, Message]) -> str:
    """Extract text from message content."""
    if isinstance(message, dict):
        content = message.get("content", "")
    else:
        content = message.content if hasattr(message, "content") else ""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
            if hasattr(block, "type") and block.type == "text":
                return block.text
    return ""


def find_last_assistant_message(messages: List[Union[dict, Message]]) -> Optional[Union[dict, Message]]:
    """Find the last assistant message in the list."""
    for msg in reversed(messages):
        if isinstance(msg, dict):
            if msg.get("role") == "assistant":
                return msg
        elif hasattr(msg, "role"):
            if msg.role == "assistant":
                return msg
    return None