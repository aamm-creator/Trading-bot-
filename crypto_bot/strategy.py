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
    sentiment: float       # 0-100 sentiment score (for position sizing)
    entry_type: str        # "squeeze_breakout", "mean_reversion", or "none"


def evaluate(df: pd.DataFrame, cfg: StrategyConfig, allow_short: bool) -> Decision:
    min_bars = max(cfg.ema_regime + 10, cfg.sentiment_lookback * 2 + 10, 250)
    if len(df) < min_bars:
        return Decision(False, False, False, False, 0.0, "not_enough_data", 50.0, "none")

    row = df.iloc[-1]
    prev = df.iloc[-2]

    sentiment = float(row["sentiment"]) if not pd.isna(row["sentiment"]) else 50.0

    # =====================================================================
    # LAYER 1: Macro Regime (200 EMA + Sentiment)
    # =====================================================================
    regime_bull = row["close"] > row["ema_regime"]
    regime_bear = row["close"] < row["ema_regime"]

    # Sentiment zones
    is_fear = sentiment < cfg.fear_threshold        # < 25 = fear
    is_greed = sentiment > cfg.greed_threshold      # > 75 = greed
    # In greed: don't open new longs. In fear: don't open new shorts.

    # =====================================================================
    # LAYER 2: Bollinger Squeeze Breakout (primary entry)
    # =====================================================================
    # Squeeze fired = was compressed, now expanding
    squeeze_fired = bool(row["squeeze_fired"])

    # Direction of breakout after squeeze
    squeeze_long = squeeze_fired and row["close"] > row["bb_upper"]
    squeeze_short = squeeze_fired and row["close"] < row["bb_lower"]

    # Also allow strong breakout even without full squeeze:
    # price breaks above BB upper + above breakout channel + ADX rising
    strong_breakout_up = (
        row["close"] > row["bb_upper"]
        and row["close"] > row["breakout_high"]
        and row["adx"] >= cfg.min_adx
    )
    strong_breakout_down = (
        row["close"] < row["bb_lower"]
        and row["close"] < row["breakout_low"]
        and row["adx"] >= cfg.min_adx
    )

    # =====================================================================
    # LAYER 3: Mean Reversion — buy dips in uptrends (secondary entry)
    # =====================================================================
    # In a bull regime with uptrend, RSI dips to oversold = buy the dip
    trend_up = row["ema_fast"] > row["ema_slow"]
    trend_down = row["ema_fast"] < row["ema_slow"]
    slope_up = row["ema_fast_slope"] > 0
    slope_down = row["ema_fast_slope"] < 0

    # RSI just bounced from oversold (was oversold, now recovering)
    rsi_bounce_up = (
        prev["rsi"] <= cfg.rsi_oversold
        and row["rsi"] > cfg.rsi_oversold
    )
    # RSI just dropped from overbought
    rsi_bounce_down = (
        prev["rsi"] >= cfg.rsi_overbought
        and row["rsi"] < cfg.rsi_overbought
    )

    mean_reversion_long = bool(
        regime_bull
        and trend_up
        and rsi_bounce_up
        and row["close"] > row["ema_regime"]   # still above 200 EMA
    )

    mean_reversion_short = bool(
        allow_short
        and regime_bear
        and trend_down
        and rsi_bounce_down
        and row["close"] < row["ema_regime"]
    )

    # =====================================================================
    # COMBINED ENTRY LOGIC
    # =====================================================================
    vol_ok = row["volume_ratio"] >= cfg.min_volume_ratio

    long_entry = False
    short_entry = False
    entry_type = "none"

    # --- LONG entries ---
    if regime_bull and not is_greed:
        # Squeeze breakout long (highest conviction)
        if (squeeze_long or strong_breakout_up) and vol_ok and slope_up:
            long_entry = True
            entry_type = "squeeze_breakout"
        # Mean reversion long (buy the dip in uptrend)
        elif mean_reversion_long:
            long_entry = True
            entry_type = "mean_reversion"

    # --- SHORT entries ---
    if allow_short and regime_bear and not is_fear:
        if (squeeze_short or strong_breakout_down) and vol_ok and slope_down:
            short_entry = True
            entry_type = "squeeze_breakout"
        elif mean_reversion_short:
            short_entry = True
            entry_type = "mean_reversion"

    # =====================================================================
    # EXIT LOGIC
    # =====================================================================
    # Exit long: regime flips OR trend reverses OR RSI extremely overbought
    long_exit = bool(
        regime_bear
        or (trend_down and not slope_up)
        or row["rsi"] > 80  # take profit on extreme greed
    )

    # Exit short: regime flips OR trend reverses OR RSI extremely oversold
    short_exit = bool(
        regime_bull
        or (trend_up and not slope_down)
        or row["rsi"] < 20  # cover on extreme fear
    )

    # Build reason string
    if long_entry:
        reason = f"long_{entry_type}"
        if is_fear:
            reason += "_fear_zone"
    elif short_entry:
        reason = f"short_{entry_type}"
        if is_greed:
            reason += "_greed_zone"
    else:
        reason = "no_entry"

    return Decision(
        long_entry=long_entry,
        short_entry=short_entry,
        long_exit=long_exit,
        short_exit=short_exit,
        atr_value=float(row["atr"]),
        reason=reason,
        sentiment=sentiment,
        entry_type=entry_type,
    )
