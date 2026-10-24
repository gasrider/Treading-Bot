#!/usr/bin/env bash
# =============================================================================
#  Trading-Bot: Vollautomatische Server-Installation (Ubuntu 22.04/24.04, Debian 12)
#
#  Auf einem frischen Server als root ausführen:
#    curl -fsSL https://raw.githubusercontent.com/gasrider/Treading-Bot/master/deploy/install.sh | bash
#
#  Optional vorher setzen (sonst automatisch):
#    DASHBOARD_PASSWORD  eigenes Dashboard-Passwort (mind. 12 Zeichen), sonst zufällig
#    DOMAIN              eigene Domain, sonst <server-ip>.sslip.io
#    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   für Handy-Benachrichtigungen
#    BRANCH              Git-Branch (Standard: master)
#
#  Das Skript kann gefahrlos erneut ausgeführt werden (bestehende .env/config bleiben).
# =============================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/gasrider/Treading-Bot.git}"
BRANCH="${BRANCH:-master}"
INSTALL_DIR="${INSTALL_DIR:-/opt/tradingbot}"
ACCESS_FILE=/root/tradingbot-zugang.txt
export DEBIAN_FRONTEND=noninteractive

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mFEHLER: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Bitte als root ausführen (oder mit: sudo bash install.sh)."
command -v apt-get >/dev/null || fail "Nur Ubuntu/Debian werden unterstützt."

step "System aktualisieren und Pakete installieren"
apt-get update -q
apt-get upgrade -yq -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold
apt-get install -yq git curl ca-certificates openssl ufw fail2ban unattended-upgrades nano
# Automatische Sicherheitsupdates des Betriebssystems
dpkg-reconfigure -f noninteractive unattended-upgrades

step "Auslagerungsspeicher prüfen (kleine Server brauchen ihn beim Bauen)"
if [ "$(swapon --show | wc -l)" -eq 0 ] && [ "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" -lt 3000000 ]; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "2 GB Swap angelegt."
fi

step "Docker installieren"
if ! command -v docker >/dev/null; then
    curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

step "Firewall einrichten (nur SSH, HTTP, HTTPS offen)"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
systemctl enable --now fail2ban >/dev/null 2>&1 || true

step "Bot-Code holen nach $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" fetch -q origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout -q "$BRANCH"
    git -C "$INSTALL_DIR" pull -q --ff-only origin "$BRANCH"
else
    git clone -q --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

step "Konfiguration anlegen"
[ -f config.yaml ] || cp config.example.yaml config.yaml
mkdir -p state logs

set_env() {  # set_env SCHLÜSSEL WERT – setzt/ersetzt eine Zeile in .env
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    grep -v "^${key}=" .env > "$tmp" || true
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
    cat "$tmp" > .env && rm -f "$tmp"
}
get_env() { grep "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-; }

NEW_ENV=0
if [ ! -f .env ]; then
    cp .env.example .env
    NEW_ENV=1
fi
chmod 600 .env

if [ -z "${DOMAIN:-}" ]; then
    DOMAIN="$(get_env DOMAIN)"
    if [ -z "$DOMAIN" ] || [ "$DOMAIN" = "localhost" ]; then
        IP="$(curl -4 -fsS --max-time 10 https://api.ipify.org || curl -4 -fsS --max-time 10 https://ifconfig.me)"
        [ -n "$IP" ] || fail "Öffentliche IP konnte nicht ermittelt werden. Bitte DOMAIN=... setzen."
        DOMAIN="${IP//./-}.sslip.io"
    fi
fi
set_env DOMAIN "$DOMAIN"

if [ "${DASHBOARD_PASSWORD:-}" = "HIER-DEIN-PASSWORT-EINTRAGEN" ]; then
    echo "WARNUNG: Platzhalter-Passwort erkannt – es wird ein zufälliges Passwort erzeugt."
    DASHBOARD_PASSWORD=""
fi
if [ -n "${DASHBOARD_PASSWORD:-}" ]; then
    [ "${#DASHBOARD_PASSWORD}" -ge 12 ] || fail "DASHBOARD_PASSWORD muss mindestens 12 Zeichen haben."
    [[ "$DASHBOARD_PASSWORD" =~ ^[A-Za-z0-9_.,:+=@!%*-]+$ ]] \
        || fail "DASHBOARD_PASSWORD: bitte nur Buchstaben, Ziffern und _ . , : + = @ ! % * - verwenden."
    set_env DASHBOARD_PASSWORD "$DASHBOARD_PASSWORD"
elif [ -z "$(get_env DASHBOARD_PASSWORD)" ]; then
    set_env DASHBOARD_PASSWORD "$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 20)"
fi
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] && set_env TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
[ -n "${TELEGRAM_CHAT_ID:-}" ] && set_env TELEGRAM_CHAT_ID "$TELEGRAM_CHAT_ID"

step "Verwaltungsbefehl 'tradingbot' und tägliches Auto-Update einrichten"
chmod +x deploy/*.sh
ln -sf "$INSTALL_DIR/deploy/tradingbot-cli.sh" /usr/local/bin/tradingbot
cat > /etc/systemd/system/tradingbot-update.service <<EOF
[Unit]
Description=Trading-Bot Update
After=docker.service network-online.target

[Service]
Type=oneshot
ExecStart=$INSTALL_DIR/deploy/update.sh
EOF
cat > /etc/systemd/system/tradingbot-update.timer <<'EOF'
[Unit]
Description=Trading-Bot täglich aktualisieren

[Timer]
OnCalendar=*-*-* 04:17:00
RandomizedDelaySec=15m
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now tradingbot-update.timer >/dev/null

step "Bot bauen und starten (dauert beim ersten Mal ein paar Minuten)"
docker compose up -d --build
docker image prune -f >/dev/null

step "Warte auf das Dashboard"
for _ in $(seq 1 30); do
    curl -fsS --max-time 3 http://127.0.0.1:8080/health >/dev/null 2>&1 && break
    sleep 2
done

USER_NAME="$(get_env DASHBOARD_USER)"; USER_NAME="${USER_NAME:-admin}"
cat > "$ACCESS_FILE" <<EOF
Trading-Bot Zugang
==================
Dashboard:  https://$DOMAIN
Benutzer:   $USER_NAME
Passwort:   $(get_env DASHBOARD_PASSWORD)

Verwaltung auf dem Server:  tradingbot help
Installationsordner:        $INSTALL_DIR
EOF
chmod 600 "$ACCESS_FILE"

TOKEN="$(get_env TELEGRAM_BOT_TOKEN)"; CHAT="$(get_env TELEGRAM_CHAT_ID)"
if [ -n "$TOKEN" ] && [ -n "$CHAT" ]; then
    curl -fsS --max-time 10 "https://api.telegram.org/bot${TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${CHAT}" \
        --data-urlencode "text=✅ Trading-Bot-Server ist eingerichtet: https://${DOMAIN}" >/dev/null || true
fi

printf '\n\033[1;32m%s\033[0m\n' "Fertig! Der Bot läuft (Modus laut config.yaml, Standard: paper)."
cat "$ACCESS_FILE"
echo
echo "Diese Zugangsdaten stehen auch in $ACCESS_FILE (Anzeige: cat $ACCESS_FILE)."
echo "Das HTTPS-Zertifikat wird beim ersten Aufruf automatisch geholt (kann ~1 Minute dauern)."
[ "$NEW_ENV" -eq 1 ] && echo "Tipp: 'tradingbot env' zum Eintragen von Telegram- bzw. später Börsen-Schlüsseln."
exit 0
