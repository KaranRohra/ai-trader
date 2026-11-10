---
name: strategy-lab
description: Monthly self-improvement for the AI Trader - studies the shadow book (every near-miss and signal with its later outcome), the journal and veto results, tests one idea against the full-history backtest, and queues at most one change for the user's approval. Nothing changes without the user picking Apply. Use on the first trading day of each month or when the user asks the bot to learn or improve.
---

# Strategy Lab (self-improvement)

**Golden rule: you only *suggest*.** Every change goes into the queue (`lab.py propose`) and is applied only when the user picks **Apply** at the start of a `trading-day` run. Risk rules are locked; `lab.py bounds` lists what may change and the allowed ranges.

Scripts: `LAB` = `scripts/lab.py`, `SCAN` = `<base>/../momentum-scan/scripts/scan.py`.

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
3. **One parameter idea at most.** Only if a bucket in `features` shows a clear, sizeable difference with n ≥ 30 (e.g. RSI 75–80 entries doing much worse than 60–75):
   a. Write the candidate config: copy `~/ai-trader/config.json` to `~/ai-trader/learning/candidate.json` and change only that parameter, inside its bounds.
   b. Backtest both on the same period: `python3 SCAN backtest --from 2016-01-01 --config <file> --out <folder>`, for the current config and for the candidate.
   c. Robustness: also backtest one step either side of the candidate value (e.g. rsi_max 74 and 76 around 75).
   d. **Gate.** Propose only if the candidate beats the current config on `cagr_to_maxdd` in **both** in-sample and out-of-sample, its max drawdown is no worse by more than 2 points, and both neighbours also beat the current config. Otherwise record "tested, not proposed" in the lab note and stop.
   e. `LAB propose --type parameter --title "..." --change '{"rsi_max": 75}' --evidence "<IS/OOS cagr_to_maxdd before→after, maxDD, trades, neighbours>"`.
4. **Execution tweaks** (`--type execution`: `limit_buffer_pct`, `max_gap_pct`). Only with ≥ 20 entries' worth of evidence from the journal, such as many `unfilled` or `skipped_gap` decisions followed by strong moves.
5. Write a short lab note to `~/ai-trader/learning/lab_<YYYY-MM>.md`: what was examined, what was found, what was queued and what was tested but not proposed. Tell the user what's waiting. They'll be asked at the next `/trading-day`.

## After approval (handled by lab.py)
- **Lesson:** added to `lessons.md`, which the analyst reads every run.
- **Parameter:** the first Apply starts a 4-week paper trial running alongside. `trading-day` logs the trial's signals, and `LAB trial-report` compares them with the current config's signals. When the trial ends, the user is asked again whether it goes live. That creates a new version (v1.0 → v1.1), saves the config to `learning/configs/`, and adds a `changelog.md` entry.
- **Rollback:** `LAB rollback-check` flags when live drawdown exceeds the version's backtest worst drawdown. It queues a rollback suggestion, and the user decides.
