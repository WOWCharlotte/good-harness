import json
import uuid
import dataclasses
from datetime import datetime
from pathlib import Path

try:
    from compact.models import Message
except ImportError:
    Message = None


def _block_to_dict(block) -> dict:
    """Convert block (dict, dataclass, or object) to dict for JSON serialization."""
    if isinstance(block, dict):
        return block
    if dataclasses.is_dataclass(block):
        return dataclasses.asdict(block)
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return {"type": getattr(block, "type", "unknown"), "text": str(block)}


def _message_to_dict(message) -> dict:
    """Convert Message dataclass or dict to JSON-serializable dict."""
    if isinstance(message, dict):
        result = dict(message)
    elif dataclasses.is_dataclass(message):
        result = dataclasses.asdict(message)
    elif hasattr(message, "model_dump"):
        result = message.model_dump()
    else:
        result = {"role": getattr(message, "role", ""), "content": getattr(message, "content", "")}
    if "content" in result and isinstance(result["content"], list):
        result["content"] = [_block_to_dict(b) for b in result["content"]]
    return result


def _dict_to_message(data: dict):
    """Restore a dict to Message object."""
    if Message is None:
        return data
    known_fields = {"content", "role", "id", "timestamp"}
    extra = {k: v for k, v in data.items() if k not in known_fields}
    return Message(
        content=data.get("content", ""),
        role=data.get("role", ""),
        id=data.get("id", ""),
        timestamp=data.get("timestamp", ""),
        **extra,
    )


class SessionManager:
    def __init__(self, session_dir: Path, model_id: str = None, system_prompt: str = None):
        self.session_dir = session_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.current_session_id = None
        self.session_file = None
        self.model_id = model_id
        self.system_prompt = system_prompt

    def _session_file(self, session_id: str) -> Path:
        return self.session_dir / f"{session_id}.jsonl"

    def _metadata_file(self) -> Path:
        return self.session_dir / "sessions.json"

    def _load_metadata(self) -> dict:
        """Load metadata from first line of session file."""
        if not self.session_file or not self.session_file.exists():
            return None
        try:
            first_line = self.session_file.read_text(encoding="utf-8").splitlines()[0]
            meta = json.loads(first_line)
            if meta.get("type") == "metadata":
                return meta
        except (IndexError, json.JSONDecodeError, OSError):
            pass
        return None

    def create_session(self) -> str:
        """Create a new session with metadata header."""
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.current_session_id = session_id
        self.session_file = self._session_file(session_id)

        meta = {
            "type": "metadata",
            "key": session_id,
            "create_at": datetime.now().isoformat(),
            "update_at": datetime.now().isoformat(),
            "model": self.model_id or "",
            "last_consolidated": 0,
        }
        self.session_file.write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")
        self._save_metadata()
        return session_id

    def load_session(self, session_id: str = None) -> tuple[str, list]:
        """Load a session by ID, or return current/empty session. Loads from last_consolidated."""
        if session_id is None:
            sessions = self.list_sessions()
            if sessions:
                session_id = sessions[0]["id"]
            else:
                return self.create_session(), []

        session_file = self._session_file(session_id)
        if session_file.exists():
            try:
                lines = [l for l in session_file.read_text(encoding="utf-8").splitlines() if l.strip()]
                if not lines:
                    return session_id, []

                # Parse metadata from first line
                meta = json.loads(lines[0])
                if meta.get("type") != "metadata":
                    # No valid metadata, load all
                    start_idx = 1
                else:
                    start_idx = max(1, meta.get("last_consolidated", 1))

                messages = []
                for i in range(start_idx, len(lines)):
                    messages.append(_dict_to_message(json.loads(lines[i])))

                self.current_session_id = session_id
                self.session_file = session_file
                return session_id, messages
            except (json.JSONDecodeError, OSError):
                pass
        return self.create_session(), []

    def save_session(self, messages: list):
        """Save all messages in one write. Metadata written once on create."""
        if not self.current_session_id or not self.session_file:
            return

        meta = self._load_metadata()
        if not meta:
            # First time saving - create metadata
            meta = {
                "type": "metadata",
                "key": self.current_session_id,
                "create_at": datetime.now().isoformat(),
                "model": self.model_id or "",
                "last_consolidated": 0,
            }

        meta["update_at"] = datetime.now().isoformat()
        meta["last_consolidated"] = 0  # Compression not yet implemented

        lines = [json.dumps(meta, ensure_ascii=False)]
        lines.extend(json.dumps(_message_to_dict(m), ensure_ascii=False) for m in messages)
        self.session_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._save_metadata()

    def append_message(self, message: dict):
        """Append a single message to the session file (after metadata)."""
        if not self.current_session_id or not self.session_file:
            return
        with open(self.session_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(_message_to_dict(message), ensure_ascii=False) + "\n")

        # Update last_consolidated
        meta = self._load_metadata()
        if meta:
            meta["update_at"] = datetime.now().isoformat()
            meta["last_consolidated"] = 0  # Compression not implemented
            # Rewrite entire file to update consolidated index
            lines = [json.dumps(meta, ensure_ascii=False)]
            content = self.session_file.read_text(encoding="utf-8").splitlines()
            if len(content) > 1:
                lines.extend(content[1:])
            self.session_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def list_sessions(self) -> list:
        """List all sessions, newest first."""
        sessions = []
        for sf in self.session_dir.glob("*.jsonl"):
            if sf.name == "sessions.json":
                continue
            try:
                meta = json.loads(sf.read_text(encoding="utf-8").splitlines()[0])
                if meta.get("type") == "metadata":
                    sessions.append({
                        "id": meta.get("key", sf.stem),
                        "create_at": meta.get("create_at", ""),
                        "updated_at": meta.get("update_at", ""),
                    })
            except Exception:
                pass
        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)

    def delete_session(self, session_id: str):
        """Delete a session."""
        sf = self._session_file(session_id)
        if sf.exists():
            sf.unlink()
        self._save_metadata()

    def _save_metadata(self):
        """Save sessions index."""
        sessions = [{
            "id": s.get("id", ""),
            "create_at": s.get("create_at", ""),
            "updated_at": s.get("updated_at", ""),
            "system_prompt": self.system_prompt[:1000] if self.system_prompt else "",
        } for s in self.list_sessions()]
        self._metadata_file().write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")
