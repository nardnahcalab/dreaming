"""
Collectors: normalize each tool's native format into Session objects.

Discovered storage locations (verified on this machine):

  OpenCode    ~/.local/share/opencode/opencode.db   (SQLite, 24GB)
              tables: session_v2, session_message (data JSON with content parts)

  Claude Code ~/.claude/projects/<proj>/<session>.jsonl (JSONL)
              line types: user, assistant, system ...

  Codex       ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl (JSONL)
              response_item/payload.type=message with role/content

  Hermes      ~/.hermes/state.db  (SQLite)
              tables: sessions, messages (role, content, timestamp)

All collectors are READ-ONLY on the source data.
"""

import json
import os
import glob
import sqlite3
import re
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from .schema import Session, Message

HOME = os.path.expanduser("~")


def _iso(ts: Any) -> Optional[str]:
    """Best-effort conversion of various timestamp formats to ISO8601."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # Heuristic: ms vs s vs us
        t = float(ts)
        if t > 1e17:      # microseconds
            t /= 1_000_000
        elif t > 1e14:    # milliseconds
            t /= 1_000
        try:
            return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(ts, str):
        return ts  # already ISO or close enough
    return None


def _clean(text: Any) -> str:
    """Extract plain text from content that may be str or list-of-blocks."""
    if text is None:
        return ""
    if isinstance(text, str):
        return text.strip()
    if isinstance(text, list):
        parts = []
        for block in text:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("type")
                if t in ("text", "thinking"):
                    parts.append(block.get("text") or block.get("thinking") or "")
                elif t == "output_text":
                    parts.append(block.get("text") or "")
                # skip tool_use/tool_result blocks: noise for memory
        return "\n".join(p for p in parts if p).strip()
    return str(text)


# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------

class OpenCodeCollector:
    tool = "opencode"

    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.join(HOME, ".local/share/opencode/opencode.db")

    def available(self) -> bool:
        return os.path.exists(self.db_path)

    def collect(self, limit: Optional[int] = None) -> List[Session]:
        if not self.available():
            return []
        # READ-ONLY URI mode: never write to the 24GB live database
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        cur.execute("""
            SELECT id, title, directory, time_created, time_updated, model
            FROM session_v2 ORDER BY time_created ASC
        """)
        rows = cur.fetchall()
        if limit:
            rows = rows[-limit:]

        sessions = []
        for row in rows:
            sid = row["id"]
            # Get messages for this session; only user/assistant text
            cur.execute("""
                SELECT type, data, time_created
                FROM session_message
                WHERE session_id = ?
                ORDER BY time_created ASC
            """, (sid,))
            msgs = []
            for m in cur.fetchall():
                mtype = m["type"]
                if mtype not in ("user", "assistant"):
                    continue
                try:
                    data = json.loads(m["data"])
                except (json.JSONDecodeError, TypeError):
                    continue
                content = _clean(data.get("content"))
                if not content:
                    continue
                # Skip pure-reasoning assistant turns with no user-visible text
                msgs.append(Message(
                    role=mtype,
                    content=content,
                    timestamp=_iso(m["time_created"]),
                ))

            if not msgs:
                continue

            sessions.append(Session(
                id=sid,
                tool=self.tool,
                messages=msgs,
                project=row["directory"] or "",
                started=_iso(row["time_created"]),
                ended=_iso(row["time_updated"]),
                title=row["title"] or "",
                meta={"model": row["model"]},
            ))
        con.close()
        return sessions


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------

class ClaudeCodeCollector:
    tool = "claude-code"

    def __init__(self, root: str = None):
        self.root = root or os.path.join(HOME, ".claude/projects")

    def available(self) -> bool:
        return os.path.isdir(self.root)

    def collect(self, limit: Optional[int] = None) -> List[Session]:
        if not self.available():
            return []
        sessions = []
        files = sorted(glob.glob(os.path.join(self.root, "*", "*.jsonl")))
        if limit:
            files = files[-limit:]

        for path in files:
            # Skip subagent files (they're children of main sessions)
            if "/subagents/" in path:
                continue
            session_id = os.path.splitext(os.path.basename(path))[0]
            project = os.path.basename(os.path.dirname(path))
            msgs = []
            started = ended = None
            title = ""

            try:
                with open(path, errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            j = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        t = j.get("type")
                        ts = j.get("timestamp")
                        if ts:
                            started = started or ts
                            ended = ts

                        if t == "ai-title":
                            title = j.get("title") or title

                        if t in ("user", "assistant"):
                            msg = j.get("message") or {}
                            content = _clean(msg.get("content"))
                            if not content:
                                continue
                            # Skip system-injected reminders
                            if content.startswith("<system-reminder"):
                                continue
                            msgs.append(Message(
                                role=t,
                                content=content,
                                timestamp=ts,
                            ))
            except OSError:
                continue

            if not msgs:
                continue

            sessions.append(Session(
                id=session_id,
                tool=self.tool,
                messages=msgs,
                project=project,
                started=started,
                ended=ended,
                title=title,
            ))
        return sessions


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------

class CodexCollector:
    tool = "codex"

    def __init__(self, root: str = None):
        self.root = root or os.path.join(HOME, ".codex/sessions")

    def available(self) -> bool:
        return os.path.isdir(self.root)

    def collect(self, limit: Optional[int] = None) -> List[Session]:
        if not self.available():
            return []
        files = sorted(glob.glob(os.path.join(self.root, "**", "rollout-*.jsonl"),
                                 recursive=True))
        if limit:
            files = files[-limit:]

        sessions = []
        for path in files:
            session_id = os.path.splitext(os.path.basename(path))[0]
            msgs = []
            cwd = ""
            started = ended = None

            try:
                with open(path, errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            j = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        ts = j.get("timestamp")
                        if ts:
                            started = started or ts
                            ended = ts

                        t = j.get("type")
                        p = j.get("payload") or {}

                        if t == "session_meta":
                            cwd = p.get("cwd") or cwd
                            continue

                        if t == "response_item" and p.get("type") == "message":
                            role = p.get("role", "")
                            content = _clean(p.get("content"))
                            if content and role in ("user", "assistant"):
                                msgs.append(Message(role=role, content=content, timestamp=ts))

                        elif t == "event_msg" and p.get("type") in ("user_message", "agent_message"):
                            # Fallback: capture turns if response_item missed them
                            content = _clean(p.get("message"))
                            if content:
                                role = "user" if p["type"] == "user_message" else "assistant"
                                # Avoid duplicates with response_item lines
                                if not msgs or msgs[-1].content[:80] != content[:80]:
                                    msgs.append(Message(role=role, content=content, timestamp=ts))
            except OSError:
                continue

            if not msgs:
                continue

            sessions.append(Session(
                id=session_id,
                tool=self.tool,
                messages=msgs,
                project=cwd,
                started=started,
                ended=ended,
                title=os.path.basename(os.path.dirname(path)),
            ))
        return sessions


# ---------------------------------------------------------------------------
# Hermes
# ---------------------------------------------------------------------------

class HermesCollector:
    tool = "hermes"

    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.join(HOME, ".hermes/state.db")

    def available(self) -> bool:
        return os.path.exists(self.db_path)

    def collect(self, limit: Optional[int] = None) -> List[Session]:
        if not self.available():
            return []
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        cur.execute("""
            SELECT id, display_name, title, cwd, started_at, ended_at, message_count
            FROM sessions ORDER BY started_at ASC
        """)
        rows = cur.fetchall()
        if limit:
            rows = rows[-limit:]

        sessions = []
        for row in rows:
            sid = row["id"]
            cur.execute("""
                SELECT role, content, timestamp
                FROM messages
                WHERE session_id = ?
                ORDER BY timestamp ASC
            """, (sid,))
            msgs = []
            for m in cur.fetchall():
                role = m["role"]
                if role not in ("user", "assistant"):
                    continue  # skip tool/system noise
                content = _clean(m["content"])
                if not content:
                    continue
                msgs.append(Message(
                    role=role,
                    content=content,
                    timestamp=_iso(m["timestamp"]),
                ))

            if not msgs:
                continue

            sessions.append(Session(
                id=sid,
                tool=self.tool,
                messages=msgs,
                project=row["cwd"] or "",
                started=_iso(row["started_at"]),
                ended=_iso(row["ended_at"]),
                title=row["title"] or row["display_name"] or "",
            ))
        con.close()
        return sessions


# ---------------------------------------------------------------------------

def all_collectors() -> List[Any]:
    return [OpenCodeCollector(), ClaudeCodeCollector(),
            CodexCollector(), HermesCollector()]
