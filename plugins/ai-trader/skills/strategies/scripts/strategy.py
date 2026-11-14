#!/usr/bin/env python3
"""Strategy manager for the AI Trader (Python standard library only). All commands print JSON.

A strategy is a folder: strategy.json (settings, stop/target, the user's risk answers, rules in words)
+ strategy.py (the buy/sell logic: any technical rule, written by Claude from the user's words).

Looking
  list                                 ready-made strategies + the user's own, which one is active
  show --id X                          one strategy's rules and risk answers in plain words
  summary                              the active strategy in a few lines (top of every trading-day run)
  vocabulary                           how to write a strategy script: the contract, helpers and examples
  check-request --text "..."           does a strategy description ask for data the bot doesn't have (fundamentals)?

Setting up a strategy (the chat flow: draft -> check -> answers -> review -> confirm)
  draft --id X [--restart]             start (or resume) a setup draft from an existing strategy
  draft --new --id X                   start an empty draft folder for the user's own strategy (Claude writes the files)
  check --id X                         every guardrail on the draft + real example signals to show the user
  questions --id X                     every question still to answer, with the current value and suggestion
  answer --id X --field F --value V    check one answer; saved to the draft only if it's valid
  review --id X [--backtest S.json]    the review screen (only when every answer is in and the checks pass;
                                       a backtest of exactly these settings can be shown on it)
  confirm --id X [--activate]          save the reviewed draft as the user's strategy (only after review)
  cancel --id X                        throw the draft away
  activate --id X                      switch the bot to an already confirmed strategy

The bot trades a strategy only after the user confirmed it on the review screen.
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

SCANNER = Path(__file__).resolve().parents[2] / "scanner" / "scripts"
sys.path.insert(0, str(SCANNER))
import engine  # noqa: E402

DRAFTS = engine.USER_STRATEGIES / "drafts"
UNIVERSE_NAMES = {"nifty50": "NIFTY 50", "nifty100": "NIFTY 100", "nifty200": "NIFTY 200", "nifty500": "NIFTY 500",
                  "midcap150": "NIFTY Midcap 150", "smallcap250": "NIFTY Smallcap 250"}
SETTING_FIELDS = ["stop", "target", "trail", "max_hold_days", "execution.limit_buffer_pct", "execution.max_gap_pct"]

# Words that mean a rule needs company fundamentals - not supported yet (technical rules only).
FUNDAMENTAL_TERMS = {
    "p/e": "P/E ratio", "pe": "P/E ratio", "p.e.": "P/E ratio", "pe ratio": "P/E ratio", "pb": "price-to-book", "price to earnings": "P/E ratio", "price-to-earnings": "P/E ratio",
    "p/b": "price-to-book", "price to book": "price-to-book", "book value": "book value", "pb ratio": "price-to-book",
    "market cap": "market capitalisation", "marketcap": "market capitalisation", "market capitalisation": "market capitalisation",
    "market capitalization": "market capitalisation", "mcap": "market capitalisation", "large cap": "market capitalisation",
    "eps": "earnings per share", "earnings per share": "earnings per share", "earnings growth": "earnings growth",
    "profit growth": "profit growth", "net profit": "profit", "revenue": "revenue / sales", "sales growth": "revenue / sales",
    "roe": "return on equity", "return on equity": "return on equity", "roce": "return on capital", "roa": "return on assets",
    "debt": "debt", "debt to equity": "debt-to-equity", "d/e": "debt-to-equity", "dividend": "dividends",
    "dividend yield": "dividend yield", "promoter": "promoter holding", "fii holding": "shareholding pattern",
    "dii holding": "shareholding pattern", "shareholding": "shareholding pattern", "peg": "PEG ratio",
    "ev/ebitda": "EV/EBITDA", "ebitda": "EBITDA", "free cash flow": "cash flow", "cash flow": "cash flow",
    "operating margin": "margins", "profit margin": "margins", "quarterly results": "results", "earnings": "earnings",
    "valuation": "valuation", "intrinsic value": "valuation", "fundamental": "fundamentals",
}
FUNDAMENTALS_MESSAGE = ("Right now the AI Trader supports technical rules only: anything built from daily price, volume "
                        "and indicators (moving averages, RSI, ATR, breakouts, candles, gaps, momentum, the NIFTY 500 "
                        "index...). Fundamental rules like {found} aren't supported yet, because the bot has no "
                        "fundamentals data to test or trade them on. You can continue without {those}, or tell me a "
                        "technical rule to use instead.")


def out(o):
    print(json.dumps(o, indent=1, default=str, ensure_ascii=False))


def fail(msg, **extra):
    out(dict({"ok": False, "problem": msg}, **extra))
    sys.exit(1)


def _num(x):
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def _rupees(x):
    """Indian digit grouping: 100000 -> 1,00,000."""
    s = f"{abs(x):.0f}"
    head, tail = s[:-3], s[-3:]
    while len(head) > 2:
        tail = head[-2:] + "," + tail
        head = head[:-2]
    return ("-" if x < 0 else "") + "₹" + (head + "," + tail if head else tail)


def _capital():
    c = engine.config() or {}
    return float(c.get("capital") or 100000)


def _fmt(text, params):
    """Fill {param} placeholders in a plain-words line."""
    class P(dict):
        def __missing__(self, k):
            return "{" + k + "}"
    return str(text).format_map(P({k: _num(v) for k, v in params.items()}))


def _val(v, params):
    return params.get(v, v) if isinstance(v, str) else v

# ---------------------------------------------------------------- plain words


def buys_words(d):
    p, u = d.get("params") or {}, d["universe"]
    uni = UNIVERSE_NAMES.get(u["index"], u["index"]) + " stocks"
    extra = []
    if u.get("min_price") is not None:
        extra.append(f"price ≥ ₹{_num(u['min_price'])}")
    if u.get("min_turnover_cr") is not None:
        extra.append(f"traded ≥ ₹{_num(u['min_turnover_cr'])} cr/day")
    lines = [uni + (", " + ", ".join(extra) if extra else "")]
    lines += [_fmt(r, p) for r in d.get("buy_rules") or []]
    if d.get("max_rank") is not None:
        lines.append(f"ranked in the top {_num(_val(d['max_rank'], p))} of the universe ({d.get('rank_say') or 'by the strategy score'})")
    lines.append("When there are more signals than slots: " + (d.get("rank_say") or "most liquid first"))
    return lines


def sells_words(d):
    p, lines = d.get("params") or {}, []
    st = d.get("stop")
    if st is None:
        lines.append("Stop-loss: none")
    elif st.get("say"):
        lines.append("Stop-loss: " + _fmt(st["say"], p))
    elif st["type"] == "pct":
        lines.append(f"Stop-loss {_num(_val(st['value'], p))}% below entry")
    elif st["type"] == "atr":
        lines.append(f"Stop-loss {_num(_val(st['mult'], p))} x ATR({_num(_val(st.get('period', 14), p))}) below entry")
    else:
        lines.append("Stop-loss at the chart level the strategy sets on the signal day")
    if st and st.get("max_pct") is not None:
        lines.append(f"Skip a trade if its stop would be more than {_num(st['max_pct'])}% away")
    tg = d.get("target")
    if tg is None:
        lines.append("Target: none (no profit target)")
    elif tg["type"] == "r":
        v = _val(tg["value"], p)
        pct = f" (+{_num(v * _val(st['value'], p))}%)" if st and st["type"] == "pct" else ""
        lines.append(f"Target 1:{_num(v)}{pct}")
    else:
        lines.append(f"Target +{_num(_val(tg['value'], p))}% above entry")
    tr = d.get("trail")
    if tr:
        lines.append("Trailing stop: " + (_fmt(tr["say"], p) if tr.get("say") else
                     f"{_num(_val(tr.get('mult'), p))} x ATR below the highest close" if tr["type"] == "atr" else
                     f"{_num(_val(tr.get('value'), p))}% below the highest close" if tr["type"] == "pct" else
                     "follows the level the strategy sets each day"))
    for r in d.get("sell_rules") or []:
        lines.append(_fmt(r, p))
    if d.get("max_hold_days"):
        lines.append(f"Time exit after {d['max_hold_days']} sessions")
    if st or tg:
        lines.append(("Stop and target are" if st and tg else "Stop is" if st else "Target is") + " placed at Zerodha as a GTT order")
    return lines


def orders_words(d):
    ex = d["execution"]
    return [f"LIMIT buy at signal close + {_num(ex['limit_buffer_pct'])}%, next morning",
            f"Skip if it opens more than {_num(ex['max_gap_pct'])}% above the signal close"]


def regime_words(rule):
    """'nifty500 > nifty500_sma(200)' -> 'buy only while NIFTY 500 is above its 200-day average'."""
    m = re.fullmatch(r"\s*nifty500\s*(>|<)\s*nifty500_(sma|ema)\((\d+)\)\s*", rule or "")
    if not m:
        return rule
    side = "above" if m.group(1) == ">" else "below"
    kind = "average" if m.group(2) == "sma" else "exponential average"
    return f"buy only while NIFTY 500 is {side} its {m.group(3)}-day {kind}"


def risk_words(r, capital):
    """(label, value, meaning) rows for the review screen."""
    rows = []
    sz = r.get("sizing")
    if sz:
        if sz["type"] == "risk":
            rows.append(("Risk per trade", f"{_num(sz['pct'])}%", f"max loss {_rupees(capital * sz['pct'] / 100)} per trade if the stop hits"))
        else:
            rows.append(("Position size", f"{_num(sz['pct'])}%", f"{_rupees(capital * sz['pct'] / 100)} per position"))
    if "max_positions" in r:
        rows.append(("Max positions", _num(r["max_positions"]) if r["max_positions"] else "No limit", "open at the same time"))
    if "max_position_pct" in r:
        v = r["max_position_pct"]
        rows.append(("Max position size", f"{_num(v)}%" if v else "No cap", f"at most {_rupees(capital * v / 100)} in one stock" if v else "cash still limits it"))
    if "weekly_loss_limit_pct" in r:
        v = r["weekly_loss_limit_pct"]
        rows.append(("Weekly loss limit", f"-{_num(v)}%" if v else "None", "no new buys for the rest of the week" if v else "never pauses for a bad week"))
    if "halt_drawdown_pct" in r:
        v = r["halt_drawdown_pct"]
        rows.append(("Drawdown halt", f"-{_num(v)}%" if v else "None", "from peak equity: no new buys until you review" if v else "never halts on a drawdown"))
    if "regime" in r:
        v = r["regime"]
        rows.append(("Market filter", "On" if v else "None", regime_words(v) if v else "buys in any market"))
    if "broker_stop_required" in r:
        rows.append(("Stop at broker", "Required" if r["broker_stop_required"] else "Not required",
                     "every position gets a GTT stop at Zerodha" if r["broker_stop_required"] else "positions may run without a broker stop"))
    if "stops_only_up" in r:
        rows.append(("Stops only move up", "Yes" if r["stops_only_up"] else "No", ""))
    return rows

# ---------------------------------------------------------------- drafts


def _draft_dir(sid):
    return DRAFTS / sid


def _load_draft(sid):
    d = _draft_dir(sid)
    if not (d / engine.SETTINGS_FILE).exists():
        fail(f"no setup draft for '{sid}'. Start one with: draft --id {sid}  (or draft --new --id {sid} for your own)")
    data = json.loads((d / engine.SETTINGS_FILE).read_text())
    script = (d / engine.SCRIPT_FILE).read_text() if (d / engine.SCRIPT_FILE).exists() else ""
    return data, script


def _save_draft(data, script=None):
    data.pop("_reviewed", None)  # any change means the review screen must be shown again
    d = _draft_dir(data["id"])
    engine.write_json(d / engine.SETTINGS_FILE, data)
    if script is not None:
        (d / engine.SCRIPT_FILE).write_text(script)


def _fingerprint(data, script, rules_only=False):
    """Hash of the draft. rules_only: everything except the risk answers (what `check` tested)."""
    return engine.fingerprint(data, script, rules_only)


SIZING_FIELDS = ("sizing", "max_positions", "max_position_pct", "regime")   # pop-up A; the rest are pop-up B


def _strategy_without_risk(data, script):
    """Build the strategy with its risk stripped (risk answers are checked one by one)."""
    x = {k: v for k, v in json.loads(json.dumps(data)).items() if not k.startswith("_")}
    x["risk"], x["risk_suggested"] = None, None
    return engine.Strategy(x, None, script)


def _risk_problem(field, value, d):
    """Plain-words problem with one risk answer (None if it's fine), including clashes with other answers."""
    r = dict({k: v for k, v in (d.get("risk") or {}).items() if k in engine.RISK_FIELDS}, **{field: value})
    has_stop = d.get("stop") is not None

    def pct(v, name, hi=100):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v <= hi:
            return f"{name} must be a number above 0 and at most {hi}"
    if field == "sizing":
        if not isinstance(value, dict) or value.get("type") not in ("risk", "fixed_pct"):
            return "sizing must be either 'risk' (% of equity lost if the stop hits) or 'fixed_pct' (% of equity per position)"
        p = pct(value.get("pct"), "the sizing %")
        if p:
            return p
        if value["type"] == "risk" and not has_stop:
            return "risk-based sizing needs a stop-loss, and this strategy has none. Use a fixed % per position, or add a stop"
    elif field == "max_positions":
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
            return "max positions must be a whole number of 1 or more (or no limit)"
    elif field in ("max_position_pct", "weekly_loss_limit_pct", "halt_drawdown_pct"):
        if value is not None:
            return pct(value, {"max_position_pct": "max position size", "weekly_loss_limit_pct": "the weekly loss limit",
                               "halt_drawdown_pct": "the drawdown halt"}[field])
    elif field == "regime":
        if value is not None:
            try:
                engine.check(engine.parse(value), {}, index_only=True, text=value)
            except engine.RuleError as e:
                return f"the market filter isn't a valid rule: {e}"
    elif field in ("broker_stop_required", "stops_only_up"):
        if not isinstance(value, bool):
            return "answer yes (true) or no (false)"
        if field == "broker_stop_required" and value and not has_stop:
            return "a broker stop can't be required: this strategy has no stop-loss. Answer no, or add a stop first"
    else:
        return f"'{field}' is not a risk question"
    if (r.get("sizing") or {}).get("type") == "risk" and not has_stop:
        return "your sizing answer is risk-based, which needs a stop-loss; this strategy has none"
    return None


def _missing(d):
    risk = d.get("risk") or {}
    return [k for k in engine.RISK_FIELDS if k not in risk]


def cmd_draft(a):
    dest = _draft_dir(a.id)
    if a.new:
        if (dest / engine.SETTINGS_FILE).exists() and not a.restart:
            fail(f"a draft '{a.id}' already exists; pass --restart to start over, or pick another id")
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        out({"ok": True, "id": a.id, "folder": str(dest),
             "next": f"write {engine.SETTINGS_FILE} and {engine.SCRIPT_FILE} into this folder (see vocabulary), then run: check --id {a.id}"})
        return
    if (dest / engine.SETTINGS_FILE).exists() and not a.restart:
        data, _ = _load_draft(a.id)
        out({"ok": True, "resumed": True, "id": a.id, "folder": str(dest), "still_to_answer": _missing(data),
             "note": "a draft already exists; it was resumed (pass --restart to start over)"})
        return
    try:
        src = engine.strategy_folder(a.id)
    except engine.StrategyError as e:
        fail(str(e))
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    data, script = _load_draft(a.id)
    risk = data.get("risk") or {}
    data["risk"] = {k: risk[k] for k in engine.RISK_FIELDS if k in risk}  # earlier answers pre-filled, not confirmed
    if risk.get("confirmed"):
        data["_previous_confirmed"] = risk["confirmed"]
    data["_started"] = dt.date.today().isoformat()
    _save_draft(data)
    out({"ok": True, "id": a.id, "name": data["name"], "folder": str(dest), "what_it_buys": buys_words(data),
         "when_it_sells": sells_words(data), "still_to_answer": _missing(data),
         "next": f"check --id {a.id}"})


def cmd_check(a):
    """All guardrails on a draft, plus recent real signals to show the user as proof."""
    data, script = _load_draft(a.id)
    if not script.strip():
        fail(f"the draft has no {engine.SCRIPT_FILE} yet")
    try:
        st = _strategy_without_risk(data, script)
    except engine.StrategyError as e:
        fail(str(e), stage="settings or script")
    import scan  # noqa: E402 - the scanner's data and universe helpers
    try:
        panel = scan.Panel(start=dt.date.today() - dt.timedelta(days=max(470, int(st.history * 1.5) + 60)))
    except Exception as e:  # noqa: BLE001
        fail(f"no market data to test on ({e}); run the scanner's update first")
    if not panel.dates:
        fail("no market data to test on; run the scanner's update first")
    index = engine.IndexData(panel.index)
    try:
        book = scan.Book(panel, st, index)
    except engine.StrategyError as e:
        fail(str(e), stage="running the script on real stocks")
    look = engine.lookahead_check(st, panel, index, sorted(book.keys))
    if not look["ok"]:
        fail(look["problem"], stage="look-ahead test", stock=look["stock"], day=look["day"])
    # recent signals (last ~250 sessions) as examples
    k1 = len(panel.dates) - 1
    found, per_day = [], {}
    for key, res in book.res.items():
        s = res.s
        for k in range(max(0, k1 - 250), k1 + 1):
            i = s.row(k)
            if i is None:
                continue
            passed, _ = scan.evaluate(st, res, i)
            if passed:
                limit = scan.tick_round(s.c[i] * (1 + st.limit_buffer_pct / 100))
                passed = st.plan_stop(res, i, limit)[2] is None  # e.g. skipped when the stop would be too wide
            if passed:
                per_day[k] = per_day.get(k, 0) + 1
                found.append((k, s.sym, i, res))
    found.sort(key=lambda x: (-x[0], x[1]))
    examples = []
    for k, sym, i, res in found[:8]:
        s = res.s
        ex = {"date": panel.dates[k].isoformat(), "symbol": sym, "close": round(s.c[i], 2),
              "day_change_pct": round((s.c[i] / s.c[i - 1] - 1) * 100, 2) if i else None,
              "volume_vs_50d_avg": round(s.v[i] / (sum(s.v[i - 50:i]) / 50), 2) if i >= 50 and sum(s.v[i - 50:i]) else None}
        ex.update({name: col[i] for name, col in res.checks.items()})
        ex.update({f: v for f, v in scan.features(s, index, i).items() if f in ("rsi", "ret126", "ext_pct")})
        examples.append(ex)
    data["_checked"] = _fingerprint(data, script, rules_only=True)
    engine.write_json(_draft_dir(a.id) / engine.SETTINGS_FILE, data)
    sessions = min(251, k1 + 1)
    out({"ok": True, "id": a.id, "what_it_buys": buys_words(data), "when_it_sells": sells_words(data),
         "orders": orders_words(data), "safety": "passed: no files, network or other programs",
         "extra_rule_hooks": [h for h, f in (("allow_entry", st._allow), ("manage", st._manage)) if f],
         "look_ahead_test": f"passed ({look['stocks_tested']} stocks, {look['cut_offs_tested']} cut-off dates)",
         "signals_last_year": len(found), "days_with_signals": len(per_day), "sessions_checked": sessions,
         "recent_examples": examples,
         "next": "show what_it_buys / when_it_sells and 3-5 recent_examples; ask 'Is this exactly your strategy?'"})


def cmd_check_request(a):
    text = " " + re.sub(r"\s+", " ", a.text.lower()) + " "
    found = []
    for term, label in FUNDAMENTAL_TERMS.items():
        if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", text) and label not in found:
            found.append(label)
    if not found:
        out({"ok": True, "technical_only": True})
        return
    names = ", ".join(found[:-1]) + (" and " if len(found) > 1 else "") + found[-1]
    out({"ok": True, "technical_only": False, "fundamentals_found": found,
         "message": FUNDAMENTALS_MESSAGE.format(found=names, those="those rules" if len(found) > 1 else "that rule"),
         "note": "show the message as is; continue only with the technical parts, if the user agrees"})


def cmd_questions(a):
    d, _ = _load_draft(a.id)
    sug = d.get("risk_suggested") or {}
    risk = d.get("risk") or {}
    p = d.get("params") or {}
    settings = [{"field": f"params.{k}", "current": v, "tunable_range": (d.get("tunable") or {}).get(k)} for k, v in p.items()]
    settings += [{"field": f, "current": d.get(f) if "." not in f else d["execution"][f.split(".")[1]]} for f in SETTING_FIELDS]
    out({"id": d["id"], "capital": _capital(),
         "step_settings": {"note": "show the strategy's settings in plain words and ask Keep / Change", "settings": settings,
                           "in_words": sells_words(d) + orders_words(d)},
         "risk_popup_a": [{"field": k, "question": engine.RISK_FIELDS[k], "answered": k in risk, "answer": risk.get(k),
                          "suggested": sug.get(k)} for k in SIZING_FIELDS],
         "risk_popup_b": [{"field": k, "question": desc, "answered": k in risk, "answer": risk.get(k), "suggested": sug.get(k)}
                         for k, desc in engine.RISK_FIELDS.items() if k not in SIZING_FIELDS],
         "suggestion_basis": sug.get("basis"),
         "still_to_answer": _missing(d)})


def cmd_answer(a):
    d, script = _load_draft(a.id)
    try:
        value = json.loads(a.value)
    except ValueError:
        value = a.value
    f = a.field
    if f in engine.RISK_FIELDS:
        prob = _risk_problem(f, value, d)
        if prob:
            fail(prob, field=f, ask_again=True)
        if not isinstance(d.get("risk"), dict):
            d["risk"] = {}
        d["risk"][f] = value
        words = [" ".join(x for x in row if x) for row in risk_words({f: value}, _capital())]
    else:
        new = json.loads(json.dumps(d))
        keys = f.split(".")
        if keys[0] not in ("params", "stop", "target", "trail", "max_hold_days", "execution", "universe", "max_rank"):
            fail(f"'{f}' can't be answered here (the buy/sell logic lives in {engine.SCRIPT_FILE})", field=f)
        node = new
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        if keys[0] == "params" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            fail(f"{keys[-1]} must be a number", field=f, ask_again=True)
        node[keys[-1]] = value
        try:
            _strategy_without_risk(new, script)
        except engine.StrategyError as e:
            fail(f"that doesn't work: {e}", field=f, ask_again=True)
        for k, v in (new.get("risk") or {}).items():  # a changed stop can clash with risk answers already given
            prob = _risk_problem(k, v, new)
            if prob:
                fail(f"that clashes with an earlier answer: {prob}", field=f, ask_again=True)
        d = new
        words = buys_words(d) + sells_words(d) if keys[0] == "params" else sells_words(d) + orders_words(d)
    _save_draft(d)
    out({"ok": True, "field": f, "saved_to_draft": value, "in_words": words, "still_to_answer": _missing(d),
         "note": "settings changed: run check again before the review" if f not in engine.RISK_FIELDS else None})


def _box(title, sections, footer):
    lines = [title, ""]
    for head, rows in sections:
        lines.append(head)
        lines += [f" • {r}" for r in rows]
        lines.append("")
    lines += footer
    return "\n".join(lines)


def cmd_review(a):
    d, script = _load_draft(a.id)
    missing = _missing(d)
    if missing:
        fail("not every question is answered yet", still_to_answer=missing)
    if d.get("_checked") != _fingerprint(d, script, rules_only=True):
        fail(f"the strategy changed since its last check (or was never checked): run check --id {a.id} first")
    bt = None
    if a.backtest:  # optional: a backtest of exactly these settings and answers, shown on the screen
        try:
            bt = json.loads(Path(a.backtest).read_text())
        except (OSError, ValueError) as e:
            fail(f"can't read the backtest summary: {e}")
        if bt.get("fingerprint") != _fingerprint(d, script):
            fail("that backtest was run on different settings or answers than the draft has now: run the backtest "
                 "on the draft again (no --set / --no-limits) and use the new summary.json", rerun_backtest=True)
    try:
        engine.Strategy(dict({k: v for k, v in d.items() if not k.startswith("_")},
                             risk=dict(d["risk"], confirmed="review")), None, script)
    except engine.StrategyError as e:
        fail(f"the answers don't work together: {e}", ask_again=True)
    cap = _capital()
    c = engine.config() or {}
    rows = risk_words(d["risk"], cap)
    w = max(len(r[0]) for r in rows)
    risk_lines = [f"{lab.ljust(w)}  {val}" + (f"  → {mean}" if mean else "") for lab, val, mean in rows]
    sections = [("WHAT IT BUYS", buys_words(d)), ("WHEN IT SELLS", sells_words(d)), ("ORDERS", orders_words(d)),
                ("YOUR RISK RULES", risk_lines)]
    if bt is None:
        sections.append(("BACKTEST", ["Not backtested in the AI Trader."]))
    else:
        f = bt["full"]
        sections.append((f"BACKTEST {f['from'][:7]} to {f['to'][:7]} (with all your answers applied)", [
            f"CAGR {f['cagr_pct']}% · max drawdown {f['max_drawdown_pct']}% · {f['trades']} trades · win rate {f['win_rate_pct']}%",
            f"Worst week {f['worst_week_pct']}% · worst month {f['worst_month_pct']}% ({f['worst_month']})",
            f"NIFTY 500 same period: CAGR {f.get('nifty500_cagr_pct')}% · max drawdown {f.get('nifty500_max_drawdown_pct')}%",
            "Costs included. A backtest is history, not a forecast."]))
    title = f"Your strategy: {d['name']} ({d['version']})          Capital {_rupees(cap)} · {c.get('mode', 'PAPER')}"
    card = _box(title, sections, ["Start with these rules   ·   Change something   ·   Cancel"])
    d["_reviewed"] = _fingerprint(d, script)
    engine.write_json(_draft_dir(d["id"]) / engine.SETTINGS_FILE, d)
    out({"ok": True, "review_screen": card,
         "next": "show review_screen exactly as is (in a code block), then ask: Start with these rules / Change something / Cancel"})


def _bump(v):
    major, _, minor = v.lstrip("v").partition(".")
    return f"v{major}.{int(minor or 0) + 1}"


def archive(data, script):
    """Keep every version (settings + script) so the strategy-lab can roll back."""
    p = engine.HOME / "learning" / "configs" / data["id"] / f"{data['version']}.json"
    rec = engine.read_json(p, {})
    rec.update(strategy_file=data, script=script)
    engine.write_json(p, rec)


def cmd_confirm(a):
    d, script = _load_draft(a.id)
    if d.get("_reviewed") != _fingerprint(d, script):
        fail("the review screen hasn't been shown for these exact answers. Run review, show it, and confirm after the user picks Start")
    final = {k: v for k, v in d.items() if not k.startswith("_")}
    final["risk"] = dict(d["risk"], confirmed=dt.date.today().isoformat())
    old_dir = engine.USER_STRATEGIES / d["id"]
    if (old_dir / engine.SETTINGS_FILE).exists():
        old = engine.read_json(old_dir / engine.SETTINGS_FILE)
        old_script = (old_dir / engine.SCRIPT_FILE).read_text() if (old_dir / engine.SCRIPT_FILE).exists() else ""
        same = ({k: v for k, v in old.items() if k != "risk"} == {k: v for k, v in final.items() if k != "risk"}
                and {k: v for k, v in (old.get("risk") or {}).items() if k != "confirmed"} == d["risk"]
                and old_script == script)
        if not same:
            archive(old, old_script)
            final["version"] = _bump(old.get("version", final["version"]))
    try:
        engine.save_user_strategy(final, script)
    except engine.StrategyError as e:
        fail(str(e))
    archive(final, script)
    shutil.rmtree(_draft_dir(d["id"]))
    res = {"ok": True, "saved": d["id"], "version": final["version"], "confirmed": final["risk"]["confirmed"]}
    if a.activate:
        res["activated"] = _activate(d["id"])
    out(res)


def cmd_cancel(a):
    if _draft_dir(a.id).exists():
        shutil.rmtree(_draft_dir(a.id))
    out({"ok": True, "cancelled": a.id, "note": "nothing was saved"})


def _activate(sid):
    try:
        st = engine.load(sid)
    except engine.StrategyError as e:
        fail(str(e))
    if not st.risk_confirmed or st.folder.parent != engine.USER_STRATEGIES:
        fail(f"'{sid}' hasn't been confirmed on the review screen yet. Set it up first (draft -> check -> answers -> review -> confirm)")
    c = engine.config()
    if c is None:
        fail("not set up yet - run the trading-setup skill first")
    prev = c.get("active_strategy")
    c["active_strategy"] = sid
    c.setdefault("strategy_history", []).append({"date": dt.date.today().isoformat(), "from": prev, "to": sid})
    engine.write_json(engine.CONFIG, c)
    pos = engine.read_json(engine.HOME / "positions.json", {}).get("positions", [])
    others = [p["symbol"] for p in pos if (p.get("strategy") or engine.LEGACY_ID) != sid]
    return {"active": sid, "previous": prev, "version": st.version, "open_positions_from_other_strategies": others,
            "note": ("those positions keep the stop, target and sell rules of the strategy that opened them") if others else None}


def cmd_activate(a):
    out(dict({"ok": True}, **_activate(a.id)))

# ---------------------------------------------------------------- looking


def _load(ref):
    try:
        return engine.load(ref)
    except engine.StrategyError as e:
        fail(str(e))


def cmd_list(a):
    active = engine.active_id()
    rows = []
    for sid in sorted(set(engine.library_ids()) | set(engine.user_ids())):
        try:
            st = engine.load(sid)
        except engine.StrategyError as e:
            rows.append({"id": sid, "error": str(e)})
            continue
        rows.append({"id": sid, "name": st.name, "version": st.version, "style": st.data.get("style"),
                     "ready_made": sid in engine.library_ids(), "set_up_by_you": st.risk_confirmed,
                     "active": sid == active, "draft_in_progress": (_draft_dir(sid) / engine.SETTINGS_FILE).exists(),
                     "about": st.data.get("about")})
    out({"active": active, "strategies": rows})


def cmd_show(a):
    st = _load(a.id)
    d = st.data
    rows = risk_words(d["risk"], _capital()) if st.risk_confirmed else []
    sug = d.get("risk_suggested")
    out({"id": st.id, "name": st.name, "version": st.version, "about": d.get("about"), "source": d.get("source"),
         "style": d.get("style"), "what_it_buys": buys_words(d), "when_it_sells": sells_words(d), "orders": orders_words(d),
         "your_risk_rules": [" ".join(x for x in r if x) for r in rows] if rows else "not set up yet",
         "confirmed_on": (d.get("risk") or {}).get("confirmed"),
         "suggested_risk": [" ".join(x for x in r if x) for r in risk_words(sug, _capital())] if sug else None,
         "suggestion_basis": (sug or {}).get("basis"), "folder": str(st.folder), "active": st.id == engine.active_id()})


def cmd_summary(a):
    sid = engine.active_id()
    if not sid:
        out({"active": None, "lines": ["No strategy set up yet - use the strategies skill"]})
        return
    st = _load(sid)
    d = st.data
    lines = [f"{st.name} {st.version}" + ("" if st.risk_confirmed else "  (NOT CONFIRMED - no new buys)")]
    if st.risk_confirmed:
        lines.append(" · ".join(f"{lab} {val}" for lab, val, _ in risk_words(d["risk"], _capital())))
    lines.append("Sells: " + " · ".join(sells_words(d)[:3]))
    out({"active": sid, "lines": lines})


def cmd_vocabulary(a):
    import aitrader_ta
    helpers = {name: (getattr(aitrader_ta, name).__doc__ or "").strip().split("\n")[0]
               for name in aitrader_ta.__all__ if callable(getattr(aitrader_ta, name)) and name != "math"}
    lib = engine.LIBRARY
    out({
        "technical_only": "rules may use daily price, volume, indicators and the NIFTY 500 - no fundamentals (P/E, market cap, ...)",
        "not_supported_yet": ["fundamentals (P/E, market cap, EPS, ROE, debt, results, ...)", "intraday / minute prices",
                              "short selling, F&O, MIS/intraday orders", "selling part of a position or adding to one",
                              "order types other than a LIMIT buy and GTT / MARKET sells", "news or sentiment data"],
        "folder": f"~/ai-trader/strategies/drafts/<id>/ with {engine.SETTINGS_FILE} + {engine.SCRIPT_FILE}",
        "script_contract": {
            "signature": "def prepare(stock, market, p): ...  (called once per stock with its full daily history)",
            "stock": "stock.date, .open, .high, .low, .close, .volume, .turnover (₹) - lists, oldest first, split-adjusted; .symbol",
            "market": "market.nifty500 (NIFTY 500 close on each of the stock's days); market.stock('SYMBOL') -> another stock's history up to the same day",
            "p": "the params from strategy.json (numbers)",
            "return": {
                "checks": "{'rule name': [True/False per day], ...} - the buy rules; buy = all of them true (names show up in explanations)",
                "buy": "[True/False per day] - optional instead of checks",
                "score": "[number per day] - ranking, higher = taken first; needed for max_rank (top N of the universe)",
                "sell": "[False / True / 'reason' per day] - sell the next morning when set (optional)",
                "stop_level": "[price per day] - needed when stop.type is 'level' (e.g. the 10-day low)",
                "trail_level": "[price per day] - needed when trail.type is 'level'",
            },
            "optional hooks (for rules prepare() can't express)": {
                "allow_entry(candidate, book, p)": (
                    "portfolio-level entry rules, called for each would-be buy in priority order. candidate: symbol, "
                    "sector, date, close, score, rank, stop_pct. book: date, equity, cash (None in the live scan), "
                    "positions [symbol, sector, entry_date, entry_price, qty, stop, target, highest_close, days_held, "
                    "close, unrealized_pct; or {symbol, sector, pending: True} for buys already accepted today], "
                    "recent_trades [the last 50 closed trades: symbol, exit_date, pnl, ret_pct, r, exit_reason]. "
                    "Return True to allow, or a short reason text to block (e.g. 'sector_full'). "
                    "Examples: max 2 stocks per sector; no new buys after 3 losing trades in a row; no buys on Fridays."),
                "manage(position, stock, p)": (
                    "rules for an open position, called every day it's held. position: the same fields as above; "
                    "stock: its history up to today only (last item = today). Return None, {'stop': price} to move "
                    "the stop (a broker GTT change), and/or {'sell': 'reason'} to sell the next morning. "
                    "Examples: stop to breakeven after +5%; sell if it hasn't gained 3% in 10 days; lock half the "
                    "gain after +20%."),
            },
            "rules": ["use only day i and earlier when computing day i (the look-ahead test rejects anything else)",
                      "imports: only aitrader_ta, math, statistics, datetime, itertools, functools, collections, bisect",
                      "no files, network, eval/exec or hidden attributes",
                      "NaN comparisons are False, so days without enough history simply don't signal"],
        },
        "helpers (from aitrader_ta import *)": helpers,
        "settings_file": {"required": ["id", "name", "version", "universe", "execution"],
                          "fields": {"universe": {"index": sorted(UNIVERSE_NAMES), "min_price": "₹ (optional)",
                                                  "min_turnover_cr": "20-day avg traded value, ₹ crore (optional)"},
                                     "history_sessions": "days of history the rules need before the first signal (e.g. 260)",
                                     "params": "numbers the user may tune; the script reads them from p",
                                     "tunable": "{param: [low, high]} the strategy-lab may test",
                                     "buy_rules / sell_rules": "plain-words lines with {param} placeholders (shown to the user)",
                                     "max_rank / rank_say": "only the top N by score may be bought / how the order is described",
                                     "stop": {"pct": {"type": "pct", "value": 8}, "atr": {"type": "atr", "mult": 2, "period": 14},
                                              "level": {"type": "level", "buffer_pct": 0.5, "say": "below the 10-day low"},
                                              "options": "min_pct / max_pct", "none": None},
                                     "target": {"r": {"type": "r", "value": 2}, "pct": {"type": "pct", "value": 15}, "none": None},
                                     "trail": {"atr": {"type": "atr", "mult": 3, "period": 14}, "pct": {"type": "pct", "value": 10},
                                               "level": {"type": "level", "say": "trails at the 20-day low"}, "none": None},
                                     "max_hold_days": "sell after N sessions (null = none)",
                                     "execution": {"limit_buffer_pct": 1, "max_gap_pct": 3},
                                     "risk": "null - the user answers it in the setup flow",
                                     "risk_suggested": "null for a new strategy (suggestions come from its backtest)"}},
        "examples": {sid: {"strategy.json": json.loads((lib / sid / engine.SETTINGS_FILE).read_text()),
                           "strategy.py": (lib / sid / engine.SCRIPT_FILE).read_text()} for sid in engine.library_ids()},
    })


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("show")
    p.add_argument("--id", required=True)
    sub.add_parser("summary")
    sub.add_parser("vocabulary")
    p = sub.add_parser("check-request")
    p.add_argument("--text", required=True)
    p = sub.add_parser("draft")
    p.add_argument("--id", required=True)
    p.add_argument("--new", action="store_true", help="an empty draft for the user's own strategy")
    p.add_argument("--restart", action="store_true")
    for name in ("check", "questions", "cancel", "activate"):
        p = sub.add_parser(name)
        p.add_argument("--id", required=True)
    p = sub.add_parser("answer")
    p.add_argument("--id", required=True)
    p.add_argument("--field", required=True)
    p.add_argument("--value", required=True, help="JSON (numbers, true/false, null, objects) or plain text")
    p = sub.add_parser("review")
    p.add_argument("--id", required=True)
    p.add_argument("--backtest", help="summary.json of a backtest of this draft, to show on the review screen")
    p = sub.add_parser("confirm")
    p.add_argument("--id", required=True)
    p.add_argument("--activate", action="store_true")
    a = ap.parse_args()
    {"list": cmd_list, "show": cmd_show, "summary": cmd_summary, "vocabulary": cmd_vocabulary,
     "check-request": cmd_check_request, "draft": cmd_draft, "check": cmd_check, "questions": cmd_questions,
     "answer": cmd_answer, "review": cmd_review, "confirm": cmd_confirm, "cancel": cmd_cancel,
     "activate": cmd_activate}[a.cmd](a)


if __name__ == "__main__":
    main()
