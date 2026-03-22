"""Cross-exchange arbitrage bot: Kraken <-> Binance for ETH/EUR."""
from __future__ import annotations

import json
import time
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("arbitrage")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_arb_config(path: str = "config_arbitrage.yaml") -> dict:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return raw


# ---------------------------------------------------------------------------
# Telegram notifications
# ---------------------------------------------------------------------------

def send_telegram(token: str, chat_id: str, text: str) -> None:
    if not token or not chat_id:
        return
    try:
        url = "https://api.telegram.org/bot{}/sendMessage".format(token)
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.warning("Telegram send failed: %s", e)


# ---------------------------------------------------------------------------
# Core arbitrage logic
# ---------------------------------------------------------------------------

class ArbitrageBot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.dry_run = cfg.get("dry_run", True)
        self.symbol = cfg["symbol"]  # e.g. "ETH/EUR"
        self.min_spread_pct = cfg.get("min_spread_pct", 0.15)
        self.trade_amount_eur = cfg.get("trade_amount_eur", 500.0)
        self.poll_seconds = cfg.get("poll_seconds", 2)
        self.max_trades_per_hour = cfg.get("max_trades_per_hour", 20)
        self.telegram_token = cfg.get("telegram_token", "")
        self.telegram_chat_id = cfg.get("telegram_chat_id", "")

        # State tracking
        self.trades = []
        self.total_profit = 0.0
        self.trades_this_hour = 0
        self.hour_start = datetime.now(timezone.utc)
        self.state_file = Path(cfg.get("state_file", "data/arbitrage_state.json"))
        self.trades_file = Path(cfg.get("trades_file", "data/arbitrage_trades.csv"))

        # Initialize exchanges
        kraken_cfg = cfg["kraken"]
        binance_cfg = cfg["binance"]

        self.kraken = ccxt.kraken({
            "apiKey": kraken_cfg["api_key"],
            "secret": kraken_cfg["api_secret"],
            "enableRateLimit": True,
        })

        self.binance = ccxt.binance({
            "apiKey": binance_cfg["api_key"],
            "secret": binance_cfg["api_secret"],
            "enableRateLimit": True,
        })

        self.exchanges = {
            "kraken": self.kraken,
            "binance": self.binance,
        }

        # Fee rates
        self.fees = {
            "kraken": cfg.get("kraken_fee", 0.0016),   # 0.16% maker
            "binance": cfg.get("binance_fee", 0.0002),  # 0.02% maker with BNB
        }

        log.info("Arbitrage bot initialized")
        log.info("Symbol: %s", self.symbol)
        log.info("Min spread: %.2f%%", self.min_spread_pct)
        log.info("Trade size: EUR %.2f", self.trade_amount_eur)
        log.info("Dry run: %s", self.dry_run)

    def get_prices(self) -> dict:
        """Fetch bid/ask from both exchanges."""
        prices = {}
        for name, exchange in self.exchanges.items():
            try:
                ticker = exchange.fetch_ticker(self.symbol)
                prices[name] = {
                    "bid": float(ticker["bid"]),
                    "ask": float(ticker["ask"]),
                    "spread_pct": (float(ticker["ask"]) - float(ticker["bid"])) / float(ticker["bid"]) * 100,
                }
            except Exception as e:
                log.error("Failed to fetch %s price: %s", name, e)
                return {}
        return prices

    def get_balances(self) -> dict:
        """Fetch EUR and ETH balances from both exchanges."""
        balances = {}
        base = self.symbol.split("/")[0]  # ETH
        quote = self.symbol.split("/")[1]  # EUR

        for name, exchange in self.exchanges.items():
            try:
                bal = exchange.fetch_balance()
                balances[name] = {
                    base: float(bal.get(base, {}).get("free", 0)),
                    quote: float(bal.get(quote, {}).get("free", 0)),
                }
            except Exception as e:
                log.error("Failed to fetch %s balance: %s", name, e)
                return {}
        return balances

    def find_opportunity(self, prices: dict) -> dict | None:
        """Check if there's a profitable spread."""
        if len(prices) < 2:
            return None

        kraken = prices["kraken"]
        binance = prices["binance"]

        # Opportunity 1: Buy on Kraken (ask), sell on Binance (bid)
        spread_1 = (binance["bid"] - kraken["ask"]) / kraken["ask"] * 100
        fee_1 = (self.fees["kraken"] + self.fees["binance"]) * 100
        net_1 = spread_1 - fee_1

        # Opportunity 2: Buy on Binance (ask), sell on Kraken (bid)
        spread_2 = (kraken["bid"] - binance["ask"]) / binance["ask"] * 100
        fee_2 = (self.fees["kraken"] + self.fees["binance"]) * 100
        net_2 = spread_2 - fee_2

        best = None
        if net_1 > net_2 and spread_1 > self.min_spread_pct:
            best = {
                "buy_exchange": "kraken",
                "sell_exchange": "binance",
                "buy_price": kraken["ask"],
                "sell_price": binance["bid"],
                "gross_spread_pct": spread_1,
                "net_spread_pct": net_1,
                "fees_pct": fee_1,
            }
        elif spread_2 > self.min_spread_pct:
            best = {
                "buy_exchange": "binance",
                "sell_exchange": "kraken",
                "buy_price": binance["ask"],
                "sell_price": kraken["bid"],
                "gross_spread_pct": spread_2,
                "net_spread_pct": net_2,
                "fees_pct": fee_2,
            }

        return best

    def execute_trade(self, opp: dict, balances: dict) -> bool:
        """Execute simultaneous buy and sell."""
        buy_ex = opp["buy_exchange"]
        sell_ex = opp["sell_exchange"]
        buy_price = opp["buy_price"]
        sell_price = opp["sell_price"]

        base = self.symbol.split("/")[0]
        quote = self.symbol.split("/")[1]

        # Calculate amount
        amount_eur = min(
            self.trade_amount_eur,
            balances[buy_ex][quote] * 0.95,  # leave 5% buffer
        )

        if amount_eur < 10:
            log.warning("Insufficient %s balance on %s", quote, buy_ex)
            return False

        # Check if we have enough ETH on sell side
        amount_crypto = amount_eur / buy_price
        if balances[sell_ex][base] < amount_crypto:
            log.warning("Insufficient %s on %s (need %.6f, have %.6f)",
                        base, sell_ex, amount_crypto, balances[sell_ex][base])
            return False

        profit_eur = amount_eur * opp["net_spread_pct"] / 100

        if self.dry_run:
            log.info("DRY RUN | Buy %.6f %s on %s @ %.2f | Sell on %s @ %.2f | Spread: %.3f%% | Profit: EUR %.2f",
                     amount_crypto, base, buy_ex, buy_price, sell_ex, sell_price,
                     opp["net_spread_pct"], profit_eur)
        else:
            try:
                # Execute both orders
                buy_order = self.exchanges[buy_ex].create_limit_buy_order(
                    self.symbol, amount_crypto, buy_price
                )
                sell_order = self.exchanges[sell_ex].create_limit_sell_order(
                    self.symbol, amount_crypto, sell_price
                )
                log.info("EXECUTED | Buy %s @ %.2f on %s | Sell @ %.2f on %s | Profit: EUR %.2f",
                         base, buy_price, buy_ex, sell_price, sell_ex, profit_eur)
            except Exception as e:
                log.error("Trade execution failed: %s", e)
                return False

        # Record trade
        trade = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "buy_exchange": buy_ex,
            "sell_exchange": sell_ex,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "amount_eur": amount_eur,
            "amount_crypto": amount_crypto,
            "gross_spread_pct": opp["gross_spread_pct"],
            "net_spread_pct": opp["net_spread_pct"],
            "estimated_profit_eur": profit_eur,
            "dry_run": self.dry_run,
        }
        self.trades.append(trade)
        self.total_profit += profit_eur
        self.trades_this_hour += 1

        # Save state
        self.save_state()

        # Telegram notification for real trades
        if not self.dry_run:
            msg = (
                "*Arbitrage Trade*\n"
                "Buy {} on {} @ {:.2f}\n"
                "Sell on {} @ {:.2f}\n"
                "Spread: {:.3f}%\n"
                "Profit: {:.2f}\n"
                "Total today: {:.2f}"
            ).format(base, buy_ex, buy_price, sell_ex, sell_price,
                     opp["net_spread_pct"], profit_eur, self.total_profit)
            send_telegram(self.telegram_token, self.telegram_chat_id, msg)

        return True

    def save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "total_profit": self.total_profit,
            "total_trades": len(self.trades),
            "last_trade": self.trades[-1] if self.trades else None,
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        self.state_file.write_text(json.dumps(state, indent=2))

    def run(self) -> None:
        """Main loop."""
        log.info("Starting arbitrage bot...")
        log.info("Fetching initial balances...")

        balances = self.get_balances()
        if balances:
            for name, bal in balances.items():
                log.info("%s: %s", name, bal)

        scan_count = 0
        while True:
            try:
                # Rate limit per hour
                now = datetime.now(timezone.utc)
                if (now - self.hour_start).seconds >= 3600:
                    self.trades_this_hour = 0
                    self.hour_start = now

                if self.trades_this_hour >= self.max_trades_per_hour:
                    time.sleep(60)
                    continue

                # Fetch prices
                prices = self.get_prices()
                if not prices:
                    time.sleep(self.poll_seconds)
                    continue

                scan_count += 1
                if scan_count % 100 == 0:
                    log.info("Scanned %d times | Trades: %d | Profit: EUR %.2f | "
                             "Kraken bid/ask: %.2f/%.2f | Binance bid/ask: %.2f/%.2f",
                             scan_count, len(self.trades), self.total_profit,
                             prices["kraken"]["bid"], prices["kraken"]["ask"],
                             prices["binance"]["bid"], prices["binance"]["ask"])

                # Check for opportunity
                opp = self.find_opportunity(prices)
                if opp:
                    log.info("Opportunity found! Spread: %.3f%% (net: %.3f%%)",
                             opp["gross_spread_pct"], opp["net_spread_pct"])

                    # Refresh balances before trading
                    balances = self.get_balances()
                    if balances:
                        self.execute_trade(opp, balances)

                time.sleep(self.poll_seconds)

            except KeyboardInterrupt:
                log.info("Shutting down...")
                self.save_state()
                break
            except Exception as e:
                log.error("Error in main loop: %s", e)
                time.sleep(10)


# ---------------------------------------------------------------------------
# Dry-run scanner mode: just monitor spreads without trading
# ---------------------------------------------------------------------------

def scan_spreads(cfg: dict, duration_minutes: int = 60) -> None:
    """Monitor spreads between exchanges for a period to assess viability."""
    bot = ArbitrageBot(cfg)
    spreads = []
    opportunities = 0
    start = time.time()
    end = start + duration_minutes * 60

    log.info("Scanning spreads for %d minutes...", duration_minutes)

    while time.time() < end:
        prices = bot.get_prices()
        if not prices:
            time.sleep(2)
            continue

        kr = prices["kraken"]
        bn = prices["binance"]

        spread_1 = (bn["bid"] - kr["ask"]) / kr["ask"] * 100  # buy kraken sell binance
        spread_2 = (kr["bid"] - bn["ask"]) / bn["ask"] * 100  # buy binance sell kraken
        best_spread = max(spread_1, spread_2)

        spreads.append(best_spread)

        opp = bot.find_opportunity(prices)
        if opp:
            opportunities += 1
            log.info("OPPORTUNITY: buy %s @ %.2f, sell %s @ %.2f, net: %.3f%%",
                     opp["buy_exchange"], opp["buy_price"],
                     opp["sell_exchange"], opp["sell_price"],
                     opp["net_spread_pct"])

        time.sleep(bot.poll_seconds)

    # Summary
    if spreads:
        import numpy as np
        spreads_arr = np.array(spreads)
        print("\n" + "=" * 50)
        print("SPREAD SCAN RESULTS")
        print("=" * 50)
        print("Duration:        {} minutes".format(duration_minutes))
        print("Samples:         {}".format(len(spreads)))
        print("Avg spread:      {:.4f}%".format(np.mean(spreads_arr)))
        print("Max spread:      {:.4f}%".format(np.max(spreads_arr)))
        print("Min spread:      {:.4f}%".format(np.min(spreads_arr)))
        print("Std dev:         {:.4f}%".format(np.std(spreads_arr)))
        print("Opportunities:   {} ({:.1f}% of scans)".format(
            opportunities, opportunities / len(spreads) * 100))
        print("Estimated daily: {} trades".format(
            int(opportunities / duration_minutes * 60 * 24)))


if __name__ == "__main__":
    import sys
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config_arbitrage.yaml"
    mode = sys.argv[2] if len(sys.argv) > 2 else "scan"
    cfg = load_arb_config(cfg_path)

    if mode == "scan":
        duration = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        scan_spreads(cfg, duration)
    elif mode == "run":
        bot = ArbitrageBot(cfg)
        bot.run()
