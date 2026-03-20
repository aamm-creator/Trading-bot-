from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import StrategyConfig


@dataclass
class Decision:
    long_entry: bool
    short_entry: bool
    long_exit: bool
    short_exit: bool
    atr_value: float
    reason: str


def evaluate(df: pd.DataFrame, cfg: StrategyConfig, allow_short: bool) -> Decision:
    if len(df) < max(cfg.ema_slow + 5, cfg.breakout_lookback + 5):
        return Decision(False, False, False, False, 0.0, "not_enough_data")

    row = df.iloc[-1]

    trend_up = row["ema_fast"] > row["ema_slow"]
    trend_down = row["ema_fast"] < row["ema_slow"]

    vol_ok = row["volume_ratio"] >= cfg.min_volume_ratio
    vol_regime_ok = row["atr_rank"] >= cfg.min_atr_percentile

    breakout_up = row["close"] > row["breakout_high"]
    breakout_down = row["close"] < row["breakout_low"]

    long_entry = bool(trend_up and breakout_up and vol_ok and vol_regime_ok)
    short_entry = bool(allow_short and trend_down and breakout_down and vol_ok and vol_regime_ok)

    long_exit = bool(trend_down)
    short_exit = bool(trend_up)

    if long_entry:
        reason = "trend_up_breakout_with_volume"
    elif short_entry:
        reason = "trend_down_breakout_with_volume"
    else:
        reason = "no_entry"

    return Decision(
        long_entry=long_entry,
        short_entry=short_entry,
        long_exit=long_exit,
        short_exit=short_exit,
        atr_value=float(row["atr"]),
        reason=reason,
    )