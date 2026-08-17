#!/usr/bin/env bash
# Rezistif dokunmatik ekranı kalibre eder.
#
#   sudo ./install/calibrate-touch.sh
#
# Ekrana dört hedef çizilir; her birine sırayla bas. Sonuç
# /var/lib/zikplayer/touch-calibration.json dosyasına yazılır.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="/opt/zikplayer/venv"

warn() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; }

[[ $EUID -eq 0 ]] || { warn "root olarak çalıştır: sudo $0"; exit 1; }
[[ -x "$VENV_DIR/bin/python" ]] || { warn "Önce install.sh çalıştır"; exit 1; }

# Panel aynı framebuffer'a çizdiği için kalibrasyon süresince durdurulur.
RESTART=0
if systemctl is-active --quiet zikplayer-panel; then
  systemctl stop zikplayer-panel
  RESTART=1
fi

set +e
cd "$REPO_DIR/server"
env $(grep -v '^#' /etc/zikplayer/zikplayer.env | grep -v '^$' | xargs) \
  "$VENV_DIR/bin/python" -m zikplayer.panel.calibrate
STATUS=$?
set -e

[[ $RESTART -eq 1 ]] && systemctl start zikplayer-panel

exit $STATUS
