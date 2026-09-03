#!/usr/bin/env bash
#
# Start the Label Jaano API.  Usage, from anywhere:   ~/dev/SIH/LabelJaano/run.sh
# Any extra arguments are passed straight to uvicorn, e.g.  ./run.sh --reload
#
# Why this script exists rather than a line in the README: both by-hand ways to
# start this backend have a trap.  `source .venv/bin/activate` silently breaks if
# the project folder is ever moved, because a virtualenv bakes absolute paths into
# its activate script and console-script shebangs; and a bare `uvicorn` can resolve
# to Anaconda's copy, whose interpreter has no fastapi installed.  Invoking the
# venv's own interpreter with `-m uvicorn` is immune to both.  That is most of what
# this does -- the rest is telling you the two things you otherwise have to look up
# every session: whether you are about to get real reads, and the URL the phone needs.

set -eo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
backend="$here/backend"
py="$backend/.venv/bin/python"
port="${PORT:-8000}"

cd "$backend"

if [ ! -x "$py" ]; then
  echo "error: no virtualenv at $backend/.venv" >&2
  echo >&2
  echo "Build it once (about two minutes):" >&2
  echo "  /opt/anaconda3/bin/python3 -m venv '$backend/.venv'" >&2
  echo "  '$py' -m pip install -r requirements-dev.txt 'google-genai>=1.0' 'pillow>=10.0'" >&2
  echo >&2
  echo "Use requirements-dev.txt, not requirements.txt -- the latter pulls paddlepaddle." >&2
  exit 1
fi

# Mock vs. real is the one thing worth knowing before the log scrolls past. The
# server prints its own `extraction mode=` line; this is the same fact, up front.
env_key="$(sed -n 's/^GEMINI_API_KEY=//p' .env 2>/dev/null | head -1 || true)"
if [ -n "${GEMINI_API_KEY:-}" ] || [ -n "$env_key" ]; then
  echo "gemini key   : found -> real optical reads"
  echo "               (the app's 'Use server mock pipeline' toggle must be OFF)"
else
  echo "gemini key   : MISSING from backend/.env -> canned values only."
  echo "               Every scan will return the same compliant label. Paste a key"
  echo "               on the GEMINI_API_KEY= line to get real reads."
fi

# The LAN address changes with the network, and a stale one in the app's settings is
# the single most common "the demo is broken" cause. So print today's every time.
ip="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
if [ -n "$ip" ]; then
  echo "phone url    : http://$ip:$port     <- app -> Settings -> API base URL"
else
  echo "phone url    : no Wi-Fi address found. A physical phone needs one;"
  echo "               http://localhost:$port is fine for a simulator."
fi
echo "stop         : Ctrl-C"
echo

exec "$py" -m uvicorn app.main:app --host 0.0.0.0 --port "$port" ${1+"$@"}
