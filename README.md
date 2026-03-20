# Kraken Crypto Trading Bot (Momentum + Risk Controls)

This project includes:
- Sourced strategy notes from X + research: `research_sources.md`
- A backtester using live exchange OHLCV data
- A live trading loop for Kraken (paper mode by default)
- Strict risk controls for small accounts

## What strategy is implemented
The bot implements a momentum-breakout model inspired by trader playbooks and research-backed momentum effects:
- Trend filter: `EMA(20) > EMA(50)` for longs (and inverse for shorts if enabled)
- Breakout filter: close breaks recent Donchian high/low
- Volume filter: breakout must occur with above-average volume
- Volatility regime filter: ATR percentile gate to avoid dead markets
- Exits: ATR stop-loss, ATR trailing stop, take-profit RR target, and trend reversal exit

Default config is conservative and long-only.

## Warning
No strategy guarantees profit. With `10 EUR`, fees can dominate results quickly, especially with leverage and market orders.

## Files
- `bot.py`: CLI entrypoint
- `config.example.yaml`: template config
- `crypto_bot/live.py`: live/paper loop
- `crypto_bot/backtest.py`: historical simulation
- `crypto_bot/strategy.py`: signal logic
- `crypto_bot/risk.py`: sizing + circuit breaker
- `crypto_bot/exchange_client.py`: Kraken execution wrapper
- `research_sources.md`: sources and rationale

## Setup
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy config.example.yaml config.yaml
```

## Backtest first
```bash
python bot.py --config config.yaml backtest --candles 3000
```

Output metrics print to terminal and trades are saved to `backtest_trades.csv`.

## Paper trade (recommended before live)
Ensure in `config.yaml`:
- `trading.dry_run: true`

Run:
```bash
python bot.py --config config.yaml trade
```

Runtime files:
- `state.json`
- `trades.csv`

## Switch to live Kraken trading
1. Create Kraken API key with trading permissions.
2. Put key/secret/passphrase in `config.yaml`.
3. Keep `symbol` on a margin-enabled pair and verify its max leverage on Kraken.
4. Set `trading.dry_run: false`.
5. Start with low leverage (for example `2` or `3`) and only increase after stable results.

## Suggested config for your 10 EUR start
- `exchange.leverage: 2` to `3` initially
- `trading.max_notional_eur: 20` to `30`
- `trading.risk_per_trade: 0.003` to `0.005`
- `trading.maker_only: true`
- `trading.allow_short: false` until proven stable

## Notes on Kraken leverage
Kraken increased leverage up to 10x for selected pairs in 2025, but limits remain pair-specific and can change. Always verify current pair limits in Kraken support docs before live use.