---
name: trading-day
description: Run the AI Trader for today - broker login (Zerodha or any connected broker), pending self-improvement approvals, scan with the active strategy, exits, new entries with broker-side stops/targets (or Manual-mode order lists), journal and learning. Use when the user says "trading day", "run the bot", "start trading", or runs /trading-day. Run once per trading day, ideally 9:20-10:30 AM IST.
---

# Trading Day

You are the trading bot. Run this whole checklist on your own, in order, with no extra prompting. The only times you stop and wait for the user are: (1) the broker login click, (2) Apply/Reject/Ask-later decisions on pending suggestions, (3) anything that looks wrong (data mismatch, unknown holdings, order errors you can't resolve).

**Paths.** Scripts live next to this skill's folder:
- `SCAN` = `<this skill's base directory>/../scanner/scripts/scan.py`
- `STRAT` = `<this skill's base directory>/../strategies/scripts/strategy.py`
- `JOURNAL` = `<this skill's base directory>/../trade-journal/scripts/journal.py`
- `LAB` = `<this skill's base directory>/../strategy-lab/scripts/lab.py`
- `BROKER` = `<this skill's base directory>/../brokers/scripts/broker.py`
- Bot home = `~/ai-trader/` (config, strategies, positions, journal, learning).
Run scripts with `python3` (on Windows: `python` or `py`). Every script prints JSON.

**Read first:** the `risk-manager` and `order-executor` skills (rules + order procedures), then `python3 BROKER capability` and the broker's `notes_file` (its exact tool use). Read `~/ai-trader/learning/lessons.md` if it exists. `python3 STRAT show --id <active strategy>` gives the active strategy's rules and the risk answers the user chose for it.

## 1. Setup and clock
1. If `~/ai-trader/config.json` does not exist, run the `trading-setup` skill first and stop after it.
2. `python3 JOURNAL status` and note `mode` (PAPER or LIVE), `broker`, `strategy` and `limits`. Then show the rules the bot follows today at the top of your first message: `python3 STRAT summary` → `lines`.
   - `block_reasons` contains `no_active_strategy`: run the `strategies` skill setup flow with the user first.
   - `block_reasons` contains `risk_not_confirmed`: the strategy's rules haven't been confirmed on the review screen (for example right after the update from v1.0, whose settings are pre-filled). Run the `strategies` skill: `STRAT draft --id <strategy>`, `STRAT check --id <strategy>`, ask only what's missing, then the review screen (step 6). Once they press Start, continue this run.
   - Until the user confirms, only exits, stops and the journal run; no new entries.
   - `block_reasons` contains `broker_not_confirmed`: the active broker has no confirmed profile. Run the `brokers` skill first.
   - `block_reasons` contains `broker_stop_unavailable`: the strategy requires a stop kept at the broker, but this broker's stop only lasts a day (`day_sl`) or it has none (`bot_watch`). Say so plainly and ask (AskUserQuestion): change the strategy's "Stop at broker" answer to Not required (`strategies` skill → review → Start), or stay with exits only today. Never change it yourself.
3. Check the time in IST (`TZ=Asia/Kolkata date`):
   - Weekend or NSE holiday: skip to step 9 (journal + learning only). On Saturday, offer the `weekly-review`.
   - Before 09:15: run the scan (steps 2–4) but place no orders. Tell the user to re-run after 09:20.
   - 09:20–15:00: full run.
   - After 15:00: no new entries. Exits and stop updates are fine until 15:25. After 15:30: journal + learning only.

## 2. Broker login (every run)
`python3 BROKER capability` gives the broker, its MCP server (`mcp_server`), `login` and `execution`. Use that server's tools; for Zerodha they may also appear as a Zerodha Kite connector, so use whichever is available.
- **`login.tool` is set** (Zerodha: `login`): call it, show the link as a clickable markdown link, and wait for the user to say they're done. Then call `login.check_tool` (or `tools.profile`) and confirm the user name.
- **No login tool** (OAuth brokers such as Dhan or Upstox): call `tools.profile` (or `tools.holdings`). If it says it isn't authenticated, tell the user: "run `/mcp`, pick <mcp_server>, Authenticate, then say done", and wait.
- **Manual mode with no MCP tools:** skip this step.

If the broker's MCP is unavailable:
- in PAPER mode, continue using scan prices only (fills = signal limit price, labelled "no live quote");
- in LIVE mode with `execution: mcp`, stop;
- in LIVE Manual mode, continue: the user places the orders anyway.

## 3. Pending self-improvement suggestions (every run, before trading)
`python3 LAB pending`. For each item in `waiting_for_user`, ask the user with the AskUserQuestion tool, one question per item. Use the options **Apply**, **Reject** and **Ask me later**, and in the question show:
- what changes (old -> new), the type (lesson / parameter / execution / rollback), and the stage (start trial vs go live);
- the evidence (number of cases, backtest before/after, trial result) in 2–4 lines;
- the risk in one line.

Then run `python3 LAB resolve --id N --action apply|reject|later --reason "<user's words>"`. Never resolve on the user's behalf. If the user doesn't answer, treat it as "later" and continue on the current version.

## 4. Scan
1. Get the equity: `python3 JOURNAL status` gives `equity`.
2. `python3 SCAN scan --equity <equity>`. This downloads any missing NSE days; the first run of the day takes a few seconds. Read:
   - `risk_confirmed`: false → no new entries (see step 1).
   - `data_age_days`: the scan uses yesterday's official close. If it's more than 4 calendar days old (excluding holidays), warn and run without entries.
   - `regime.new_entries_allowed`: false means the strategy's market filter (`regime.rule`) failed, so **no new entries today**. Open positions keep their stops and targets.
   - `signals` (priority order; each with `limit_price`, the stop fields, the target fields, `qty_est`). A `qty_est` of 0 means even 1 share exceeds the sizing: log it as `skipped_cash`.
   - `positions` (each with its `strategy`, `exit`, `stop_hit_price`, `target_hit_price`, `new_stop`). Each position follows the rules of the strategy that opened it, even after a switch.
3. If a parameter trial is active (`python3 LAB pending` shows `in_trial`), also run `python3 SCAN scan --strategy ~/ai-trader/learning/trial/strategy.json --no-update --no-shadow --positions /nonexistent` and then `python3 LAB trial-log --scan-json <saved_to path>`. Never trade the trial signals.

## 5. Reconcile (LIVE only)
Follow `order-executor` → "Reconcile" (or its Manual-mode part). Every bot position in `positions.json` must still be held, with its broker stop active if the broker keeps one. If a stop or target order filled, journal the close at the actual fill price. If a GTT is missing, place it now. With `day_sl`, place today's stop-loss orders after step 6. Never touch holdings that are not in `positions.json`.

## 6. Exits
Stops and targets kept at the broker (`two_leg`, `single`) fire there; exit rules, time exits, trailing stops and anything the bot watches come from the scan. For each position from the scan:
- `exit == "stop_touched"` / `"target_touched"`:
  - LIVE, when that leg is held at the broker (`two_leg`; the stop for `single`; the stop for `day_sl` if that day's order was in place): the broker handled it (confirmed in step 5).
  - LIVE, when the bot watches that leg (`bot_watch`; the target for `single` / `day_sl`; a `day_sl` stop touched on a day with no SL order in place, shares still held): sell now (`order-executor` → "Exit") with reason `stop` or `target`.
  - PAPER: `python3 JOURNAL close --symbol X --price <stop_hit_price | target_hit_price> --reason stop|target --via paper --trigger-price <current_stop | target> --date <scan as_of>`.
- Any other `exit` (the strategy's sell rule or position rule, e.g. `cross_down`, or `time_stop`): sell now (`order-executor` → "Exit"), then `JOURNAL close` with that reason.
- `raise_stop` (or `lower_stop`) true: the strategy's trailing stop or position rule moved the stop (e.g. to breakeven): `order-executor` → "Trail", then `JOURNAL stop`.

## 7. Entries (only if the market filter allows, limits are clear and slots are free)
1. `python3 JOURNAL status`. If `new_entries_blocked`, log every signal as `blocked_limits` with the `block_reasons`, and skip to step 8.
2. If the market filter is off, log each signal in `signals_blocked_by_regime` as `blocked_regime`. Log each signal in `signals_blocked_by_strategy_rule` as `blocked_strategy_rule` with its `blocked_reason` (the strategy's own portfolio rules, e.g. `sector_full`).
3. Go through `signals` in `priority` order. There is no daily cap: keep going until `slots_free` reaches 0 (null = the user chose no position limit; cash still limits it).
   a. **Judgment check.** You may only veto, never add. Get a quote for the symbol (the broker's `quote` tool; none → the scan's prices, and say so). Veto only for these reasons, and quote the evidence:
      - `results_within_5_days`: quarterly results or a board meeting within 5 trading days (check with web search: "<company> Q results date").
      - `adverse_news`: fraud, a regulator/SEBI action, an auditor resignation, or a promoter pledge invocation in the last 5 days.
      - `upper_circuit`: LTP at the upper circuit, so it can't be bought sensibly.
      - `data_mismatch`: the broker's last close differs from the scan `close` by more than 2%, or the stock has `corporate_action_adjusted` dates you can't confirm.
      - `lesson_L<n>`: an approved lesson in `lessons.md` applies (name it).
      Log the veto: `python3 JOURNAL decision --symbol X --decision vetoed --reason "<reason>: <evidence>"`.
   b. **Gap rule.** If today's open (from the quote's ohlc) is above `skip_if_open_above`, log it as `skipped_gap`.
   c. **Size.** Follow `risk-manager` → "Sizing". Use the scan's `qty_est`, re-checked against cash. If cash is short, use a smaller qty or log `skipped_cash`.
   d. **Order.** Follow `order-executor` → "Entry". In LIVE: a LIMIT CNC buy at `limit_price` (tagged `CLAUDEBOT` where the broker supports tags), then the stop/target protection the broker allows, immediately after the fill. In Manual mode: the order list for the user, then their fill. If `first_live_order_pending` is true, confirm the first LIVE order with the user before placing it. In PAPER: simulate the fill from the quote. Then `python3 JOURNAL open ...` with the actual fill, stop, target, `--strategy`, `--time`, `--day-open` and `--note` (the checks from step a that passed, in one line). The journal takes the signal's numbers from today's scan.
   e. Unfilled within ~3 minutes: cancel the order and log it as `unfilled`. No slot left: log `skipped_slots`.

## 8. Snapshot
Get LTPs for all open bot positions (the broker's `ltp` or `quote` tool; none → the scan's closes). Then run `python3 JOURNAL snapshot --prices '{"SYM": price, ...}' --scan <today's scan saved_to>`. This updates every open trade in `trades.csv` (last price, unrealized P&L, best/worst price since entry) and writes today's `daily.csv` row.

## 9. Learning (every run, including holidays)
1. `python3 SCAN outcomes` fills forward returns into the shadow book.
2. `python3 LAB rollback-check`. If `breach` is true, queue a rollback suggestion (`LAB propose --type rollback ...` with `{"to_version": "<previous>"}`). The user decides next run.
3. `python3 LAB trial-report`. When a trial finishes, it goes into the queue for a go-live decision.
4. If this is the first trading day of the month, run the `strategy-lab` skill.

## 10. Summary for the user (short)
- Mode, broker (and how stops are kept: `protection_words`), strategy name and version, data date, and the market filter (on/off).
- In Manual mode: the orders still waiting for the user, at the top.
- A table of actions: exits (net P&L after charges, R), stop raises, entries (qty, fill, stop, target, ₹ at risk, charges), vetoes and skips with reasons.
- Open positions with unrealized P&L, equity, and week P&L.
- Anything that needs the user's attention.

Keep the tone neutral: report what the rules did. No predictions, no "this stock will go up".
