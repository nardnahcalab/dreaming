"""
Core data structures for AI memory consolidation (Dreaming).

This module defines the fundamental types for representing:
- Session transcripts (episodic memory)
- Consolidated memories (abstract memory)
- Memory stores for different architectures
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum
from datetime import datetime
import json


class MemoryType(Enum):
    """Types of memory in the system"""
    EPISODIC = "episodic"       # Raw session transcripts
    ABSTRACT = "abstract"       # LLM's compressed summary
    FACTUAL = "factual"         # Specific facts (names, dates, preferences)
    PROCEDURAL = "procedural"   # How to do things (skills, preferences)
    PREFERENCES = "preferences" # User preferences and constraints


class SessionState(Enum):
    """State of a session"""
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


@dataclass
class Message:
    """Single message in a conversation"""
    role: str  # "user", "assistant", "system"
    content: str
    timestamp: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    """
    Represents a single conversation session with an AI agent.
    This is the raw data that dreaming processes.
    """
    id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    messages: List[Message] = field(default_factory=list)
    state: SessionState = SessionState.ACTIVE
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str, **kwargs) -> Message:
        """Add a message to the session"""
        msg = Message(
            role=role,
            content=content,
            timestamp=datetime.now(),
            metadata=kwargs
        )
        self.messages.append(msg)
        return msg

    def get_conversation_text(self) -> str:
        """Get the full conversation as text"""
        parts = []
        for msg in self.messages:
            parts.append(f"{msg.role}: {msg.content}")
        return "\n".join(parts)

    @property
    def duration(self) -> Optional[float]:
        """Get session duration in seconds"""
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time).total_seconds()
        return None

    @property
    def num_messages(self) -> int:
        """Get number of messages"""
        return len(self.messages)


@dataclass
class MemoryEntry:
    """
    Represents a single piece of consolidated memory.
    This is what dreaming produces from session transcripts.
    """
    id: str
    content: str
    memory_type: MemoryType
    importance: float  # 0.0 to 1.0
    confidence: float  # 0.0 to 1.0
    created_at: datetime
    last_accessed: datetime
    source_session_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def update_access(self):
        """Update last accessed time"""
        self.last_accessed = datetime.now()

    def decay(self, decay_rate: float = 0.01):
        """
        Apply importance decay over time.
        Memories that aren't accessed become less important.
        """
        days_since_access = (datetime.now() - self.last_accessed).days
        self.importance *= (1 - decay_rate) ** days_since_access


@dataclass
class MemoryStore:
    """
    Container for all memories in a system.
    Different architectures handle this differently.
    """
    id: str
    created_at: datetime
    updated_at: datetime
    entries: List[MemoryEntry] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_entry(self, entry: MemoryEntry):
        """Add a memory entry"""
        self.entries.append(entry)
        self.updated_at = datetime.now()

    def remove_entry(self, entry_id: str) -> bool:
        """Remove a memory entry"""
        for i, entry in enumerate(self.entries):
            if entry.id == entry_id:
                del self.entries[i]
                self.updated_at = datetime.now()
                return True
        return False

    def get_entry(self, entry_id: str) -> Optional[MemoryEntry]:
        """Get a memory entry by ID"""
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None

    @property
    def size(self) -> int:
        """Get number of memory entries"""
        return len(self.entries)

    @property
    def total_importance(self) -> float:
        """Get total importance of all memories"""
        return sum(entry.importance for entry in self.entries)

    def get_important_memories(self, threshold: float = 0.5) -> List[MemoryEntry]:
        """Get memories above importance threshold"""
        return [e for e in self.entries if e.importance >= threshold]

    def apply_decay(self, decay_rate: float = 0.01):
        """Apply importance decay to all memories"""
        for entry in self.entries:
            entry.decay(decay_rate)

    def prune_low_importance(self, threshold: float = 0.1) -> List[MemoryEntry]:
        """
        Remove memories below importance threshold.
        This is the "planned forgetting" mentioned in research.
        """
        removed = []
        keep = []
        for entry in self.entries:
            if entry.importance < threshold:
                removed.append(entry)
            else:
                keep.append(entry)
        self.entries = keep
        self.updated_at = datetime.now()
        return removed


@dataclass
class ConsolidationResult:
    """
    Result of a memory consolidation (dreaming) process.
    Used for evaluation and rollback.
    """
    id: str
    timestamp: datetime
    input_store_id: str
    output_store_id: str
    num_sessions_processed: int
    num_entries_added: int
    num_entries_updated: int
    num_entries_removed: int
    duration_seconds: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def net_change(self) -> int:
        """Net change in memory entries"""
        return self.num_entries_added - self.num_entries_removed


def create_session_from_transcript(
    session_id: str,
    transcript: List[Tuple[str, str]],  # [(role, content), ...]
    start_time: Optional[datetime] = None
) -> Session:
    """
    Helper to create a Session from a simple transcript.

    Args:
        session_id: Unique identifier
        transcript: List of (role, content) tuples
        start_time: When the session started

    Returns:
        Session object
    """
    session = Session(
        id=session_id,
        start_time=start_time or datetime.now()
    )

    for role, content in transcript:
        session.add_message(role, content)

    return session


def create_memory_entry(
    entry_id: str,
    content: str,
    memory_type: MemoryType,
    importance: float = 0.5,
    confidence: float = 0.5,
    source_session_ids: Optional[List[str]] = None
) -> MemoryEntry:
    """
    Helper to create a MemoryEntry.

    Args:
        entry_id: Unique identifier
        content: The memory content
        memory_type: Type of memory
        importance: How important (0.0 to 1.0)
        confidence: How confident (0.0 to 1.0)
        source_session_ids: Sessions this came from

    Returns:
        MemoryEntry object
    """
    now = datetime.now()
    return MemoryEntry(
        id=entry_id,
        content=content,
        memory_type=memory_type,
        importance=importance,
        confidence=confidence,
        created_at=now,
        last_accessed=now,
        source_session_ids=source_session_ids or []
    )


# Example usage
if __name__ == "__main__":
    # Create a sample session
    session = create_session_from_transcript(
        session_id="session_001",
        transcript=[
            ("user", "Help me plan a trip to Singapore in July"),
            ("assistant", "I'll help you plan your Singapore trip for July."),
            ("user", "I prefer hotels with strong AC and quiet dinners"),
            ("assistant", "Noted! I'll prioritize hotels with strong AC and quiet dining options."),
            ("user", "I'm vegetarian"),
            ("assistant", "I'll make sure all restaurant recommendations are vegetarian-friendly.")
        ]
    )

    print(f"Session: {session.id}")
    print(f"Messages: {session.num_messages}")
    print(f"Duration: {session.duration} seconds")
    print(f"\nConversation:")
    print(session.get_conversation_text())

    # Create a memory store
    store = MemoryStore(
        id="memory_store_001",
        created_at=datetime.now(),
        updated_at=datetime.now()
    )

    # Add some memories
    store.add_entry(create_memory_entry(
        entry_id="mem_001",
        content="User is planning a trip to Singapore in July 2026",
        memory_type=MemoryType.FACTUAL,
        importance=0.8,
        confidence=0.9,
        source_session_ids=["session_001"]
    ))

    store.add_entry(create_memory_entry(
        entry_id="mem_002",
        content="User prefers hotels with strong AC",
        memory_type=MemoryType.PREFERENCES,
        importance=0.7,
        confidence=0.8,
        source_session_ids=["session_001"]
    ))

    store.add_entry(create_memory_entry(
        entry_id="mem_003",
        content="User is vegetarian",
        memory_type=MemoryType.FACTUAL,
        importance=0.9,
        confidence=0.95,
        source_session_ids=["session_001"]
    ))

    print(f"\nMemory Store: {store.id}")
    print(f"Entries: {store.size}")
    print(f"Total importance: {store.total_importance:.2f}")

    for entry in store.entries:
        print(f"  [{entry.memory_type.value}] {entry.content}")
        print(f"    Importance: {entry.importance}, Confidence: {entry.confidence}")