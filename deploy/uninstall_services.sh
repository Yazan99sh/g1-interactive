#!/usr/bin/env bash
# Stop, disable and remove the systemd --user services.
set -euo pipefail

UNIT_DIR="$HOME/.config/systemd/user"
for svc in g1-interactive g1-control-panel; do
  systemctl --user stop "$svc.service" 2>/dev/null || true
  systemctl --user disable "$svc.service" 2>/dev/null || true
  rm -f "$UNIT_DIR/$svc.service"
  echo "removed $svc.service"
done
systemctl --user daemon-reload
echo "Done."
