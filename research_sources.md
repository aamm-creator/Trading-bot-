# Sourced Strategy Notes (X + Research)

Date compiled: 2026-03-09

## Important caveat
Claims on X are usually not independently audited. I treated X as idea generation and only kept patterns that also have support in published research or exchange microstructure docs.

## Strategy families worth automating first

### 1) Regime-based momentum + mean reversion
- X source: Koroush AK thread says his two most profitable styles are momentum (trending markets) and mean reversion (choppy/ranging markets), with explicit criteria like trend structure, MA fan-out, high-volume breakouts for momentum and resistance/support rejection for mean reversion.
- Why this survives filtering: academic crypto literature repeatedly finds momentum effects in returns.
- Bot translation: trade breakouts only when trend and volatility filters are aligned; avoid forcing trades in chop.

Sources:
- https://x.com/KoroushAK/status/1822303054131032352
- https://en.rattibha.com/thread/1822303054131032352
- https://www.nber.org/papers/w24877
- https://www.nber.org/papers/w25882

### 2) Funding-rate and open-interest confluence (derivatives)
- X sources: Daan Crypto threads explain funding mechanics and how extreme one-sided funding + OI context can create squeeze risk and contrarian opportunities.
- Why this survives filtering: perpetual pricing is explicitly anchored by funding mechanics in formal models.
- Bot translation: if you trade perp markets, add a filter that blocks crowded-side entries when funding/OI is extreme.

Sources:
- https://x.com/DaanCrypto/status/1603419538879569922
- https://x.com/DaanCrypto/status/1733147206297735633
- https://en.rattibha.com/thread/1603419538879569922
- https://en.rattibha.com/thread/1733147206297735633
- https://www.nber.org/papers/w32936

### 3) Execution edge and cost control
- Exchange docs show fee drag is often the largest hidden killer for small accounts.
- Bot translation: maker-first orders, low turnover, hard stop-loss, and daily loss circuit breaker.

Sources:
- https://www.kraken.com/features/fee-schedule
- https://support.kraken.com/articles/201893638-how-trading-fees-work-on-kraken

## Kraken-specific constraints for this bot
- Margin leverage is pair-dependent; many pairs remain below 10x while selected pairs reached 10x on Kraken Pro in 2025.
- API margin params and exchange-specific behaviors differ by endpoint and pair.

Sources:
- https://support.kraken.com/en-it/articles/227876608-margin-trading-pairs-and-their-maximum-leverage
- https://blog.kraken.com/product/pro/margin-increased-up-to-10x
- https://blog.kraken.com/product/pro/10x-increased-margin
- https://docs.kraken.com/api/docs/websocket-v1/addorder

## Why Kraken for your use case
- Good API support, regulated venue, EUR pairs, and now some 10x margin pairs.
- For very small capital (10 EUR), fee/slippage control matters more than raw leverage. This bot defaults to maker-only and low-risk position sizing to reduce fee bleed.