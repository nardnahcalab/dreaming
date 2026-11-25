"""
Recall: search both memory layers.

Two layers, matching the research:
  - CRYSTALLIZED (abstract): fast, curated, from the latest dream
  - EPISODIC (raw): complete transcripts, exact source of truth

Search is keyword/score based (no external deps). Crystallized entries
score higher because they're curated; episodic fills gaps with raw evidence.
"""

import os
import re
from typing import List, Dict, Any, Optional

from .schema import Session, MemoryEntry
from .episodic import EpisodicStore
from .dreaming import DreamStore


def _tokenize(text: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and len(t) > 2]


def _score(query_tokens: List[str], text: str, base: float = 0.0) -> float:
    if not query_tokens:
        return 0.0
    toks = set(_tokenize(text))
    if not toks:
        return 0.0
    hits = len(set(query_tokens) & toks)
    if hits == 0:
        return 0.0
    return base * (hits / len(query_tokens))


def search(root: str, query: str, limit: int = 10) -> Dict[str, Any]:
    """Search crystallized memory first, then episodic for evidence."""
    qt = _tokenize(query)

    # --- crystallized layer ---
    dream_store = DreamStore(root)
    latest = dream_store.latest_id()
    crystallized: List[Dict[str, Any]] = []
    if latest:
        art = dream_store.load(latest)
        for e in art.entries:
            s = _score(qt, f"{e.content} {e.category}", base=1.0)
            # importance boosts score (curated memories matter more)
            s += e.importance * 0.3
            if s >= 0.15:
                crystallized.append({
                    "content": e.content,
                    "category": e.category,
                    "importance": e.importance,
                    "confidence": e.confidence,
                    "score": round(s, 3),
                    "dream": e.last_seen_dream or art.id,
                })
    crystallized.sort(key=lambda x: -x["score"])

    # --- episodic layer ---
    episodic_store = EpisodicStore(root)
    episodic: List[Dict[str, Any]] = []
    for s in episodic_store.all_sessions():
        for i, m in enumerate(s.messages):
            if m.role not in ("user", "assistant"):
                continue
            sc = _score(qt, m.content, base=0.6)
            if sc >= 0.15:
                snippet = m.content.strip().replace("\n", " ")[:300]
                episodic.append({
                    "tool": s.tool,
                    "session": s.id,
                    "project": s.project,
                    "when": s.started,
                    "snippet": snippet,
                    "score": round(sc, 3),
                })
    episodic.sort(key=lambda x: -x["score"])

    return {
        "query": query,
        "crystallized": crystallized[:limit],
        "episodic": episodic[:limit],
        "dream": latest,
    }
