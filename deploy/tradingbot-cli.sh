#!/usr/bin/env bash
# Verwaltungsbefehl für den Server. Wird als /usr/local/bin/tradingbot verlinkt.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

case "${1:-help}" in
    status)
        docker compose ps --format 'table {{.Name}}\t{{.Status}}'
        echo
        docker compose exec -T bot python main.py status
        ;;
    logs)       docker compose logs -f --tail 100 bot ;;
    stop)
        read -r -p "NOT-AUS: alle Positionen verkaufen und Handel stoppen? (ja/nein) " answer
        [ "$answer" = "ja" ] || { echo "Abgebrochen."; exit 0; }
        date -u +%FT%TZ > state/STOP
        echo "Not-Aus angefordert – wird innerhalb ~1 Minute ausgeführt (Status: tradingbot status)."
        ;;
    resume)
        date -u +%FT%TZ > state/RESUME
        echo "Freigabe angefordert – der Bot handelt ab dem nächsten Durchlauf wieder."
        ;;
    restart)    docker compose restart ;;
    pause)      docker compose stop bot && echo "Bot pausiert. ACHTUNG: offene Positionen haben jetzt keine Stop-Überwachung!" ;;
    start)      docker compose up -d ;;
    config)     "${EDITOR:-nano}" config.yaml && docker compose restart bot dashboard ;;
    env)        "${EDITOR:-nano}" .env && docker compose up -d ;;
    backtest)   docker compose run --rm bot python main.py backtest ;;
    update)     ./deploy/update.sh --force && tail -n 5 logs/update.log ;;
    access)     cat /root/tradingbot-zugang.txt ;;
    *)
        cat <<'EOF'
tradingbot <befehl>

  status    Container- und Kontostatus anzeigen
  logs      Live-Log des Bots (Strg+C beendet die Anzeige)
  stop      NOT-AUS: alles verkaufen, Handel stoppen
  resume    Handel nach Not-Aus/Notbremse wieder freigeben
  restart   Alle Dienste neu starten
  pause     Bot anhalten (ohne zu verkaufen!) – start setzt fort
  start     Alle Dienste starten
  config    Strategie/Risiko bearbeiten (config.yaml), danach Neustart
  env       Passwörter/Schlüssel bearbeiten (.env), danach Neustart
  backtest  Backtest auf dem Server ausführen
  update    Neueste Version von GitHub holen und neu starten
  access    Dashboard-Adresse und Zugangsdaten anzeigen
EOF
        ;;
esac
