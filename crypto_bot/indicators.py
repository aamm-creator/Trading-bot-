from __future__ import annotations

import pandas as pd

from .config import StrategyConfig


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def with_features(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    out = df.copy()

    out["ema_fast"] = out["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=cfg.ema_slow, adjust=False).mean()
    out["atr"] = _atr(out, cfg.atr_period)
    out["atr_pct"] = out["atr"] / out["close"]

    out["breakout_high"] = (
        out["high"].rolling(cfg.breakout_lookback).max().shift(1)
    )
    out["breakout_low"] = (
        out["low"].rolling(cfg.breakout_lookback).min().shift(1)
    )

    out["vol_sma"] = out["volume"].rolling(20).mean()
    out["volume_ratio"] = out["volume"] / out["vol_sma"]

    atr_rank_window = 100
    out["atr_rank"] = (
        out["atr_pct"]
        .rolling(atr_rank_window)
        .rank(pct=True)
        .fillna(0.0)
    )

    return out