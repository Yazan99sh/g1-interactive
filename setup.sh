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

# Never run under sudo/root — this installs into YOUR user's Python env. sudo
# discards your conda env and creates root-owned files.
if [ "$(id -u)" = "0" ]; then
  echo "ERROR: don't run setup.sh with sudo." >&2
  echo "       Run it as your normal user (activate your conda env first if you use one)." >&2
  exit 1
fi

echo "== G1 Interactive setup =="

# Pick a Python env, in order of preference:
#   1) a conda env you've already activated (CONDA_DEFAULT_ENV, not base)
#   2) a conda env named $CONDA_ENV that exists
#   3) a local .venv (created if missing, repaired if a previous run left it broken)
if [ -n "${CONDA_DEFAULT_ENV:-}" ] && [ "${CONDA_DEFAULT_ENV}" != "base" ]; then
  echo "Using active conda env: $CONDA_DEFAULT_ENV"
  PY="python"
elif command -v conda >/dev/null 2>&1 && conda env list | awk '{print $1}' | grep -qx "$CONDA_ENV"; then
  echo "Using conda env: $CONDA_ENV"
  PY="conda run -n $CONDA_ENV python"
else
  if [ -d .venv ] && ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    echo "Removing broken .venv from a previous run"; rm -rf .venv
  fi
  if [ ! -d .venv ]; then
    echo "Creating venv at .venv"
    python3 -m venv .venv 2>/dev/null || {
      echo "ERROR: 'python3 -m venv' failed — install it (sudo apt install python3-venv)" >&2
      echo "       or activate a conda env and re-run." >&2
      exit 1
    }
  fi
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
  3) System audio lib for the mic/speaker:  sudo apt install -y libportaudio2
  4) Bring-up order: see RUNBOOK.md
  5) Start the control panel:  $PY -m controlpanel   ->  http://<host>:8800
EOF
