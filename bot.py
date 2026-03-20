from __future__ import annotations

import argparse

from crypto_bot.backtest import run as run_backtest
from crypto_bot.live import run as run_live


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kraken-focused crypto momentum bot")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config file (default: config.yaml)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    backtest = sub.add_parser("backtest", help="Run historical backtest")
    backtest.add_argument(
        "--candles",
        type=int,
        default=2000,
        help="Number of candles to fetch from exchange",
    )

    sub.add_parser("trade", help="Run live/paper trading loop")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.command == "backtest":
        run_backtest(args.config, args.candles)
    elif args.command == "trade":
        run_live(args.config)


if __name__ == "__main__":
    main()