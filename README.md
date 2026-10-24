# 🤖 Krypto-Trading-Bot

Vollautomatischer Trading-Bot für Kryptowährungen (z. B. BTC/EUR, ETH/EUR).
Er läuft rund um die Uhr, kauft und verkauft selbstständig und schützt dein Kapital mit festen Risikoregeln.

> ⚠️ **Bitte zuerst lesen:** Kein Bot garantiert Gewinne. Die meisten Privatanleger verlieren beim Trading Geld.
> Dieser Bot ist auf **Kapitalschutz** ausgelegt, nicht auf schnelle Gewinne. Setze nur Geld ein, dessen
> Totalverlust du verkraften kannst. Starte **immer** mit Paper-Trading (virtuelles Geld).

---

## Inhalt
1. [Wie der Bot handelt](#1-wie-der-bot-handelt)
2. [Installation](#2-installation-auf-deinem-pc)
3. [Backtest](#3-schritt-1-backtest)
4. [Paper-Trading](#4-schritt-2-paper-trading-4-8-wochen)
5. [Telegram-Benachrichtigungen](#5-telegram-benachrichtigungen-empfohlen)
6. [24/7-Betrieb auf einem Server](#6-247-betrieb-auf-einem-server)
7. [Live gehen mit echtem Geld](#7-schritt-3-live-gehen-mit-echtem-geld)
8. [Überwachung und Not-Aus](#8-überwachung-und-not-aus)
9. [Steuern in Österreich](#9-steuern-in-österreich)
10. [Konfiguration anpassen](#10-konfiguration-anpassen)
11. [Fehlerbehebung](#11-fehlerbehebung)

---

## 1. Wie der Bot handelt

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
| Not-Aus | Datei `state/STOP` anlegen → alles wird verkauft, der Bot beendet sich |

Signale werden nur auf **abgeschlossenen** Kerzen berechnet. Es gibt also keine Fehlsignale durch Kerzen, die sich noch bewegen.

---

## 2. Installation auf deinem PC

**Voraussetzungen:** [Python 3.11 oder neuer](https://www.python.org/downloads/) (unter Windows bei der Installation „Add Python to PATH“ anhaken) und [Git](https://git-scm.com/downloads).

```bash
git clone https://github.com/gasrider/Treading-Bot.git
cd Treading-Bot

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

Konfigurationsdatei für Geheimnisse anlegen:
```bash
# Windows:
copy .env.example .env
# Mac/Linux:
cp .env.example .env
```

Funktionstest (sollte „14 passed“ melden):
```bash
python -m pytest -q
```

---

## 3. Schritt 1: Backtest

Teste die Strategie zuerst auf den Kursdaten der letzten 2 Jahre. Dafür brauchst du kein Konto.

```bash
python main.py backtest
```

Du siehst für jedes Paar unter anderem:
- **Rendite %** gegenüber **Buy & Hold %** (einfach kaufen und halten)
- **Max. Drawdown %**: der größte zwischenzeitliche Verlust. *Frag dich: Würde ich das aushalten?*
- **Trefferquote**, **Profit-Faktor** (über 1,3 ist gut) und Anzahl der Trades

Alle einzelnen Trades und die Kapitalkurve landen als CSV in `results/` und lassen sich in Excel öffnen.

> 💡 Ein Trendfolge-Bot verliert in Seitwärtsphasen oft mehrere kleine Trades hintereinander und verdient
> an wenigen großen Trends. Eine Trefferquote von 35–45 % ist dabei normal.

> ⚠️ **Überoptimierung vermeiden:** Wenn du Parameter so lange änderst, bis der Backtest toll aussieht,
> passt die Strategie nur auf die Vergangenheit. Ändere wenig und teste auf mehreren Paaren und Zeiträumen.

Offline-Demo ohne Internet: `python main.py backtest --synthetic`

---

## 4. Schritt 2: Paper-Trading (4–8 Wochen)

In `config.yaml` steht standardmäßig `mode: paper`. Der Bot handelt dann mit **echten Live-Kursen und virtuellen 1000 €**. Dafür brauchst du kein Konto und keine API-Schlüssel.

```bash
python main.py run       # Bot starten (läuft, bis du Strg+C drückst)
python main.py status    # in einem zweiten Fenster: Kontostand und Positionen
```

Lass ihn **mindestens 4–8 Wochen** laufen, am besten gleich 24/7 auf einem Server (siehe Abschnitt 6).
Erst wenn das Ergebnis ungefähr zum Backtest passt, geht es weiter.

---

## 5. Telegram-Benachrichtigungen (empfohlen)

Dann bekommst du jeden Kauf, Verkauf und Fehler aufs Handy.

1. In Telegram den Kontakt **@BotFather** öffnen, `/newbot` senden und den Anweisungen folgen.
2. Du bekommst einen **Token** (z. B. `123456:ABC-...`). Trage ihn in `.env` bei `TELEGRAM_BOT_TOKEN=` ein.
3. Schreib deinem neuen Bot eine beliebige Nachricht.
4. Öffne im Browser `https://api.telegram.org/bot<DEIN_TOKEN>/getUpdates` und suche `"chat":{"id":123456789`.
   Diese Zahl kommt in `TELEGRAM_CHAT_ID=`.
5. Starte den Bot neu. Du solltest „🤖 Bot gestartet“ erhalten.

---

## 6. 24/7-Betrieb auf einem Server

Dein PC müsste sonst Tag und Nacht laufen. Besser ist ein kleiner Cloud-Server (VPS), z. B. **Hetzner Cloud CX22** (ca. 4–5 €/Monat, Rechenzentrum in Deutschland/Finnland) mit Ubuntu 24.04. Ein Raspberry Pi geht auch.

### Variante A: Docker (empfohlen)
Mit dem Server per SSH verbinden (`ssh root@<SERVER-IP>`), dann:

```bash
# Docker installieren
curl -fsSL https://get.docker.com | sh

# Bot holen und einrichten
git clone https://github.com/gasrider/Treading-Bot.git
cd Treading-Bot
cp .env.example .env
nano .env                 # Telegram-Daten eintragen (später auch API-Keys)
mkdir -p state logs

# Starten (läuft im Hintergrund und startet nach Server-Neustart automatisch)
docker compose up -d --build

# Live-Log ansehen (Strg+C beendet nur die Anzeige)
docker compose logs -f

# Status
docker compose exec tradingbot python main.py status
```

Nach einer Änderung an `config.yaml`: `docker compose restart`.
Nach einem Code-Update: `git pull && docker compose up -d --build`.

### Variante B: systemd (z. B. Raspberry Pi)
Siehe die Anleitung in `deploy/tradingbot.service`.

**Server-Sicherheit:** SSH-Login nur mit Schlüssel, Passwort-Login abschalten, `apt update && apt upgrade` regelmäßig ausführen.

---

## 7. Schritt 3: Live gehen mit echtem Geld

Mach das **erst nach erfolgreichem Paper-Trading**.

### 7.1 Börsenkonto
Voreingestellt ist **Kraken**: EU-reguliert, EUR-Paare, SEPA-Einzahlung, gute API. Andere Börsen gehen auch (z. B. `bitvavo`), dann `exchange:` in `config.yaml` ändern.

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
In `.env`:
```
EXCHANGE_API_KEY=dein_key
EXCHANGE_API_SECRET=dein_secret
LIVE_TRADING_CONFIRM=JA
```
In `config.yaml`:
```yaml
mode: live
risk:
  min_order_value: 10   # an die Mindestorder der Börse anpassen
```
Danach neu starten (`docker compose up -d`).

**Wichtig für kleine Konten:** Bei 1 % Risiko und 200 € Kapital sind Positionen oft nur 20–60 € groß. Liegt eine Order unter dem Minimum der Börse (z. B. 0,0001 BTC), lässt der Bot den Trade aus und schreibt das ins Log.

**Hinweise:**
- Der Bot verwaltet **nur die Coins, die er selbst gekauft hat**. Eigene Bestände bleiben unberührt, solange du sie nicht in denselben Paaren manuell handelst.
- Paper- und Live-Zustand werden getrennt gespeichert (`state/paper_state.json` und `state/live_state.json`).
- Stops werden vom Bot überwacht, **nicht** als Order an der Börse hinterlegt. Ist der Bot offline, greift auch kein Stop. Deshalb gehören Server-Betrieb und Telegram-Fehlermeldungen dazu.

---

## 8. Überwachung und Not-Aus

| Aktion | Befehl |
|---|---|
| Status | `python main.py status` (Docker: `docker compose exec tradingbot python main.py status`) |
| Logs | `logs/bot.log` bzw. `docker compose logs -f` |
| **Not-Aus** (alles verkaufen und beenden) | `touch state/STOP` (Windows: leere Datei `STOP` im Ordner `state` anlegen) |
| Pausieren ohne Verkauf | `docker compose stop` (offene Positionen bleiben **ohne Stop-Überwachung**!) |
| Notbremse lösen | Erst die Ursache prüfen, dann `python main.py reset-halt` |

Nach einem Not-Aus `state/STOP` wieder löschen, bevor du neu startest.

**Routine:** täglich kurz Telegram anschauen, wöchentlich `status` prüfen und mit dem Backtest vergleichen.

---

## 9. Steuern in Österreich

*(Allgemeine Information, keine Steuerberatung.)*
- Gewinne aus Kryptowährungen, die nach dem 28.02.2021 angeschafft wurden, unterliegen dem **Sondersteuersatz von 27,5 %**.
- Jeder Verkauf von Krypto gegen Euro ist ein steuerlich relevanter Vorgang. Ein Bot erzeugt **viele** davon.
- Ausländische Börsen wie Kraken behalten in der Regel **keine KESt** ein. Du musst die Gewinne selbst in der Einkommensteuererklärung angeben (Beilage E1kv).
- Bewahre alle Trade-Historien auf (Börsen-Export und `closed_trades` in der State-Datei). Tools wie Blockpit helfen bei der Aufstellung.
- Am besten vorher kurz mit einem Steuerberater sprechen.

---

## 10. Konfiguration anpassen

Alle Einstellungen stehen kommentiert in `config.yaml`. Die wichtigsten:

- `symbols`: Handelspaare, z. B. `[BTC/EUR, ETH/EUR, SOL/EUR]`
- `timeframe`: `1h` (Standard), `4h` (weniger Trades, weniger Gebühren) oder `15m` (mehr Trades, mehr Rauschen)
- `risk.risk_per_trade`: **nicht über 0,02 (2 %)** gehen
- `strategy.*`: Parameter der Strategie. Nach jeder Änderung neu backtesten!

Projektaufbau:
```
main.py                  Befehle: backtest | run | status | reset-halt
config.yaml              Einstellungen
.env                     Geheimnisse (API-Keys, Telegram) – nie teilen!
tradingbot/
  strategy.py            Kauf-/Verkaufsregeln
  indicators.py          EMA, RSI, ATR
  risk.py                Positionsgröße, Tageslimit, Notbremse
  broker.py              Paper- und Live-Orderausführung
  bot.py                 Dauerbetrieb
  backtest.py            Historischer Test
  data.py                Kursdaten laden
tests/                   Automatische Tests (python -m pytest)
```

---

## 11. Fehlerbehebung

| Problem | Lösung |
|---|---|
| `Netzwerkproblem ...` im Log | Kurze Internet- oder Börsenstörung. Der Bot versucht es automatisch erneut. |
| `Live-Modus gesperrt` | `LIVE_TRADING_CONFIRM=JA` in `.env` setzen |
| `Order zu klein` | Mehr Kapital einsetzen oder `max_position_pct` erhöhen |
| `Invalid key` / `Permission denied` | Prüfe API-Key, Secret, Rechte und IP-Whitelist |
| Backtest lädt keine Daten | `backtest.data_exchange` auf eine andere Börse setzen (z. B. `kraken`, `bitvavo`) |
| Bot macht tagelang nichts | Das ist normal: Der Bot wartet auf einen Aufwärtstrend. Im Log steht bei jeder Kerze, was er sieht. |
