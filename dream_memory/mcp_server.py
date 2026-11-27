"""
MCP server: expose crystallized memory to OpenCode, Claude Code, Codex and
Hermes as custom tool calls.

Pure Python 3.12 stdlib -- no `mcp` SDK dependency. The stdio transport is
newline-delimited JSON-RPC 2.0 (messages are single-line and MUST NOT contain
embedded newlines), which is all these four clients require.

CRITICAL: stdout is the protocol channel. Every log line goes to stderr, or
the client sees a malformed frame and drops the connection.

Tools
-----
  dream_recall    search crystallized memory (+ optional episodic evidence)
  dream_status    what is in memory right now
  dream_remember  contribute a memory back into the inbox (write-back)

`instructions` returned from `initialize` are injected into OpenCode's system
prompt verbatim, so they double as a zero-cost hint about the other tools.
"""

import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

from . import context as ctx
from . import inbox as inbox_mod

SERVER_NAME = "dream-memory"
SERVER_VERSION = "1.0.0"

# Protocol revisions we can speak. The client proposes one; we echo it when we
# know it, otherwise fall back to our default. OpenCode's `legacy` mode speaks
# up to 2025-11-25, so never claim anything newer than that.
PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
DEFAULT_PROTOCOL = "2025-06-18"

INSTRUCTIONS = (
    "Crystallized memory from the dreaming pipeline is available through the "
    "dream-memory tools. Call dream_recall(query) before re-deriving user "
    "preferences, project context, or lessons already learned -- it is cheaper "
    "and more accurate than exploring from scratch. Call dream_remember(...) "
    "to persist a durable fact, preference or lesson you discover, so future "
    "sessions inherit it. A bounded digest of the same memory is already "
    "present in your context if one was generated."
)

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "dream_recall",
        "description": (
            "Search the user's crystallized (curated, distilled) memory from "
            "past sessions. Returns facts, preferences, projects, lessons and "
            "tasks, ranked by relevance and importance. Use this before "
            "re-deriving anything about the user, their projects or their "
            "conventions. Set include_episodic=true to also return raw "
            "transcript snippets as evidence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "What to look for, e.g. 'pytest preferences'"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50,
                          "default": 8,
                          "description": "Max results per layer"},
                "include_episodic": {"type": "boolean", "default": False,
                                     "description": "Also search raw transcripts"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "dream_status",
        "description": (
            "Report the current state of local memory: episodic session count, "
            "latest dream, category breakdown, pending remembered entries, and "
            "where the always-on digest lives."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "dream_remember",
        "description": (
            "Persist a durable memory so future sessions inherit it. Use for "
            "stable facts, user preferences, project constraints, and lessons "
            "worth keeping -- NOT for transient task state. Entries are "
            "append-only and are folded into crystallized memory by the next "
            "dream, where they may be revised or dropped. They appear in the "
            "always-on digest immediately."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string",
                            "description": "The memory, as one self-contained sentence"},
                "category": {"type": "string",
                             "enum": list(inbox_mod.CATEGORIES),
                             "default": "fact"},
                "importance": {"type": "number", "minimum": 0, "maximum": 1,
                               "default": 0.6,
                               "description": "0=minor, 1=critical"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1,
                               "default": 0.7},
                "agent": {"type": "string",
                          "description": "Calling agent name (opencode, claude, codex, hermes)"},
            },
            "required": ["content"],
        },
    },
]


def log(msg: str) -> None:
    """stdout is the MCP channel -- everything diagnostic goes to stderr."""
    sys.stderr.write("[dream-memory] %s\n" % msg)
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# tool implementations
# ---------------------------------------------------------------------------

def _tool_recall(args: Dict[str, Any]) -> str:
    from .recall import search
    root = _root()
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    limit = int(args.get("limit") or 8)
    include_episodic = bool(args.get("include_episodic"))

    res = search(root, query, limit=limit)
    crystallized = res.get("crystallized") or []
    episodic = res.get("episodic") or [] if include_episodic else []

    lines: List[str] = []
    if crystallized:
        lines.append("CRYSTALLIZED MEMORY (curated, %d):" % len(crystallized))
        for e in crystallized:
            lines.append("  [%.2f] (%s, imp=%.1f) %s" % (
                e.get("score", 0), e.get("category", "?"),
                float(e.get("importance", 0)), e.get("content", "")))
    else:
        lines.append("CRYSTALLIZED MEMORY: no matches for %r." % query)

    if include_episodic:
        if episodic:
            lines.append("")
            lines.append("EPISODIC EVIDENCE (raw transcripts, %d):" % len(episodic))
            for s in episodic:
                lines.append("  [%.2f] %s · %s" % (
                    s.get("score", 0), s.get("tool", "?"),
                    (s.get("snippet") or "").strip()))
        else:
            lines.append("")
            lines.append("EPISODIC EVIDENCE: no matches.")

    # Pending write-backs are not yet in a dream, so surface them explicitly.
    from .dreaming import DreamStore
    pend = inbox_mod.pending(root, DreamStore(root))
    if pend:
        lines.append("")
        lines.append("PENDING (remembered, not yet dreamed, %d):" % len(pend))
        for r in pend:
            lines.append("  (%s) %s" % (r.get("category", "fact"),
                                        r.get("content", "")))

    return "\n".join(lines)


def _tool_status(_args: Dict[str, Any]) -> str:
    from .dreaming import DreamStore
    from .episodic import EpisodicStore
    root = _root()

    epi = EpisodicStore(root)
    stats = epi.stats()
    ds = DreamStore(root)
    last_id = ds.latest_id()
    art = ds.load(last_id) if last_id else None

    cats: Dict[str, int] = {}
    if art:
        for e in art.entries:
            cats[e.category] = cats.get(e.category, 0) + 1

    pend = inbox_mod.pending(root, ds)

    lines = [
        "DREAM MEMORY STATUS",
        "  root:          %s" % root,
        "  episodic:      %d sessions, %d messages" % (
            stats.get("sessions", 0), stats.get("messages", 0)),
        "  by tool:       %s" % (stats.get("by_tool") or {}),
        "  latest dream:  %s (%d entries)" % (
            last_id or "(none)", len(art.entries) if art else 0),
        "  dreams:        %s" % (", ".join(ds.dream_ids()) or "(none)"),
        "  categories:    %s" % (cats or {}),
        "  pending inbox: %d" % len(pend),
        "  digest:        %s" % ctx.canonical_path(root),
    ]
    return "\n".join(lines)


def _tool_remember(args: Dict[str, Any]) -> str:
    root = _root()
    content = str(args.get("content") or "").strip()
    if not content:
        raise ValueError("content is required")

    rec = inbox_mod.remember(
        root,
        content=content,
        category=str(args.get("category") or "fact"),
        importance=args.get("importance", 0.6),
        confidence=args.get("confidence", 0.7),
        agent=str(args.get("agent") or ""),
    )

    # Regenerate digests so the memory is live immediately, not next dream.
    from .dreaming import DreamStore
    ds = DreamStore(root)
    last_id = ds.latest_id()
    art = ds.load(last_id) if last_id else None
    extra = inbox_mod.pending_entries(root, ds)
    try:
        ctx.write_digests(root, art, extra=extra)
    except Exception as ex:  # never fail the tool because a target file moved
        log("digest refresh failed: %s" % ex)

    return ("Remembered as %s (%s, importance %.2f).\n"
            "It is in the always-on digest now and will be folded into "
            "crystallized memory by the next dream."
            % (rec["id"], rec["category"], rec["importance"]))


HANDLERS = {
    "dream_recall": _tool_recall,
    "dream_status": _tool_status,
    "dream_remember": _tool_remember,
}


def _root() -> str:
    return os.environ.get("DREAM_ROOT") or os.path.expanduser("~/.dream-memory")


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------

class ProtocolError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _pick_protocol(requested: Any) -> str:
    if isinstance(requested, str) and requested in PROTOCOL_VERSIONS:
        return requested
    return DEFAULT_PROTOCOL


def handle(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC message. Returns a response dict, or None for
    notifications (which must not be answered)."""
    method = msg.get("method")
    msg_id = msg.get("id")
    is_notification = "id" not in msg

    try:
        if method == "initialize":
            params = msg.get("params") or {}
            return _ok(msg_id, {
                "protocolVersion": _pick_protocol(
                    params.get("protocolVersion")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME,
                               "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            })

        if method in ("notifications/initialized", "notifications/cancelled"):
            return None

        if method == "ping":
            return _ok(msg_id, {})

        if method == "tools/list":
            return _ok(msg_id, {"tools": TOOLS})

        if method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            handler = HANDLERS.get(name)
            if handler is None:
                raise ProtocolError(-32602, "unknown tool: %s" % name)
            try:
                text = handler(args if isinstance(args, dict) else {})
                return _ok(msg_id, {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                })
            except Exception as ex:
                # Tool failures are results, not protocol errors: clients show
                # them to the model instead of tearing down the session.
                log("tool %s failed: %s" % (name, ex))
                return _ok(msg_id, {
                    "content": [{"type": "text",
                                 "text": "dream-memory error: %s" % ex}],
                    "isError": True,
                })

        if method in ("resources/list", "prompts/list", "resources/templates/list"):
            key = {"resources/list": "resources",
                   "prompts/list": "prompts",
                   "resources/templates/list": "resourceTemplates"}[method]
            return _ok(msg_id, {key: []})

        if is_notification:
            return None
        raise ProtocolError(-32601, "method not found: %s" % method)

    except ProtocolError as ex:
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": ex.code, "message": ex.message,
                          **({"data": ex.data} if ex.data is not None else {})}}
    except Exception as ex:
        if is_notification:
            log("notification error: %s\n%s" % (ex, traceback.format_exc()))
            return None
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32603, "message": str(ex)}}


def _ok(msg_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def main() -> int:
    log("starting (root=%s, pid=%d)" % (_root(), os.getpid()))
    stdin = sys.stdin
    stdout = sys.stdout

    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as ex:
            stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "parse error: %s" % ex},
            }) + "\n")
            stdout.flush()
            continue

        if not isinstance(msg, dict):
            continue

        try:
            resp = handle(msg)
        except Exception as ex:  # defensive: never let the loop die
            log("unhandled: %s\n%s" % (ex, traceback.format_exc()))
            resp = {"jsonrpc": "2.0", "id": msg.get("id"),
                    "error": {"code": -32603, "message": str(ex)}}

        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()

    log("stdin closed, exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
