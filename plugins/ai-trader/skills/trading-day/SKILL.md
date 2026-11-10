---
name: trading-day
description: Run the AI Trader for today - Kite login, pending self-improvement approvals, NIFTY 500 momentum scan, exits, new entries with broker-side two-leg GTT (stop + target), journal and learning. Use when the user says "trading day", "run the bot", "start trading", or runs /trading-day. Run once per trading day, ideally 9:20-10:30 AM IST.
---

# Trading Day

You are the trading bot. Run this whole checklist on your own, in order, with no extra prompting. The only times you stop and wait for the user are: (1) the Kite login click, (2) Apply/Reject/Ask-later decisions on pending suggestions, (3) anything that looks wrong (data mismatch, unknown holdings, order errors you can't resolve).

**Paths.** Scripts live next to this skill's folder:
- `SCAN` = `<this skill's base directory>/../momentum-scan/scripts/scan.py`
- `JOURNAL` = `<this skill's base directory>/../trade-journal/scripts/journal.py`
- `LAB` = `<this skill's base directory>/../strategy-lab/scripts/lab.py`
- Bot home = `~/ai-trader/` (config, positions, journal, learning).
Run scripts with `python3` (on Windows: `python` or `py`). Every script prints JSON.

**Read first:** the `risk-manager` and `order-executor` skills (rules + exact order procedures). Read `~/ai-trader/learning/lessons.md` if it exists.

## 1. Setup and clock
1. If `~/ai-trader/config.json` does not exist, run the `trading-setup` skill first and stop after it.
2. `python3 JOURNAL status` and note `mode` (PAPER or LIVE).
3. Check the time in IST (`TZ=Asia/Kolkata date`):
   - Weekend or NSE holiday: skip to step 9 (journal + learning only). On Saturday, offer the `weekly-review`.
   - Before 09:15: run the scan (steps 2–4) but place no orders. Tell the user to re-run after 09:20.
   - 09:20–15:00: full run.
   - After 15:00: no new entries. Exits and stop updates are fine until 15:25. After 15:30: journal + learning only.

## 2. Kite login (every run)
Use the Kite MCP tools. They may appear as the plugin's `kite` server or as a Zerodha Kite connector; use whichever is available. Call `login`, show the link as a clickable markdown link, and wait for the user to say they're done. Then call `get_profile` and confirm the user name.
If Kite is unavailable: in PAPER mode, continue using scan prices only (fills = signal limit price, labelled "no live quote"). In LIVE mode, stop.

## 3. Pending self-improvement suggestions (every run, before trading)
`python3 LAB pending`. For each item in `waiting_for_user`, ask the user with the AskUserQuestion tool, one question per item. Use the options **Apply**, **Reject** and **Ask me later**, and in the question show:
- what changes (old -> new), the type (lesson / parameter / execution / rollback), and the stage (start trial vs go live);
- the evidence (number of cases, backtest before/after, trial result) in 2–4 lines;
- the risk in one line.

Then run `python3 LAB resolve --id N --action apply|reject|later --reason "<user's words>"`. Never resolve on the user's behalf. If the user doesn't answer, treat it as "later" and continue on the current version.

## 4. Scan
1. Get the equity: `python3 JOURNAL status` gives `equity`.
2. `python3 SCAN scan --equity <equity>`. This downloads any missing NSE days; the first run of the day takes a few seconds. Read:
   - `data_age_days`: the scan uses yesterday's official close. If it's more than 4 calendar days old (excluding holidays), warn and run without entries.
   - `regime.new_entries_allowed`: false means NIFTY 500 is below its 200-DMA, so **no new entries today**. Open positions keep their GTT stop and target.
   - `signals` (priority order; each with `limit_price`, `stop_level`, `stop_pct`, `target_est`, `qty_est`). A `qty_est` of 0 means even 1 share exceeds the risk budget: log it as `skipped_cash`.
   - `positions` (each with `exit`, `stop_hit_price`, `target_hit_price`).
3. If a parameter trial is active (`python3 LAB pending` shows `in_trial`), also run `python3 SCAN scan --config ~/ai-trader/learning/trial/config.json --no-update --no-shadow --positions /nonexistent` and then `python3 LAB trial-log --scan-json <saved_to path>`. Never trade the trial signals.

## 5. Reconcile (LIVE only)
Follow `order-executor` → "Reconcile". Every bot position in `positions.json` must still be held with an ACTIVE GTT. If a GTT triggered, journal the close at the actual fill price (reason `stop`). If a GTT is missing, place it now. Never touch holdings that are not in `positions.json`.

## 6. Exits
Exits are the two-leg GTT: the stop or the 1:2 target. For each position from the scan:
- `exit == "stop_touched"` / `"target_touched"`:
  - LIVE: the GTT handled it (confirmed in step 5).
  - PAPER: `python3 JOURNAL close --symbol X --price <stop_hit_price | target_hit_price> --reason stop|target --date <scan as_of>`.
- Any other `exit` (only if a rule exit or time stop is enabled in config): sell now (`order-executor` → "Exit"), then `JOURNAL close` with that reason.
- `raise_stop == true` happens only if trailing is enabled in config: `order-executor` → "Trail", then `JOURNAL stop`.

## 7. Entries (only if the regime allows, limits are clear and slots are free)
1. `python3 JOURNAL status`. If `new_entries_blocked`, log every signal as `blocked_limits` and skip to step 8.
2. If the regime is off, log each signal in `signals_blocked_by_regime` as `blocked_regime`.
3. Go through `signals` in `priority` order. There is no daily cap: keep going until all 5 slots are full.
   a. **Judgment check.** You may only veto, never add. Get a Kite `get_quotes` for the symbol. Veto only for these reasons, and quote the evidence:
      - `results_within_5_days`: quarterly results or a board meeting within 5 trading days (check with web search: "<company> Q results date").
      - `adverse_news`: fraud, a regulator/SEBI action, an auditor resignation, or a promoter pledge invocation in the last 5 days.
      - `upper_circuit`: LTP at the upper circuit, so it can't be bought sensibly.
      - `data_mismatch`: Kite's last close differs from the scan `close` by more than 2%, or the stock has `corporate_action_adjusted` dates you can't confirm.
      - `lesson_L<n>`: an approved lesson in `lessons.md` applies (name it).
      Log the veto: `python3 JOURNAL decision --symbol X --decision vetoed --reason "<reason>: <evidence>"`.
   b. **Gap rule.** If today's open (from the quote's ohlc) is above `skip_if_open_above`, log it as `skipped_gap`.
   c. **Size.** Follow `risk-manager` → "Sizing". Use the scan's `qty_est`, re-checked against cash. If cash is short, use a smaller qty or log `skipped_cash`.
   d. **Order.** Follow `order-executor` → "Entry". In LIVE: a LIMIT CNC buy at `limit_price` with tag `CLAUDEBOT`, then the two-leg GTT (stop + target) immediately after the fill. In PAPER: simulate the fill from the quote. Then `python3 JOURNAL open ...` with the actual fill, stop and target, plus `--rank` and `--score`.
   e. Unfilled within ~3 minutes: cancel the order and log it as `unfilled`. No slot left: log `skipped_slots`.

## 8. Snapshot
Get LTPs for all open bot positions (Kite `get_ltp`). Then run `python3 JOURNAL snapshot --prices '{"SYM": price, ...}'`.

## 9. Learning (every run, including holidays)
1. `python3 SCAN outcomes` fills forward returns into the shadow book.
2. `python3 LAB rollback-check`. If `breach` is true, queue a rollback suggestion (`LAB propose --type rollback ...` with `{"to_version": "<previous>"}`). The user decides next run.
3. `python3 LAB trial-report`. When a trial finishes, it goes into the queue for a go-live decision.
4. If this is the first trading day of the month, run the `strategy-lab` skill.

## 10. Summary for the user (short)
- Mode, strategy version, data date, and regime (NIFTY 500 vs its 200-DMA).
- A table of actions: exits, stop raises, entries (qty, price, stop, ₹ at risk), vetoes and skips with reasons.
- Open positions with unrealized P&L, equity, and week P&L.
- Anything that needs the user's attention.

Keep the tone neutral: report what the rules did. No predictions, no "this stock will go up".
