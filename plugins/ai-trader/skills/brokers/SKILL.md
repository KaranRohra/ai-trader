---
name: brokers
description: Connect, show or switch the AI Trader's broker - Zerodha is ready-made (Kite MCP bundled); Dhan, FYERS, Groww, Upstox or any broker with an official MCP are connected for the user (the MCP is added for them, its tools matched and tested read-only, charges asked, review, confirm); brokers without an MCP, or with a read-only one, use Manual mode (the bot lists the orders, the user places them). Use when the user wants another broker, asks which brokers work, or trading-setup reaches the broker step.
---

# Brokers

`BROKER` = `python3 <this skill's base directory>/scripts/broker.py`. Every command prints JSON.

The bot trades through one broker at a time (`~/ai-trader/config.json` → `broker`, default `zerodha`). A **broker profile** records five things:
- which MCP server to use;
- which of its tools does each job;
- how stops are kept (`protection`);
- whether the MCP places orders or the user does (`execution`: `mcp` or `manual`);
- the broker's charges.

The other skills read it with `BROKER capability`.

**Safety while connecting:**
- Never call a tool that places, changes or cancels an order or a GTT. Only the read-only checks.
- An access token is a secret. Don't print it back, don't write it into any file in `~/ai-trader`, and prefer the broker's sign-in route.
- A newly connected broker that the plugin's author hasn't tested starts in PAPER. Its first LIVE order is shown to the user before it's placed.

## Show
`BROKER list` lists the profiles and the active one. `BROKER capability` shows the active broker, its protection level (`protection_words`) and any `problems`. Explain it in plain words.

## Connect or switch: the flow
Keep the user's effort to clicks: one question at a time, with AskUserQuestion.

### 1. Pick
Run `BROKER catalog`. Ask: **Zerodha** (ready-made) · **Another broker with an official MCP** · **Any other broker / no MCP** (Manual mode).
- For the second option, ask a follow-up listing the catalog brokers (Dhan, FYERS, Groww, Upstox). Show each one's `can_trade`, `prerequisites` and "not tested by the author yet". Upstox's MCP is read-only, so it runs in Manual mode with live data from the MCP.
- A broker that isn't listed but has an MCP: ask for its MCP URL, then run `BROKER draft --id <short name> --url <url> --transport http|sse --name "<Broker>"`.

**Zerodha:** run `BROKER use --id zerodha`. That's all; the Kite MCP comes with the plugin.

### 2. Draft
Run `BROKER draft --id <id>`, or for Manual mode `BROKER draft --id <id> --manual --name "<Broker>"`. Running it again resumes the saved draft, so after a restart pick up where it stopped. It returns `errors` (what's still missing) and `notes_path` (where the exact tool use goes).

### 3. Add the MCP for the user (skip in Manual mode without an MCP)
- Check whether it's already there:
  - its tools are available in this session (named like `mcp__<server>__…`, or as a claude.ai connector — load them with ToolSearch `+<server>`), or
  - `claude mcp list` shows it.
- If it isn't there: run `BROKER connect-cmd --id <id>` and show the user the command and the `prerequisites`. Ask "May I add the <Broker> MCP to your Claude Code?", then run the command with Bash.
  - FYERS: use the plain command first, which signs in with FYERS. Use `--alt` (an access-token header) only if that sign-in doesn't work and the user agrees. The token lasts a day.
  - Not in the catalog: `claude mcp add --transport <http|sse> --scope user <id> <url>`.
- Then say exactly this, and stop: "Restart Claude Code. Open `/mcp`, pick **<server>** and **Authenticate** if it asks (sign in to <Broker> in the browser). Then run `/ai-trader:brokers` (or say *continue broker setup*)."

### 4. Match the tools
Load the server's tools (ToolSearch `+<server>`) and read every tool's description and input schema. Fill the slots with `BROKER set --id <id> --json '{"tools": {...}}'`:
- **Reading:** `profile`, `quote`, `ltp`, `funds`, `holdings`, `orders`, `order_status`.
- **Trading:** `place_order`, `cancel_order`.
- **Stop at the broker:** `protect_list`, `protect_place`, `protect_modify`, `protect_cancel`.

Rules for filling the slots:
- A slot the broker doesn't have is `null`. Catalog `known_tools` are hints; the real tool list wins.
- One tool may do several jobs (Dhan's `trading_agent_tool`, `portfolio_agent_tool` and so on). Put it in each slot it serves, and write in the notes which action or argument selects each job.
- Also set:
  - `login`: `{"tool": <a login tool or null>, "check_tool": <profile tool>, "daily": true|false|null, "how": "<one line>"}`;
  - `supports_tag`: whether `place_order` has a tag field;
  - `mcp_server`: the name in `/mcp`.
- If `place_order`, `cancel_order` or `holdings` is missing, the MCP can't trade for the bot: switch the draft to Manual (`set --json '{"execution": "manual"}'`).

### 5. Read-only checks
Call each of these:
- `profile`: the user name;
- `funds`: the free cash;
- `holdings`: the count;
- `quote` for RELIANCE: price and open;
- `orders`: today's count.

Record the results with `BROKER set --id <id> --json '{"checks": [{"check": "holdings", "ok": true, "note": "4 holdings"}, ...]}'`. A failed check: note why (e.g. "Data API plan not subscribed"). `holdings` must pass for an MCP broker.

### 6. Stop-loss: the protection level
Pick the best the broker really has, judged from the tool schemas and the catalog's `protection_to_check`:
- **`two_leg`:** a SELL-side order on shares you already hold that stays active across days with **both** a stop and a target trigger: an OCO / two-leg GTT, or a forever order with OCO.
- **`single`:** the same, but one trigger only: a single GTT or forever order.
- **`day_sl`:** `place_order` accepts a stop-loss (SL / SL-M / STOP_LOSS) SELL order for delivery, valid for the day. Also write `sl_order_args`: how to place it, e.g. `"order_type SL, trigger_price, price, validity DAY"`.
- **`bot_watch`:** none of these.

Orders that bundle a new entry with its exits (bracket / super / cover orders) don't count unless the docs say they can protect existing delivery holdings across days. If you're unsure, pick the lower level and say why.

In **Manual mode**, ask the user what their broker's app offers instead: GTT/OCO with stop + target, GTT single, only day stop-loss orders, or none.

Explain the result in plain words (the table in `order-executor` → "Protection levels"). With `day_sl` or `bot_watch`, say clearly that positions are unprotected between runs, and that strategies whose risk says "Stop at broker: Required" can't open LIVE positions on this broker. Then run `BROKER set --id <id> --json '{"protection": "...", "protection_available": [...]}'`.

### 7. Charges
Ask two questions, telling the user to check the broker's charges page:
- brokerage per delivery order in ₹ (₹0 at Zerodha, Dhan and others; ₹20 or 0.1% at some);
- the DP charge per stock sold in ₹ (about ₹13–20 incl. GST).

The statutory charges (STT, exchange, SEBI, stamp, GST) are the same everywhere. Run `BROKER set --id <id> --json '{"charges": {"brokerage_per_order": 0, "dp_per_sell": 15.93}}'`.

### 8. Notes file (MCP brokers)
Write the exact tool use to `notes_path`, in the same shape as `library/zerodha.md`:
- login;
- reading: which field holds the LTP, open, previous close, circuit limit, free cash and holdings qty;
- buy LIMIT, sell MARKET, cancel, and order status (fields for status, filled qty, average price, time);
- the SL order, if `day_sl`;
- GTT place, modify, delete and list, with each argument name and an example;
- quirks (DDPI, confirmations, expiry).

Write only what the schemas and docs show.

### 9. Check, review, confirm
1. `BROKER check --id <id>`: fix every `error`.
2. `BROKER review --id <id>`: show `settings`, `tools` and `read_only_checks` as tables, plus every `warning`.
3. Ask: **Confirm** · **Change something**.
4. On Confirm: `BROKER confirm --id <id>`. It saves the profile and makes it the active broker. It refuses to switch while the bot has open positions at the old broker, because their stops live there; say so. A switch from LIVE puts the bot in PAPER if the new broker is untested (`mode_changed`).

Then tell the user what changes day to day:
- the login (link, OAuth in `/mcp`, or none);
- how stops are kept;
- in Manual mode, that every order shows up as a list to place in their app.

## Not supported
- Several brokers at once.
- Exchanges other than NSE, and products other than delivery.
- Order types beyond a LIMIT buy, MARKET sell and the stop orders above.

Backtests still use NSE data and the active broker's charges.
