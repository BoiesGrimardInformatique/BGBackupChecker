#!/usr/bin/env bash
# Installe les unités systemd *utilisateur* : régénération du tableau toutes
# les 5 minutes, sans droits root, sans service réseau exposé.
set -euo pipefail
PROJET="$(cd "$(dirname "$0")/.." && pwd)"
UNITES="$HOME/.config/systemd/user"
mkdir -p "$UNITES"

sed "s|__PROJET__|$PROJET|g" "$PROJET/systemd/backup-monitor.service" \
  > "$UNITES/backup-monitor.service"
cp "$PROJET/systemd/backup-monitor.timer" "$UNITES/backup-monitor.timer"

systemctl --user daemon-reload
systemctl --user enable --now backup-monitor.timer
echo "Timer activé. Vérifier avec : systemctl --user list-timers backup-monitor.timer"
echo "Journal : journalctl --user -u backup-monitor.service -f"
