#!/usr/bin/env python3
"""End-to-end MCP handshake test against `dream.py mcp` (repo copy).

Speaks NDJSON JSON-RPC over stdio exactly as the four agents do, and asserts
that stdout carries ONLY protocol frames (any stray log line would corrupt
the transport).
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DREAM_ROOT = "/tmp/opencode/dream-mcp-test"


def frame(m):
    return (json.dumps(m) + "\n").encode()


def main():
    env = dict(os.environ)
    env["DREAM_ROOT"] = DREAM_ROOT
    # Isolate agent-file writes too: dream_remember regenerates digests, and
    # without this the test would rewrite the real ~/.hermes/USER.md.
    env["DREAM_DIGEST_HOME"] = "/tmp/opencode/dream-digest-test"
    shutil.rmtree(env["DREAM_DIGEST_HOME"], ignore_errors=True)

    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "dream_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "dream_recall",
                    "arguments": {"query": "pytest preferences", "limit": 3}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "dream_remember",
                    "arguments": {"content": "MCP test memory entry",
                                  "category": "lesson",
                                  "importance": 0.8, "agent": "test"}}},
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
         "params": {"name": "dream_recall",
                    "arguments": {"query": "MCP test memory entry"}}},
        {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
         "params": {"name": "nope", "arguments": {}}},
    ]

    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "dream.py"), "mcp"],
        input=b"".join(frame(m) for m in msgs),
        capture_output=True, timeout=120, env=env, cwd=ROOT,
    )

    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")

    fails = []

    # 1. stdout must be pure JSON lines
    frames = []
    for i, line in enumerate(out.splitlines()):
        if not line.strip():
            continue
        try:
            frames.append(json.loads(line))
        except json.JSONDecodeError as ex:
            fails.append("stdout line %d is not JSON: %r (%s)" % (i, line[:80], ex))

    print("stderr (should be logs only):")
    for ln in err.splitlines():
        print("   ", ln)
    print()

    by_id = {f.get("id"): f for f in frames if "id" in f}

    # 2. initialize
    r = by_id.get(1, {}).get("result", {})
    if not r:
        fails.append("no initialize result")
    else:
        print("initialize ->", r.get("protocolVersion"),
              r.get("serverInfo"))
        if "instructions" not in r:
            fails.append("initialize missing instructions")
        if r.get("protocolVersion") not in ("2024-11-05", "2025-03-26",
                                            "2025-06-18", "2025-11-25"):
            fails.append("bad protocol version %r" % r.get("protocolVersion"))

    # 3. ping
    if by_id.get(2, {}).get("result") != {}:
        fails.append("ping did not return {}")

    # 4. tools/list
    tools = by_id.get(3, {}).get("result", {}).get("tools", [])
    names = sorted(t["name"] for t in tools)
    print("tools/list   ->", names)
    if names != ["dream_recall", "dream_remember", "dream_status"]:
        fails.append("unexpected tool set: %s" % names)
    for t in tools:
        if not t.get("description") or "inputSchema" not in t:
            fails.append("tool %s missing description/schema" % t.get("name"))

    # 5. dream_status
    st = by_id.get(4, {})
    st_text = _text(st)
    print("\ndream_status ->\n" + (st_text or "  (none)"))
    if not st_text or "DREAM MEMORY STATUS" not in st_text:
        fails.append("dream_status returned no status block")

    # 6. dream_recall
    rc_text = _text(by_id.get(5, {}))
    print("\ndream_recall ->\n" + (rc_text or "  (none)"))
    if not rc_text:
        fails.append("dream_recall returned nothing")

    # 7. dream_remember
    rm = by_id.get(6, {})
    rm_text = _text(rm)
    print("\ndream_remember ->", rm_text)
    if rm.get("result", {}).get("isError"):
        fails.append("dream_remember errored: %s" % rm_text)
    if not rm_text or "Remembered as" not in rm_text:
        fails.append("dream_remember bad ack: %r" % rm_text)

    # 8. recall should now surface the pending entry
    rc2 = _text(by_id.get(7, {}))
    print("\nrecall after remember ->\n" + (rc2 or "  (none)"))
    if not rc2 or "MCP test memory entry" not in rc2:
        fails.append("remembered memory not visible to recall")

    # 9. unknown tool -> JSON-RPC -32602 (spec: unknown tool name is an
    #    invalid-params error, distinct from a tool that ran and failed)
    unk = by_id.get(8, {})
    err = unk.get("error") or {}
    if err.get("code") != -32602:
        fails.append("unknown tool should return -32602, got: %r"
                     % json.dumps(unk)[:200])

    # 10. no response for the notification
    if any(f.get("id") == "notifications/initialized" for f in frames):
        fails.append("notification was answered")

    print("\n" + "=" * 60)
    if fails:
        print("FAILURES (%d):" % len(fails))
        for f in fails:
            print("  -", f)
        return 1
    print("ALL MCP CHECKS PASSED (%d frames)" % len(frames))
    return 0


def _text(resp):
    res = resp.get("result") or {}
    if res.get("isError"):
        return "ERROR: " + "".join(
            c.get("text", "") for c in res.get("content", []))
    return "".join(c.get("text", "") for c in res.get("content", []))


if __name__ == "__main__":
    sys.exit(main())
