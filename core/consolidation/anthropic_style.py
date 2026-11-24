"""
Anthropic-style Dreams: Preserve input, produce parallel artifact.

This architecture:
- NEVER modifies the input memory store
- Produces a separate, reorganized memory store
- Allows comparison between pre-dream and post-dream
- Supports rollback if dream degrades performance
- Designed to detect and avoid degradation

Based on: Anthropic's Dreams for Managed Agents (May 2026)
"""

from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
import copy
import json

from ..memory.data_structures import (
    Session, MemoryEntry, MemoryStore, MemoryType,
    ConsolidationResult, create_memory_entry
)


class AnthropicDreams:
    """
    Anthropic-style dreaming implementation.
    
    Key characteristics:
    1. Input store is NEVER modified
    2. Produces a separate, reorganized memory store
    3. Allows comparison between pre-dream and post-dream
    4. Supports rollback if dream degrades performance
    5. Designed to detect and avoid degradation
    
    This is the more cautious approach that prioritizes safety
    and allows teams to evaluate whether dreaming is helping
    or quietly degrading the agent.
    """

    def __init__(self, input_store: MemoryStore):
        """
        Initialize with an input memory store (which will be preserved).

        Args:
            input_store: The input memory store (will NOT be modified)
        """
        self.input_store = input_store
        self.dream_history: List[Dict[str, Any]] = []

    def dream(
        self,
        sessions: List[Session],
        consolidation_prompt: Optional[str] = None
    ) -> Tuple[MemoryStore, ConsolidationResult]:
        """
        Execute a dreaming pass, producing a NEW memory store.

        Args:
            sessions: Session transcripts to process
            consolidation_prompt: Optional custom prompt for consolidation

        Returns:
            Tuple of (new_memory_store, ConsolidationResult)
        """
        start_time = datetime.now()

        # Create a copy of the input store (preserve original)
        new_store = copy.deepcopy(self.input_store)
        new_store.id = f"{self.input_store.id}_dreamed_{len(self.dream_history) + 1}"
        new_store.updated_at = datetime.now()

        # Count initial state
        initial_size = new_store.size
        initial_importance = new_store.total_importance

        # Process each session
        entries_added = 0
        entries_updated = 0

        for session in sessions:
            # Extract memories from session
            new_entries = self._extract_memories(session)

            # Merge with existing memories in the NEW store
            for entry in new_entries:
                existing = self._find_similar_entry(new_store, entry)
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
                    new_store.add_entry(entry)
                    entries_added += 1

        # Apply decay to all memories in new store
        new_store.apply_decay(decay_rate=0.01)

        # Prune low-importance memories (planned forgetting)
        removed = new_store.prune_low_importance(threshold=0.1)
        entries_removed = len(removed)

        # Record result
        duration = (datetime.now() - start_time).total_seconds()
        result = ConsolidationResult(
            id=f"dream_{len(self.dream_history) + 1}",
            timestamp=datetime.now(),
            input_store_id=self.input_store.id,
            output_store_id=new_store.id,  # Different store (parallel artifact)
            num_sessions_processed=len(sessions),
            num_entries_added=entries_added,
            num_entries_updated=entries_updated,
            num_entries_removed=entries_removed,
            duration_seconds=duration,
            metadata={
                "architecture": "anthropic_style",
                "initial_size": initial_size,
                "final_size": new_store.size,
                "initial_importance": initial_importance,
                "final_importance": new_store.total_importance,
                "input_preserved": True
            }
        )

        # Record in history
        dream_record = {
            "result": result,
            "new_store": new_store,
            "input_store_snapshot": self._create_snapshot(self.input_store)
        }
        self.dream_history.append(dream_record)

        return new_store, result

    def evaluate_dream(
        self,
        new_store: MemoryStore,
        eval_function: callable
    ) -> Dict[str, Any]:
        """
        Evaluate whether the dream improved or degraded performance.

        This is the key advantage of Anthropic's architecture:
        you can compare pre-dream and post-dream performance.

        Args:
            new_store: The dreamed memory store
            eval_function: Function that evaluates a memory store
                         and returns a score

        Returns:
            Dictionary with evaluation results
        """
        # Evaluate input store (before dream)
        input_score = eval_function(self.input_store)

        # Evaluate new store (after dream)
        new_score = eval_function(new_store)

        # Determine if dream helped or hurt
        improvement = new_score - input_score
        helped = improvement > 0

        return {
            "input_score": input_score,
            "new_score": new_score,
            "improvement": improvement,
            "helped": helped,
            "recommendation": "promote" if helped else "reject"
        }

    def rollback_to_previous(self) -> Optional[MemoryStore]:
        """
        Roll back to the previous memory store.

        Returns:
            The previous store, or None if this is the first dream
        """
        if len(self.dream_history) < 2:
            return None

        # Remove the last dream
        last_dream = self.dream_history.pop()

        # Return the store from the previous dream
        # (or the original input store if this was the first dream)
        if len(self.dream_history) == 0:
            return self.input_store
        else:
            return self.dream_history[-1]["new_store"]

    def get_dream_comparison(self, dream_index: int) -> Dict[str, Any]:
        """
        Get comparison between input and output for a specific dream.

        Args:
            dream_index: Index of the dream to compare

        Returns:
            Dictionary with comparison data
        """
        if dream_index >= len(self.dream_history):
            return {}

        dream = self.dream_history[dream_index]
        result = dream["result"]
        new_store = dream["new_store"]

        return {
            "input_store": {
                "id": self.input_store.id,
                "size": self.input_store.size,
                "total_importance": self.input_store.total_importance
            },
            "output_store": {
                "id": new_store.id,
                "size": new_store.size,
                "total_importance": new_store.total_importance
            },
            "changes": {
                "added": result.num_entries_added,
                "updated": result.num_entries_updated,
                "removed": result.num_entries_removed,
                "net_change": result.net_change
            },
            "timestamp": result.timestamp
        }

    def _create_snapshot(self, store: MemoryStore) -> Dict[str, Any]:
        """Create a snapshot of a memory store for comparison"""
        return {
            "id": store.id,
            "size": store.size,
            "total_importance": store.total_importance,
            "entries": [
                {
                    "id": e.id,
                    "content": e.content,
                    "importance": e.importance,
                    "confidence": e.confidence
                }
                for e in store.entries
            ]
        }

    def _extract_memories(self, session: Session) -> List[MemoryEntry]:
        """
        Extract memories from a session transcript.
        
        In a real implementation, this would use an LLM to analyze
        the conversation and extract important information.
        """
        entries = []
        session_text = session.get_conversation_text()

        # Simple heuristic extraction (in production, use LLM)
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
            if 'singapore' in line and ('trip' in line or 'travel' in line or 'visit' in line):
                facts.append("User is planning a trip to Singapore")
            if 'july' in line:
                facts.append("Trip is in July")

        return facts

    def _find_similar_entry(self, store: MemoryStore, new_entry: MemoryEntry) -> Optional[MemoryEntry]:
        """Find an existing entry similar to the new one in the given store"""
        for entry in store.entries:
            if self._are_similar(entry.content, new_entry.content):
                return entry
        return None

    def _are_similar(self, text1: str, text2: str) -> bool:
        """Check if two texts are similar (simplified)"""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 or not words2:
            return False
        overlap = len(words1 & words2) / min(len(words1), len(words2))
        return overlap > 0.5

    def _merge_content(self, existing: str, new: str) -> str:
        """Merge two content strings"""
        if existing in new:
            return new
        if new in existing:
            return existing
        return f"{existing}; {new}"


class AnthropicDreamsWithSelectiveForgetting(AnthropicDreams):
    """
    Enhanced Anthropic-style dreaming with selective forgetting.
    
    This addresses the "plan to forget" principle from the research:
    Consolidation is additive by default, so you need to decide
    what your system drops, not just what it keeps.
    """

    def dream_with_forgetting(
        self,
        sessions: List[Session],
        forgetting_strategy: str = "adaptive",
        forgetting_threshold: float = 0.3
    ) -> Tuple[MemoryStore, ConsolidationResult]:
        """
        Execute dreaming with selective forgetting.

        Args:
            sessions: Session transcripts to process
            forgetting_strategy: "adaptive", "aggressive", or "conservative"
            forgetting_threshold: Importance threshold for forgetting

        Returns:
            Tuple of (new_memory_store, ConsolidationResult)
        """
        # First, run standard dream
        new_store, result = self.dream(sessions)

        # Apply selective forgetting
        forgotten = self._apply_selective_forgetting(
            new_store,
            forgetting_strategy,
            forgetting_threshold
        )

        # Update result
        result.num_entries_removed += len(forgotten)
        result.metadata["forgotten_entries"] = [
            {"id": e.id, "content": e.content, "importance": e.importance}
            for e in forgotten
        ]

        return new_store, result

    def _apply_selective_forgetting(
        self,
        store: MemoryStore,
        strategy: str,
        threshold: float
    ) -> List[MemoryEntry]:
        """
        Apply selective forgetting based on strategy.

        Strategies:
        - "adaptive": Forget based on importance and age
        - "aggressive": Forget more aggressively
        - "conservative": Only forget very low importance
        """
        forgotten = []
        keep = []

        for entry in store.entries:
            should_forget = False

            if strategy == "adaptive":
                # Forget if importance is low OR if it's old and not accessed
                age_days = (datetime.now() - entry.last_accessed).days
                if entry.importance < threshold or (age_days > 30 and entry.importance < 0.5):
                    should_forget = True

            elif strategy == "aggressive":
                # Forget if importance is below threshold
                if entry.importance < threshold * 1.5:
                    should_forget = True

            elif strategy == "conservative":
                # Only forget if importance is very low
                if entry.importance < threshold * 0.5:
                    should_forget = True

            if should_forget:
                forgotten.append(entry)
            else:
                keep.append(entry)

        store.entries = keep
        store.updated_at = datetime.now()

        return forgotten