---
name: momentum-scan
description: The Momentum Breakout strategy rules for the NIFTY 500 and the scanner script that applies them on free NSE end-of-day data (no login). Use to scan for signals, explain why a stock did or didn't qualify, update NSE data, or backtest the rules.
---

# Momentum Breakout: the strategy

The script `scripts/scan.py` applies these rules exactly. Claude never computes indicators by hand and never changes a rule while trading. Rule changes go only through `strategy-lab` with the user's approval.

## Data
- Official NSE end-of-day files from the NSE archives: daily price file (bhavcopy), index closes, and the NIFTY 500 list. Free, no login, cached in `~/ai-momentum-trader/data/`.
- Splits, bonuses and demergers are detected from the overnight price jump (a >20% gap that lands on a standard ratio). Older prices are then adjusted, so there are no fake breakouts.
- Signals use **yesterday's official close**. Orders go in the next morning.

## Rules (default version v1.0)
| Step | Rule |
|---|---|
| Universe | NIFTY 500, price ≥ ₹50, 20-day average traded value ≥ ₹10 cr |
| Market regime | NIFTY 500 index below its 200-day average → **no new entries** (exits still run) |
| Trend | close > 50-day average > 200-day average |
| Momentum score | (0.5 × 6-month return + 0.5 × 12-month return) ÷ 1-year volatility, ranked across the universe. Must be **top 50** |
| Breakout | close above the highest high of the previous 252 sessions (**52-week high**) |
| Volume | day's volume ≥ 1.5 × 50-day average |
| RSI(14) | between 60 and 80 |
| Not stretched | close no more than 10% above the 20-day EMA |
| Priority | strongest momentum score first, any number per day, up to 5 open positions |

**Orders:** LIMIT buy at signal close + 1%. Skip the stock if it opens more than 3% above the signal close.

**Stop-loss:** a fixed **14%**: entry − 14% of the signal close (the scan gives this as `stop_distance` in ₹).

**Target:** a fixed **1:2**, i.e. entry + 2 × (entry − stop), about **+28%**. Stop and target sit at Zerodha as **one two-leg GTT (OCO)**: whichever triggers first cancels the other.

**Exits:** only the stop or the target. There is no trailing and there are no rule exits.

**Sizing:** 2% risk per trade with a 14% stop, so each position is ~14.3% of equity. Max 10 positions, but cash usually limits it to ~7; extra signals are skipped for lack of cash.

**Backtest (2016-01 → 2026-11, ₹1L, Zerodha costs, idle cash earns nothing):**
- 19.5% CAGR, −20.4% max drawdown, 250 trades, 54% win rate.
- Losing years: 2018 −18.2%, 2022 −10.5%, 2025 −9.7%. Worst month −13.9% (March 2026).
- NIFTY 500 price index over the same period: 11.6% CAGR, −38.3% max drawdown.
- Why this setting: it is the middle of a stable area. Stops of 13–16% with a 1:2 target all gave 16.6–20.1% CAGR with −20% to −26% max drawdown. Sharper settings such as 10% / 1:3 scored well alone but collapsed one step away.
- Treat 19.5% as one point in that range, not a promise.

## Commands
- `python3 scripts/scan.py update` downloads missing days.
- `python3 scripts/scan.py scan --equity 100000` returns JSON with `regime`, `signals` (with `limit_price`, `skip_if_open_above`, `stop_distance`, `qty_est`) and `positions` (with `new_stop`, `exit`). It also appends near-misses to the shadow book for learning.
- `python3 scripts/scan.py scan --date 2026-10-30 --no-shadow` scans as of a past date.
- `python3 scripts/scan.py outcomes` fills forward returns into the shadow book.
- `python3 scripts/scan.py backtest --from 2016-01-01 [--config file.json]` replays the same rules over history:
  - The universe is the top 500 by 6-month traded value each month, including delisted stocks.
  - It counts Zerodha delivery costs.
  - It reports the full period, in-sample (before 2021) and out-of-sample (2021 onward).

## Explaining a stock
If the user asks why a stock was or wasn't picked, run a scan with `--no-shadow` and look for the stock in the output. Only stocks that pass every rule are listed. For a stock that isn't listed, check the shadow book (`~/ai-momentum-trader/learning/shadow_book.csv`): its `failed` column names the rules it missed.
