#!/usr/bin/env bash
# Holt neue Versionen von GitHub und startet den Bot neu – nur wenn es Änderungen gibt.
# Läuft täglich per systemd-Timer (tradingbot-update.timer) oder manuell: tradingbot update
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/update.log 2>&1
echo "=== $(date '+%F %T') Update-Prüfung"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git fetch -q origin "$BRANCH"
if [ "$(git rev-parse HEAD)" = "$(git rev-parse "origin/$BRANCH")" ] && [ "${1:-}" != "--force" ]; then
    echo "Keine Änderungen."
    exit 0
fi

git pull -q --ff-only origin "$BRANCH"
echo "Neue Version: $(git log -1 --format='%h %s')"
docker compose up -d --build
docker image prune -f >/dev/null
echo "Update abgeschlossen."
