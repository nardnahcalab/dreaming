"""
Evaluation: compare two dream artifacts (Principle #3).

Measures what matters per the research:
  - Retention: how many important memories survived between dreams
  - Freshness: how much new information was incorporated
  - Degradation: did high-importance entries disappear? (Zhang's risk)
  - Coverage: category balance across the consolidated store
"""

from typing import Dict, Any, List, Optional
from .schema import DreamArtifact, MemoryEntry


def compare(before: Optional[DreamArtifact],
            after: Optional[DreamArtifact]) -> Dict[str, Any]:
    """Compare two dreams. before=None means first dream (baseline)."""
    if after is None:
        return {"error": "no after artifact"}

    b_entries = before.entries if before else []
    a_entries = after.entries

    b_contents = {e.content.lower().strip() for e in b_entries}
    a_contents = {e.content.lower().strip() for e in a_entries}

    kept = b_contents & a_contents
    lost = b_contents - a_contents
    added = a_contents - b_contents

    # Zhang's failure mode: did we lose HIGH-importance memories?
    b_by_content = {e.content.lower().strip(): e for e in b_entries}
    important_lost = [
        {"content": b_by_content[c].content,
         "importance": b_by_content[c].importance,
         "category": b_by_content[c].category}
        for c in lost if b_by_content[c].importance >= 0.7
    ]

    # Category coverage
    def cats(entries: List[MemoryEntry]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for e in entries:
            out[e.category] = out.get(e.category, 0) + 1
        return out

    return {
        "before": before.id if before else "(none - baseline)",
        "after": after.id,
        "entries_before": len(b_entries),
        "entries_after": len(a_entries),
        "kept": len(kept),
        "lost": len(lost),
        "added": len(added),
        "retention_rate": round(len(kept) / len(b_contents), 3) if b_contents else 1.0,
        "important_lost": important_lost,
        "degradation_warning": len(important_lost) > 0,
        "categories_before": cats(b_entries),
        "categories_after": cats(a_entries),
        "avg_importance_before": round(
            sum(e.importance for e in b_entries) / len(b_entries), 3) if b_entries else 0,
        "avg_importance_after": round(
            sum(e.importance for e in a_entries) / len(a_entries), 3) if a_entries else 0,
    }


def run_self_test() -> Dict[str, Any]:
    """
    Zhang-style sanity test on the current dream: does the consolidated
    store preserve high-importance, specific memories?
    """
    from .dreaming import DreamStore
    import os
    root = os.environ.get("DREAM_ROOT")
    if not root:
        return {"error": "DREAM_ROOT not set"}
    store = DreamStore(root)
    latest = store.latest_id()
    if not latest:
        return {"error": "no dreams yet - run `dream` first"}
    art = store.load(latest)
    entries = art.entries

    total = len(entries)
    high_imp = [e for e in entries if e.importance >= 0.7]
    vague = [e for e in entries if len(e.content.split()) < 4]
    dupes = total - len({e.content.lower().strip() for e in entries})

    return {
        "dream": latest,
        "total_entries": total,
        "high_importance": len(high_imp),
        "vague_entries": len(vague),
        "duplicate_contents": dupes,
        "healthy": dupes == 0 and (len(vague) / total if total else 0) < 0.3,
        "note": "healthy = no duplicates and <30% vague (<4 word) memories",
    }
