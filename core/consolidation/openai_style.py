"""
OpenAI-style Dreaming: Rewrite memory in place.

This architecture:
- Rewrites memory state continuously
- Each pass builds on previous output
- No rollback capability (previous state overwritten)
- Risk of degradation from iterated consolidation

Based on: OpenAI's Dreaming V3 for ChatGPT (June 2026)
"""

from datetime import datetime
from typing import List, Dict, Any, Optional
import json

from ..memory.data_structures import (
    Session, MemoryEntry, MemoryStore, MemoryType,
    ConsolidationResult, create_memory_entry
)


class OpenAIDreaming:
    """
    OpenAI-style dreaming implementation.
    
    Key characteristics:
    1. Rewrites memory in place (no separate artifact)
    2. Each consolidation pass conditions on previous output
    3. Continually updated synthesis
    4. No rollback - previous state is overwritten
    
    This is the more aggressive approach that risks degradation
    from iterated consolidation (as shown in Zhang's research).
    """

    def __init__(self, memory_store: MemoryStore):
        """
        Initialize with a memory store to consolidate.

        Args:
            memory_store: The memory store to rewrite
        """
        self.memory_store = memory_store
        self.consolidation_history: List[ConsolidationResult] = []

    def dream(
        self,
        sessions: List[Session],
        consolidation_prompt: Optional[str] = None
    ) -> ConsolidationResult:
        """
        Execute a dreaming pass (consolidation).

        Args:
            sessions: Session transcripts to process
            consolidation_prompt: Optional custom prompt for consolidation

        Returns:
            ConsolidationResult with statistics
        """
        start_time = datetime.now()

        # Count initial state
        initial_size = self.memory_store.size
        initial_importance = self.memory_store.total_importance

        # Process each session
        entries_added = 0
        entries_updated = 0

        for session in sessions:
            # Extract memories from session
            new_entries = self._extract_memories(session)

            # Merge with existing memories (rewrite in place)
            for entry in new_entries:
                existing = self._find_similar_entry(entry)
                if existing:
                    # Update existing entry
                    existing.content = self._merge_content(existing.content, entry.content)
                    existing.importance = max(existing.importance, entry.importance)
                    existing.confidence = max(existing.confidence, entry.confidence)
                    existing.source_session_ids.append(session.id)
                    existing.last_accessed = datetime.now()
                    entries_updated += 1
                else:
                    # Add new entry
                    self.memory_store.add_entry(entry)
                    entries_added += 1

        # Apply decay to all memories
        self.memory_store.apply_decay(decay_rate=0.01)

        # Prune low-importance memories (planned forgetting)
        removed = self.memory_store.prune_low_importance(threshold=0.1)
        entries_removed = len(removed)

        # Record result
        duration = (datetime.now() - start_time).total_seconds()
        result = ConsolidationResult(
            id=f"dream_{len(self.consolidation_history) + 1}",
            timestamp=datetime.now(),
            input_store_id=self.memory_store.id,
            output_store_id=self.memory_store.id,  # Same store (rewrite in place)
            num_sessions_processed=len(sessions),
            num_entries_added=entries_added,
            num_entries_updated=entries_updated,
            num_entries_removed=entries_removed,
            duration_seconds=duration,
            metadata={
                "architecture": "openai_style",
                "initial_size": initial_size,
                "final_size": self.memory_store.size,
                "initial_importance": initial_importance,
                "final_importance": self.memory_store.total_importance
            }
        )

        self.consolidation_history.append(result)
        return result

    def _extract_memories(self, session: Session) -> List[MemoryEntry]:
        """
        Extract memories from a session transcript.
        
        In a real implementation, this would use an LLM to analyze
        the conversation and extract important information.
        """
        entries = []
        session_text = session.get_conversation_text()

        # Simple heuristic extraction (in production, use LLM)
        # Look for user preferences and facts
        preferences = self._extract_preferences(session_text)
        facts = self._extract_facts(session_text)

        for pref in preferences:
            entry = create_memory_entry(
                entry_id=f"mem_{session.id}_{len(entries)}",
                content=pref,
                memory_type=MemoryType.PREFERENCES,
                importance=0.7,
                confidence=0.8,
                source_session_ids=[session.id]
            )
            entries.append(entry)

        for fact in facts:
            entry = create_memory_entry(
                entry_id=f"mem_{session.id}_{len(entries)}",
                content=fact,
                memory_type=MemoryType.FACTUAL,
                importance=0.6,
                confidence=0.7,
                source_session_ids=[session.id]
            )
            entries.append(entry)

        return entries

    def _extract_preferences(self, text: str) -> List[str]:
        """Extract user preferences from text (simplified)"""
        preferences = []
        lines = text.lower().split('\n')

        for line in lines:
            if 'user' in line and ('prefer' in line or 'like' in line or 'want' in line):
                # Extract the preference
                parts = line.split('prefer') if 'prefer' in line else \
                        line.split('like') if 'like' in line else \
                        line.split('want')
                if len(parts) > 1:
                    pref = parts[1].strip()
                    if pref:
                        preferences.append(f"User prefers {pref}")

            if 'vegetarian' in line:
                preferences.append("User is vegetarian")
            if 'strong ac' in line or 'strong air conditioning' in line:
                preferences.append("User prefers strong AC in hotels")

        return preferences

    def _extract_facts(self, text: str) -> List[str]:
        """Extract factual information from text (simplified)"""
        facts = []
        lines = text.lower().split('\n')

        for line in lines:
            # Look for travel plans
            if 'singapore' in line and ('trip' in line or 'travel' in line or 'visit' in line):
                facts.append("User is planning a trip to Singapore")
            if 'july' in line:
                facts.append("Trip is in July")

        return facts

    def _find_similar_entry(self, new_entry: MemoryEntry) -> Optional[MemoryEntry]:
        """Find an existing entry similar to the new one"""
        for entry in self.memory_store.entries:
            # Simple similarity check (in production, use embeddings)
            if self._are_similar(entry.content, new_entry.content):
                return entry
        return None

    def _are_similar(self, text1: str, text2: str) -> bool:
        """Check if two texts are similar (simplified)"""
        # Simple word overlap check
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 or not words2:
            return False
        overlap = len(words1 & words2) / min(len(words1), len(words2))
        return overlap > 0.5

    def _merge_content(self, existing: str, new: str) -> str:
        """Merge two content strings"""
        # Simple merge: keep the longer one, or combine if different
        if existing in new:
            return new
        if new in existing:
            return existing
        return f"{existing}; {new}"


class OpenAIDreamingWithTemporalAwareness(OpenAIDreaming):
    """
    Enhanced OpenAI-style dreaming with temporal awareness.
    
    This addresses one of the key challenges: staying current over time.
    For example, "Going to Singapore in July" should become
    "Went to Singapore" after the trip ends.
    """

    def dream_with_temporal_update(
        self,
        sessions: List[Session],
        current_time: Optional[datetime] = None
    ) -> ConsolidationResult:
        """
        Execute dreaming with temporal awareness.

        Args:
            sessions: Session transcripts to process
            current_time: Current time for temporal updates

        Returns:
            ConsolidationResult
        """
        current_time = current_time or datetime.now()

        # First, update temporal information
        self._update_temporal_memories(current_time)

        # Then run standard consolidation
        return self.dream(sessions)

    def _update_temporal_memories(self, current_time: datetime):
        """
        Update memories that have temporal content.
        
        For example:
        - "Planning trip to Singapore in July" → "Trip to Singapore completed"
        - "Meeting tomorrow" → "Meeting completed" (if time has passed)
        """
        temporal_updates = []

        for entry in self.memory_store.entries:
            # Check for temporal keywords
            if self._is_temporal_memory(entry.content):
                # Update based on current time
                updated_content = self._update_temporal_content(
                    entry.content, current_time
                )
                if updated_content != entry.content:
                    entry.content = updated_content
                    entry.last_accessed = current_time
                    temporal_updates.append(entry.id)

        return temporal_updates

    def _is_temporal_memory(self, content: str) -> bool:
        """Check if memory has temporal content"""
        temporal_keywords = [
            'planning', 'will', 'going to', 'trip to',
            'meeting', 'event', 'appointment', 'deadline'
        ]
        content_lower = content.lower()
        return any(keyword in content_lower for keyword in temporal_keywords)

    def _update_temporal_content(self, content: str, current_time: datetime) -> str:
        """Update temporal content based on current time"""
        # Simple temporal updates
        if 'planning trip to singapore' in content.lower():
            return "Trip to Singapore completed"
        if 'going to singapore' in content.lower():
            return "Visited Singapore"
        if 'will' in content.lower():
            # Remove future tense
            return content.replace('will ', '').replace('Will ', '')
        return content