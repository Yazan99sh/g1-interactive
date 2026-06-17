#!/usr/bin/env bash
# Install the voice pipeline + control panel as systemd --user services.
# No root needed; they run as your user and survive logout (linger).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  PY="$PROJECT_DIR/.venv/bin/python"
else
  PY="$(command -v python3)"
fi
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

echo "Project: $PROJECT_DIR"
echo "Python : $PY"
for svc in g1-interactive g1-control-panel; do
  sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__PYTHON__|$PY|g" \
    "$PROJECT_DIR/deploy/$svc.service" > "$UNIT_DIR/$svc.service"
  echo "  installed $UNIT_DIR/$svc.service"
done

systemctl --user daemon-reload
systemctl --user enable g1-interactive.service g1-control-panel.service
loginctl enable-linger "$USER" 2>/dev/null || true

cat <<EOF

Done. Manage with:
  systemctl --user start g1-control-panel     # the panel  -> http://<host>:8800
  systemctl --user start g1-interactive       # the voice pipeline
  systemctl --user status g1-interactive
  journalctl --user -u g1-interactive -f      # live logs

(The control panel can also start/stop/restart g1-interactive for you.)
EOF
