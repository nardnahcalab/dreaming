"""
Dreaming engine: consolidates episodic memory into abstract memory.

Implements all four principles from the learning guide:

  1. PRESERVE THE INPUT  - reads episodic store read-only, never writes to it
  2. INSTRUMENT THE LOOP - every dream logged to dreaming/history.jsonl
  3. COMPARE WITH EVAL   - artifacts are versioned so pre/post can be compared
  4. PLAN TO FORGET      - consolidation caps entry count by importance;
                           episodic memory is NEVER pruned

Architecture choice: Anthropic-style (parallel artifact).
  Each dream writes dream-NNNN.json as a NEW file. The previous dream
  file is never modified. `latest` is a pointer that can be rolled back.

The "brain" of the dream is a Dell Enterprise Hub deployment (DEH): a
Dockerised SGLang server discovered via the `dell-ai` SDK and spoken to
over the OpenAI-compatible wire. Nothing leaves the box.
If no DEH endpoint is reachable, a deterministic heuristic consolidator
runs so the pipeline still works offline.
"""

import json
import os
import re
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional, Tuple

from .schema import (
    Session, MemoryEntry, DreamArtifact, now_iso
)
from .deh import DEHLLM, discover_endpoint
from . import inbox as inbox_mod

# Backwards-compatible name: the Ollama client is gone, DEHLLM replaces it.
LocalLLM = DEHLLM


# ---------------------------------------------------------------------------
# LLM access (Dell Enterprise Hub -> OpenAI-compatible endpoint)
# ---------------------------------------------------------------------------

# The client itself lives in .deh (DEHLLM): endpoint discovery ladder plus
# /v1/chat/completions transport. Re-exported here so existing imports of
# `dream_memory.dreaming.LocalLLM` keep working.


# ---------------------------------------------------------------------------
# Dream prompts
# ---------------------------------------------------------------------------

DREAM_SYSTEM = """You are a memory consolidation engine ("dreaming").
You read raw conversation transcripts and extract DURABLE memories that
should persist for future sessions.

Extract memories in these categories:
  - fact:       stable facts about the user or their environment
  - preference: how the user likes things done, likes/dislikes
  - project:    ongoing work, goals, project context
  - lesson:     lessons learned, solutions that worked, conventions
  - task:       open tasks, commitments, things to follow up

Rules:
- Only include what will still matter in FUTURE conversations.
- Skip transient details (one-off errors, temporary file paths, chatter).
- Each memory must be self-contained: understandable without context.
- Be specific. "User prefers pytest over unittest for Python tests" not
  "user has testing preferences".
- Mark importance 0.0-1.0 (how much a future session loses without it)
  and confidence 0.0-1.0 (how clearly the transcript supports it).
- Do NOT invent facts not present in the transcripts.
- Do NOT echo the example structure below verbatim -- write real memories
  drawn from the transcripts.

Output STRICT JSON only, no prose, matching:
{"entries": [{"content": "The bs2 app is a React 18 + Vite + Tailwind app",
              "category": "project",
              "importance": 0.8, "confidence": 0.9}]}
"""


def _build_dream_prompt(sessions: List[Session],
                        previous_entries: List[MemoryEntry]) -> str:
    # Budget transcript chars so the prompt stays inside typical local-model
    # context windows (~100K tokens). Split evenly across the batch.
    total_budget = 120_000
    per_session = max(2_000, min(12_000, total_budget // max(1, len(sessions))))
    # Leave room for the previous crystallized memory
    prev_json = ""
    if previous_entries:
        prev_json = json.dumps([e.to_dict() for e in previous_entries],
                               ensure_ascii=False, indent=1)
        per_session = max(2_000, (total_budget - len(prev_json)) //
                          max(1, len(sessions)))

    parts = []
    if previous_entries:
        parts.append("EXISTING CRYSTALLIZED MEMORY (from previous dream):\n" +
                     prev_json)
        parts.append("\nUpdate rule: keep still-relevant entries, revise stale "
                     "ones (e.g. 'planning trip' -> 'trip completed' if now "
                     "past), drop obsolete ones, add new ones from the "
                     "transcripts below.")

    parts.append(f"\nNEW TRANSCRIPTS SINCE LAST DREAM ({len(sessions)} sessions):\n")
    for i, s in enumerate(sessions, 1):
        header = f"--- Session {i}: [{s.tool}] {s.title or s.id} ---"
        if s.project:
            header += f"\n(project: {s.project})"
        if s.started:
            header += f"\n(when: {s.started})"
        parts.append(header)
        parts.append(s.transcript_text(max_chars=per_session))

    parts.append("\nProduce the crystallized memory JSON now.")
    return "\n".join(parts)


def _extract_json(text: str) -> Dict[str, Any]:
    """Robustly extract a JSON object from LLM output."""
    text = text.strip()
    # strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # find first { ... last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("No JSON found in LLM output")


# Placeholder text a small model may echo back instead of writing a real
# memory. Caught so junk never reaches crystallized memory or the digests.
_PLACEHOLDERS = {"...", "…", "example", "memory", "content",
                 "entry", "text", "n/a", "none", "placeholder", "tbd"}


def _degenerate(content: str) -> bool:
    """True for placeholder/junk content that is not a usable memory."""
    c = content.strip()
    if len(c) < 12:
        return True
    if c.lower().strip(" .!?") in _PLACEHOLDERS:
        return True
    # Strip everything that is not a letter/digit: if almost nothing is left
    # the content was punctuation/ellipses ("...", "----", "!!!").
    alnum = re.sub(r"[^0-9A-Za-z]+", "", c)
    if len(alnum) < 6:
        return True
    # A single repeated character ("aaaa...") is not a memory either.
    if len(set(alnum)) <= 2:
        return True
    # Every token is 1-2 chars ("a b c d e f g") -- no word carries meaning.
    words = c.split()
    if words and max(len(w) for w in words) < 3:
        return True
    return False


# Retries before the heuristic consolidator takes over. Degenerate replies are
# stochastic, so a second attempt with a nudge usually succeeds.
LLM_ATTEMPTS = 3

_RETRY_NUDGE = (
    "\n\nREMINDER: your previous reply contained PLACEHOLDER text such as "
    '"..." instead of real memories. That is wrong. Write concrete, '
    "self-contained memories drawn from the transcripts above, e.g. "
    '"User prefers pytest over unittest for Python tests". '
    "Output the STRICT JSON object now."
)


def _parse_entries(data: Dict[str, Any],
                   batch: List[Session]) -> List[MemoryEntry]:
    """Convert parsed LLM JSON into MemoryEntry objects, dropping junk.

    Raises nothing: entries that are missing, malformed or degenerate are
    silently skipped, so an all-placeholder reply yields an empty list and the
    caller can retry.
    """
    out: List[MemoryEntry] = []
    raw_entries = data.get("entries", [])
    if not isinstance(raw_entries, list):
        return out
    for i, e in enumerate(raw_entries):
        if not isinstance(e, dict):
            continue
        content = str(e.get("content", "")).strip()
        # Skip placeholder junk the model sometimes echoes back (real bug:
        # a 38-session dream returned content "...").
        if not content or _degenerate(content):
            continue
        category = str(e.get("category", "fact")).lower()
        if category not in inbox_mod.CATEGORIES:
            category = "fact"
        out.append(MemoryEntry(
            id=f"mem_{i:04d}",
            content=content,
            category=category,
            importance=_clamp(e.get("importance", 0.5)),
            confidence=_clamp(e.get("confidence", 0.5)),
            sources=[s.content_hash for s in batch],
        ))
    return out


# ---------------------------------------------------------------------------
# Heuristic fallback (works fully offline, no model needed)
# ---------------------------------------------------------------------------

_PREFERENCE_PATTERNS = [
    (r"\b(?:i|user)\s+(?:prefer|like|want|love|hate|dislike)s?\b", "preference"),
    (r"\b(?:don'?t|do not|never)\s+(?:like|want|use)\b", "preference"),
]
_FACT_PATTERNS = [
    (r"\bi\s+am\b|\bi'?m\b", "fact"),
    (r"\bmy\s+(?:name|job|role|company|city|timezone)\b", "fact"),
    (r"\bi\s+live\b|\bi\s+work\b", "fact"),
]
_PROJECT_PATTERNS = [
    (r"\bwe\s+(?:are|'?re)\s+building\b", "project"),
    (r"\bmy\s+project\b|\bthe\s+project\b", "project"),
    (r"\bgoal\b|\bdeadline\b|\brelease\b", "project"),
]
_LESSON_PATTERNS = [
    r"\bworks?\s+well\b", r"\bshould\s+use\b", r"\bremember\s+that\b",
    r"\bconvention\b", r"\balways\s+(?:use|do)\b",
]


def heuristic_consolidate(sessions: List[Session],
                          previous: List[MemoryEntry]) -> List[MemoryEntry]:
    """Deterministic extraction without any model. Used as fallback."""
    candidates: Dict[str, MemoryEntry] = {}

    # carry forward previous entries (they're already curated)
    for e in previous:
        candidates[e.content.lower()] = e

    for s in sessions:
        for m in s.messages:
            if m.role != "user":
                continue
            text = m.content.strip()
            if len(text) < 15 or len(text) > 500:
                continue
            lines = [l.strip() for l in text.split("\n") if 15 < len(l.strip()) < 300]
            for line in lines:
                low = line.lower()
                category = None
                for pat, cat in _PREFERENCE_PATTERNS:
                    if re.search(pat, low):
                        category = cat
                        break
                if not category:
                    for pat in _FACT_PATTERNS:
                        if re.search(pat, low):
                            category = "fact"
                            break
                if not category:
                    for pat in _PROJECT_PATTERNS:
                        if re.search(pat, low):
                            category = "project"
                            break
                if not category:
                    for pat in _LESSON_PATTERNS:
                        if re.search(pat, low):
                            category = "lesson"
                            break
                if not category:
                    continue

                key = low
                if key in candidates:
                    continue
                candidates[key] = MemoryEntry(
                    id=f"mem_{len(candidates):04d}",
                    content=line,
                    category=category,
                    importance=0.6,
                    confidence=0.5,
                    sources=[s.content_hash],
                )

    entries = list(candidates.values())
    entries.sort(key=lambda e: e.importance, reverse=True)
    return entries


# ---------------------------------------------------------------------------
# Dream store (versioned artifacts, never overwritten)
# ---------------------------------------------------------------------------

class DreamStore:
    def __init__(self, root: str):
        self.root = root
        self.dream_dir = os.path.join(root, "dreaming")
        self.history_path = os.path.join(root, "dreaming", "history.jsonl")
        self.latest_path = os.path.join(root, "dreaming", "latest.txt")
        os.makedirs(self.dream_dir, exist_ok=True)

    def dream_ids(self) -> List[str]:
        # full ids like "dream-0001" (zero-padded, so sorted() == chronological)
        ids = [f[:-5] for f in os.listdir(self.dream_dir)
               if f.startswith("dream-") and f.endswith(".json")]
        return sorted(ids)

    def next_id(self) -> str:
        ids = self.dream_ids()
        n = int(ids[-1].split("-")[1]) + 1 if ids else 1
        return f"dream-{n:04d}"

    def path(self, dream_id: str) -> str:
        return os.path.join(self.dream_dir, f"{dream_id}.json")

    def load(self, dream_id: str) -> Optional[DreamArtifact]:
        p = self.path(dream_id)
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return DreamArtifact.from_dict(json.load(f))

    def latest_id(self) -> Optional[str]:
        if os.path.exists(self.latest_path):
            with open(self.latest_path) as f:
                did = f.read().strip()
            if did and os.path.exists(self.path(did)):
                return did
        ids = self.dream_ids()
        return ids[-1] if ids else None

    def save(self, artifact: DreamArtifact) -> str:
        """Write a NEW dream file. Never overwrites an existing dream."""
        p = self.path(artifact.id)
        if os.path.exists(p):
            raise FileExistsError(f"{artifact.id} already exists - dreams are immutable")
        with open(p, "x") as f:
            json.dump(artifact.to_dict(), f, ensure_ascii=False, indent=1)
        # update pointer
        with open(self.latest_path, "w") as f:
            f.write(artifact.id)
        return p

    def set_latest(self, dream_id: str) -> bool:
        """Rollback: repoint latest to an older dream."""
        if not os.path.exists(self.path(dream_id)):
            return False
        with open(self.latest_path, "w") as f:
            f.write(dream_id)
        return True

    def log(self, record: Dict[str, Any]) -> None:
        """Principle #2: instrument the loop."""
        record["ts"] = now_iso()
        with open(self.history_path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# The dreamer
# ---------------------------------------------------------------------------

class Dreamer:
    def __init__(self, root: str, model: str = None):
        from .episodic import EpisodicStore
        self.root = root
        self.episodic = EpisodicStore(root)
        self.dreams = DreamStore(root)
        self.llm = LocalLLM(model=model)

    def dream(self, use_llm: bool = True, max_sessions: int = 40,
              max_entries: int = 60) -> Tuple[DreamArtifact, Dict[str, Any]]:
        """
        Run one dreaming pass.

        Returns (artifact, report). The artifact is a NEW immutable file.
        """
        all_sessions = self.episodic.all_sessions()

        # Which sessions are already consolidated? Union across ALL dreams
        # (not just the latest): a session processed by dream-0001 must not be
        # reprocessed by dream-0003 even if dream-0002 didn't mention it.
        last_id = self.dreams.latest_id()
        last = self.dreams.load(last_id) if last_id else None
        already = set()
        for did in self.dreams.dream_ids():
            art = self.dreams.load(did)
            if not art:
                continue
            for e in art.entries:
                already.update(e.sources)
            already.update(art.stats.get("processed_hashes", []))

        new_sessions = [s for s in all_sessions if s.content_hash not in already]
        batch = new_sessions[-max_sessions:]  # dream on the most recent

        # Nothing new since last dream: don't write an empty artifact.
        if not batch:
            report = {
                "dream_id": last_id,
                "skipped": True,
                "new_sessions": 0,
                "batch_sessions": 0,
                "entries": len(last.entries) if last else 0,
                "pruned": 0,
                "used_llm": False,
                "model": None,
                "llm_error": None,
            }
            self.dreams.log({"event": "dream_skipped",
                             "reason": "no new sessions",
                             "previous_dream": last_id})
            return (last, report)

        # Memories agents contributed via `dream_remember` join the prior set,
        # so the normal update rule (keep / revise / drop) carries them into
        # this artifact as ordinary crystallized entries.
        pending_inbox = inbox_mod.pending_entries(self.root, self.dreams)
        previous_entries = list(last.entries if last else [])
        previous_entries.extend(pending_inbox)

        used_llm = False
        entries: List[MemoryEntry] = []
        llm_error = None
        llm_attempts = 0

        if use_llm and batch and self.llm.available():
            # The model sometimes answers with a placeholder TEMPLATE instead
            # of real memories ("content": "..."). It is stochastic -- the
            # same prompt can return 10 usable entries on the next try -- so
            # retry with a corrective nudge before falling back to heuristics.
            for attempt in range(LLM_ATTEMPTS):
                llm_attempts = attempt + 1
                try:
                    prompt = _build_dream_prompt(batch, previous_entries)
                    if attempt:
                        prompt += _RETRY_NUDGE
                    temp = 0.2 if attempt == 0 else 0.6
                    raw = self.llm.chat(DREAM_SYSTEM, prompt,
                                        temperature=temp)
                    data = _extract_json(raw)
                    entries = _parse_entries(data, batch)
                    if entries:
                        used_llm = True
                        llm_error = None
                        break
                    llm_error = (
                        "degenerate output on attempt "
                        f"{attempt + 1}/{LLM_ATTEMPTS}")
                except Exception as ex:  # noqa: BLE001 - fall back gracefully
                    llm_error = f"{type(ex).__name__}: {ex}"
                    # A transport failure will not be fixed by retrying the
                    # same call; only degenerate output is worth another go.
                    if not isinstance(ex, ValueError):
                        break

        if not used_llm:
            entries = heuristic_consolidate(batch, previous_entries)

        # Dedup by content, then apply "plan to forget": cap by importance
        seen = set()
        deduped = []
        for e in sorted(entries, key=lambda x: -x.importance):
            key = e.content.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(e)
        pruned = deduped[:max_entries]
        dropped = len(deduped) - len(pruned)

        dream_id = self.dreams.next_id()
        artifact = DreamArtifact(
            id=dream_id,
            timestamp=now_iso(),
            entries=pruned,
            sessions_processed=len(batch),
            sessions_total=len(all_sessions),
            previous_dream=last_id or "",
            stats={
                "used_llm": used_llm,
                "model": self.llm.model if used_llm else None,
                "llm_error": llm_error,
                "llm_attempts": llm_attempts,
                "new_sessions": len(new_sessions),
                "batch_sessions": len(batch),
                "entries_before_prune": len(deduped),
                "entries_pruned": dropped,
                "previous_entries": len(previous_entries),
                "processed_hashes": [s.content_hash for s in batch],
                # Consumption marker: union of these across ALL dreams decides
                # which inbox records are still pending (never deleted).
                "inbox_ids": inbox_mod.take_ids(previous_entries),
            },
        )

        # Principle #1 & #3: write NEW artifact, never touch input or old dreams
        self.dreams.save(artifact)

        # Principle #2: instrument
        self.dreams.log({
            "event": "dream",
            "dream_id": dream_id,
            "previous_dream": last_id,
            "sessions_processed": len(batch),
            "entries": len(pruned),
            "used_llm": used_llm,
            "model": self.llm.model if used_llm else None,
            "llm_error": llm_error,
            "llm_attempts": llm_attempts,
            "entries_pruned": dropped,
        })

        report = {
            "dream_id": dream_id,
            "used_llm": used_llm,
            "model": self.llm.model if used_llm else None,
            "llm_error": llm_error,
            "llm_attempts": llm_attempts,
            "new_sessions": len(new_sessions),
            "batch_sessions": len(batch),
            "entries": len(pruned),
            "pruned": dropped,
        }
        return artifact, report


def _clamp(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, f))
