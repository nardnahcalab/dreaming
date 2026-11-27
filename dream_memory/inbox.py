"""
Write-back inbox: memories contributed by agents via the MCP `dream_remember`
tool.

Design constraints:

  * The episodic store is APPEND-ONLY and written exclusively by the four
    collectors. Agents must never write there, so remembered memories land in
    a separate append-only JSONL file instead.
  * The inbox is never rewritten either. Consumption is tracked by unioning
    `inbox_ids` across every dream artifact (the same pattern used for
    `processed_hashes`), so a consumed entry is merely *marked*, never
    deleted.
  * Pending entries are merged into `previous_entries` on the next dream, so
    they flow through the normal LLM update rule (keep / revise / drop) and
    become ordinary crystallized entries with full provenance.
  * The always-on digest reads the inbox directly, so a remembered memory is
    visible to agents immediately -- before any dream runs.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from .schema import MemoryEntry

INBOX_FILENAME = "inbox.jsonl"

# The five categories the dream prompt enforces. Remembered memories must use
# the same taxonomy or they would be rejected by the next dream's validation.
CATEGORIES = ("fact", "preference", "project", "lesson", "task")


def inbox_path(root: str) -> str:
    return os.path.join(root, INBOX_FILENAME)


def remember(root: str, content: str, category: str = "fact",
             importance: float = 0.6, confidence: float = 0.7,
             agent: str = "") -> Dict[str, Any]:
    """
    Append one remembered memory to the inbox. Returns the stored record.

    Input is sanitised rather than rejected: an unknown category falls back
    to "fact", and importance/confidence are clamped to [0, 1].
    """
    content = (content or "").strip()
    if not content:
        raise ValueError("content must be non-empty")

    if category not in CATEGORIES:
        category = "fact"

    def _clamp(v: Any) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.5

    record = {
        "id": "rem-%d-%s" % (int(time.time() * 1000), uuid.uuid4().hex[:6]),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "content": content,
        "category": category,
        "importance": _clamp(importance),
        "confidence": _clamp(confidence),
        "agent": (agent or "").strip(),
    }

    os.makedirs(root, exist_ok=True)
    with open(inbox_path(root), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_inbox(root: str) -> List[Dict[str, Any]]:
    """Read every inbox record, skipping malformed lines."""
    path = inbox_path(root)
    if not os.path.exists(path):
        return []
    out: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("id"):
                out.append(rec)
    return out


def consumed_ids(dream_store: Any) -> set:
    """Union of `inbox_ids` across ALL dreams (never re-ingest an entry)."""
    ids: set = set()
    for did in dream_store.dream_ids():
        art = dream_store.load(did)
        if not art:
            continue
        ids.update(art.stats.get("inbox_ids", []) or [])
    return ids


def pending(root: str, dream_store: Any) -> List[Dict[str, Any]]:
    """Inbox records not yet carried by any dream."""
    seen = consumed_ids(dream_store)
    return [r for r in load_inbox(root) if r["id"] not in seen]


def pending_entries(root: str, dream_store: Any) -> List[MemoryEntry]:
    """Pending records as MemoryEntry objects, ready to join `previous_entries`.

    `sources` carries the inbox record id rather than a session hash, which
    keeps provenance honest: these memories came from an agent, not a
    transcript.
    """
    entries: List[MemoryEntry] = []
    for rec in pending(root, dream_store):
        entries.append(MemoryEntry(
            id=rec["id"],
            content=rec["content"],
            category=rec.get("category", "fact"),
            importance=float(rec.get("importance", 0.6)),
            confidence=float(rec.get("confidence", 0.5)),
            sources=[rec["id"]],
            created_dream="inbox",
            last_seen_dream="inbox",
        ))
    return entries


def take_ids(entries: List[MemoryEntry]) -> List[str]:
    """Inbox ids represented in a batch of entries (for stats bookkeeping)."""
    out = []
    for e in entries:
        for s in e.sources:
            if isinstance(s, str) and s.startswith("rem-"):
                out.append(s)
    return out
