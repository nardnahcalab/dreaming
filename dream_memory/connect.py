"""
Register the dream-memory MCP server with all four agents.

Each agent is wired with its own official CLI where one exists (that keeps
unrelated config intact and matches the vendor's supported path). Hermes has
no registration CLI, so its YAML is edited directly with a backup first.

  opencode   opencode mcp add dream --global -- <cmd>
  claude     claude mcp add --scope user dream -- <cmd>
  codex      codex mcp add dream -- <cmd>
  hermes     ~/.hermes/config.yaml  ->  mcp_servers.dream   (text insert)

The registration command is always `python3 <abs>/dream.py mcp`, so the
server needs no installation step and runs on the same stdlib-only code the
rest of the pipeline uses.
"""

import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

AGENT_NAMES = ("opencode", "claude", "codex", "hermes")


def server_command() -> List[str]:
    """Absolute path to the MCP entry point (never depends on cwd)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dream_py = os.path.join(here, "dream.py")
    return [sys.executable, dream_py, "mcp"]


def _run(argv: List[str], timeout: int = 60) -> Tuple[bool, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError:
        return False, "%s: command not found" % argv[0]
    except subprocess.TimeoutExpired:
        return False, "%s: timed out" % argv[0]
    except Exception as ex:
        return False, str(ex)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        return False, out or ("exit %d" % proc.returncode)
    return True, out


def _find(binary: str) -> Optional[str]:
    return shutil.which(binary)


def _already(detail: str) -> bool:
    """True when a vendor CLI rejected the add because it is already there.

    Not a failure: the desired end state is already reached.
    """
    low = (detail or "").lower()
    return any(p in low for p in (
        "already exists", "already registered", "already configured",
        "already added", "already in use", "duplicate",
    ))


def _register(argv: List[str], agent: str) -> Dict[str, Any]:
    ok, out = _run(argv)
    if ok:
        return _ok(agent, out)
    if _already(out):
        return _ok(agent, "already registered")
    return _fail(f"{agent}: {out}")


# --- opencode --------------------------------------------------------------
def connect_opencode() -> Dict[str, Any]:
    cmd = _find("opencode")
    if not cmd:
        return _fail("opencode CLI not found on PATH")
    return _register([cmd, "mcp", "add", "dream", "--global", "--"]
                     + server_command(), "opencode")


# --- claude code -----------------------------------------------------------
def connect_claude() -> Dict[str, Any]:
    cmd = _find("claude")
    if not cmd:
        return _fail("claude CLI not found on PATH")
    return _register([cmd, "mcp", "add", "--scope", "user", "dream", "--"]
                     + server_command(), "claude")


# --- codex -----------------------------------------------------------------
def connect_codex() -> Dict[str, Any]:
    cmd = _find("codex")
    if not cmd:
        return _fail("codex CLI not found on PATH")
    res = _register([cmd, "mcp", "add", "dream", "--"]
                    + server_command(), "codex")
    if res["ok"]:
        return res
    out = res["detail"]
    # `codex mcp add` does not need auth, but surface the known
    # "refresh token revoked" failure clearly rather than as a generic error.
    if "refresh token" in out or "log out and sign in" in out:
        return _fail("codex: config write may have failed -- %s\n"
                     "         fix with: codex logout && codex login" % out)
    return res


# --- hermes ----------------------------------------------------------------
HERMES_CONFIG = os.path.expanduser("~/.hermes/config.yaml")

_HERMES_BLOCK = """mcp_servers:
  dream:
    command: {cmd}
    args:
{args}
    timeout: 60
    connect_timeout: 30
"""


def _hermes_args_block(cmd: List[str]) -> str:
    # `command` already carries cmd[0]; args must be the remainder only.
    return "\n".join("      - %s" % a for a in cmd[1:])


def connect_hermes(path: str = HERMES_CONFIG) -> Dict[str, Any]:
    if not os.path.exists(path):
        return _fail("hermes: %s not found" % path)

    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as ex:
        return _fail("hermes: cannot read config: %s" % ex)

    lines = text.splitlines()

    # Already registered? Find `dream:` nested under a top-level `mcp_servers:`.
    if _hermes_has_dream(lines):
        return _ok("hermes", "already registered in %s" % path)

    # Back up first -- this is the user's live agent config.
    backup = "%s.bak.dream_%s" % (path, time.strftime("%Y%m%d_%H%M%S"))
    try:
        shutil.copy2(path, backup)
    except OSError as ex:
        return _fail("hermes: backup failed (%s); refusing to edit" % ex)

    cmd = server_command()
    block = _HERMES_BLOCK.format(cmd=cmd[0], args=_hermes_args_block(cmd))

    # Insert under an existing top-level `mcp_servers:` if present, else append.
    insert_at = None
    mcp_idx = None
    for i, ln in enumerate(lines):
        if ln and not ln[0].isspace() and ln.rstrip() == "mcp_servers:":
            mcp_idx = i
            break
        if ln and not ln[0].isspace() and ln.split("#")[0].strip() and \
                not ln.startswith("---"):
            # remember the last top-level key position for fallback insertion
            pass

    if mcp_idx is not None:
        # our nested block, indented under the existing key
        nested = ["  dream:",
                  "    command: %s" % cmd[0]]
        nested += ["    args:"] + ["      - %s" % a for a in cmd[1:]]
        nested += ["    timeout: 60", "    connect_timeout: 30"]
        j = mcp_idx + 1
        while j < len(lines) and (not lines[j].strip()
                                  or lines[j][0].isspace()):
            j += 1
        lines[j:j] = nested
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(block.splitlines())

    new_text = "\n".join(lines) + "\n"

    # Validate our own insert is structurally sane before touching the file:
    # every added line must be either blank, a top-level key, or indented.
    added = [ln for ln in new_text.splitlines()
             if ln.strip() and not ln[0].isspace()
             and not ln.startswith(("mcp_servers:", "#", "---"))]
    stray = [ln for ln in added if ln.split(":")[0] not in _known_top_keys(text)]
    if stray:
        return _fail("hermes: refusing to write, would create top-level "
                     "keys: %s" % stray)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_text)
    except OSError as ex:
        return _fail("hermes: write failed: %s" % ex)

    return _ok("hermes", "registered in %s (backup: %s)"
               % (path, os.path.basename(backup)))


def _known_top_keys(text: str) -> set:
    keys = set()
    for ln in text.splitlines():
        if ln and not ln[0].isspace() and ":" in ln:
            keys.add(ln.split(":", 1)[0].strip())
    return keys


def _hermes_has_dream(lines: List[str]) -> bool:
    in_block = False
    for ln in lines:
        if ln and not ln[0].isspace():
            in_block = (ln.rstrip() == "mcp_servers:")
            continue
        if in_block and ln.strip().startswith("dream:"):
            return True
    return False


# --- helpers ---------------------------------------------------------------
def _ok(agent: str, detail: str) -> Dict[str, Any]:
    return {"agent": agent, "ok": True, "detail": detail.strip()}


def _fail(detail: str) -> Dict[str, Any]:
    return {"agent": "?", "ok": False, "detail": detail.strip()}


CONNECTORS = {
    "opencode": connect_opencode,
    "claude": connect_claude,
    "codex": connect_codex,
    "hermes": connect_hermes,
}


def connect(agents: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Register with the requested agents (default: all four)."""
    wanted = agents or list(AGENT_NAMES)
    results = []
    for name in wanted:
        fn = CONNECTORS.get(name)
        if fn is None:
            results.append({"agent": name, "ok": False,
                            "detail": "unknown agent"})
            continue
        try:
            results.append(fn())
        except Exception as ex:
            results.append({"agent": name, "ok": False, "detail": str(ex)})
    return results
