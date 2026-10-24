"""Einstiegspunkt.

  python main.py backtest              Strategie auf historischen Daten testen
  python main.py backtest --synthetic  Offline-Demo mit künstlichen Daten
  python main.py run                   Bot starten (paper oder live, laut config.yaml)
  python main.py status                Aktuellen Zustand anzeigen
  python main.py reset-halt            Not-Aus/Notbremse nach Prüfung wieder lösen
  python main.py dashboard             Web-Dashboard starten
"""
import argparse
import logging
import shutil
import time
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from tradingbot.backtest import run_backtest
from tradingbot.bot import TradingBot, status_text
from tradingbot.broker import LiveBroker, PaperBroker
from tradingbot.config import load_config, trading_costs
from tradingbot.control import RESUME_FILE
from tradingbot.data import fetch_history, make_exchange, synthetic
from tradingbot.markets import build_universe
from tradingbot.notifier import Notifier
from tradingbot.sources import MarketData, YahooSource
from tradingbot.state import StateStore


def setup_logging(filename="bot.log"):
    Path("logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    file_handler = RotatingFileHandler(f"logs/{filename}", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console])


def state_path(cfg) -> str:
    return f"state/{cfg['mode']}_state.json"


def cmd_backtest(cfg, args):
    out = Path("results")
    out.mkdir(exist_ok=True)
    if args.synthetic:
        instruments = {"SYNTHETISCH": None}
    else:
        instruments = build_universe(cfg, include_yahoo=args.alle)
    yahoo = YahooSource()
    summary = []
    for symbol, inst in instruments.items():
        try:
            if inst is None:
                df = synthetic(bars=8000)
            elif inst.source == "yahoo":
                print(f"Lade {symbol} ({inst.name}) {cfg['timeframe']} von Yahoo ...")
                df = yahoo.history(symbol, cfg["timeframe"])
                time.sleep(1)  # Yahoo schonen
            else:
                ex = make_exchange(cfg["backtest"]["data_exchange"])
                print(f"Lade {cfg['backtest']['days']} Tage {symbol} {cfg['timeframe']} von {ex.id} ...")
                df = fetch_history(ex, symbol, cfg["timeframe"], cfg["backtest"]["days"])
        except Exception as e:
            print(f"  {symbol}: keine Daten ({e})")
            continue
        if len(df) < 300:
            print(f"  {symbol}: zu wenige Kerzen ({len(df)}) – übersprungen")
            continue
        costs = trading_costs(cfg, inst.asset_class, "backtest") if inst else None
        result = run_backtest(df, cfg, costs)
        m = result.metrics
        summary.append((symbol, inst.class_label if inst else "-", m, df.index[0], df.index[-1]))
        if not args.alle:
            print(f"\n===== Backtest {symbol} ({df.index[0]:%Y-%m-%d} bis {df.index[-1]:%Y-%m-%d}) =====")
            for key, value in m.items():
                print(f"  {key:<18} {value}")
        name = symbol.replace("/", "-").replace("=", "").replace("^", "")
        result.trades.to_csv(out / f"trades_{name}.csv", index=False)
        result.equity.to_csv(out / f"equity_{name}.csv")
    if args.alle and summary:
        print("\n===== Rangliste aller Märkte (nach Rendite) =====")
        print(f"{'Markt':<10} {'Klasse':<10} {'Rendite %':>9} {'Halten %':>9} {'Max DD %':>9} {'Trades':>6} {'Treffer %':>9} {'PF':>5}")
        for symbol, label, m, _, _ in sorted(summary, key=lambda r: -r[2].get("Rendite %", 0)):
            print(f"{symbol:<10} {label:<10} {m.get('Rendite %', 0):>9} {m.get('Buy & Hold %', 0):>9} "
                  f"{m.get('Max. Drawdown %', 0):>9} {m.get('Trades', 0):>6} {m.get('Trefferquote %', '-'):>9} "
                  f"{m.get('Profit-Faktor', '-'):>5}")
        print("Hinweis: Yahoo liefert Stundenkerzen nur für ca. 2 Jahre; Gebühren wie in config.yaml.")
    print(f"\nDetails (alle Trades, Kapitalkurve) in: {out.resolve()}")


def cmd_run(cfg, args):
    secrets = cfg["secrets"]
    notifier = Notifier(secrets["telegram_token"], secrets["telegram_chat_id"])
    store = StateStore(state_path(cfg))
    state = store.load()
    if cfg["mode"] == "live":
        if not (secrets["api_key"] and secrets["api_secret"]):
            sys.exit("Live-Modus: EXCHANGE_API_KEY und EXCHANGE_API_SECRET fehlen in .env")
        if secrets["live_confirm"] != "JA":
            sys.exit("Live-Modus gesperrt: setze LIVE_TRADING_CONFIRM=JA in .env, wenn du sicher bist.")
        exchange = make_exchange(cfg["exchange"], secrets["api_key"], secrets["api_secret"])
        broker = LiveBroker(exchange, cfg["quote"])
    else:
        exchange = make_exchange(cfg["exchange"])
        p = cfg["paper"]
        broker = PaperBroker(exchange, state, p["starting_balance"], p["fee"], p["slippage"])
    # Aktien, ETFs, Rohstoffe, Devisen (Yahoo) vorerst nur im Paper-Modus
    yahoo = YahooSource() if cfg["mode"] == "paper" else None
    market = MarketData(exchange, yahoo, cfg["quote"])
    bot = TradingBot(cfg, exchange, broker, store, notifier, state, config_path=args.config, market=market)
    try:
        bot.run()
    except KeyboardInterrupt:
        print("Beendet.")


def cmd_status(cfg, args):
    print(f"Modus: {cfg['mode']}")
    print(status_text(StateStore(state_path(cfg)).load(), cfg["quote"]))


def cmd_reset_halt(cfg, args):
    Path("state").mkdir(exist_ok=True)
    RESUME_FILE.write_text("")
    print("Freigabe angefordert: Der Bot hebt Not-Aus/Notbremse beim nächsten Durchlauf auf "
          "(spätestens nach poll_seconds bzw. beim nächsten Start).")


def cmd_dashboard(cfg, args):
    from tradingbot.dashboard import serve

    serve(args.config, state_path(cfg))


def main():
    parser = argparse.ArgumentParser(description="Krypto-Trading-Bot")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    bt = sub.add_parser("backtest")
    bt.add_argument("--synthetic", action="store_true", help="künstliche Daten statt Börsendaten")
    bt.add_argument("--alle", action="store_true", help="alle Märkte testen (Aktien, ETFs, Rohstoffe, Devisen, Krypto)")
    sub.add_parser("run")
    sub.add_parser("status")
    sub.add_parser("reset-halt")
    sub.add_parser("dashboard")
    args = parser.parse_args()
    if not Path(args.config).exists() and Path("config.example.yaml").exists():
        shutil.copy("config.example.yaml", args.config)
        print(f"{args.config} aus config.example.yaml angelegt – dort kannst du alles anpassen.")
    cfg = load_config(args.config)
    setup_logging("dashboard.log" if args.command == "dashboard" else "bot.log")
    commands = {
        "backtest": cmd_backtest, "run": cmd_run, "status": cmd_status,
        "reset-halt": cmd_reset_halt, "dashboard": cmd_dashboard,
    }
    commands[args.command](cfg, args)


if __name__ == "__main__":
    main()
