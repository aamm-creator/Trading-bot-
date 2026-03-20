from __future__ import annotations

import csv
from dataclasses import asdict

import pandas as pd

from .config import load_config
from .exchange_client import ExchangeClient
from .indicators import with_features
from .risk import size_position
from .strategy import evaluate


def _fee_rate(maker_only: bool, maker: float, taker: float) -> float:
    return maker if maker_only else taker


def run(config_path: str, candles: int) -> None:
    cfg = load_config(config_path)
    client = ExchangeClient(cfg.exchange, cfg.trading)

    raw = client.fetch_ohlcv(candles)
    df = with_features(raw, cfg.strategy)

    equity = cfg.trading.paper_equity_eur
    equity_curve = [equity]

    trades: list[dict[str, float | str]] = []
    pos = None

    warmup = max(cfg.strategy.ema_slow + 2, cfg.strategy.breakout_lookback + 2, 120)

    for i in range(warmup, len(df)):
        view = df.iloc[: i + 1]
        row = view.iloc[-1]
        price = float(row["close"])
        decision = evaluate(view, cfg.strategy, cfg.trading.allow_short)

        if pos is None:
            side = None
            direction = None
            if decision.long_entry:
                side = "buy"
                direction = "long"
            elif decision.short_entry:
                side = "sell"
                direction = "short"

            if side is None:
                equity_curve.append(equity)
                continue

            stop_distance = decision.atr_value * cfg.strategy.atr_stop_mult
            sizing = size_position(
                equity=equity,
                entry_price=price,
                stop_distance=stop_distance,
                risk_per_trade=cfg.trading.risk_per_trade,
                leverage=cfg.exchange.leverage,
                max_notional=cfg.trading.max_notional_eur,
            )
            if sizing.amount <= 0:
                equity_curve.append(equity)
                continue

            fee_entry = sizing.notional * _fee_rate(
                cfg.trading.maker_only,
                cfg.trading.fee_rate_maker,
                cfg.trading.fee_rate_taker,
            )
            equity -= fee_entry

            if direction == "long":
                stop_price = price - stop_distance
                tp_price = price + stop_distance * cfg.strategy.rr_take_profit
            else:
                stop_price = price + stop_distance
                tp_price = price - stop_distance * cfg.strategy.rr_take_profit

            pos = {
                "opened": str(row["timestamp"]),
                "side": direction,
                "entry": price,
                "amount": sizing.amount,
                "notional": sizing.notional,
                "leverage": sizing.leverage_used,
                "stop": stop_price,
                "tp": tp_price,
                "fees": fee_entry,
            }
            equity_curve.append(equity)
            continue

        # manage open position
        assert pos is not None
        side = str(pos["side"])
        stop = float(pos["stop"])
        tp = float(pos["tp"])

        trail = decision.atr_value * cfg.strategy.atr_stop_mult
        if side == "long":
            stop = max(stop, price - trail)
        else:
            stop = min(stop, price + trail)
        pos["stop"] = stop

        close_reason = ""
        if side == "long":
            if price <= stop:
                close_reason = "stop_loss"
            elif price >= tp:
                close_reason = "take_profit"
            elif decision.long_exit:
                close_reason = "trend_reversal"
        else:
            if price >= stop:
                close_reason = "stop_loss"
            elif price <= tp:
                close_reason = "take_profit"
            elif decision.short_exit:
                close_reason = "trend_reversal"

        if close_reason:
            amount = float(pos["amount"])
            entry = float(pos["entry"])
            notional_exit = amount * price

            gross_pnl = (
                (price - entry) * amount
                if side == "long"
                else (entry - price) * amount
            )
            fee_exit = notional_exit * _fee_rate(
                cfg.trading.maker_only,
                cfg.trading.fee_rate_maker,
                cfg.trading.fee_rate_taker,
            )
            net_pnl = gross_pnl - fee_exit
            equity += net_pnl

            total_fees = float(pos["fees"]) + fee_exit
            trades.append(
                {
                    "opened": str(pos["opened"]),
                    "closed": str(row["timestamp"]),
                    "side": side,
                    "entry": entry,
                    "exit": price,
                    "amount": amount,
                    "leverage": float(pos["leverage"]),
                    "gross_pnl": gross_pnl,
                    "net_pnl": net_pnl,
                    "fees": total_fees,
                    "reason": close_reason,
                }
            )
            pos = None

        equity_curve.append(equity)

    if pos is not None:
        row = df.iloc[-1]
        price = float(row["close"])
        amount = float(pos["amount"])
        entry = float(pos["entry"])
        side = str(pos["side"])

        gross_pnl = (
            (price - entry) * amount if side == "long" else (entry - price) * amount
        )
        fee_exit = amount * price * _fee_rate(
            cfg.trading.maker_only,
            cfg.trading.fee_rate_maker,
            cfg.trading.fee_rate_taker,
        )
        net_pnl = gross_pnl - fee_exit
        equity += net_pnl
        trades.append(
            {
                "opened": str(pos["opened"]),
                "closed": str(row["timestamp"]),
                "side": side,
                "entry": entry,
                "exit": price,
                "amount": amount,
                "leverage": float(pos["leverage"]),
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "fees": float(pos["fees"]) + fee_exit,
                "reason": "forced_close_end_of_data",
            }
        )

    start = cfg.trading.paper_equity_eur
    final = equity
    total_return = ((final - start) / start) * 100 if start > 0 else 0.0

    wins = sum(1 for t in trades if float(t["net_pnl"]) > 0)
    win_rate = (wins / len(trades) * 100) if trades else 0.0

    curve = pd.Series(equity_curve)
    running_max = curve.cummax()
    drawdown = (curve - running_max) / running_max.replace(0, 1)
    max_dd = float(drawdown.min()) * 100

    print("Backtest summary")
    print(f"Symbol: {cfg.exchange.symbol}")
    print(f"Timeframe: {cfg.exchange.timeframe}")
    print(f"Candles: {len(df)}")
    print(f"Trades: {len(trades)}")
    print(f"Win rate: {win_rate:.2f}%")
    print(f"Start equity: {start:.2f}")
    print(f"Final equity: {final:.2f}")
    print(f"Return: {total_return:.2f}%")
    print(f"Max drawdown: {max_dd:.2f}%")

    if trades:
        with open(cfg.storage.backtest_trades_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
            writer.writeheader()
            writer.writerows(trades)
        print(f"Saved trades to {cfg.storage.backtest_trades_file}")

    print("Config used:")
    print(asdict(cfg))