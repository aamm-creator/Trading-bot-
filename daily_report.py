"""Send daily trading bot summary via Telegram."""
import json
import csv
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
import urllib.request

TELEGRAM_TOKEN = "8474324608:AAF89bsm0o4FFduao2DIaIRcQYlrnpytv_Y"
CHAT_ID = "8317437816"
BASE = Path("/opt/Trading-bot-/data")

BOTS = [
    {"name": "Steady Eddie", "emoji": "shield", "trades_file": "steady_eddie_trades.csv", "state_file": "steady_eddie_state.json"},
    {"name": "Loco Mode", "emoji": "fire", "trades_file": "loco_mode_trades.csv", "state_file": "loco_mode_state.json"},
]


def send_telegram(text):
    url = "https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_TOKEN)
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)


def get_balance():
    try:
        sys.path.insert(0, "/opt/Trading-bot-")
        from crypto_bot.config import load_config
        import ccxt
        cfg = load_config("/opt/Trading-bot-/config_prod_steady_eddie.yaml")
        ex = ccxt.kraken({"apiKey": cfg.exchange.api_key, "secret": cfg.exchange.api_secret})
        balance = ex.fetch_balance()
        return float(balance.get("EUR", {}).get("total", 0))
    except Exception as e:
        print("Balance error: {}".format(e))
        return 0.0


def read_trades(filepath):
    if not filepath.exists():
        return []
    with open(filepath, "r") as f:
        return list(csv.DictReader(f))


def bot_stats(bot, today, yesterday):
    trades = read_trades(BASE / bot["trades_file"])

    total_trades = len(trades)
    total_pnl = sum(float(t.get("net_pnl", 0)) for t in trades)

    today_trades = [t for t in trades if t.get("closed", "")[:10] == today or t.get("closed", "")[:10] == yesterday]
    today_count = len(today_trades)
    today_pnl = sum(float(t.get("net_pnl", 0)) for t in today_trades)

    wins = [t for t in trades if float(t.get("net_pnl", 0)) > 0]
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0

    return (
        "*{}*\n"
        "  Trades 24h: {}\n"
        "  Profit 24h: \u20ac{:+.2f}\n"
        "  Total trades: {}\n"
        "  Total profit: \u20ac{:+.2f}\n"
        "  Win rate: {:.1f}%"
    ).format(bot["name"], today_count, today_pnl, total_trades, total_pnl, win_rate)


def main():
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    balance = get_balance()
    start_capital = 200.0
    total_return = ((balance - start_capital) / start_capital) * 100 if start_capital > 0 else 0

    live_since = datetime(2026, 3, 22)
    days_live = (now - live_since).days
    if days_live < 1:
        days_live = 1

    lines = [
        "Daily Trading Report",
        today,
        "",
    ]

    for bot in BOTS:
        lines.append(bot_stats(bot, today, yesterday))
        lines.append("")

    lines.extend([
        "*Account Summary*",
        "  Starting capital: \u20ac200.00",
        "  Current balance: \u20ac{:.2f}".format(balance),
        "  Total return: {:+.2f}%".format(total_return),
        "  Days live: {}".format(days_live),
    ])

    msg = "\n".join(lines)
    send_telegram(msg)
    print("Report sent!")
    print(msg)


if __name__ == "__main__":
    main()
