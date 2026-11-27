#!/usr/bin/env bash
# run_dream.sh - one full memory cycle: sync all tools, then dream.
# Designed to be run by cron/systemd timer. See README_DREAM_MEMORY.md.
#
# Install (daily at 3am):
#   crontab -e
#   0 3 * * * /home/bala/experiments/dreaming/run_dream.sh >> ~/.dream-memory/dream_cron.log 2>&1
set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

export DREAM_ROOT="${DREAM_ROOT:-$HOME/.dream-memory}"
export DREAM_MODEL="${DREAM_MODEL:-meta-models/Muse-Glimmer-30B}"
export DREAM_DEH_SLUG="${DREAM_DEH_SLUG:-muse-glimmer}"

echo "=== $(date -Is) memory cycle start ==="

# Prefer the project venv (holds the dell-ai SDK); fall back to system python.
if [ -x "$DIR/.venv/bin/python" ]; then
  PY="$DIR/.venv/bin/python"
else
  PY="python3"
fi

"$PY" dream.py sync || echo "WARN: sync failed"
"$PY" dream.py dream --max-sessions "${DREAM_MAX_SESSIONS:-20}" || echo "WARN: dream failed"

# Health check: alert (log loudly) if high-importance memories were lost
"$PY" dream.py eval 2>&1 | grep -q "DEGRADATION WARNING" \
  && echo "ALERT: degradation detected - consider rollback (dream.py eval)"

echo "=== $(date -Is) memory cycle done ==="
