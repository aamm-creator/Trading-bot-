"""Scalper Bot - Mean reversion on 15-min BTC/USDT for Binance."""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class ScalperConfig:
    # Bollinger Bands
    bb_period: int = 20
    bb_std: float = 2.0
    # RSI
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    # VWAP
    vwap_enabled: bool = True
    # Stops
    atr_period: int = 14
    stop_atr_mult: float = 0.75  # tight stop
    tp_atr_mult: float = 1.5    # let winners run more
    # Sizing
    risk_per_trade: float = 0.01  # 1% risk per trade
    max_notional: float = 50000.0
    leverage: float = 3.0
    # Fees (Binance maker)
    fee_rate: float = 0.0002  # 0.02%
    # Filters
    min_volume_ratio: float = 1.2  # stricter - only trade with volume
    max_spread_pct: float = 0.1
    # Time filter: avoid trading in first/last 15 min of 4h candle (optional)
    cooldown_bars: int = 2  # bars to wait after a trade


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    alpha = 1.0 / period
    avg_gain = gain.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def add_indicators(df: pd.DataFrame, cfg: ScalperConfig) -> pd.DataFrame:
    out = df.copy()

    # Bollinger Bands
    bb_mid = out["close"].rolling(cfg.bb_period).mean()
    bb_std = out["close"].rolling(cfg.bb_period).std()
    out["bb_upper"] = bb_mid + cfg.bb_std * bb_std
    out["bb_lower"] = bb_mid - cfg.bb_std * bb_std
    out["bb_mid"] = bb_mid
    out["bb_pct"] = (out["close"] - out["bb_lower"]) / (out["bb_upper"] - out["bb_lower"]).replace(0, 1)

    # RSI
    out["rsi"] = _rsi(out["close"], cfg.rsi_period)

    # ATR
    out["atr"] = _atr(out, cfg.atr_period)

    # Volume ratio
    out["vol_sma"] = out["volume"].rolling(20).mean()
    out["volume_ratio"] = out["volume"] / out["vol_sma"].replace(0, 1)

    # EMA 50 for trend context
    out["ema_50"] = out["close"].ewm(span=50, adjust=False).mean()
    out["ema_200"] = out["close"].ewm(span=200, adjust=False).mean()

    # Price distance from BB mid (mean reversion target)
    out["dist_from_mid"] = (out["close"] - bb_mid) / bb_mid * 100

    return out


def run_scalper_backtest(csv_path: str, cfg: ScalperConfig = None) -> None:
    if cfg is None:
        cfg = ScalperConfig()

    print("Loading data from {}...".format(csv_path))
    df_raw = pd.read_csv(csv_path, parse_dates=["timestamp"])
    print("Loaded {:,} candles (15-min)".format(len(df_raw)))
    print("Range: {} -> {}".format(df_raw["timestamp"].iloc[0], df_raw["timestamp"].iloc[-1]))

    df = add_indicators(df_raw, cfg)

    equity = 1000.0
    start_equity = equity
    peak_equity = equity
    equity_curve = [equity]
    trades = []
    pos = None
    cooldown = 0
    warmup = 250

    for i in range(warmup, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = float(row["close"])

        if cooldown > 0:
            cooldown -= 1
            equity_curve.append(equity)
            if pos is not None:
                # Still manage open position during cooldown
                pass
            else:
                continue

        # --- Manage open position ---
        if pos is not None:
            side = pos["side"]
            stop = float(pos["stop"])
            tp = float(pos["tp"])

            close_reason = ""
            if side == "long":
                if price <= stop:
                    close_reason = "stop_loss"
                elif price >= tp:
                    close_reason = "take_profit"
                elif row["rsi"] > cfg.rsi_overbought:
                    close_reason = "rsi_exit"
                elif price >= float(row["bb_mid"]):
                    close_reason = "mean_revert_target"
            else:
                if price >= stop:
                    close_reason = "stop_loss"
                elif price <= tp:
                    close_reason = "take_profit"
                elif row["rsi"] < cfg.rsi_oversold:
                    close_reason = "rsi_exit"
                elif price <= float(row["bb_mid"]):
                    close_reason = "mean_revert_target"

            if close_reason:
                amount = float(pos["amount"])
                entry = float(pos["entry"])
                gross_pnl = (price - entry) * amount if side == "long" else (entry - price) * amount
                fee_exit = amount * price * cfg.fee_rate
                net_pnl = gross_pnl - fee_exit
                equity += net_pnl
                peak_equity = max(peak_equity, equity)

                trades.append({
                    "opened": str(pos["opened"]),
                    "closed": str(row["timestamp"]),
                    "side": side,
                    "entry": entry,
                    "exit": price,
                    "amount": amount,
                    "gross_pnl": gross_pnl,
                    "net_pnl": net_pnl,
                    "fees": float(pos["fees"]) + fee_exit,
                    "reason": close_reason,
                    "hold_bars": i - pos["bar_idx"],
                })
                pos = None
                cooldown = cfg.cooldown_bars

            equity_curve.append(equity)
            continue

        # --- Entry logic: Mean Reversion ---
        vol_ok = row["volume_ratio"] >= cfg.min_volume_ratio
        atr_val = float(row["atr"])

        # LONG: price breaks below lower BB + RSI deeply oversold + bouncing
        long_signal = (
            row["bb_pct"] < 0.05  # price in bottom 5% of BB range
            and row["rsi"] < 30
            and row["rsi"] > prev["rsi"]  # RSI turning up
            and vol_ok
        )

        # SHORT: price breaks above upper BB + RSI deeply overbought + turning
        short_signal = (
            row["bb_pct"] > 0.95  # price in top 5% of BB range
            and row["rsi"] > 70
            and row["rsi"] < prev["rsi"]  # RSI turning down
            and vol_ok
        )

        # Trend filter: only long above EMA200 (bull market), only short below
        if long_signal and row["close"] < row["ema_200"]:
            long_signal = False
        if short_signal and row["close"] > row["ema_200"]:
            short_signal = False

        side = None
        if long_signal:
            side = "long"
        elif short_signal:
            side = "short"

        if side is None:
            equity_curve.append(equity)
            continue

        # Position sizing
        stop_distance = atr_val * cfg.stop_atr_mult
        if stop_distance <= 0:
            equity_curve.append(equity)
            continue

        risk_amount = equity * cfg.risk_per_trade
        amount = risk_amount / stop_distance
        notional = amount * price
        max_amount = cfg.max_notional / price
        amount = min(amount, max_amount)
        notional = amount * price

        fee_entry = notional * cfg.fee_rate
        equity -= fee_entry

        if side == "long":
            stop_price = price - stop_distance
            tp_price = price + stop_distance * cfg.tp_atr_mult
        else:
            stop_price = price + stop_distance
            tp_price = price - stop_distance * cfg.tp_atr_mult

        pos = {
            "opened": str(row["timestamp"]),
            "side": side,
            "entry": price,
            "amount": amount,
            "notional": notional,
            "stop": stop_price,
            "tp": tp_price,
            "fees": fee_entry,
            "bar_idx": i,
        }
        equity_curve.append(equity)

    # Force close
    if pos is not None:
        row = df.iloc[-1]
        price = float(row["close"])
        amount = float(pos["amount"])
        entry = float(pos["entry"])
        side = pos["side"]
        gross_pnl = (price - entry) * amount if side == "long" else (entry - price) * amount
        fee_exit = amount * price * cfg.fee_rate
        net_pnl = gross_pnl - fee_exit
        equity += net_pnl
        trades.append({
            "opened": str(pos["opened"]),
            "closed": str(row["timestamp"]),
            "side": side,
            "entry": entry,
            "exit": price,
            "amount": amount,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "fees": float(pos["fees"]) + fee_exit,
            "reason": "forced_close",
            "hold_bars": len(df) - pos["bar_idx"],
        })

    # --- Stats ---
    final = equity
    total_return = ((final - start_equity) / start_equity) * 100

    wins = [t for t in trades if float(t["net_pnl"]) > 0]
    losses = [t for t in trades if float(t["net_pnl"]) <= 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else 0
    avg_win = sum(float(t["net_pnl"]) for t in wins) / len(wins) if wins else 0
    avg_loss = sum(float(t["net_pnl"]) for t in losses) / len(losses) if losses else 0
    total_fees = sum(float(t["fees"]) for t in trades)

    gross_profits = sum(float(t["net_pnl"]) for t in trades if float(t["net_pnl"]) > 0)
    gross_losses = abs(sum(float(t["net_pnl"]) for t in trades if float(t["net_pnl"]) <= 0))
    profit_factor = gross_profits / gross_losses if gross_losses > 0 else float("inf")

    avg_hold = sum(int(t["hold_bars"]) for t in trades) / len(trades) if trades else 0

    curve = pd.Series(equity_curve)
    running_max = curve.cummax()
    drawdown = (curve - running_max) / running_max.replace(0, 1)
    max_dd = float(drawdown.min()) * 100

    days = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days
    years = days / 365.25
    ann_return = ((final / start_equity) ** (1 / years) - 1) * 100 if years > 0 and final > start_equity else 0

    # Exit reasons
    reasons = {}
    for t in trades:
        r = t["reason"]
        reasons[r] = reasons.get(r, 0) + 1

    trades_per_day = len(trades) / days if days > 0 else 0

    print("\n" + "=" * 60)
    print("SCALPER BOT BACKTEST - 15min BTC/USDT (Binance fees)")
    print("=" * 60)
    print("Date range:      {} -> {}".format(df["timestamp"].iloc[0].date(), df["timestamp"].iloc[-1].date()))
    print("Candles:         {:,}".format(len(df)))
    print("Duration:        {:.1f} years".format(years))
    print("")
    print("--- Performance ---")
    print("Start equity:    ${:,.2f}".format(start_equity))
    print("Final equity:    ${:,.2f}".format(final))
    print("Total return:    {:+.2f}%".format(total_return))
    print("Annualized:      {:+.2f}%".format(ann_return))
    print("Max drawdown:    {:.2f}%".format(max_dd))
    print("Profit factor:   {:.2f}".format(profit_factor))
    print("")
    print("--- Trades ---")
    print("Total trades:    {:,}".format(len(trades)))
    print("Trades/day:      {:.1f}".format(trades_per_day))
    print("Win rate:        {:.1f}%".format(win_rate))
    print("Avg win:         ${:+.4f}".format(avg_win))
    print("Avg loss:        ${:+.4f}".format(avg_loss))
    print("Avg hold time:   {:.1f} bars ({:.1f} hours)".format(avg_hold, avg_hold * 0.25))
    print("Total fees:      ${:,.2f}".format(total_fees))
    print("Fees % of start: {:.1f}%".format(total_fees / start_equity * 100))
    print("")
    print("Exit reasons:")
    for reason, count in sorted(reasons.items()):
        print("  {}: {}".format(reason, count))

    # Per year
    if trades:
        print("\n{:<8} {:<8} {:<10} {:<14} {:<10}".format("Year", "Trades", "WinRate", "PnL", "Return%"))
        print("-" * 52)
        for t in trades:
            t["_year"] = str(t["opened"])[:4]
        years_list = sorted(set(t["_year"] for t in trades))
        for year in years_list:
            yt = [t for t in trades if t["_year"] == year]
            yw = [t for t in yt if float(t["net_pnl"]) > 0]
            ywr = len(yw) / len(yt) * 100 if yt else 0
            ypnl = sum(float(t["net_pnl"]) for t in yt)
            yret = ypnl / start_equity * 100
            print("{:<8} {:<8} {:<10.1f} ${:<13.2f} {:+.2f}".format(year, len(yt), ywr, ypnl, yret))

    # Save
    if trades:
        outfile = "data/scalper_trades.csv"
        with open(outfile, "w", encoding="utf-8", newline="") as f:
            keys = [k for k in trades[0].keys() if not k.startswith("_")]
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(trades)
        print("\nTrades saved to {}".format(outfile))

    curve.to_csv("data/scalper_equity_curve.csv", index=False, header=["equity"])
    print("Equity curve saved to data/scalper_equity_curve.csv")


if __name__ == "__main__":
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "data/btc_usdt_15m_10y.csv"
    run_scalper_backtest(csv_path)
