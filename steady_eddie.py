"""Backtest v3c — 3-Layer Strategy with partial take-profits and improved exits."""
from __future__ import annotations

import csv
import sys

import pandas as pd

from crypto_bot.config import load_config
from crypto_bot.indicators import with_features
from crypto_bot.risk import size_position
from crypto_bot.strategy import evaluate


def _fee_rate(maker_only: bool, maker: float, taker: float) -> float:
    return maker if maker_only else taker


def run_csv_backtest(config_path: str, csv_path: str) -> None:
    cfg = load_config(config_path)

    print(f"Loading data from {csv_path}...")
    df_raw = pd.read_csv(csv_path, parse_dates=["timestamp"])
    print(f"Loaded {len(df_raw):,} candles (raw)")

    df_raw = df_raw.set_index("timestamp")
    df_resampled = df_raw.resample("4h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna().reset_index()
    print(f"Resampled to 4H: {len(df_resampled):,} candles")
    print(f"Date range: {df_resampled['timestamp'].iloc[0]} -> {df_resampled['timestamp'].iloc[-1]}")

    df = with_features(df_resampled, cfg.strategy)

    equity = cfg.trading.paper_equity_eur
    peak_equity = equity
    equity_curve = [equity]
    trades: list[dict] = []
    pos = None

    warmup = max(cfg.strategy.ema_regime + 10, cfg.strategy.sentiment_lookback * 2 + 10, 250)
    fee_r = _fee_rate(cfg.trading.maker_only, cfg.trading.fee_rate_maker, cfg.trading.fee_rate_taker)

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

            # Sentiment-based risk sizing
            if decision.sentiment < cfg.strategy.fear_threshold:
                risk = cfg.trading.risk_per_trade_fear
            elif decision.sentiment > cfg.strategy.greed_threshold:
                risk = cfg.trading.risk_per_trade * 0.5
            else:
                risk = cfg.trading.risk_per_trade

            # Mean reversion = tighter stops
            if decision.entry_type == "mean_reversion":
                stop_mult = cfg.strategy.atr_stop_mult * 0.6
            else:
                stop_mult = cfg.strategy.atr_stop_mult

            stop_distance = decision.atr_value * stop_mult

            sizing = size_position(
                equity=equity,
                entry_price=price,
                stop_distance=stop_distance,
                risk_per_trade=risk,
                leverage=cfg.exchange.leverage,
                max_notional=cfg.trading.max_notional_eur,
            )
            if sizing.amount <= 0:
                equity_curve.append(equity)
                continue

            fee_entry = sizing.notional * fee_r
            equity -= fee_entry

            if direction == "long":
                stop_price = price - stop_distance
                tp1_price = price + stop_distance * 2.0   # partial TP at 2:1
                tp2_price = price + stop_distance * 5.0   # full TP at 5:1
            else:
                stop_price = price + stop_distance
                tp1_price = price - stop_distance * 2.0
                tp2_price = price - stop_distance * 5.0

            pos = {
                "opened": str(row["timestamp"]),
                "side": direction,
                "entry": price,
                "amount": sizing.amount,
                "amount_remaining": sizing.amount,  # tracks partial exits
                "notional": sizing.notional,
                "leverage": sizing.leverage_used,
                "stop": stop_price,
                "tp1": tp1_price,
                "tp2": tp2_price,
                "tp1_hit": False,
                "fees": fee_entry,
                "entry_type": decision.entry_type,
                "sentiment_at_entry": decision.sentiment,
                "stop_mult": stop_mult,
                "partial_pnl": 0.0,    # accumulated PnL from partial exits
                "partial_fees": 0.0,
            }
            equity_curve.append(equity)
            continue

        # --- Manage open position ---
        assert pos is not None
        side_str = str(pos["side"])
        stop = float(pos["stop"])
        tp1 = float(pos["tp1"])
        tp2 = float(pos["tp2"])
        tp1_hit = bool(pos["tp1_hit"])
        amount_remaining = float(pos["amount_remaining"])

        # Trailing stop
        trail = decision.atr_value * float(pos["stop_mult"])
        if side_str == "long":
            stop = max(stop, price - trail)
        else:
            stop = min(stop, price + trail)
        pos["stop"] = stop

        # --- Check partial TP1 (close 50% at 2:1 RR) ---
        if not tp1_hit:
            tp1_triggered = (
                (side_str == "long" and price >= tp1)
                or (side_str == "short" and price <= tp1)
            )
            if tp1_triggered:
                # Close 50%
                partial_amount = amount_remaining * 0.5
                entry = float(pos["entry"])
                if side_str == "long":
                    partial_gross = (price - entry) * partial_amount
                else:
                    partial_gross = (entry - price) * partial_amount
                partial_fee = partial_amount * price * fee_r
                partial_net = partial_gross - partial_fee
                equity += partial_net

                pos["tp1_hit"] = True
                pos["amount_remaining"] = amount_remaining - partial_amount
                pos["partial_pnl"] = float(pos["partial_pnl"]) + partial_net
                pos["partial_fees"] = float(pos["partial_fees"]) + partial_fee

                # Move stop to breakeven + small profit after TP1
                if side_str == "long":
                    pos["stop"] = max(stop, float(pos["entry"]) + trail * 0.2)
                else:
                    pos["stop"] = min(stop, float(pos["entry"]) - trail * 0.2)

                amount_remaining = float(pos["amount_remaining"])
                stop = float(pos["stop"])

        # --- Check full exit ---
        close_reason = ""
        if amount_remaining <= 0:
            close_reason = "fully_partialed_out"
        elif side_str == "long":
            if price <= stop:
                close_reason = "stop_loss"
            elif price >= tp2:
                close_reason = "take_profit_full"
            elif decision.long_exit:
                # Squeeze trades: only exit on regime flip, not trend reversal
                if pos["entry_type"] == "squeeze_breakout":
                    regime_bear = row["close"] < row["ema_regime"]
                    if regime_bear:
                        close_reason = "regime_flip"
                else:
                    close_reason = "trend_reversal"
        else:
            if price >= stop:
                close_reason = "stop_loss"
            elif price <= tp2:
                close_reason = "take_profit_full"
            elif decision.short_exit:
                if pos["entry_type"] == "squeeze_breakout":
                    regime_bull = row["close"] > row["ema_regime"]
                    if regime_bull:
                        close_reason = "regime_flip"
                else:
                    close_reason = "trend_reversal"

        if close_reason:
            entry = float(pos["entry"])
            notional_exit = amount_remaining * price

            if amount_remaining > 0:
                gross_pnl = (
                    (price - entry) * amount_remaining
                    if side_str == "long"
                    else (entry - price) * amount_remaining
                )
                fee_exit = notional_exit * fee_r
                net_pnl = gross_pnl - fee_exit
                equity += net_pnl
            else:
                gross_pnl = 0.0
                fee_exit = 0.0
                net_pnl = 0.0

            # Total PnL = partial exits + final exit
            total_net = float(pos["partial_pnl"]) + net_pnl
            total_fees = float(pos["fees"]) + float(pos["partial_fees"]) + fee_exit
            total_gross = gross_pnl + (total_net - float(pos["partial_pnl"]) + net_pnl - net_pnl)

            peak_equity = max(peak_equity, equity)

            trades.append({
                "opened": str(pos["opened"]),
                "closed": str(row["timestamp"]),
                "side": side_str,
                "entry_type": str(pos["entry_type"]),
                "entry": entry,
                "exit": price,
                "amount": float(pos["amount"]),
                "leverage": float(pos["leverage"]),
                "gross_pnl": total_net + total_fees,  # approx gross
                "net_pnl": total_net,
                "fees": total_fees,
                "reason": close_reason,
                "sentiment": float(pos["sentiment_at_entry"]),
                "tp1_hit": bool(pos["tp1_hit"]),
            })
            pos = None

        equity_curve.append(equity)

    # Force close
    if pos is not None:
        row = df.iloc[-1]
        price = float(row["close"])
        amount_remaining = float(pos["amount_remaining"])
        entry = float(pos["entry"])
        side_str = str(pos["side"])

        if amount_remaining > 0:
            gross_pnl = (
                (price - entry) * amount_remaining if side_str == "long"
                else (entry - price) * amount_remaining
            )
            fee_exit = amount_remaining * price * fee_r
            net_pnl = gross_pnl - fee_exit
            equity += net_pnl
        else:
            net_pnl = 0.0
            fee_exit = 0.0

        total_net = float(pos["partial_pnl"]) + net_pnl
        total_fees = float(pos["fees"]) + float(pos["partial_fees"]) + fee_exit

        trades.append({
            "opened": str(pos["opened"]),
            "closed": str(row["timestamp"]),
            "side": side_str,
            "entry_type": str(pos["entry_type"]),
            "entry": entry,
            "exit": price,
            "amount": float(pos["amount"]),
            "leverage": float(pos["leverage"]),
            "gross_pnl": total_net + total_fees,
            "net_pnl": total_net,
            "fees": total_fees,
            "reason": "forced_close_end_of_data",
            "sentiment": float(pos["sentiment_at_entry"]),
            "tp1_hit": bool(pos["tp1_hit"]),
        })

    # --- Stats ---
    start = cfg.trading.paper_equity_eur
    final = equity
    total_return = ((final - start) / start) * 100 if start > 0 else 0.0

    wins = [t for t in trades if float(t["net_pnl"]) > 0]
    losses = [t for t in trades if float(t["net_pnl"]) <= 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0

    avg_win = sum(float(t["net_pnl"]) for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(float(t["net_pnl"]) for t in losses) / len(losses) if losses else 0.0
    total_fees_sum = sum(float(t["fees"]) for t in trades)

    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1

    entry_types: dict[str, int] = {}
    for t in trades:
        entry_types[t["entry_type"]] = entry_types.get(t["entry_type"], 0) + 1

    tp1_hits = sum(1 for t in trades if t.get("tp1_hit"))

    curve = pd.Series(equity_curve)
    running_max = curve.cummax()
    drawdown = (curve - running_max) / running_max.replace(0, 1)
    max_dd = float(drawdown.min()) * 100

    gross_profits = sum(float(t["net_pnl"]) for t in trades if float(t["net_pnl"]) > 0)
    gross_losses = abs(sum(float(t["net_pnl"]) for t in trades if float(t["net_pnl"]) <= 0))
    profit_factor = gross_profits / gross_losses if gross_losses > 0 else float("inf")

    squeeze_trades = [t for t in trades if t["entry_type"] == "squeeze_breakout"]
    mr_trades = [t for t in trades if t["entry_type"] == "mean_reversion"]

    def _stats(tlist: list[dict]) -> tuple[int, float, float]:
        if not tlist:
            return 0, 0.0, 0.0
        w = [t for t in tlist if float(t["net_pnl"]) > 0]
        wr = len(w) / len(tlist) * 100
        pnl = sum(float(t["net_pnl"]) for t in tlist)
        return len(tlist), wr, pnl

    sq_n, sq_wr, sq_pnl = _stats(squeeze_trades)
    mr_n, mr_wr, mr_pnl = _stats(mr_trades)

    days = (df_resampled['timestamp'].iloc[-1] - df_resampled['timestamp'].iloc[0]).days
    years = days / 365.25
    ann_return = ((final / start) ** (1 / years) - 1) * 100 if years > 0 and final > 0 else 0.0

    print("\n" + "=" * 65)
    print("BACKTEST RESULTS — 3-LAYER STRATEGY v3c (PARTIAL TP + BETTER EXITS)")
    print("=" * 65)
    print(f"Date range:        {df_resampled['timestamp'].iloc[0].date()} -> {df_resampled['timestamp'].iloc[-1].date()}")
    print(f"Timeframe:         4H candles ({len(df):,} bars)")
    print(f"Duration:          {years:.1f} years")
    print()
    print(f"--- Performance ---")
    print(f"Start equity:      ${start:,.2f}")
    print(f"Final equity:      ${final:,.2f}")
    print(f"Total return:      {total_return:+.2f}%")
    print(f"Annualized return: {ann_return:+.2f}%")
    print(f"Max drawdown:      {max_dd:.2f}%")
    print(f"Profit factor:     {profit_factor:.2f}")
    print()
    print(f"--- Trades ---")
    print(f"Total trades:      {len(trades)}")
    print(f"Win rate:          {win_rate:.1f}%")
    print(f"Avg win:           ${avg_win:+.2f}")
    print(f"Avg loss:          ${avg_loss:+.2f}")
    print(f"Win/Loss ratio:    {abs(avg_win / avg_loss) if avg_loss != 0 else 0:.2f}:1")
    print(f"Total fees paid:   ${total_fees_sum:.2f}")
    print(f"TP1 partial hits:  {tp1_hits} / {len(trades)}")
    print()
    print(f"--- Entry Types ---")
    print(f"Squeeze breakout:  {sq_n} trades | WR: {sq_wr:.1f}% | PnL: ${sq_pnl:+.2f}")
    print(f"Mean reversion:    {mr_n} trades | WR: {mr_wr:.1f}% | PnL: ${mr_pnl:+.2f}")
    print()
    print(f"Exit reasons:")
    for reason, count in sorted(reasons.items()):
        print(f"  {reason}: {count}")

    if trades:
        print(f"\n{'Year':<8} {'Trades':<8} {'WinRate':<10} {'PnL':<14} {'Return%':<10} {'Cum.Ret%':<10}")
        print("-" * 62)
        for t in trades:
            t["_year"] = str(t["opened"])[:4]
        years_list = sorted(set(t["_year"] for t in trades))
        cum_pnl = 0.0
        for year in years_list:
            yt = [t for t in trades if t["_year"] == year]
            yw = [t for t in yt if float(t["net_pnl"]) > 0]
            ywr = len(yw) / len(yt) * 100 if yt else 0
            ypnl = sum(float(t["net_pnl"]) for t in yt)
            cum_pnl += ypnl
            yret = ypnl / start * 100
            cret = cum_pnl / start * 100
            print(f"{year:<8} {len(yt):<8} {ywr:<10.1f} ${ypnl:<13.2f} {yret:<+10.2f} {cret:<+10.2f}")

    if trades:
        outfile = "data/backtest_v3c_trades.csv"
        with open(outfile, "w", encoding="utf-8", newline="") as f:
            keys = [k for k in trades[0].keys() if not k.startswith("_")]
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(trades)
        print(f"\nTrades saved to {outfile}")

    curve.to_csv("data/equity_curve_v3c.csv", index=False, header=["equity"])
    print(f"Equity curve saved to data/equity_curve_v3c.csv")


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    csv_path = sys.argv[2] if len(sys.argv) > 2 else "data/btc_usdt_15m_5y.csv"
    run_csv_backtest(config_path, csv_path)
