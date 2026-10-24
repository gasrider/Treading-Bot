# 🤖 Krypto-Trading-Bot

Vollautomatischer Trading-Bot für Kryptowährungen (z. B. BTC/EUR, ETH/EUR). Er läuft **rund um die Uhr auf einem Cloud-Server**, kauft und verkauft selbstständig und schützt dein Kapital mit festen Risikoregeln.
Du steuerst ihn über ein **Web-Dashboard mit Passwort**, das du am Handy und am PC öffnen kannst.

> ⚠️ **Bitte zuerst lesen:** Kein Bot garantiert Gewinne. Die meisten Privatanleger verlieren beim Trading Geld.
> Dieser Bot ist auf **Kapitalschutz** ausgelegt, nicht auf schnelle Gewinne. Setze nur Geld ein, dessen
> Totalverlust du verkraften kannst. Er startet **immer** im Paper-Modus (virtuelles Geld).

---

## Inhalt
1. [Schnellstart: Server in 15 Minuten](#1-schnellstart-server-in-15-minuten)
2. [Das Dashboard](#2-das-dashboard)
3. [Telegram-Benachrichtigungen](#3-telegram-benachrichtigungen-empfohlen)
4. [Verwaltung auf dem Server](#4-verwaltung-auf-dem-server)
5. [Wie der Bot handelt](#5-wie-der-bot-handelt)
6. [Backtest](#6-backtest)
7. [Live gehen mit echtem Geld](#7-live-gehen-mit-echtem-geld)
8. [Sicherheit](#8-sicherheit)
9. [Steuern in Österreich](#9-steuern-in-österreich)
10. [Konfiguration anpassen](#10-konfiguration-anpassen)
11. [Lokal auf dem PC (optional)](#11-lokal-auf-dem-pc-optional)
12. [Fehlerbehebung](#12-fehlerbehebung)

---

## 1. Schnellstart: Server in 15 Minuten

Du brauchst einen kleinen Cloud-Server (VPS). Empfehlung: **[Hetzner Cloud](https://www.hetzner.com/cloud)**. Das kleinste Modell (2 vCPU, 4 GB RAM) kostet ca. 4–6 € im Monat, die Rechenzentren in Nürnberg/Falkenstein liegen nah an Österreich und rechnen nach Stunden ab.

### Variante A: Ganz ohne Kommandozeile (Cloud-Config)

1. Bei Hetzner registrieren und in der [Cloud Console](https://console.hetzner.cloud) ein **Projekt** anlegen.
2. **Server hinzufügen**:
   - Standort: **Nürnberg** oder **Falkenstein**
   - Image: **Ubuntu 24.04**
   - Typ: der kleinste **Shared vCPU**-Server (x86)
   - Netzwerk: **öffentliche IPv4 aktiviert** lassen
   - SSH-Key: optional (empfohlen, siehe [Sicherheit](#8-sicherheit))
3. Ganz unten bei **„Cloud config“** den Inhalt von [`deploy/cloud-init.yaml`](deploy/cloud-init.yaml) einfügen und **zuerst dein eigenes Passwort eintragen**:
   ```yaml
   export DASHBOARD_PASSWORD='MeinSicheresPasswort2026'
   ```
   (mind. 12 Zeichen; erlaubt sind Buchstaben, Ziffern und `_ . , : + = @ ! % * -`). Wenn du Telegram schon eingerichtet hast ([Abschnitt 3](#3-telegram-benachrichtigungen-empfohlen)), trage auch Token und Chat-ID ein.
4. **Erstellen & kaufen**. Der Server richtet sich jetzt **5–10 Minuten lang selbst ein**.
5. Die IP-Adresse des Servers steht in der Cloud Console, z. B. `203.0.113.10`. Dein Dashboard ist dann erreichbar unter:
   **`https://203-0-113-10.sslip.io`** (Punkte durch Bindestriche ersetzt)
   Benutzer: `admin`, Passwort: dein Passwort aus Schritt 3.

### Variante B: Per SSH (für jeden Ubuntu/Debian-Server)

Server mit Ubuntu 24.04 erstellen, dann verbinden und einen Befehl ausführen:
```bash
ssh root@<SERVER-IP>
curl -fsSL https://raw.githubusercontent.com/gasrider/Treading-Bot/master/deploy/install.sh | bash
```
Am Ende zeigt das Skript Dashboard-Adresse, Benutzer und ein **zufällig erzeugtes Passwort** an. Später kannst du es jederzeit mit `tradingbot access` wieder anzeigen.
Ein eigenes Passwort setzt du so: `curl -fsSL ... | DASHBOARD_PASSWORD='DeinPasswort123' bash`

### Was das Installations-Skript automatisch macht
- installiert System-Updates und **automatische Sicherheitsupdates**
- richtet die **Firewall** ein (nur SSH, HTTP und HTTPS offen) und **fail2ban** gegen Passwort-Angriffe
- installiert **Docker** und startet drei Dienste: den **Bot**, das **Dashboard** und **Caddy**, das automatisch ein **HTTPS-Zertifikat** holt
- richtet ein **tägliches Auto-Update** ein (04:17 Uhr): Neue Versionen aus diesem Repo werden automatisch eingespielt
- legt den Verwaltungsbefehl **`tradingbot`** an
- startet alles nach einem Server-Neustart automatisch wieder

Der Bot startet im **Paper-Modus**: echte Kurse, virtuelle 1000 €. Lass ihn so **mindestens 4–8 Wochen** laufen.

> 💡 **Eigene Domain?** Wenn du z. B. `bot.meinname.at` hast: einen DNS-A-Eintrag auf die Server-IP setzen, dann
> `tradingbot env` → `DOMAIN=bot.meinname.at` eintragen und speichern. Caddy holt sich das Zertifikat automatisch.

---

## 2. Das Dashboard

Unter `https://<deine-adresse>` siehst du nach dem Login:

- **Status**: läuft / gestoppt / keine Antwort, dazu der Modus (PAPER oder LIVE)
- **Kapital**, Höchststand, realisierter Gewinn, Trefferquote
- **Kapitalverlauf** als Grafik
- **Offene Positionen** mit Stop und Ziel sowie die **letzten Trades**
- die letzten **Log-Zeilen**
- **🛑 NOT-AUS**: verkauft sofort alles und stoppt den Handel. Danach erscheint **▶️ Handel wieder freigeben**.

Die Seite aktualisiert sich jede Minute und ist für das Handy optimiert. Tipp: Im Handy-Browser „Zum Startbildschirm hinzufügen“.

---

## 3. Telegram-Benachrichtigungen (empfohlen)

Du bekommst jeden Kauf und Verkauf, jeden Fehler, die Notbremse und eine **Warnung, wenn der Bot nicht mehr antwortet** aufs Handy.

1. In Telegram den Kontakt **@BotFather** öffnen, `/newbot` senden und den Anweisungen folgen.
2. Du bekommst einen **Token** (z. B. `123456:ABC-...`).
3. Schreib deinem neuen Bot eine beliebige Nachricht.
4. Öffne im Browser `https://api.telegram.org/bot<DEIN_TOKEN>/getUpdates` und suche `"chat":{"id":123456789`. Diese Zahl ist deine **Chat-ID**.
5. Auf dem Server `tradingbot env` ausführen, `TELEGRAM_BOT_TOKEN=` und `TELEGRAM_CHAT_ID=` ausfüllen, mit Strg+O speichern und mit Strg+X schließen. Die Dienste starten automatisch neu, und du solltest „🤖 Bot gestartet“ erhalten.

---

## 4. Verwaltung auf dem Server

Per SSH verbinden (`ssh root@<SERVER-IP>`), dann:

| Befehl | Was passiert |
|---|---|
| `tradingbot status` | Zustand der Dienste, Kontostand, Positionen |
| `tradingbot logs` | Live-Log (Strg+C beendet nur die Anzeige) |
| `tradingbot stop` | **NOT-AUS**: alles verkaufen, Handel stoppen |
| `tradingbot resume` | Handel nach Not-Aus/Notbremse wieder freigeben |
| `tradingbot config` | Strategie/Risiko bearbeiten (`config.yaml`), danach automatischer Neustart |
| `tradingbot env` | Passwörter und Schlüssel bearbeiten (`.env`), danach automatischer Neustart |
| `tradingbot backtest` | Backtest direkt auf dem Server |
| `tradingbot update` | Neueste Version sofort holen (passiert sonst täglich automatisch) |
| `tradingbot access` | Dashboard-Adresse und Zugangsdaten anzeigen |
| `tradingbot pause` / `start` | Bot anhalten/fortsetzen (**ohne** Verkauf; dann gibt es keine Stop-Überwachung!) |

Alles liegt in `/opt/tradingbot`. Der Zustand (Kontostand, Positionen, Trades) steht in `state/`, die Logs in `logs/`.
Deine `config.yaml` und `.env` werden von Updates **nie überschrieben**.

---

## 5. Wie der Bot handelt

**Strategie: Trendfolge** (nur Kaufen und Verkaufen, kein Hebel, keine Leerverkäufe)

| Regel | Bedeutung |
|---|---|
| **Einstieg** | Die schnelle EMA (20) kreuzt die langsame EMA (50) nach oben **und** der Kurs liegt über der EMA 200 (Aufwärtstrend) **und** der RSI ist unter 70 (nicht überkauft). |
| **Stop-Loss** | Einstieg − 2 × ATR (passt sich an die Schwankungsbreite an). |
| **Take-Profit** | Einstieg + 4 × ATR (Gewinn doppelt so groß wie das Risiko). |
| **Nachziehender Stop** | Steigt der Kurs, wird der Stop nachgezogen (Höchstkurs − 2,5 × ATR), um Gewinne zu sichern. |
| **Trendwende** | Kreuzt die schnelle EMA nach unten, wird verkauft. |

**Risikomanagement (das Wichtigste):**

| Schutz | Standard |
|---|---|
| Max. Verlust pro Trade | **1 %** des Kontos. Die Positionsgröße wird automatisch so berechnet. |
| Max. Positionsgröße | 30 % des Kontos |
| Max. offene Positionen | 2 |
| Tagesverlust-Limit | −3 % → keine neuen Trades bis zum nächsten Tag |
| **Notbremse** | −15 % vom Höchststand → alles wird verkauft, der Bot stoppt |
| **Not-Aus** | Knopf im Dashboard oder `tradingbot stop` |
| **Watchdog** | Gibt der Bot 15 Minuten kein Lebenszeichen, kommt eine Telegram-Warnung. |

Signale werden nur auf **abgeschlossenen** Kerzen berechnet. Es gibt also keine Fehlsignale durch Kerzen, die sich noch bewegen.

---

## 6. Backtest

Teste die Strategie auf den Kursdaten der letzten 2 Jahre, am einfachsten direkt auf dem Server mit `tradingbot backtest`.

Du siehst für jedes Paar unter anderem:
- **Rendite %** gegenüber **Buy & Hold %** (einfach kaufen und halten)
- **Max. Drawdown %**: der größte zwischenzeitliche Verlust. *Frag dich: Würde ich das aushalten?*
- **Trefferquote**, **Profit-Faktor** (über 1,3 ist gut) und Anzahl der Trades

> 💡 Ein Trendfolge-Bot verliert in Seitwärtsphasen oft mehrere kleine Trades hintereinander und verdient
> an wenigen großen Trends. Eine Trefferquote von 35–45 % ist dabei normal.

> ⚠️ **Überoptimierung vermeiden:** Wenn du Parameter so lange änderst, bis der Backtest toll aussieht,
> passt die Strategie nur auf die Vergangenheit. Ändere wenig und teste auf mehreren Paaren und Zeiträumen.

---

## 7. Live gehen mit echtem Geld

Mach das **erst nach 4–8 Wochen erfolgreichem Paper-Trading**, wenn das Ergebnis ungefähr zum Backtest passt.

### 7.1 Börsenkonto
Voreingestellt ist **Kraken**: EU-reguliert, EUR-Paare, SEPA-Einzahlung, gute API. Andere Börsen gehen auch (z. B. `bitvavo`), dann `exchange:` in der Konfiguration ändern.

1. Konto auf kraken.com eröffnen und verifizieren (Ausweis, Wohnsitz).
2. EUR per SEPA-Überweisung einzahlen. **Starte klein: 100–300 €.**

### 7.2 API-Schlüssel erstellen
Kraken → *Settings → API → Create API Key*:

| Recht | Einstellung |
|---|---|
| Query Funds | ✅ |
| Query Open Orders & Trades | ✅ |
| Query Closed Orders & Trades | ✅ |
| Create & Modify Orders | ✅ |
| **Withdraw Funds** | ❌ **NIEMALS aktivieren** |
| IP-Whitelist | ✅ Nur die IP deines Servers eintragen |

So kann selbst ein gestohlener Schlüssel kein Geld abheben.

### 7.3 Bot umstellen
1. `tradingbot env` ausführen und eintragen:
   ```
   EXCHANGE_API_KEY=dein_key
   EXCHANGE_API_SECRET=dein_secret
   LIVE_TRADING_CONFIRM=JA
   ```
2. `tradingbot config` ausführen und `mode: paper` auf `mode: live` ändern.
3. Im Dashboard steht jetzt **LIVE – echtes Geld** (rot).

**Wichtig für kleine Konten:** Bei 1 % Risiko und 200 € Kapital sind Positionen oft nur 20–60 € groß. Liegt eine Order unter dem Minimum der Börse (z. B. 0,0001 BTC), lässt der Bot den Trade aus und schreibt das ins Log.

**Hinweise:**
- Der Bot verwaltet **nur die Coins, die er selbst gekauft hat**. Eigene Bestände bleiben unberührt, solange du sie nicht in denselben Paaren manuell handelst.
- Paper- und Live-Zustand werden getrennt gespeichert (`state/paper_state.json` und `state/live_state.json`).
- Stops werden vom Bot überwacht, **nicht** als Order an der Börse hinterlegt. Ist der Bot offline, greift auch kein Stop. Deshalb gibt es Watchdog und Telegram-Warnung.

---

## 8. Sicherheit

Schon eingebaut:
- Das Dashboard ist nur über **HTTPS** und mit **Passwort** erreichbar. Nach 10 Fehlversuchen wird die IP 15 Minuten gesperrt.
- Dashboard und Webserver bekommen die **Börsen-Schlüssel nicht** zu sehen, nur der Bot-Container hat sie.
- Die Firewall lässt nur SSH, HTTP und HTTPS durch, fail2ban blockt SSH-Angreifer, Sicherheitsupdates laufen automatisch.
- `.env` ist nur für root lesbar und wird nie zu GitHub hochgeladen.

Empfohlen:
- **SSH-Key statt Passwort:** Beim Erstellen des Servers bei Hetzner einen SSH-Key hinterlegen ([Anleitung von Hetzner](https://community.hetzner.com/tutorials/howto-ssh-key/de)).
- **2-Faktor-Login** bei Hetzner, Kraken, GitHub und Telegram aktivieren.
- **API-Schlüssel ohne Auszahlungsrecht** und mit IP-Whitelist (siehe 7.2).
- **Updates kommen aus diesem GitHub-Repo.** Wer Schreibzugriff auf das Repo hat, kann Code auf deinen Server bringen. Schütze deinen GitHub-Account deshalb mit 2FA.
  Wenn du keine automatischen Updates willst: `systemctl disable --now tradingbot-update.timer`

---

## 9. Steuern in Österreich

*(Allgemeine Information, keine Steuerberatung.)*
- Gewinne aus Kryptowährungen, die nach dem 28.02.2021 angeschafft wurden, unterliegen dem **Sondersteuersatz von 27,5 %**.
- Jeder Verkauf von Krypto gegen Euro ist ein steuerlich relevanter Vorgang. Ein Bot erzeugt **viele** davon.
- Ausländische Börsen wie Kraken behalten in der Regel **keine KESt** ein. Du musst die Gewinne selbst in der Einkommensteuererklärung angeben (Beilage E1kv).
- Bewahre alle Trade-Historien auf (Börsen-Export und `closed_trades` in `state/live_state.json`). Tools wie Blockpit helfen bei der Aufstellung.
- Am besten vorher kurz mit einem Steuerberater sprechen.

---

## 10. Konfiguration anpassen

Mit `tradingbot config` bearbeitest du die Einstellungen. Alle Optionen sind dort kommentiert, die Vorlage ist [`config.example.yaml`](config.example.yaml). Die wichtigsten:

- `symbols`: Handelspaare, z. B. `[BTC/EUR, ETH/EUR, SOL/EUR]`
- `timeframe`: `1h` (Standard), `4h` (weniger Trades, weniger Gebühren) oder `15m` (mehr Trades, mehr Rauschen)
- `risk.risk_per_trade`: **nicht über 0,02 (2 %)** gehen
- `strategy.*`: Parameter der Strategie. Nach jeder Änderung neu backtesten!

Projektaufbau:
```
main.py                  Befehle: backtest | run | status | reset-halt | dashboard
config.example.yaml      Vorlage – wird beim ersten Start zu config.yaml kopiert
.env.example             Vorlage für Passwörter/Schlüssel (.env)
docker-compose.yml       Bot + Dashboard + Caddy (HTTPS)
deploy/
  install.sh             Vollautomatische Server-Installation
  cloud-init.yaml        Für die Einrichtung ohne SSH (Hetzner "Cloud config")
  tradingbot-cli.sh      Verwaltungsbefehl "tradingbot"
  update.sh              Auto-Update (täglich per systemd-Timer)
  Caddyfile              HTTPS-Webserver
tradingbot/
  strategy.py            Kauf-/Verkaufsregeln
  indicators.py          EMA, RSI, ATR
  risk.py                Positionsgröße, Tageslimit, Notbremse
  broker.py              Paper- und Live-Orderausführung
  bot.py                 Dauerbetrieb, Not-Aus, Lebenszeichen
  dashboard.py           Web-Dashboard + Watchdog
  backtest.py            Historischer Test
  data.py                Kursdaten laden
tests/                   Automatische Tests (laufen bei jedem Push auf GitHub)
```

---

## 11. Lokal auf dem PC (optional)

Zum Ausprobieren oder Entwickeln brauchst du [Python 3.11+](https://www.python.org/downloads/) (unter Windows „Add Python to PATH“ anhaken) und [Git](https://git-scm.com/downloads):

```bash
git clone https://github.com/gasrider/Treading-Bot.git
cd Treading-Bot
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Mac/Linux
pip install -r requirements.txt

python -m pytest -q                       # Funktionstest
python main.py backtest                   # Backtest
python main.py backtest --synthetic       # Offline-Demo ohne Internet
python main.py run                        # Bot im Paper-Modus (Strg+C beendet)
python main.py status                     # Zustand anzeigen
```

Mit Docker Desktop geht auch der komplette Stack inklusive Dashboard: `.env.example` nach `.env` kopieren, `DASHBOARD_PASSWORD` setzen, `config.example.yaml` nach `config.yaml` kopieren und dann `docker compose up -d --build`. Das Dashboard ist danach unter `https://localhost` erreichbar (Zertifikatswarnung bestätigen).

---

## 12. Fehlerbehebung

| Problem | Lösung |
|---|---|
| Dashboard nicht erreichbar | 10 Minuten nach dem Erstellen nochmal versuchen. Dann per SSH `tradingbot status` und `cat /var/log/tradingbot-install.log` prüfen. |
| Browser warnt vor dem Zertifikat | Das erste Zertifikat kann 1–2 Minuten dauern. Prüfe, ob Port 80/443 in einer Hetzner-Firewall offen ist. |
| Passwort vergessen | Per SSH: `tradingbot access` |
| Status „Keine Antwort“ | `tradingbot logs` ansehen, ggf. `tradingbot restart` |
| `Netzwerkproblem ...` im Log | Kurze Internet- oder Börsenstörung. Der Bot versucht es automatisch erneut. |
| `Live-Modus gesperrt` | `LIVE_TRADING_CONFIRM=JA` per `tradingbot env` setzen |
| `Order zu klein` | Mehr Kapital einsetzen oder `max_position_pct` erhöhen |
| `Invalid key` / `Permission denied` | Prüfe API-Key, Secret, Rechte und IP-Whitelist |
| Backtest lädt keine Daten | `backtest.data_exchange` auf eine andere Börse setzen (z. B. `kraken`, `bitvavo`) |
| Bot macht tagelang nichts | Das ist normal: Der Bot wartet auf einen Aufwärtstrend. Im Log steht bei jeder Kerze, was er sieht. |
| Update-Protokoll | `/opt/tradingbot/logs/update.log` |
