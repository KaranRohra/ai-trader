---
name: scanner
description: The AI Trader's scanner - runs any strategy (its strategy.py logic + strategy.json settings; the active one by default) on free NSE end-of-day data (no login), returns signals with limit price, stop, target and size, and checks open positions for stops, targets, trailing stops, sell rules and position rules. Use to scan for signals, explain why a stock did or didn't qualify, or update NSE data.
---

# Scanner

`scripts/scan.py` applies the strategy's rules exactly. Claude never computes indicators by hand and never changes a rule while trading. Strategies are chosen and edited with the `strategies` skill; rule changes from learning go through `strategy-lab` with the user's approval.

## Data
- Official NSE end-of-day files from the NSE archives: the daily price file (bhavcopy), index closes, and index constituent lists (NIFTY 50/100/200/500, Midcap 150, Smallcap 250). Free, no login, cached in `~/ai-trader/data/`.
- Splits, bonuses and demergers are detected from the overnight price jump (a >20% gap that lands on a standard ratio). Older prices are then adjusted, so there are no fake breakouts.
- Signals use **yesterday's official close**. Orders go in the next morning.

## How a strategy is applied (`scripts/engine.py`)
Each strategy is a folder: `strategy.json` (settings) + `strategy.py` (logic). The same script drives the daily scan and the backtest engine used by the strategy-lab.
1. **Universe:** the strategy's index list, plus its minimum price and liquidity.
2. **The script** (`prepare(stock, market, p)`) runs once per stock on its full daily history. It returns, per day, its buy `checks` (or `buy`), a `score`, and optionally `sell`, `stop_level` and `trail_level`. Helpers live in `scripts/aitrader_ta.py`.
3. **Ranking:** stocks are ranked each day by `score` (1 = highest; most liquid first if there's no score). With `max_rank`, only the top N may be bought.
4. **Market filter:** the `regime` rule from the strategy's risk block, e.g. `nifty500 > nifty500_sma(200)`. If it fails, there are no new entries (exits still run).
5. **Stop / target / trailing stop / time exit** come from `strategy.json`, and **sell** signals from the script (sold the next morning).
6. **Size** comes from the user's risk block: `risk` sizing = equity × pct ÷ stop distance; `fixed_pct` = equity × pct ÷ price; capped by `max_position_pct`.
7. **Guardrails** (run by the strategies skill's `check`):
   - a static safety check: allowed imports only, no files, network or eval;
   - the look-ahead test: answers for past days must not change when later days are added.

## Commands
- `python3 scripts/scan.py update` downloads missing days.
- `python3 scripts/scan.py scan --equity 100000` scans with the active strategy. The JSON has:
  - `strategy`, `strategy_version`, and `risk_confirmed` (false = the user hasn't answered this strategy's risk questions, so **don't trade it**);
  - `regime.new_entries_allowed`;
  - `signals`, each with `limit_price`, `skip_if_open_above`, `stop_type`, then `stop_level` (fixed chart level) or `stop_distance` (₹, measured from the fill), `target_type`/`target_value`/`target_est`, `trail`, `qty_est` and `priority`;
  - `positions`, each judged by **the strategy that opened it**, with `exit`, `stop_hit_price`, `target_hit_price`, `new_stop`, `raise_stop`/`lower_stop`.
  It also adds near-misses (stocks failing at most 2 rules) to the shadow book for learning.
- `--strategy <id or folder>` scans another strategy, `--date YYYY-MM-DD --no-shadow` scans a past date.
- `python3 scripts/scan.py outcomes` fills forward returns into the shadow book.
- `python3 scripts/scan.py backtest --strategy <id> --from 2016-01-01` replays a strategy over history. It's used by the strategy-lab to test ideas before suggesting them; it isn't part of strategy setup:
  - The universe is a monthly point-in-time proxy for the strategy's index, built from 6-month traded value (NIFTY 500 = top 500, Midcap 150 = ranks 101–250, ...), including delisted stocks.
  - It counts Zerodha delivery costs, and simulates the weekly loss limit and drawdown halt (`--no-limits` to turn them off).
  - It reports the full period, in-sample (before 2021), out-of-sample (2021 onward), year by year, the worst week and month, and `risk_suggestions` for the risk questions.
  - `--set key=value` changes one setting for a run, e.g. `--set params.rsi_max=75 --set risk.weekly_loss_limit_pct=null`.
  - `--sweep key=v1,v2,...` compares several values on the same data, e.g. `--sweep risk.sizing.pct=1,1.5,2`.
  - A strategy whose risk isn't confirmed yet is tested with its suggested risk block, and the output says so (`risk_used`).

## Explaining a stock
If the user asks why a stock was or wasn't picked, run a scan with `--no-shadow` and look for the stock in the output. Only stocks that pass every rule are listed. For a stock that isn't listed, check the shadow book (`~/ai-trader/learning/shadow_book.csv`, filtered by `strategy`): its `failed` column names the rules it missed.
