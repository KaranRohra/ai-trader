---
name: backtest
description: Backtest any AI Trader strategy (the active one, a ready-made one, the user's own, or a setup draft) on years of free NSE data, with the period and starting capital the user picks. Opens a full HTML report (equity vs the strategy's benchmark index, drawdown, performance metrics, trade analysis, every trade) and shows a summary in chat. Use when the user says "backtest", runs /backtest, or asks how a strategy would have done in the past or before trading it with real money.
---

# Backtest

Replays a strategy over past NSE data **exactly like the live bot trades it**: the same `strategy.py` logic, settings and risk rules, signals at the close, LIMIT buys the next morning, stops and targets like orders waiting at the broker (GTT), delivery costs (the active broker's brokerage and DP charge; Zerodha's by default). The engine lives in the scanner skill.

Scripts: `SCAN` = `<this skill's base directory>/../scanner/scripts/scan.py`, `STRAT` = `<base>/../strategies/scripts/strategy.py`. Both print JSON.

## Rules for you
1. **Neutral tone.** Never call a result good, bad, safe or better than another. State the numbers next to the benchmark index and let the user decide. Always say a backtest is history, not a forecast.
2. **Pop-ups**: AskUserQuestion, at most 4 questions per call, first option = the usual choice. The user can always type their own value.
3. **Never change a strategy here.** This skill only tests. Changes go through the `strategies` skill (setup flow + review screen).
4. Show `summary_card` **exactly as returned, inside a code block**. Never retype or round its numbers.
5. Don't show code-level errors. If a script fails, explain in plain words what to do.

## The flow

### Step 1 · Which strategy?
`python3 STRAT list`. Ask with one pop-up, in this order:
- the **active** strategy (label "<name> (active)");
- any other strategy the user set up (`set_up_by_you`);
- ready-made strategies they haven't set up, described as "uses its suggested risk settings";
- a setup in progress (`draft_in_progress`) as "<name> (setup in progress)".

The strategy reference for the next steps: its `id`, or for a setup in progress the folder `~/ai-trader/strategies/drafts/<id>`.
If the backtest fails with "no risk settings to test with", the strategy has no risk answers yet: tell the user its risk questions come first and offer to start them (the `strategies` skill).

### Step 2 · Period and capital (one pop-up, 2 questions)
- **Period**: "Last 10 years" (from 2016-01-01, first) / "Last 5 years" / "Last 3 years". Custom dates via "Other": earliest start 2014-01-01, at least 6 months long.
- **Starting capital**: their bot capital from `~/ai-trader/config.json` (first, e.g. "₹1,00,000 (your bot capital)") / "₹1,00,000" / "₹5,00,000". Leave out duplicates.

### Step 3 · Data check
`python3 SCAN data-plan --strategy <ref> --from <start> [--to <end>]` (it adds the warm-up months the indicators need).
- `download_needed: false` and `benchmark_download_needed: false` → go to step 4.
- Only `benchmark_download_needed: true` → no question needed (small files): run `python3 SCAN update-indices --from <start> [--to <end>]` and tell the user in one line ("Reading <benchmark> values, about <benchmark_est_minutes> min").
- `download_needed: true` → ask once with a pop-up: "The backtest needs **<est_trading_days> days** of NSE data you don't have yet: about **<est_mb> MB**, roughly **<est_minutes> min**, from the free official NSE archive (no login). It's a one-time download; later backtests only fetch new days." Options: **Download now** / **Shorter period** (back to step 2) / **Cancel**.
- Download one chunk per command, in order, using `chunks` from the data-plan: `python3 SCAN update --from <chunk from> --to <chunk to>`. After each chunk, tell the user in one line (e.g. "2018 ✓ · 5 of 12 years done"). If the user stops it or a command fails, finished days are kept, and running the same chunks again continues where it stopped. Days downloaded this way already include every benchmark index.

### Step 4 · Run it
`python3 SCAN backtest --strategy <ref> --from <start> [--to <end>] --capital <capital> --open`
- **Benchmark**: the report compares with the strategy's own index (its universe: NIFTY 50/100/200/500, Midcap 150 or Smallcap 250), or the `benchmark` its `strategy.json` names (`"none"` = no comparison). Don't ask about it. If the user asks to compare with another index for this run, add `--benchmark <id>` (also to the data-plan command); to change it for good, use the `strategies` skill. NIFTY Midcap 150 and Smallcap 250 values start 2016-04-01; the report says so when the period starts earlier.
- It takes about a minute or two on 10 years. Tell the user it's running.
- `"problem": "data_missing"` → go back to step 3 with the `data_plan` it returns.
- It writes everything to the run's `output_folder` (`~/ai-trader/backtests/<strategy>_<version>_<from>_<to>/`):
  - `report.html`: the full report, opened in the browser, in tabs: Overview (equity, drawdown, year by year, first vs second half, month by month) · Performance metrics (Sharpe, Sortino, Calmar, volatility, drawdowns, beta/alpha, trade stats, each next to the benchmark) · Trade analysis (returns distribution, winning/losing streaks, time patterns, exits, skipped signals) · All trades (filters: winners/losers, exit, year, stock) · Strategy & method;
  - `summary.json`: every number;
  - `trades.csv`: every trade, opens in Excel or Google Sheets;
  - `equity.csv`: daily value next to the benchmark index.

### Step 5 · Show the result
1. Show `summary_card` in a code block.
2. Add 2–4 plain lines of what stands out, neutrally. For example:
   - the years it lost money;
   - how the first half of the period (`in_sample`) compares with the second half (`out_of_sample`, from `split_date`);
   - the risk-adjusted numbers next to the benchmark (`full.performance`: Sharpe, Sortino, Calmar), stated without judging them;
   - the longest losing streak (`analysis.streaks.trades.loss.max`), since that is what living through the strategy feels like;
   - `full.avg_capital_invested_pct` (how much of the money was actually invested);
   - how many signals were skipped for cash or slots (`full.signals_skipped`).
3. Say the report is open in the browser and give the path to the folder.
4. Mention when relevant:
   - the risk rules used (`risk_used`): for a ready-made strategy the user hasn't set up, these are its *suggested* rules, not theirs;
   - limits: daily end-of-day data, NSE cash stocks, long only, entries the next morning, and a traded-value stand-in for past index lists. Slippage beyond the limit price and taxes aren't modelled.

### Step 6 · What next? (one pop-up)
- **Set up / change this strategy**: hand over to the `strategies` skill (the setup flow there can show this backtest on its review screen).
- **Backtest another period or strategy**: back to step 1.
- **Done**.

## During strategy setup
The `strategies` skill calls this flow from its optional "Backtest it before you start?" step with the **draft folder** as the reference. Skip step 1 and step 6. After step 5, return the `summary.json` path from `output_folder` so the review screen can show it (`STRAT review --id <id> --backtest <output_folder>/summary.json`).

## Questions users ask
- **"What is it compared with?"** `benchmark.name` in the summary: the strategy's own index unless its settings name another. `benchmark.id: none` means the strategy has no benchmark: the report then shows only the strategy's own numbers.
- **"What is the first half vs second half table?"** The tested period cut in two at its middle date, each half with its own CAGR, drawdown, Sharpe and trades next to the benchmark. It shows whether the results came from one part of the period only. `--split <date>` cuts at a chosen date instead.
- **"What do Sharpe / Sortino / Calmar mean?"** Sharpe: yearly return above a 6% risk-free rate divided by volatility. Sortino: the same, divided by downside volatility only. Calmar: CAGR divided by max drawdown. Each is shown next to the benchmark index for the same days; the report's Performance metrics tab explains every number in one line.
- **"Why is it different from my live results?"** Fills, slippage, the exact index list and timing differ, and a backtest can't know the future. The strategy-lab compares live results with the backtest every month.
- **"Can I test a change?"** Make the change in the `strategies` skill, then backtest the draft before confirming it.
- **"Where are my old backtests?"** `~/ai-trader/backtests/`: one folder per run, each with its own `report.html`.
