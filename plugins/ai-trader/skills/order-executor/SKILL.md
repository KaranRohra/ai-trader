---
name: order-executor
description: Order procedures for the AI Trader on any broker - LIMIT delivery entries, the broker-side stop and target (two-leg GTT/OCO, single GTT, day stop-loss order or bot-watched, as the broker allows and the strategy defines), trailing-stop updates, exits, reconciliation, Manual mode (the user places the orders the bot lists), PAPER-mode simulation and error handling. Use whenever the bot places, changes or checks an order.
---

# Order Executor

## The broker comes first
Run `python3 <this skill's base directory>/../brokers/scripts/broker.py capability` once per run. It tells you:
- `tools`: which MCP tool does each job (`quote`, `ltp`, `funds`, `holdings`, `orders`, `order_status`, `place_order`, `cancel_order`, `protect_list`, `protect_place`, `protect_modify`, `protect_cancel`). `null` means the broker's MCP has no such tool.
- `notes_file`: **read it**. It has that broker's exact tool arguments, tick rules and quirks (Zerodha: `brokers/library/zerodha.md`). The steps below say *what* to do; the notes file says *how*. When this file and the notes differ on an argument, follow the notes.
- `execution`: `mcp` (the MCP places orders) or `manual` (you list the orders and the user places them; see "Manual mode").
- `protection`: how the stop and target are kept. `two_leg`, `single`, `day_sl` or `bot_watch`; see "Protection levels".
- `supports_tag`: whether orders can carry the tag `CLAUDEBOT`.
- `first_live_order_pending`: true for a newly connected broker that the plugin author hasn't tested. Before the first LIVE order on it, show the user the exact tool call and arguments and get a yes (AskUserQuestion). After it fills correctly, run `broker.py first-order-done`.
- `ok: false` or `problems`: don't place LIVE orders. Tell the user to fix the broker profile with the `brokers` skill.

In the steps below, "the `place_order` tool" means the tool named in `tools.place_order`, and so on.

**Mode.** Read `mode` from `~/ai-trader/config.json`. In **PAPER** mode, never place, change or cancel anything at the broker; use quotes only. In **LIVE** mode follow the steps exactly.

**Prices.** Prices must be on NSE's tick grid. The scan already rounds `limit_price` and stops. For a price you compute yourself, round **down** to a multiple of 0.05 (under ₹1,000), 0.10 (₹1,000–5,000), 0.50 (₹5,000–10,000) or 1.00 (above that). If the broker rejects a tick size, round to the next coarser step and retry once.

**Bot orders.** Every order the bot places is NSE, delivery (CNC), with the tag `CLAUDEBOT` if `supports_tag` is true. Always save the order id; the bot's positions are the ones in `positions.json`, and its order ids are in the journal.

**No quote tool** (`tools.quote` and `tools.ltp` are null, e.g. Dhan without its Data API plan): use the scan's close as the price. Skip the gap and circuit checks, which need a live open, and say so in the decision note. The LIMIT price still caps what a buy can pay.

## Protection levels (how the stop and target are kept)
| `protection` | Stop | Target |
|---|---|---|
| `two_leg` | one two-leg GTT/OCO at the broker holds both | the same order |
| `single` | a single GTT at the broker | checked by the bot at each run: on `target_touched`, sell (see "Exit") |
| `day_sl` | a stop-loss SELL order (`order_type` SL, see `sl_order_args`), valid for the day; placed again on every run day | checked by the bot at each run |
| `bot_watch` | no order at the broker: on `stop_touched`, sell at the next run | checked by the bot at each run |

With `day_sl` and `bot_watch`, a position is unprotected between runs. `journal.py status` blocks LIVE entries (`broker_stop_unavailable`) for strategies whose risk says the broker stop is required.

## Entry
**LIVE (`execution: mcp`):**
1. With the `place_order` tool: BUY, the quantity, product CNC, order_type LIMIT, price = `limit_price`, validity DAY, the tag if supported. Then run `journal.py event --symbol X --event order_placed --price <limit> --qty <qty> --order-id <id>`.
2. Poll the `order_status` tool (or the `orders` tool) every ~30 s for up to 3 minutes:
   - **Complete:** the fill is the average price.
   - **Partially filled after 3 minutes:** cancel the rest with the `cancel_order` tool (`event --event order_partial --qty <filled>`) and continue with the filled qty.
   - **Nothing filled:** cancel the order (`event --event order_cancelled`) and log the decision `unfilled`.
   - **Rejected:** `event --event order_rejected --note "<the broker's reason>"`. Don't retry the same order; tell the user. For Groww, a rejected sell often means DDPI isn't enabled.
   - Note the fill's exchange time (HH:MM:SS) for the journal.
3. **Work out the stop and target from the fill** (the signal says which kinds the strategy uses):
   - **Stop:** `stop_level` if the scan gives one (chart-level stops), otherwise fill − `stop_distance` (pct and ATR stops), tick-rounded down. No `stop_type` means the strategy has no stop.
   - **Target:** with `target_type` r, fill + `target_value` × (fill − stop); with `target_type` pct, fill × (1 + `target_value`/100); tick-rounded up. No `target_type` means no target.
4. **Immediately** protect the position, as the broker's `protection` allows (sell side, CNC, quantity = filled qty, LTP from a fresh quote):
   - **`two_leg`:**
     - **Stop and target:** ONE two-leg GTT/OCO with the `protect_place` tool. The stop leg triggers at the stop with limit price stop × 0.98 (tick-rounded down), so it fills in a fast fall. The target leg triggers at the target with limit price target × 0.995 (tick-rounded down).
     - **Stop only:** a single GTT at the stop, limit price stop × 0.98.
     - **Target only:** a single GTT at the target, limit price target × 0.995.
   - **`single`:** a single GTT at the stop, the same way. If there's no stop, place a single GTT at the target. The bot watches the target.
   - **`day_sl`:** with the `place_order` tool, a stop-loss SELL order for the day at the stop (limit price stop × 0.98), using `sl_order_args`. Then run `event --event stop_order_placed --gtt-id <order id>`.
   - **`bot_watch`:** nothing at the broker.
   - **Neither a stop nor a target:** nothing at the broker. The position is closed only by the strategy's exit rules.
   - A GTT needs stop < LTP < target. If the LTP is already at or below the stop, sell at MARKET instead and journal `stop`.
   - Save the returned id. A failed attempt: `event --event error --note "<what failed>"`.
5. If the protecting order fails twice and the strategy's risk has `broker_stop_required: true`, sell the position at MARKET and journal the close with reason `broker_stop_failed`. If it's false, keep the position, journal it without an id and tell the user.
6. Run `journal.py open --symbol X --qty <filled> --price <avg fill> [--stop <stop>] [--target <target>] --strategy <id> --gtt-id <protection id> --order-id <id> --time <fill time> --day-open <quote's open> --note "<the judgment checks done>"`. The journal fills in the signal, plan and risk columns from the scan, and records the broker and protection level.

**PAPER:**
1. Get a quote. If its open is above `skip_if_open_above`, log `skipped_gap`.
2. If the LTP is at or below `limit_price`, the fill is the LTP. Otherwise log `unfilled`.
3. Work out the stop and target as in LIVE step 3. Then `journal.py open ... --stop --target --strategy --time <now> --day-open <open> --note "..."` (no GTT id, no order id).

## Exit
This covers a strategy exit rule, a time exit, a manual close, and a target or stop the bot watches.

**LIVE (`execution: mcp`):**
1. Remove the protection first:
   - GTT: delete it with the `protect_cancel` tool (`event --event gtt_deleted --gtt-id <id>`). If it has already triggered or is gone, check holdings first.
   - Day SL order: cancel it with the `cancel_order` tool (`event --event stop_order_cancelled`).
2. With the `place_order` tool: SELL, CNC, MARKET, quantity = the bot's qty only, the tag if supported (`event --event exit_order_placed --order-id <id>`).
3. Confirm the fill, then run `journal.py close --symbol X --price <avg fill> --reason <reason> --via market_sell --trigger-price <LTP when you decided to sell, or the watched stop/target> --order-id <id> --time <fill time> --note "<one line: what the rule saw>"`.

**PAPER:** `journal.py close ... --via paper` at the current LTP.

## Trail
When the scan says `raise_stop` or `lower_stop` for a position. `lower_stop` only appears when the strategy's risk has `stops_only_up: false`.
- **LIVE `two_leg` / `single`:** with the `protect_modify` tool, change the same GTT: the stop trigger becomes `new_stop`, its limit new_stop × 0.98. Keep any target leg and the qty unchanged.
  - If there is no modify tool or the change fails, delete the GTT and place a new one, and save the new id (`event --event gtt_replaced --gtt-id <new id> --old <old id>`).
  - If the position had no GTT yet, place a single stop GTT.
  - Then run `journal.py stop --symbol X --stop <new_stop> --gtt-id <id if changed> --reason <trail | breakeven | the strategy's rule>`.
- **LIVE `day_sl`:** cancel today's SL order, place a new one at `new_stop` (`event --event stop_order_placed --gtt-id <id>`), then run `journal.py stop`.
- **`bot_watch` and PAPER:** `journal.py stop ... --reason ...` only.

## Reconcile (LIVE, start of every run)
For each position in `positions.json`:
1. Look it up in the `holdings` tool's result (count settled + T1 qty), and find its protection: in the `protect_list` tool's result by id for a GTT, or in the `orders` tool's result for a day SL order.
2. **Held, and the protection is active** (or the broker's `protection` is `bot_watch`): OK.
3. **Not held, or the qty dropped:**
   - A GTT triggered: find the sell in the `orders` tool's result or the order history, or use the triggered leg's price. Then run `journal.py close --reason stop --via gtt_stop`, or `--reason target --via gtt_target` (whichever leg fired), with `--price <actual avg fill> --trigger-price <that leg's trigger> --order-id <sell order id> --time <fill time> --date <fill date>`.
   - A day SL order filled: `journal.py close --reason stop --via day_sl ...` the same way.
4. **Held, but the GTT is missing, cancelled or expired** (some brokers expire GTTs, Zerodha after 1 year): place a new one at the journal's stop (and target, if any) and record it with `journal.py event --symbol X --event gtt_replaced --gtt-id <new id> --note "<why>"` (this also updates the id). Positions the journal shows with no stop and no target never had a GTT; that's expected.
5. **`day_sl`:** yesterday's SL order has expired. Place today's at the journal's `stop` for every held position (`event --event stop_order_placed --gtt-id <id>`), after the exits in step 6 of trading-day have run.
6. **A qty mismatch you can't explain:** don't trade that symbol, and tell the user.

Holdings that aren't in `positions.json` belong to the user. Ignore them completely: never sell them, and never change or delete their GTTs or orders.

## Manual mode (`execution: manual`)
The user places every order in the broker's app; the bot does everything else. If the profile has read-only tools (`holdings`, `quote`, `orders`, e.g. Upstox), use them for quotes and reconciling; otherwise use the scan's prices and ask the user.

**LIVE entries:** after the judgment checks, show one clear instruction block per stock. Log it with `journal.py event --symbol X --event manual_instruction --note "<the instruction>"`. The block looks like this:
```
BUY  RELIANCE  qty 12  LIMIT ₹2,840.50  Delivery (CNC)  Day order
Then, once it fills, at the stop-loss:   SELL 12 GTT/OCO stop ₹2,442.85 (limit ₹2,394.00) · target ₹3,636.10 (limit ₹3,617.90)
```
The second line follows the broker's `protection` level:
- `two_leg`: one OCO / two-leg GTT;
- `single`: a GTT stop;
- `day_sl`: "an SL order today; I'll remind you every run day";
- `bot_watch`: "no order needed: I'll tell you when to sell".

Then ask the user with AskUserQuestion: **Filled** (then ask the average price, qty and time if they're not in a read-only tool), **Not filled** (log `unfilled`), or **Skipped** (log `vetoed` with their reason). On a fill, work out the stop and target from the actual fill price, show the exact protection line again with those numbers, then run `journal.py open ... --note "manual: <checks>"`, adding `--gtt-id <id>` if the user gives the GTT id.

**Exits and trails:** show the exact SELL (or GTT change) to make. After the user confirms, run `journal.py close ... --via manual` (or `journal.py stop`).

**Reconcile:** ask the user once per run whether any stop or target fired at the broker since the last run (list each position with its stop and target, and the scan's `stop_touched` / `target_touched` flags). Use the holdings tool if there is one.

PAPER in Manual mode works exactly like PAPER above.
