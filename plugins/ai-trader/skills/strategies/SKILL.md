---
name: strategies
description: Choose, set up, create, edit or switch the AI Trader's trading strategy - ready-made strategies (Momentum Breakout, EMA 20/50 Crossover) or the user's own described in plain words (any technical rule) - through a guided chat flow - settings, every risk rule, extra rules, a review screen and the user's confirmation. Use when the user wants to see strategies, add their own, change rules or risk, or switch what the bot trades.
---

# Strategies

The bot trades one **active** strategy. A strategy is a folder with two files: `strategy.json` (settings, stop/target, the user's risk answers, the rules in plain words) and `strategy.py` (the buy/sell logic). Ready-made ones are in this skill's `library/`; the user's are in `~/ai-trader/strategies/<id>/`. For a user's own strategy, **you write `strategy.py`** from their words, so **any technical rule** works. Every strategy is set up through the same guided chat flow: the user picks from options or types a value, each answer is checked immediately, and nothing is saved until the user has seen the **review screen** and picked **Start**. Users never edit files or type commands.

Scripts: `STRAT` = `<this skill's base directory>/scripts/strategy.py`, `SCAN` = `<base>/../scanner/scripts/scan.py`, `LAB` = `<base>/../strategy-lab/scripts/lab.py`. All print JSON.

## Rules for you
1. **The user decides every rule.** Never assume an answer or fill one in silently. For ready-made strategies you may show the stored suggestion with its reason (`suggestion_basis`); for the user's own strategy there is no suggestion, just explain each option. "No limit" is a valid answer where offered.
2. **Every answer goes through `STRAT answer`.** If it returns `ok: false`, explain the `problem` in your own plain words and ask that question again. Never save a value any other way.
3. **Nothing is final until review.** `STRAT confirm` refuses unless the review screen was shown for exactly these settings and answers, and you only call `confirm` after the user picks **Start**.
4. **One question at a time in pop-ups.** Use AskUserQuestion with at most 4 questions per call. For ready-made strategies, put the suggested option first with "(suggested)" in its label. Every option's description says what it means **in rupees for their capital**. The user can always type their own value ("Other").
5. **Neutral tone.** Never call a strategy or setting good, safe or better. Explain what each choice does and let the user decide.
6. **Technical rules only (for now).** Rules can use daily price, volume, indicators, candles, gaps, other stocks' prices and the NIFTY 500. Fundamentals are not supported (P/E, market cap, EPS, ROE, debt, dividends, results, promoter holding...). Before building a user's strategy, run `python3 STRAT check-request --text "<their description>"`. If `technical_only` is false, show its `message` to the user as is, then continue only with the technical parts if they agree, or with a technical replacement they choose. Do the same if a fundamental idea comes up later in the conversation.

The only rule that is the same for every strategy (not asked): **bot-only positions.** The bot never touches holdings outside the bot and never buys a stock the user already holds outside it.

## The setup flow
Order: pick → check → settings → risk questions → **"Any more rules?"** → review → Start. This skill doesn't backtest. Users may have tested the strategy elsewhere and come here to automate it. If they have a backtest summary of this exact draft, it can be shown on the review screen (`STRAT review --backtest <summary.json>`).

### Step 1 · Pick a strategy
`python3 STRAT list`. Ask with one pop-up: the ready-made strategies (label = name, description = `style` + one line from `about`), plus **"Describe my own strategy"** (→ "Your own strategy" below, then come back to step 3). If the user already has one set up, mention which is active.

### Step 2 · What it does
`python3 STRAT draft --id <id>` starts the setup (or resumes an unfinished one), then `python3 STRAT check --id <id>` runs the guardrails and finds recent real signals. Show:
- `what_it_buys` and `when_it_sells` as two short bullet lists;
- how many times it would have bought in the last year (`signals_last_year`) and 2–3 recent examples, so the user can check them on a chart.

### Step 3 · Strategy settings (Keep / Change)
`python3 STRAT questions --id <id>` → `step_settings.in_words` lists the stop-loss, target, trailing stop, sell rules and order rules in plain words. Ask in one pop-up: "Keep these settings" (first) / "Change something". If they change something:
- Ask which item, then offer 2–3 common values plus their own.
- Save it with `python3 STRAT answer --id <id> --field <field> --value '<json>'`. Fields: `params.<name>` (e.g. `params.stop_pct` = 12), `stop`, `target`, `trail`, `max_hold_days`, `execution.limit_buffer_pct`, `execution.max_gap_pct`. `STRAT vocabulary` shows the formats.
- A change to the buy/sell **logic** (not just a number) means editing the draft's `strategy.py` and updating `buy_rules`/`sell_rules` to match.
- After any settings or logic change, run `STRAT check --id <id>` again (the review requires it).

### Step 4 · Risk questions (two pop-ups of 4)
`STRAT questions` → `risk_popup_a` and `risk_popup_b` list all 8, with any earlier answer and, for ready-made strategies, the suggestion plus `suggestion_basis`. Describe every option in rupees for the user's capital:
- **Pop-up A:**
  - **risk per trade** (or % of capital per position for a strategy without a stop): "2% = at most ₹2,000 lost per trade if the stop hits";
  - **max positions:** "10 = up to 10 stocks at once; cash still limits it", plus "No limit";
  - **max position size:** "20% = at most ₹20,000 in one stock", plus "No cap";
  - **market filter:** "buy only while NIFTY 500 is above its 200-day average" vs "None".
- **Pop-up B:**
  - **weekly loss limit:** "−8% = after a ₹8,000 loss in a week, no new buys until Monday", plus "No limit";
  - **drawdown halt:** "−30% = if equity falls 30% from its peak, no new buys until you review", plus "No halt";
  - **stop at broker required** and **stops only move up:** Yes / No, each with one line on what it protects against. If the strategy has no stop, broker stop must be No (the script will say so).

Save every answer with `STRAT answer` (values as JSON: `{"type": "risk", "pct": 2}`, `10`, `null`, `"nifty500 > nifty500_sma(200)"`, `true`). Ask again for any answer the script rejects, explaining its `problem` in plain words.

### Step 5 · "Any more rules?"
Ask with a pop-up: **"Any more rules you'd like to add?"**, with options "No, that's everything" (first) and "Yes, add a rule". The user types the rule.
1. `python3 STRAT check-request --text "<their rule>"`. For fundamentals, show the `message` (rule 6).
2. Decide where it goes:
   - **About which stocks to buy** (any price/volume/indicator condition): a new entry in `checks` inside `prepare()`.
   - **A new sell rule** for a stock: the `sell` list in `prepare()`.
   - **About the whole portfolio** (e.g. "max 2 stocks per sector", "no new buys after 3 losses in a row", "no buys on Fridays"): the `allow_entry(candidate, book, p)` hook.
   - **About an open position** (e.g. "move the stop to breakeven after +5%", "sell if it hasn't gained 3% in 10 days"): the `manage(position, stock, p)` hook.
   - **A risk number:** back to step 4.
3. Put any new number in `params`, and add a plain-words line to `buy_rules` or `sell_rules`.
4. `STRAT check --id <id>`, read the new rule back in plain words, and ask "Any more rules?" again until they say no.
5. If a rule can't be built yet (`STRAT vocabulary` → `not_supported_yet`: fundamentals, intraday prices, short selling/F&O, selling part of a position, other order types, news), say so plainly and offer the closest technical alternative. Never add it silently in some other form.

### Step 6 · Review screen
1. `python3 STRAT review --id <id>` → show `review_screen` **exactly as returned, inside a code block**.
2. Ask: **Start with these rules** / **Change something** / **Cancel**.
   - **Start:** `python3 STRAT confirm --id <id> --activate`. If `open_positions_from_other_strategies` is not empty, explain that those positions keep the stop, target and sell rules of the strategy that opened them.
   - **Change something:** ask which item, go back to that step, save the new answer, then show a fresh review screen.
   - **Cancel:** `python3 STRAT cancel --id <id>`. Nothing is saved.
3. Tell the user in one line: the bot follows these rules from the next `/trading-day`, and every run shows them at the top. In LIVE mode, say that real orders will follow them.

## Your own strategy (described in plain words)
1. `python3 STRAT check-request --text "<description>"`. If it finds fundamentals, show its `message` (see rule 6) before going on.
2. Fill the gaps with pop-ups, one topic each:
   - which stocks (universe);
   - the buy rules, exactly as the user means them (ask about anything ambiguous, e.g. "breakout = close above the high, or intraday high above it?");
   - the order when there are more signals than slots;
   - the stop-loss (or none);
   - target / trailing stop / sell rules / time exit;
   - order rules.
   For any gap, offer the common choices; never invent one silently.
3. `python3 STRAT draft --new --id <short-id>`, then write the two files into that folder. `python3 STRAT vocabulary` has the full contract, the helper functions and both ready-made strategies as examples.
   - **`strategy.py`** defines `prepare(stock, market, p)`. It gets one stock's whole daily history and returns lists with one value per day:
     - `checks` (each buy rule by name);
     - `score` (ranking);
     - optionally `sell`, `stop_level` and `trail_level`.
     Use the helpers (`from aitrader_ta import *`: `sma`, `ema`, `rsi`, `atr`, `highest_before`, `avg_before`, `crossed_above`, `gap_pct`, `is_green`, `count_true`, ...). Day *i* may only use day *i* and earlier. Put every number the user may tune in `params` and read it from `p`.
   - **`strategy.json`**:
     - id, name, version `v1.0`, `about`, universe, `history_sessions`, `params`, `tunable`;
     - `buy_rules` / `sell_rules` (plain-words lines with `{param}` placeholders), `rank_say`, `max_rank` if "top N";
     - stop / target / trail / `max_hold_days`, execution;
     - `"risk": null, "risk_suggested": null`.
4. `python3 STRAT check --id <id>`. It runs everything automatically:
   - the settings check and safety check (no files, network or other programs);
   - a run on every stock in the universe;
   - the **look-ahead test** (rejects any use of future data);
   - a search for recent real signals.
   If it reports a `problem`, fix the files and run it again. Don't show the user code-level errors, just fix them.
5. Show the user:
   - `what_it_buys` / `when_it_sells`;
   - how many signals it found in the last year;
   - 3–5 `recent_examples` in plain words, e.g. "KALYANKJIL, 11 Nov 2026: opened 3%+ up, closed green, volume 9× its average".
   Then ask with a pop-up: "Is this exactly your strategy?" (Yes / Change something).
6. Continue with step 3 (settings) → 4 (risk questions) → 5 (more rules) → 6 (review), exactly as for a ready-made strategy.
7. Limits to mention when relevant:
   - daily NSE end-of-day data only (no intraday), NSE cash stocks only (delivery/CNC), long only;
   - entries the morning after the signal;
   - universes: NIFTY 50/100/200/500, Midcap 150, Smallcap 250;
   - technical data only (rule 6).

## Changing things later
- **"Change my risk" / "change the stop" / "add a rule"** etc.: `STRAT draft --id <active>` pre-fills the current answers. Ask only what they want to change (a new rule follows step 5), then the review screen (step 6) and confirm. A changed strategy gets a new version (v1.0 → v1.1); the old one is archived for rollback.
- **"Switch strategy":** step 1. If the other strategy is already set up and the user doesn't want changes, show `STRAT show --id <id>` and confirm with a pop-up before `STRAT activate --id <id>`.
- Existing positions keep their GTT stops and targets. New rules apply to new entries, and exit rules apply from the next run.
- **"What am I running?":** `STRAT summary` (short) or `STRAT show --id <id>` (full).

## Ready-made strategies
| id | Style | Idea |
|---|---|---|
| `momentum-breakout` | trend, weeks–months | Top-50 momentum stocks closing at a 52-week high on volume; 14% stop, 1:2 target (Part 1's strategy) |
| `ema-crossover` | trend, weeks–months | 20/50 EMA cross above the 200-DMA, exit on the cross back, 3×ATR stop |

Each `strategy.json` → `source` says where the idea comes from and what was adapted. Library folders never change; the user's setup is saved as their own copy in `~/ai-trader/strategies/<id>/`. 
