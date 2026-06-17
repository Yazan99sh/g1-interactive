#!/usr/bin/env bash
# One-shot environment setup for a fresh Linux host.
#   ./setup.sh            # core + panel deps into .venv (or conda env $CONDA_ENV)
#   ./setup.sh --with-sdk # also install unitree_sdk2_python from source
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
CONDA_ENV="${CONDA_ENV:-tv}"
WITH_SDK=0
for a in "$@"; do [ "$a" = "--with-sdk" ] && WITH_SDK=1; done

echo "== G1 Interactive setup =="

# Pick a Python environment: an existing conda env named $CONDA_ENV, else a local .venv.
if command -v conda >/dev/null 2>&1 && conda env list | awk '{print $1}' | grep -qx "$CONDA_ENV"; then
  echo "Using conda env: $CONDA_ENV"
  PY="conda run -n $CONDA_ENV python"
else
  if [ ! -d .venv ]; then echo "Creating venv at .venv"; python3 -m venv .venv; fi
  PY="$PROJECT_DIR/.venv/bin/python"
fi

echo "Upgrading pip + installing core requirements…"
$PY -m pip install --upgrade pip >/dev/null
$PY -m pip install -r requirements.txt
echo "Installing control-panel requirements…"
$PY -m pip install -r controlpanel/requirements.txt

if ! $PY -c "import unitree_sdk2py" >/dev/null 2>&1; then
  if [ "$WITH_SDK" = "1" ]; then
    echo "Installing unitree_sdk2_python (robot DDS SDK)…"
    TMP="$(mktemp -d)"
    git clone --depth 1 https://github.com/unitreerobotics/unitree_sdk2_python "$TMP/sdk"
    $PY -m pip install -e "$TMP/sdk"
  else
    echo "NOTE: unitree_sdk2py not installed — robot features will fall back to host."
    echo "      Install it with:  ./setup.sh --with-sdk"
    echo "      (or: git clone https://github.com/unitreerobotics/unitree_sdk2_python && pip install -e ./unitree_sdk2_python)"
  fi
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example — fill in keys (or use the panel's Environment tab)."
fi

cat <<EOF

NEXT STEPS:
  1) Set keys + DDS_INTERFACE in .env  (or the control panel -> Environment)
  2) $PY tools/doctor.py               # preflight checks
  3) Bring-up order: see RUNBOOK.md
  4) Start the control panel:  $PY -m controlpanel   ->  http://<host>:8800
EOF
