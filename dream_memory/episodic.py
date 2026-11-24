"""
Episodic store: append-only, never modified.

Implements Principle #1 from the learning guide:
  "Preserve the input - write the result to a new artifact and
   keep the previous state."

Design:
  storage/
    episodic/
      index.json            # hash -> metadata (rewritten on ADD only)
      sessions/<hash>.json  # one file per session, written ONCE, never touched
      ingest_log.jsonl      # append-only log of every ingest operation

Sessions are content-addressed: identical sessions are stored once.
A stored session file is never opened for writing again.
"""

import json
import os
import hashlib
from typing import List, Dict, Any, Optional, Set

from .schema import Session, now_iso


class EpisodicStore:
    def __init__(self, root: str):
        self.root = root
        self.sessions_dir = os.path.join(root, "episodic", "sessions")
        self.index_path = os.path.join(root, "episodic", "index.json")
        self.log_path = os.path.join(root, "episodic", "ingest_log.jsonl")
        os.makedirs(self.sessions_dir, exist_ok=True)

    # ---------- read ----------

    def known_hashes(self) -> Set[str]:
        if not os.path.exists(self.index_path):
            return set()
        with open(self.index_path) as f:
            index = json.load(f)
        return set(index.keys())

    def load_index(self) -> Dict[str, Any]:
        if not os.path.exists(self.index_path):
            return {}
        with open(self.index_path) as f:
            return json.load(f)

    def get(self, content_hash: str) -> Optional[Session]:
        path = self._session_path(content_hash)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return self._session_from_dict(json.load(f))

    def all_sessions(self, tool: Optional[str] = None) -> List[Session]:
        """Load every stored session (optionally filtered by tool)."""
        index = self.load_index()
        out = []
        for h in sorted(index.keys(), key=lambda k: index[k].get("started") or ""):
            meta = index[h]
            if tool and meta.get("tool") != tool:
                continue
            s = self.get(h)
            if s:
                out.append(s)
        return out

    def stats(self) -> Dict[str, Any]:
        index = self.load_index()
        by_tool: Dict[str, int] = {}
        total_msgs = 0
        for meta in index.values():
            t = meta.get("tool", "unknown")
            by_tool[t] = by_tool.get(t, 0) + 1
            total_msgs += meta.get("messages", 0)
        return {
            "sessions": len(index),
            "messages": total_msgs,
            "by_tool": by_tool,
        }

    # ---------- write (append-only) ----------

    def add(self, session: Session) -> bool:
        """
        Store a session if not already present.

        Returns True if stored, False if duplicate.
        A session file, once written, is NEVER rewritten.
        """
        h = session.content_hash
        path = self._session_path(h)

        if os.path.exists(path):
            self._log({"event": "skip_duplicate", "hash": h,
                       "tool": session.tool, "session": session.id})
            return False

        data = session.to_dict()
        data["stored_at"] = now_iso()

        # Write-once: exclusive create fails if file exists (race safety)
        with open(path, "x") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)

        # Update index (index is metadata only; session content is immutable)
        index = self.load_index()
        index[h] = {
            "tool": session.tool,
            "session_id": session.id,
            "project": session.project,
            "title": session.title,
            "started": session.started,
            "ended": session.ended,
            "messages": len(session.messages),
            "stored_at": data["stored_at"],
        }
        self._write_index(index)

        self._log({"event": "stored", "hash": h, "tool": session.tool,
                   "session": session.id, "messages": len(session.messages)})
        return True

    def add_many(self, sessions: List[Session]) -> Dict[str, int]:
        stored, dupes = 0, 0
        for s in sessions:
            if self.add(s):
                stored += 1
            else:
                dupes += 1
        return {"stored": stored, "duplicates": dupes, "total": len(sessions)}

    # ---------- internals ----------

    def _session_path(self, h: str) -> str:
        return os.path.join(self.sessions_dir, f"{h}.json")

    def _write_index(self, index: Dict[str, Any]) -> None:
        tmp = self.index_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(index, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, self.index_path)  # atomic

    def _log(self, record: Dict[str, Any]) -> None:
        record["ts"] = now_iso()
        with open(self.log_path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _session_from_dict(d: Dict[str, Any]) -> Session:
        from .schema import Message
        return Session(
            id=d["id"],
            tool=d["tool"],
            messages=[Message(**m) for m in d.get("messages", [])],
            project=d.get("project", ""),
            started=d.get("started"),
            ended=d.get("ended"),
            title=d.get("title", ""),
            meta=d.get("meta", {}),
        )
