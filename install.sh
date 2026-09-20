#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")" && pwd)"
venv="$repo_root/.venv"
skip_system="$(printenv SKIP_SYSTEM || true)"

command -v python3 >/dev/null || {
  echo "Python 3.10+ is required." >&2
  exit 1
}
command -v node >/dev/null || {
  echo "Node.js 18+ is required for OpenCLI." >&2
  exit 1
}

if [ ! -x "$venv/bin/python" ]; then
  python3 -m venv "$venv"
fi

"$venv/bin/python" -m pip install -r "$repo_root/requirements.txt"
"$venv/bin/python" -m pip install "https://github.com/Panniantong/agent-reach/archive/main.zip"

if [ "$skip_system" = "1" ]; then
  "$venv/bin/agent-reach" install --env=auto
else
  "$venv/bin/agent-reach" install --env=auto --system --channels=opencli,twitter,xiaohongshu
fi

echo
echo "Installed. Run:"
echo "  $venv/bin/python scripts/research_trip.py --help"
echo "  $venv/bin/agent-reach doctor --json"
echo
echo "Keep a logged-in Chrome session available for XiaoHongShu/OpenCLI sources."
