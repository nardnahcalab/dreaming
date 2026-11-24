"""
Schema: normalized data structures shared by all components.

Every collector normalizes its tool's format into these structures,
so the episodic store and dreaming engine never need to know which
tool a session came from.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import hashlib
import json


@dataclass
class Message:
    """A single message normalized across tools."""
    role: str                # "user" | "assistant" | "system" | "tool"
    content: str             # plain text content
    timestamp: Optional[str] = None  # ISO8601 if available
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Session:
    """
    A normalized conversation session from any tool.

    This is the unit of episodic memory: raw, immutable, append-only.
    """
    id: str                  # tool-native session id
    tool: str                # "opencode" | "claude-code" | "codex" | "hermes"
    messages: List[Message] = field(default_factory=list)
    project: str = ""        # working directory / project name
    started: Optional[str] = None
    ended: Optional[str] = None
    title: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        """
        Stable hash of the session content.

        Used for append-only dedup: if we've already stored this exact
        session, we never store it again (and never modify it).
        """
        h = hashlib.sha256()
        h.update(self.tool.encode())
        h.update(self.id.encode())
        for m in self.messages:
            h.update(m.role.encode())
            h.update(m.content.encode())
        return h.hexdigest()[:24]

    def transcript_text(self, max_chars: int = 60000) -> str:
        """Render the session as readable text for the dreaming LLM."""
        parts = []
        for m in self.messages:
            if m.role not in ("user", "assistant"):
                continue
            # Trim very long tool outputs; they rarely carry durable memory
            content = m.content.strip()
            if len(content) > 3000:
                content = content[:3000] + " ...[truncated]"
            if content:
                parts.append(f"{m.role.upper()}: {content}")
        text = "\n\n".join(parts)
        return text[:max_chars]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["content_hash"] = self.content_hash
        return d


@dataclass
class MemoryEntry:
    """
    A single piece of consolidated (abstract) memory produced by dreaming.

    Unlike episodic sessions, these CAN be revised by later dreams --
    but each dream writes a NEW artifact, so every previous version
    remains on disk (Anthropic-style: input never modified).
    """
    id: str
    content: str
    category: str            # "fact" | "preference" | "project" | "lesson" | "task"
    importance: float = 0.5  # 0.0 - 1.0
    confidence: float = 0.5
    sources: List[str] = field(default_factory=list)  # session hashes
    created_dream: str = ""  # which dream produced this
    last_seen_dream: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryEntry":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class DreamArtifact:
    """
    The output of one dreaming pass.

    KEY DESIGN (from the learning guide):
      - Never overwrites: each dream creates dream-NNNN.json
      - Input (episodic) is never modified
      - `latest` pointer can be rolled back to any previous dream
    """
    id: str                  # "dream-0001"
    timestamp: str
    entries: List[MemoryEntry] = field(default_factory=list)
    sessions_processed: int = 0
    sessions_total: int = 0
    previous_dream: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "sessions_processed": self.sessions_processed,
            "sessions_total": self.sessions_total,
            "previous_dream": self.previous_dream,
            "stats": self.stats,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DreamArtifact":
        return cls(
            id=d["id"],
            timestamp=d.get("timestamp", ""),
            entries=[MemoryEntry.from_dict(e) for e in d.get("entries", [])],
            sessions_processed=d.get("sessions_processed", 0),
            sessions_total=d.get("sessions_total", 0),
            previous_dream=d.get("previous_dream", ""),
            stats=d.get("stats", {}),
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1)
