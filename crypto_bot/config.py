from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ExchangeConfig:
    id: str
    api_key: str
    api_secret: str
    api_passphrase: str
    symbol: str
    timeframe: str
    use_margin: bool
    margin_mode: str
    leverage: float


@dataclass
class TradingConfig:
    dry_run: bool
    paper_equity_eur: float
    poll_seconds: int
    candles_limit: int
    allow_short: bool
    maker_only: bool
    risk_per_trade: float
    risk_per_trade_fear: float       # higher risk when sentiment = fear (buy the dip)
    max_daily_drawdown: float
    max_notional_eur: float
    fee_rate_maker: float
    fee_rate_taker: float


@dataclass
class StrategyConfig:
    # EMAs
    ema_fast: int
    ema_slow: int
    ema_regime: int                  # 200 EMA for bull/bear regime

    # Bollinger Bands
    bb_period: int                   # Bollinger Band period (20)
    bb_std: float                    # Bollinger Band std dev (2.0)

    # Keltner Channels
    kc_period: int                   # Keltner Channel period (20)
    kc_atr_mult: float               # Keltner Channel ATR multiplier (1.5)

    # Squeeze
    squeeze_lookback: int            # How many bars squeeze must persist

    # Breakout (kept for compatibility)
    breakout_lookback: int

    # ATR / stops
    atr_period: int
    atr_stop_mult: float
    rr_take_profit: float

    # Volume
    min_volume_ratio: float

    # ADX
    adx_period: int
    min_adx: float

    # EMA slope
    ema_slope_lookback: int

    # RSI mean reversion (Layer 3)
    rsi_period: int                  # RSI period (14)
    rsi_oversold: float              # RSI buy-the-dip threshold (35)
    rsi_overbought: float            # RSI short threshold (65)

    # Sentiment proxy
    sentiment_lookback: int          # lookback for fear/greed proxy (90 bars)
    fear_threshold: float            # below this = fear (buy more aggressively)
    greed_threshold: float           # above this = greed (reduce size / take profit)


@dataclass
class StorageConfig:
    state_file: str
    trades_file: str
    backtest_trades_file: str


@dataclass
class BotConfig:
    exchange: ExchangeConfig
    trading: TradingConfig
    strategy: StrategyConfig
    storage: StorageConfig


def _required(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise KeyError(f"Missing required config field: {key}")
    return data[key]


def load_config(path: str | Path) -> BotConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

    exch = _required(raw, "exchange")
    trading = _required(raw, "trading")
    strategy = _required(raw, "strategy")
    storage = _required(raw, "storage")

    return BotConfig(
        exchange=ExchangeConfig(
            id=str(_required(exch, "id")),
            api_key=str(exch.get("api_key", "")),
            api_secret=str(exch.get("api_secret", "")),
            api_passphrase=str(exch.get("api_passphrase", "")),
            symbol=str(_required(exch, "symbol")),
            timeframe=str(_required(exch, "timeframe")),
            use_margin=bool(exch.get("use_margin", False)),
            margin_mode=str(exch.get("margin_mode", "isolated")),
            leverage=float(exch.get("leverage", 1.0)),
        ),
        trading=TradingConfig(
            dry_run=bool(trading.get("dry_run", True)),
            paper_equity_eur=float(trading.get("paper_equity_eur", 10.0)),
            poll_seconds=int(trading.get("poll_seconds", 60)),
            candles_limit=int(trading.get("candles_limit", 400)),
            allow_short=bool(trading.get("allow_short", False)),
            maker_only=bool(trading.get("maker_only", True)),
            risk_per_trade=float(trading.get("risk_per_trade", 0.01)),
            risk_per_trade_fear=float(trading.get("risk_per_trade_fear", 0.02)),
            max_daily_drawdown=float(trading.get("max_daily_drawdown", 0.05)),
            max_notional_eur=float(trading.get("max_notional_eur", 50000.0)),
            fee_rate_maker=float(trading.get("fee_rate_maker", 0.001)),
            fee_rate_taker=float(trading.get("fee_rate_taker", 0.001)),
        ),
        strategy=StrategyConfig(
            ema_fast=int(strategy.get("ema_fast", 20)),
            ema_slow=int(strategy.get("ema_slow", 50)),
            ema_regime=int(strategy.get("ema_regime", 200)),
            bb_period=int(strategy.get("bb_period", 20)),
            bb_std=float(strategy.get("bb_std", 2.0)),
            kc_period=int(strategy.get("kc_period", 20)),
            kc_atr_mult=float(strategy.get("kc_atr_mult", 1.5)),
            squeeze_lookback=int(strategy.get("squeeze_lookback", 6)),
            breakout_lookback=int(strategy.get("breakout_lookback", 20)),
            atr_period=int(strategy.get("atr_period", 14)),
            atr_stop_mult=float(strategy.get("atr_stop_mult", 2.5)),
            rr_take_profit=float(strategy.get("rr_take_profit", 3.0)),
            min_volume_ratio=float(strategy.get("min_volume_ratio", 1.2)),
            adx_period=int(strategy.get("adx_period", 14)),
            min_adx=float(strategy.get("min_adx", 20.0)),
            ema_slope_lookback=int(strategy.get("ema_slope_lookback", 5)),
            rsi_period=int(strategy.get("rsi_period", 14)),
            rsi_oversold=float(strategy.get("rsi_oversold", 35.0)),
            rsi_overbought=float(strategy.get("rsi_overbought", 65.0)),
            sentiment_lookback=int(strategy.get("sentiment_lookback", 90)),
            fear_threshold=float(strategy.get("fear_threshold", 25.0)),
            greed_threshold=float(strategy.get("greed_threshold", 75.0)),
        ),
        storage=StorageConfig(
            state_file=str(storage.get("state_file", "state.json")),
            trades_file=str(storage.get("trades_file", "trades.csv")),
            backtest_trades_file=str(
                storage.get("backtest_trades_file", "backtest_trades.csv")
            ),
        ),
    )
