---
name: strategy-lab
description: Monthly self-improvement for the AI Trader - per strategy, studies the shadow book (every near-miss and signal with its later outcome), the journal and veto results, tests one idea against the full-history backtest, and queues at most one change (rule parameter, execution or risk suggestion) for the user's approval. Nothing changes without the user picking Apply. Use on the first trading day of each month or when the user asks the bot to learn or improve.
---

# Strategy Lab (self-improvement)

**Golden rule: you only *suggest*.** Every change goes into the queue (`lab.py propose`) and is applied only when the user picks **Apply** at the start of a `trading-day` run. `lab.py bounds` lists what the active strategy lets you tune and the ranges. Risk settings belong to the user: you may *suggest* a new answer (`--type risk`) with backtest evidence; it changes nothing until they apply it.

Scripts: `LAB` = `scripts/lab.py`, `SCAN` = `<base>/../scanner/scripts/scan.py`. Everything works on the active strategy by default; add `--strategy <id>` for another one that still has open positions.

## Why it's careful
The bot makes only ~20 real trades a year. Tuning on so few is curve-fitting. So learning uses:
1. **The shadow book.** Every day the scan records every near-miss and signal with its features, and their returns 5/10/20/60 sessions later. That's hundreds of examples a month.
2. **The full-history backtest gate.** An idea must also work over 10 years of data, not just last month.
3. **A 4-week forward trial** before a parameter goes live.

## Monthly steps
1. `python3 SCAN outcomes`, then `python3 LAB features --min-n 15`, `python3 LAB veto-report` and `python3 LAB bounds`.
2. **Lessons (Claude's judgment).** Look for a veto pattern backed by **at least 8 cases** in the shadow book or journal. For example, "breakouts with results inside 5 days returned −3% median over 20 days, n=11". Queue it:
   `LAB propose --type lesson --title "..." --lesson "<rule the analyst will apply>" --evidence "<numbers>" --cases N`.
   If `veto-report` says vetoes are not helping, or an active lesson's cases now do as well as the taken trades, queue `--type retire_lesson --change '{"lesson_id": "L<n>"}'`.
3. **One parameter idea at most.** Only if a bucket in `features` shows a clear, sizeable difference with n ≥ 30 (e.g. RSI 75–80 entries doing much worse than 60–75), and only for a parameter in `bounds` → `tunable_params`:
   a. Backtest the current strategy and the candidate on the same period in one go: `python3 SCAN backtest --from 2016-01-01 --sweep params.<name>=<current>,<candidate>`.
   b. Robustness: include one step either side of the candidate value in the same sweep (e.g. `params.rsi_max=80,74,75,76`).
   c. **Gate.** Propose only if the candidate beats the current setting on `cagr_to_maxdd` in **both** in-sample and out-of-sample (`is_cagr_to_maxdd`, `oos_cagr_to_maxdd`), its max drawdown is no worse by more than 2 points, and both neighbours also beat the current setting. Otherwise record "tested, not proposed" in the lab note and stop.
   d. `LAB propose --type parameter --title "..." --change '{"rsi_max": 75}' --evidence "<IS/OOS cagr_to_maxdd before→after, maxDD, trades, neighbours>"`.
4. **Execution tweaks** (`--type execution`: `limit_buffer_pct`, `max_gap_pct`). Only with ≥ 20 entries' worth of evidence from the journal, such as many `unfilled` or `skipped_gap` decisions followed by strong moves.
5. **Risk suggestions** (`--type risk`, rarely): only when live results or a new backtest show a user's risk answer is acting very differently than expected. For example, the weekly limit firing far more often than the backtest said, or capital sitting idle because of `max_positions`. Back it with a backtest sweep of that setting (`--sweep risk.<field>=...`), state the trade-off plainly, and propose: `LAB propose --type risk --title "..." --change '{"weekly_loss_limit_pct": 10}' --evidence "..."`. The question shows the user's current answer → the suggestion.
6. Write a short lab note to `~/ai-trader/learning/lab_<YYYY-MM>.md`: what was examined, what was found, what was queued and what was tested but not proposed. Tell the user what's waiting. They'll be asked at the next `/trading-day`.

## After approval (handled by lab.py)
- **Lesson:** added to `lessons.md`, which the analyst reads every run.
- **Parameter:** the first Apply starts a 4-week paper trial running alongside (`learning/trial/strategy.json`). `trading-day` logs the trial's signals, and `LAB trial-report` compares them with the current strategy's signals. When the trial ends, the user is asked again whether it goes live. That creates a new version of the strategy (v1.0 → v1.1), archives the old one in `learning/configs/<strategy>/`, and adds a `changelog.md` entry.
- **Execution / risk:** applied as a new strategy version right away (risk answers get a new `confirmed` date).
- **Rollback:** `LAB rollback-check` flags when live drawdown exceeds the version's backtest worst drawdown (recorded with `LAB record-backtest`). It queues a rollback suggestion, and the user decides.
