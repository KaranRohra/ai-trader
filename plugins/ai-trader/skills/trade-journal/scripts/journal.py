#!/usr/bin/env python3
"""Bot bookkeeping (Python standard library only). The bot keeps its OWN books, separate from
the rest of the Zerodha account, so equity/P&L only ever include trades the bot made.

Files in ~/ai-trader/
  config.json               mode, capital, active strategy (optional "charges" overrides the rates below)
  positions.json            open bot positions (what the scanner and order steps work from)
  journal/trades.csv        ONE ROW PER TRADE, written when the buy fills (status OPEN), updated every run,
                            finished at the exit (status CLOSED): why it was bought, the plan, the fill,
                            the risk, the broker stop, best/worst price while open, the exit, charges, result
  journal/trade_events.csv  every event of every trade with date and time (orders, fills, GTTs, stop moves, exit)
  journal/decisions.csv     every signal decision: taken / vetoed / skipped_* / blocked_* with the signal's numbers
  journal/daily.csv         one row per run day: equity, cash, P&L for the day and week, drawdown, open risk, NIFTY 500

Commands (all print JSON)
  init --capital 100000 [--mode PAPER|LIVE]
  status [--prices '{"SYM": 123.4}']
  decision --symbol X --decision D --reason "..."            (the signal's numbers are filled in from the scan)
  open --symbol X --qty N --price P [--stop S] [--target T] [--strategy ID] [--gtt-id ID] [--order-id ID]
       [--time HH:MM:SS] [--day-open P] [--note "checks done"] [--scan FILE] [--rank R] [--score S] [--date D]
  event --symbol X --event E [--price P] [--qty N] [--order-id] [--gtt-id] [--old V] [--new V] [--note] [--time]
  stop --symbol X --stop S [--gtt-id ID] [--reason trail|breakeven|...] [--highest-close C]
  close --symbol X --price P --reason R [--order-id ID] [--via gtt_stop|gtt_target|market_sell|paper]
        [--trigger-price P] [--time HH:MM:SS] [--note "..."] [--date D]
  snapshot [--prices '{...}'] [--scan FILE] [--date D]
  trade --symbol X | --id TRADE_ID                           one trade in full, with its events
  trades [--status OPEN|CLOSED] [--strategy ID]              short list of trades
  weekly [--week 2026-W45]
"""
import argparse
import csv
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scanner" / "scripts"))
import engine  # noqa: E402

HOME = engine.HOME
CONFIG = HOME / "config.json"
POSITIONS = HOME / "positions.json"
RUNS = HOME / "runs"
J = HOME / "journal"
TRADES = J / "trades.csv"
EVENTS = J / "trade_events.csv"
DECISIONS = J / "decisions.csv"
DAILY = J / "daily.csv"

# Estimated Zerodha equity-delivery charges (NSE). Rates change: check zerodha.com/charges and override any of
# them in config.json -> "charges". Values are estimates; the contract note is the final word.
CHARGES_DEFAULT = {
    "brokerage_per_order": 0.0,   # delivery brokerage
    "stt_pct": 0.1,               # STT, buy and sell
    "exchange_pct": 0.00297,      # NSE transaction charge, buy and sell
    "sebi_per_crore": 10.0,       # SEBI turnover fee, buy and sell
    "stamp_buy_pct": 0.015,       # stamp duty, buy only
    "gst_pct": 18.0,              # GST on brokerage + exchange + SEBI fees
    "dp_per_sell": 15.34,         # DP charge per stock per sell day (GST included)
}

TRADE_GROUPS = {
    "trade": ["trade_id", "status", "symbol", "sector", "strategy", "strategy_name", "strategy_version", "mode"],
    "why_bought": ["signal_date", "signal_close", "signal_rank", "signal_score", "rules_passed", "rsi", "vol_ratio",
                   "breakout_pct", "ext_pct", "atr", "turnover_cr", "sma50", "sma200", "ret126_pct",
                   "market_filter", "nifty500_close", "entry_check_note"],
    "plan": ["limit_price", "skip_if_open_above", "planned_qty", "planned_stop", "planned_target", "sizing_rule"],
    "entry": ["entry_date", "entry_time", "entry_order_id", "qty", "entry_price", "entry_day_open", "entry_vs_limit",
              "entry_vs_signal_close_pct", "entry_value", "entry_stt", "entry_exchange", "entry_sebi", "entry_stamp",
              "entry_gst", "entry_charges", "entry_cost"],
    "risk_at_entry": ["initial_stop", "initial_stop_pct", "risk_per_share", "risk_amount", "risk_pct_of_equity",
                      "target", "target_pct", "reward_risk", "position_pct_of_equity", "equity_at_entry",
                      "open_positions_at_entry"],
    "broker_protection": ["gtt_id", "gtt_type", "current_stop", "stop_moves", "last_stop_move_date"],
    "while_open": ["last_price", "last_price_date", "unrealized_pnl", "unrealized_pct", "sessions_held",
                   "highest_close", "best_price", "best_price_date", "best_pct", "best_r", "worst_price",
                   "worst_price_date", "worst_pct", "worst_r"],
    "exit": ["exit_date", "exit_time", "exit_order_id", "exit_via", "exit_reason", "exit_trigger_price", "exit_price",
             "exit_slippage", "exit_value", "exit_stt", "exit_exchange", "exit_sebi", "exit_gst", "exit_dp",
             "exit_charges"],
    "result": ["gross_pnl", "total_charges", "net_pnl", "return_pct", "net_return_pct", "r_multiple", "net_r",
               "calendar_days_held", "captured_pct_of_best", "outcome", "equity_after"],
    "notes": ["exit_note"],
}
TRADE_COLS = [c for cols in TRADE_GROUPS.values() for c in cols]
EVENT_COLS = ["date", "time", "trade_id", "symbol", "event", "price", "qty", "old_value", "new_value", "order_id",
              "gtt_id", "note"]
DEC_COLS = ["date", "time", "strategy", "symbol", "decision", "reason", "signal_date", "close", "rank", "score",
            "sector", "limit_price", "stop_est", "target_est", "qty_est", "value_est", "trade_id", "version", "mode"]
DAILY_COLS = ["date", "equity", "cash", "invested", "open_positions", "unrealized_total", "realized_total",
              "day_pnl", "day_pnl_pct", "week_pnl", "week_pnl_pct", "peak_equity", "drawdown_pct", "open_risk",
              "charges_total", "trades_closed_total", "nifty500_close", "mode", "strategy", "version"]
EVENT_NAMES = ["signal", "order_placed", "order_partial", "order_cancelled", "order_rejected", "entry_filled",
               "gtt_placed", "gtt_modified", "gtt_deleted", "gtt_replaced", "gtt_triggered", "stop_moved",
               "exit_order_placed", "exit_filled", "closed", "error", "note"]

DEFAULT_CONFIG = {
    "mode": "PAPER",
    "capital": 100000,
    "order_tag": "CLAUDEBOT",
    "active_strategy": None,   # chosen with the strategies skill, together with its risk settings
}


def out(obj):
    print(json.dumps(obj, indent=1, default=str))


def cfg():
    c = engine.config()  # moves a v1.0 config to the multi-strategy layout on first use
    if c is None:
        sys.exit("not set up yet - run the trading-setup skill (journal.py init)")
    return c


def strategy(sid=None):
    """The strategy object (active by default), or None if it can't be loaded."""
    try:
        return engine.load(sid)
    except engine.StrategyError:
        return None


def sid_version(sid=None):
    st = strategy(sid)
    if st:
        return st.id, st.version
    return sid or "", ""


def num(x):
    """CSV text / JSON value -> float, or None."""
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def r2(x, n=2):
    return None if x is None else round(x, n)


def positions():
    return json.loads(POSITIONS.read_text()).get("positions", []) if POSITIONS.exists() else []


def save_positions(p):
    tmp = POSITIONS.with_suffix(".tmp")
    tmp.write_text(json.dumps({"positions": p}, indent=1))
    tmp.replace(POSITIONS)


def rows(path):
    if not path.exists():
        return []
    with path.open() as fh:
        res = list(csv.DictReader(fh))
    for r in res if path != EVENTS else []:
        if not r.get("strategy"):
            r["strategy"] = engine.LEGACY_ID  # rows written before Part 2 all came from Momentum Breakout
    return res


def write_all(path, cols, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(data)
    tmp.replace(path)


def _header(path):
    with path.open() as fh:
        return next(csv.reader(fh), [])


def _upgrade(path, cols):
    """Rewrite a CSV written by an older version with the current columns (keeps every row)."""
    if path.exists() and _header(path) != cols:
        write_all(path, cols, rows(path))


def append(path, cols, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    _upgrade(path, cols)
    new = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def today():
    return dt.date.today().isoformat()


def now_time():
    return dt.datetime.now().strftime("%H:%M:%S")


def week_start(d):
    return d - dt.timedelta(days=d.weekday())


# ---------------------------------------------------------------- charges

def rates():
    c = engine.read_json(CONFIG, {}) if CONFIG.exists() else {}
    return dict(CHARGES_DEFAULT, **(c.get("charges") or {}))


def leg_charges(side, value, rt=None):
    """Estimated charges for one buy or sell order of `value` rupees."""
    rt = rt or rates()
    brokerage = rt["brokerage_per_order"]
    stt = value * rt["stt_pct"] / 100
    exchange = value * rt["exchange_pct"] / 100
    sebi = value * rt["sebi_per_crore"] / 1e7
    stamp = value * rt["stamp_buy_pct"] / 100 if side == "buy" else 0.0
    gst = (brokerage + exchange + sebi) * rt["gst_pct"] / 100
    dp = rt["dp_per_sell"] if side == "sell" else 0.0
    total = brokerage + stt + exchange + sebi + stamp + gst + dp
    return {"brokerage": round(brokerage, 2), "stt": round(stt, 2), "exchange": round(exchange, 2),
            "sebi": round(sebi, 2), "stamp": round(stamp, 2), "gst": round(gst, 2), "dp": round(dp, 2),
            "total": round(total, 2)}


# ---------------------------------------------------------------- trades store

def trades():
    return rows(TRADES)


def save_trades(data):
    write_all(TRADES, TRADE_COLS, data)


def closed_trades():
    return [t for t in trades() if t.get("status") == "CLOSED"]


def realized_total():
    return sum(num(t.get("net_pnl")) or 0.0 for t in closed_trades())


def trade_for(symbol=None, trade_id=None, status=None):
    for t in reversed(trades()):
        if (trade_id and t["trade_id"] == trade_id) or (symbol and t["symbol"] == symbol and not trade_id):
            if status is None or t.get("status") == status:
                return t
    return None


def update_trade(trade_id, fields):
    data = trades()
    for t in data:
        if t["trade_id"] == trade_id:
            t.update({k: v for k, v in fields.items()})
            save_trades(data)
            return t
    return None


def new_trade_id(symbol, entry_date):
    base = f"{symbol}-{entry_date.replace('-', '')}"
    have = {t["trade_id"] for t in trades()}
    tid, n = base, 2
    while tid in have:
        tid, n = f"{base}-{n}", n + 1
    return tid


def log_event(symbol, event, trade_id="", date=None, time=None, **kw):
    append(EVENTS, EVENT_COLS, dict({"date": date or today(), "time": now_time() if time is None else time,
                                     "trade_id": trade_id,
                                     "symbol": symbol, "event": event}, **{k: v for k, v in kw.items() if v is not None}))


def equity_now(prices):
    c = cfg()
    pos = positions()
    cash = float(c["capital"]) + realized_total() - sum(p["cost_basis"] for p in pos)
    invested = 0.0
    missing = []
    for p in pos:
        px = prices.get(p["symbol"])
        if px is None:
            px = p.get("last_price") or p["entry_price"]
            missing.append(p["symbol"])
        invested += p["qty"] * float(px)
    return cash, invested, cash + invested, missing


# ---------------------------------------------------------------- scan files (where a signal's numbers come from)

def _scan_files():
    if not RUNS.exists():
        return []
    res = []
    for f in RUNS.glob("scan_*.json"):
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if str(d.get("strategy_version", "")).endswith("-trial"):
            continue  # trial scans are never traded
        res.append((d.get("as_of", ""), f.stat().st_mtime, d))
    res.sort(key=lambda x: (x[0], x[1]))
    return [d for _, _, d in res]


def load_scan(path):
    return json.loads(Path(path).expanduser().read_text())


def latest_scan(on_or_before=None):
    files = [d for d in _scan_files() if not on_or_before or d.get("as_of", "") <= on_or_before]
    return files[-1] if files else None


def find_signal(symbol, on_or_before=None, max_age_days=10, scan=None):
    """(scan, signal) for the latest scan on or before the date that listed this symbol as a signal."""
    files = [scan] if scan else list(reversed(_scan_files()))
    limit = None
    if on_or_before:
        limit = (dt.date.fromisoformat(on_or_before) - dt.timedelta(days=max_age_days)).isoformat()
    for d in files:
        a = d.get("as_of", "")
        if on_or_before and not scan and (a > on_or_before or a < limit):
            continue
        for key in ("signals", "signals_blocked_by_regime", "signals_blocked_by_strategy_rule"):
            for sg in d.get(key) or []:
                if sg.get("symbol") == symbol:
                    return d, sg
    return None, None


def scan_position(scan, symbol):
    for p in (scan or {}).get("positions") or []:
        if p.get("symbol") == symbol:
            return p
    return None


def _market_filter(scan):
    reg = (scan or {}).get("regime") or {}
    on = reg.get("new_entries_allowed")
    return ("on" if on else "off") if on is not None else "", reg.get("nifty500_close")


def _sizing_words(st):
    risk = (st.risk or {}) if st else {}
    sz = risk.get("sizing")
    if not sz:
        return ""
    words = f"risk {sz['pct']:g}% of equity per trade" if sz.get("type") == "risk" else f"{sz['pct']:g}% of equity per position"
    if risk.get("max_position_pct"):
        words += f", max {risk['max_position_pct']:g}% in one stock"
    return words


def signal_fields(scan, sg):
    if not sg:
        return {}
    mf, n500 = _market_filter(scan)
    rp = sg.get("rules_passed")
    return {"signal_date": scan.get("as_of"), "signal_close": sg.get("close"), "signal_rank": sg.get("rank"),
            "signal_score": sg.get("score"), "rules_passed": ";".join(rp) if isinstance(rp, list) else (rp or ""),
            "rsi": sg.get("rsi"), "vol_ratio": sg.get("vol_ratio"), "breakout_pct": sg.get("breakout_pct"),
            "ext_pct": sg.get("ext_pct"), "atr": sg.get("atr"), "turnover_cr": sg.get("turnover_cr"),
            "sma50": sg.get("sma50"), "sma200": sg.get("sma200"), "ret126_pct": sg.get("ret126"),
            "market_filter": mf, "nifty500_close": n500, "sector": sg.get("sector", ""),
            "limit_price": sg.get("limit_price"), "skip_if_open_above": sg.get("skip_if_open_above"),
            "planned_qty": sg.get("qty_est"), "planned_stop": sg.get("stop_level") or sg.get("stop_est"),
            "planned_target": sg.get("target_est")}


def _gtt_type(stop, target, gtt_id):
    if not gtt_id:
        return "none"
    return "two_leg" if stop and target else ("stop_only" if stop else "target_only")


def build_entry(p, sig, equity_before, open_before, st, note="", entry_time="", day_open=None, mode=""):
    """A full OPEN trade row from a position dict + its signal fields."""
    qty, price = int(p["qty"]), float(p["entry_price"])
    value = qty * price
    ch = leg_charges("buy", value)
    stop, target = num(p.get("initial_stop")), num(p.get("target"))
    rps = price - stop if stop is not None else None
    risk_amt = rps * qty if rps is not None else None
    sclose, limit = num(sig.get("signal_close")), num(sig.get("limit_price"))
    row = dict(sig)
    row.update({
        "trade_id": p["trade_id"], "status": "OPEN", "symbol": p["symbol"],
        "strategy": p.get("strategy") or engine.LEGACY_ID, "strategy_name": st.name if st else "",
        "strategy_version": p.get("version", ""), "mode": p.get("mode") or mode,
        "signal_rank": sig.get("signal_rank") if sig.get("signal_rank") is not None else p.get("rank"),
        "signal_score": sig.get("signal_score") if sig.get("signal_score") is not None else p.get("score"),
        "entry_check_note": note, "sizing_rule": _sizing_words(st),
        "entry_date": p["entry_date"], "entry_time": entry_time, "entry_order_id": ";".join(p.get("order_ids") or []),
        "qty": qty, "entry_price": price, "entry_day_open": day_open,
        "entry_vs_limit": r2(price - limit) if limit else None,
        "entry_vs_signal_close_pct": r2((price / sclose - 1) * 100) if sclose else None,
        "entry_value": r2(value), "entry_stt": ch["stt"], "entry_exchange": ch["exchange"], "entry_sebi": ch["sebi"],
        "entry_stamp": ch["stamp"], "entry_gst": ch["gst"], "entry_charges": ch["total"],
        "entry_cost": r2(value + ch["total"]),
        "initial_stop": stop, "initial_stop_pct": r2(rps / price * 100) if rps is not None else None,
        "risk_per_share": r2(rps), "risk_amount": r2(risk_amt),
        "risk_pct_of_equity": r2(risk_amt / equity_before * 100) if risk_amt is not None and equity_before else None,
        "target": target, "target_pct": r2((target / price - 1) * 100) if target else None,
        "reward_risk": r2((target - price) / rps) if target and rps else None,
        "position_pct_of_equity": r2(value / equity_before * 100) if equity_before else None,
        "equity_at_entry": r2(equity_before), "open_positions_at_entry": open_before,
        "gtt_id": p.get("gtt_id") or "", "gtt_type": _gtt_type(stop, target, p.get("gtt_id")),
        "current_stop": stop, "stop_moves": 0, "last_stop_move_date": "",
        "last_price": price, "last_price_date": p["entry_date"], "unrealized_pnl": 0.0, "unrealized_pct": 0.0,
        "sessions_held": 0, "highest_close": p.get("highest_close") or price,
    })
    return row, ch


# ---------------------------------------------------------------- upgrade from older journals

def _old_trade(r):
    """A closed trade written by v1.0 / v2.0 (18 columns) -> the v2.1 columns (what's known)."""
    sym, ed = r.get("symbol", ""), r.get("entry_date", "")
    oids = [x for x in (r.get("order_ids") or "").split(";") if x]
    pnl = num(r.get("pnl"))
    return {"trade_id": f"{sym}-{ed.replace('-', '')}", "status": "CLOSED", "symbol": sym,
            "strategy": r.get("strategy") or engine.LEGACY_ID, "strategy_version": r.get("version", ""),
            "mode": r.get("mode", ""), "signal_rank": r.get("entry_rank"), "signal_score": r.get("entry_score"),
            "entry_date": ed, "entry_order_id": oids[0] if oids else "", "qty": r.get("qty"),
            "entry_price": r.get("entry"), "initial_stop": r.get("initial_stop"), "current_stop": "",
            "exit_date": r.get("exit_date"), "exit_order_id": oids[-1] if len(oids) > 1 else "",
            "exit_reason": r.get("exit_reason"), "exit_price": r.get("exit"), "net_pnl": r.get("pnl"),
            "return_pct": r.get("ret_pct"), "r_multiple": r.get("r"), "calendar_days_held": r.get("hold_days"),
            "outcome": "" if pnl is None else ("win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"),
            "exit_note": f"from the {r.get('version') or 'older'} journal: charges were not itemized then"}


def ensure_journal():
    """Bring an older journal up to v2.1 once: back it up, convert closed trades, add open positions as OPEN trades."""
    if not CONFIG.exists():
        return
    old_trades = TRADES.exists() and _header(TRADES) != TRADE_COLS
    pos = positions()
    missing = [p for p in pos if not p.get("trade_id")]
    if not old_trades and not missing and (not DECISIONS.exists() or _header(DECISIONS) == DEC_COLS) \
            and (not DAILY.exists() or _header(DAILY) == DAILY_COLS):
        return
    backup = J / "backup_before_v2.1"
    if J.exists() and not backup.exists():
        backup.mkdir(parents=True)
        for f in J.glob("*.csv"):
            shutil.copy2(f, backup / f.name)
        if POSITIONS.exists():
            shutil.copy2(POSITIONS, backup / "positions.json")
    if old_trades:
        save_trades([_old_trade(r) for r in rows(TRADES)])
    if DECISIONS.exists() and _header(DECISIONS) != DEC_COLS:
        decs = rows(DECISIONS)
        for d in decs:
            scan, sg = find_signal(d["symbol"], d["date"])
            if sg and not d.get("close"):
                d.update({"signal_date": scan.get("as_of"), "close": sg.get("close"), "rank": sg.get("rank"),
                          "score": sg.get("score"), "sector": sg.get("sector"), "limit_price": sg.get("limit_price"),
                          "stop_est": sg.get("stop_level") or sg.get("stop_est"), "target_est": sg.get("target_est"),
                          "qty_est": sg.get("qty_est"), "value_est": sg.get("value_est")})
            p = next((x for x in pos if x["symbol"] == d["symbol"] and x["entry_date"] == d["date"]), None)
            if d["decision"] == "taken" and p and not d.get("trade_id"):
                d["trade_id"] = p.get("trade_id") or f"{p['symbol']}-{p['entry_date'].replace('-', '')}"
        write_all(DECISIONS, DEC_COLS, decs)
    _upgrade(DAILY, DAILY_COLS)
    if not missing:
        return
    c = cfg()
    decs = rows(DECISIONS)
    daily = rows(DAILY)
    data = trades()
    for p in missing:
        p.setdefault("strategy", engine.LEGACY_ID)
        p["trade_id"] = new_trade_id(p["symbol"], p["entry_date"])
        st = strategy(p["strategy"])
        scan, sg = find_signal(p["symbol"], p["entry_date"])
        sig = signal_fields(scan, sg)
        taken = next((d for d in reversed(decs) if d["symbol"] == p["symbol"] and d["decision"] == "taken"
                      and d["date"] == p["entry_date"]), None)
        before = [r for r in daily if r["date"] < p["entry_date"] and r.get("equity")]
        eq = num(before[-1]["equity"]) if before else float(c["capital"]) + realized_total()
        n_open = sum(1 for q in pos if q["entry_date"] < p["entry_date"])
        row, ch = build_entry(p, sig, eq, n_open, st, entry_time=(taken or {}).get("time", ""), mode=c["mode"],
                              note="added from the bot's saved records when the journal was upgraded to v2.1")
        if p.get("last_price"):
            px = float(p["last_price"])
            row.update({"last_price": px, "last_price_date": (daily[-1]["date"] if daily else p["entry_date"]),
                        "unrealized_pnl": r2((px - row["entry_price"]) * row["qty"]),
                        "unrealized_pct": r2((px / row["entry_price"] - 1) * 100)})
        if p.get("stop") is not None and p.get("initial_stop") is not None and float(p["stop"]) != float(p["initial_stop"]):
            row.update({"current_stop": p["stop"], "stop_moves": 1})
        p["cost_basis"] = row["entry_cost"]  # itemized charges replace the old 0.12% estimate
        data.append(row)
        if sg:
            log_event(p["symbol"], "signal", p["trade_id"], date=scan.get("as_of"), time="",
                      price=sg.get("close"), note=f"rank {sg.get('rank')}, score {sg.get('score')}")
        log_event(p["symbol"], "entry_filled", p["trade_id"], date=p["entry_date"], time=row["entry_time"],
                  price=row["entry_price"], qty=row["qty"], order_id=row["entry_order_id"],
                  note="added from saved records (journal upgrade to v2.1)")
        if p.get("gtt_id"):
            log_event(p["symbol"], "gtt_placed", p["trade_id"], date=p["entry_date"], time=row["entry_time"],
                      gtt_id=p["gtt_id"], new_value=f"stop {p.get('initial_stop')} / target {p.get('target')}",
                      note=row["gtt_type"])
    save_trades(data)
    save_positions(pos)


# ---------------------------------------------------------------- commands

def cmd_init(a):
    HOME.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists() and not a.force:
        c = json.loads(CONFIG.read_text())
        out({"status": "already set up", "config": c})
        return
    c = dict(DEFAULT_CONFIG, capital=a.capital, mode=a.mode.upper(), created=today())
    CONFIG.write_text(json.dumps(c, indent=1))
    for d in ("journal", "learning/configs", "weekly", "runs", "strategies"):
        (HOME / d).mkdir(parents=True, exist_ok=True)
    if not POSITIONS.exists():
        save_positions([])
    out({"status": "created", "home": str(HOME), "config": c,
         "next": "choose a strategy and answer its risk questions (strategies skill)"})


def _week_base(c, ws):
    before = [r for r in rows(DAILY) if r["date"] < ws and r.get("equity")]
    return float(before[-1]["equity"]) if before else float(c["capital"])


def cmd_status(a):
    c = cfg()
    prices = json.loads(a.prices) if a.prices else {}
    cash, invested, equity, missing = equity_now(prices)
    pos = positions()
    for p in pos:
        px = prices.get(p["symbol"], p.get("last_price") or p["entry_price"])
        p["price"] = px
        p["unrealized"] = round((float(px) - p["entry_price"]) * p["qty"], 2)
        p["unrealized_pct"] = round((float(px) / p["entry_price"] - 1) * 100, 2)
    ws = week_start(dt.date.today()).isoformat()
    week_base = _week_base(c, ws)
    week_pnl = equity - week_base
    # the limits are the ones the user chose for the active strategy (null = no such limit)
    st = strategy()
    risk = (st.risk if st and st.risk_confirmed else None) or {}
    weekly_lim, halt_lim, max_pos = risk.get("weekly_loss_limit_pct"), risk.get("halt_drawdown_pct"), risk.get("max_positions")
    weekly_block = bool(weekly_lim) and week_pnl <= -week_base * weekly_lim / 100
    peak = max([float(c["capital"]), equity] + [float(r["equity"]) for r in rows(DAILY) if r.get("equity")])
    drawdown_pct = 100 * (equity / peak - 1)
    halt = bool(halt_lim) and drawdown_pct <= -halt_lim
    no_strategy = st is None
    unconfirmed = st is not None and not st.risk_confirmed
    reasons = [r for r, f in (("no_active_strategy", no_strategy), ("risk_not_confirmed", unconfirmed),
                              ("weekly_loss_limit", weekly_block), ("drawdown_halt", halt)) if f]
    out({
        "mode": c["mode"], "capital": c["capital"],
        "strategy": st.id if st else c.get("active_strategy"), "strategy_name": st.name if st else None,
        "version": st.version if st else None,
        "limits": {"weekly_loss_limit_pct": weekly_lim, "halt_drawdown_pct": halt_lim, "max_positions": max_pos},
        "equity": round(equity, 2), "cash": round(cash, 2), "invested": round(invested, 2),
        "realized_total": round(realized_total(), 2),
        "week_start": ws, "week_pnl": round(week_pnl, 2), "week_pnl_pct": round(100 * week_pnl / week_base, 2),
        "peak_equity": round(peak, 2), "drawdown_pct": round(drawdown_pct, 2),
        "open_positions": pos, "slots_free": (max_pos - len(pos)) if max_pos else None,
        "new_entries_blocked": bool(reasons),
        "block_reasons": reasons,
        "prices_missing_for": missing,
    })


def cmd_decision(a):
    c = cfg()
    sid, ver = sid_version(a.strategy)
    d = a.date or today()
    scan = load_scan(a.scan) if a.scan else None
    scan, sg = find_signal(a.symbol, d, scan=scan)
    sg = sg or {}
    pos = next((p for p in positions() if p["symbol"] == a.symbol), None)
    append(DECISIONS, DEC_COLS, {
        "date": d, "time": dt.datetime.now().strftime("%H:%M"), "strategy": sid, "symbol": a.symbol,
        "decision": a.decision, "reason": a.reason, "signal_date": (scan or {}).get("as_of", ""),
        "close": sg.get("close"), "rank": sg.get("rank"), "score": sg.get("score"), "sector": sg.get("sector"),
        "limit_price": sg.get("limit_price"), "stop_est": sg.get("stop_level") or sg.get("stop_est"),
        "target_est": sg.get("target_est"), "qty_est": sg.get("qty_est"), "value_est": sg.get("value_est"),
        "trade_id": (pos or {}).get("trade_id", "") if a.decision == "taken" else "", "version": ver, "mode": c["mode"]})
    out({"logged": a.symbol, "decision": a.decision, "signal_found": bool(sg)})


def cmd_open(a):
    c = cfg()
    pos = positions()
    if any(p["symbol"] == a.symbol for p in pos):
        sys.exit(f"{a.symbol} is already an open bot position")
    st = strategy(a.strategy)
    if st is None:
        sys.exit(f"can't load strategy '{a.strategy or c.get('active_strategy')}'")
    if a.stop is None and (st.risk or {}).get("broker_stop_required", True):
        sys.exit(f"refused: {st.id} requires a stop for every position (--stop)")
    ed = a.date or today()
    t = a.time or now_time()
    _, _, equity_before, _ = equity_now({})
    scan = load_scan(a.scan) if a.scan else None
    scan, sg = find_signal(a.symbol, ed, scan=scan)
    sig = signal_fields(scan, sg)
    p = {"symbol": a.symbol, "strategy": st.id, "qty": a.qty, "entry_price": a.price, "entry_date": ed,
         "stop": a.stop, "initial_stop": a.stop, "target": a.target, "highest_close": a.price, "gtt_id": a.gtt_id,
         "order_ids": [x for x in [a.order_id] if x], "rank": a.rank if a.rank is not None else sig.get("signal_rank"),
         "score": a.score if a.score is not None else sig.get("signal_score"), "mode": c["mode"], "version": st.version,
         "trade_id": new_trade_id(a.symbol, ed)}
    row, ch = build_entry(p, sig, equity_before, len(pos), st, note=a.note or "", entry_time=t,
                          day_open=a.day_open, mode=c["mode"])
    p["cost_basis"] = row["entry_cost"]
    pos.append(p)
    data = trades()
    data.append(row)
    save_trades(data)
    save_positions(pos)
    # events: the signal, any order events logged before the fill, the fill, the broker GTT
    evs = rows(EVENTS)
    claimed = False
    for e in evs:
        if e["symbol"] == a.symbol and not e.get("trade_id") and e["date"] == ed:
            e["trade_id"], claimed = p["trade_id"], True
    if claimed:
        write_all(EVENTS, EVENT_COLS, evs)
    if sg:
        log_event(a.symbol, "signal", p["trade_id"], date=scan.get("as_of"), time="", price=sg.get("close"),
                  note=f"rank {sg.get('rank')}, score {sg.get('score')}")
    log_event(a.symbol, "entry_filled", p["trade_id"], date=ed, time=t, price=a.price, qty=a.qty,
              order_id=a.order_id, note=f"limit {sig.get('limit_price')}" if sig.get("limit_price") else None)
    if a.gtt_id:
        log_event(a.symbol, "gtt_placed", p["trade_id"], date=ed, time=t, gtt_id=a.gtt_id,
                  new_value=f"stop {a.stop} / target {a.target}", note=row["gtt_type"])
    append(DECISIONS, DEC_COLS, {
        "date": ed, "time": t[:5], "strategy": st.id, "symbol": a.symbol, "decision": "taken",
        "reason": f"filled {a.qty} @ {a.price}, stop {a.stop}, target {a.target}",
        "signal_date": sig.get("signal_date", ""), "close": sig.get("signal_close"), "rank": sig.get("signal_rank"),
        "score": sig.get("signal_score"), "sector": sig.get("sector"), "limit_price": sig.get("limit_price"),
        "stop_est": sig.get("planned_stop"), "target_est": sig.get("planned_target"), "qty_est": sig.get("planned_qty"),
        "value_est": (sg or {}).get("value_est"), "trade_id": p["trade_id"], "version": st.version, "mode": c["mode"]})
    out({"opened": a.symbol, "trade_id": p["trade_id"], "strategy": st.id, "qty": a.qty, "price": a.price,
         "stop": a.stop, "target": a.target, "charges": ch, "cost": row["entry_cost"],
         "risk_amount": row["risk_amount"], "risk_pct_of_equity": row["risk_pct_of_equity"],
         "signal_found": bool(sg)})


def cmd_event(a):
    cfg()
    p = next((x for x in positions() if x["symbol"] == a.symbol), None)
    tid = (p or {}).get("trade_id", "")
    log_event(a.symbol, a.event, tid, date=a.date, time=a.time, price=a.price, qty=a.qty, order_id=a.order_id,
              gtt_id=a.gtt_id, old_value=a.old, new_value=a.new, note=a.note)
    if p and a.gtt_id and a.event in ("gtt_placed", "gtt_replaced"):
        pos = positions()
        for x in pos:
            if x["symbol"] == a.symbol:
                x["gtt_id"] = a.gtt_id
        save_positions(pos)
        update_trade(tid, {"gtt_id": a.gtt_id})
    out({"logged": a.event, "symbol": a.symbol, "trade_id": tid or None})


def cmd_stop(a):
    pos = positions()
    for p in pos:
        if p["symbol"] == a.symbol:
            st = strategy(p.get("strategy") or engine.LEGACY_ID)
            only_up = (st.risk or {}).get("stops_only_up", True) if st else True
            if only_up and p.get("stop") is not None and a.stop < p["stop"]:
                sys.exit(f"refused: this strategy's stops only move up ({p['stop']} -> {a.stop})")
            old = p.get("stop")
            p["stop"] = a.stop
            if a.gtt_id:
                p["gtt_id"] = a.gtt_id
            if a.highest_close:
                p["highest_close"] = max(p.get("highest_close") or 0, a.highest_close)
            save_positions(pos)
            tid = p.get("trade_id", "")
            t = trade_for(trade_id=tid) if tid else None
            if t:
                update_trade(tid, {"current_stop": a.stop, "stop_moves": int(num(t.get("stop_moves")) or 0) + 1,
                                   "last_stop_move_date": a.date or today(), "gtt_id": p.get("gtt_id") or "",
                                   "highest_close": p.get("highest_close")})
            log_event(a.symbol, "stop_moved", tid, date=a.date, old_value=old, new_value=a.stop, gtt_id=p.get("gtt_id"),
                      note=a.reason)
            out({"symbol": a.symbol, "trade_id": tid or None, "old_stop": old, "stop": a.stop})
            return
    sys.exit(f"{a.symbol} is not an open bot position")


def _best_worst(t, entry, rps, cand):
    """Update best/worst price fields from (price, date) candidates."""
    best, worst = num(t.get("best_price")), num(t.get("worst_price"))
    upd = {}
    for px, d in cand:
        if px is None:
            continue
        if best is None or px > best:
            best, upd["best_price"], upd["best_price_date"] = px, px, d
        if worst is None or px < worst:
            worst, upd["worst_price"], upd["worst_price_date"] = px, px, d
    if best is not None:
        upd["best_pct"] = r2((best / entry - 1) * 100)
        upd["best_r"] = r2((best - entry) / rps) if rps else None
    if worst is not None:
        upd["worst_pct"] = r2((worst / entry - 1) * 100)
        upd["worst_r"] = r2((worst - entry) / rps) if rps else None
    return upd


def _weekdays_between(d0, d1):
    a, b = dt.date.fromisoformat(d0), dt.date.fromisoformat(d1)
    return sum(1 for i in range(1, (b - a).days + 1) if (a + dt.timedelta(days=i)).weekday() < 5)


def cmd_close(a):
    c = cfg()
    pos = positions()
    p = next((x for x in pos if x["symbol"] == a.symbol), None)
    if not p:
        sys.exit(f"{a.symbol} is not an open bot position")
    exit_date, t_exit = a.date or today(), a.time or now_time()
    tid = p.get("trade_id", "")
    t = trade_for(trade_id=tid) or {}
    qty, entry = int(p["qty"]), float(p["entry_price"])
    value = qty * a.price
    ch = leg_charges("sell", value)
    entry_ch = num(t.get("entry_charges"))
    if entry_ch is None:
        entry_ch = p["cost_basis"] - qty * entry
    gross = (a.price - entry) * qty
    total_ch = entry_ch + ch["total"]
    net = gross - total_ch
    init_stop = num(p.get("initial_stop"))
    rps = entry - init_stop if init_stop is not None else None
    scan = latest_scan(exit_date)
    sp = scan_position(scan, a.symbol) if scan else None
    cand = [(num((sp or {}).get("best_high")), (sp or {}).get("best_high_date")),
            (num((sp or {}).get("worst_low")), (sp or {}).get("worst_low_date")), (a.price, exit_date)]
    bw = _best_worst(t, entry, rps, cand)
    best = num(bw.get("best_price", t.get("best_price")))
    sessions = (sp or {}).get("sessions_held")
    if sessions is None:
        sessions = _weekdays_between(p["entry_date"], exit_date)  # no scan row: weekdays (holidays not excluded)
    elif scan.get("as_of") < exit_date:
        sessions += 1  # the scan is from the previous close; the exit day is one more session
    via = a.via or ("paper" if (p.get("mode") or c["mode"]) == "PAPER" else "")
    fields = dict(bw, **{
        "status": "CLOSED", "exit_date": exit_date, "exit_time": t_exit, "exit_order_id": a.order_id or "",
        "exit_via": via, "exit_reason": a.reason, "exit_trigger_price": a.trigger_price, "exit_price": a.price,
        "exit_slippage": r2(a.price - a.trigger_price) if a.trigger_price else None,
        "exit_value": r2(value), "exit_stt": ch["stt"], "exit_exchange": ch["exchange"], "exit_sebi": ch["sebi"],
        "exit_gst": ch["gst"], "exit_dp": ch["dp"], "exit_charges": ch["total"],
        "gross_pnl": r2(gross), "total_charges": r2(total_ch), "net_pnl": r2(net),
        "return_pct": r2((a.price / entry - 1) * 100),
        "net_return_pct": r2(net / (qty * entry + entry_ch) * 100),
        "r_multiple": r2((a.price - entry) / rps) if rps and rps > 0 else None,
        "net_r": r2(net / (rps * qty)) if rps and rps > 0 else None,
        "calendar_days_held": (dt.date.fromisoformat(exit_date) - dt.date.fromisoformat(p["entry_date"])).days,
        "sessions_held": sessions,
        "captured_pct_of_best": r2((a.price - entry) / (best - entry) * 100) if best and best > entry else None,
        "outcome": "win" if net > 0 else "loss" if net < 0 else "breakeven",
        "last_price": a.price, "last_price_date": exit_date, "unrealized_pnl": "", "unrealized_pct": "",
        "exit_note": a.note or ""})
    if not t:  # an open position with no trade row (shouldn't happen after the upgrade): build it now
        st = strategy(p.get("strategy"))
        p.setdefault("trade_id", new_trade_id(p["symbol"], p["entry_date"]))
        tid = p["trade_id"]
        row, _ = build_entry(p, {}, None, None, st, mode=c["mode"])
        data = trades()
        data.append(row)
        save_trades(data)
    update_trade(tid, fields)
    save_positions([x for x in pos if x["symbol"] != a.symbol])
    _, _, eq_after, _ = equity_now({})
    row = update_trade(tid, {"equity_after": r2(eq_after)})
    if a.trigger_price and via.startswith("gtt"):
        log_event(a.symbol, "gtt_triggered", tid, date=exit_date, time=t_exit, price=a.trigger_price,
                  gtt_id=p.get("gtt_id"))
    log_event(a.symbol, "exit_filled", tid, date=exit_date, time=t_exit, price=a.price, qty=qty,
              order_id=a.order_id, note=a.reason)
    log_event(a.symbol, "closed", tid, date=exit_date, time=t_exit, new_value=r2(net),
              note=f"net P&L after charges; {fields['outcome']}")
    out({"closed": {k: row.get(k) for k in ("trade_id", "symbol", "qty", "entry_price", "exit_price", "exit_reason",
                                             "gross_pnl", "total_charges", "net_pnl", "return_pct", "r_multiple",
                                             "calendar_days_held", "outcome")}})


def cmd_snapshot(a):
    c = cfg()
    prices = json.loads(a.prices) if a.prices else {}
    d = a.date or today()
    scan = load_scan(a.scan) if a.scan else latest_scan(d)
    pos = positions()
    for p in pos:
        if p["symbol"] in prices:
            p["last_price"] = prices[p["symbol"]]
    save_positions(pos)
    # update every open trade's live columns
    data = trades()
    unreal = open_risk = 0.0
    for p in pos:
        t = next((x for x in data if x["trade_id"] == p.get("trade_id")), None)
        if t is None:
            continue
        sp = scan_position(scan, p["symbol"]) or {}
        px = num(prices.get(p["symbol"])) or num(sp.get("close")) or num(p.get("last_price")) or p["entry_price"]
        entry, qty = float(p["entry_price"]), int(p["qty"])
        init_stop = num(p.get("initial_stop"))
        rps = entry - init_stop if init_stop is not None else None
        cand = [(num(sp.get("best_high")), sp.get("best_high_date")), (num(sp.get("worst_low")), sp.get("worst_low_date"))]
        if p["symbol"] in prices and d > p["entry_date"]:
            cand.append((px, d))
        t.update(_best_worst(t, entry, rps, cand))
        hc = max(num(sp.get("highest_close")) or 0, num(p.get("highest_close")) or 0) or None
        t.update({"last_price": px, "last_price_date": d, "unrealized_pnl": r2((px - entry) * qty),
                  "unrealized_pct": r2((px / entry - 1) * 100), "highest_close": hc,
                  "current_stop": p.get("stop"), "gtt_id": p.get("gtt_id") or ""})
        if sp.get("sessions_held") is not None:
            t["sessions_held"] = sp["sessions_held"]
        unreal += (px - entry) * qty
        if p.get("stop") is not None:
            open_risk += max(0.0, (px - float(p["stop"])) * qty)
    save_trades(data)
    cash, invested, equity, missing = equity_now(prices)
    existing = [r for r in rows(DAILY) if r["date"] != d]
    prev = [r for r in existing if r["date"] < d and r.get("equity")]
    prev_eq = float(prev[-1]["equity"]) if prev else float(c["capital"])
    ws = week_start(dt.date.fromisoformat(d)).isoformat()
    wb = [r for r in existing if r["date"] < ws and r.get("equity")]
    week_base = float(wb[-1]["equity"]) if wb else float(c["capital"])
    peak = max([float(c["capital"]), equity] + [float(r["equity"]) for r in existing if r.get("equity")])
    all_t = trades()
    charges_total = sum((num(t.get("entry_charges")) or 0) + (num(t.get("exit_charges")) or 0) for t in all_t)
    _, n500 = _market_filter(scan)
    sid, ver = sid_version()
    new = {"date": d, "equity": round(equity, 2), "cash": round(cash, 2), "invested": round(invested, 2),
           "open_positions": len(pos), "unrealized_total": round(unreal, 2), "realized_total": round(realized_total(), 2),
           "day_pnl": round(equity - prev_eq, 2), "day_pnl_pct": round((equity / prev_eq - 1) * 100, 2),
           "week_pnl": round(equity - week_base, 2), "week_pnl_pct": round((equity / week_base - 1) * 100, 2),
           "peak_equity": round(peak, 2), "drawdown_pct": round((equity / peak - 1) * 100, 2),
           "open_risk": round(open_risk, 2), "charges_total": round(charges_total, 2),
           "trades_closed_total": sum(1 for t in all_t if t.get("status") == "CLOSED"),
           "nifty500_close": n500, "mode": c["mode"], "strategy": sid, "version": ver}
    write_all(DAILY, DAILY_COLS, sorted(existing + [new], key=lambda r: r["date"]))
    out(dict(new, prices_missing_for=missing, scan_used=(scan or {}).get("as_of")))


def _grouped(t):
    return {g: {c: t.get(c) for c in cols if t.get(c) not in (None, "")} for g, cols in TRADE_GROUPS.items()}


def cmd_trade(a):
    cfg()
    t = trade_for(symbol=a.symbol, trade_id=a.id)
    if not t:
        sys.exit(f"no trade found for {a.id or a.symbol}")
    evs = sorted((e for e in rows(EVENTS) if e.get("trade_id") == t["trade_id"]), key=lambda e: (e["date"], e["time"]))
    out({"trade": _grouped(t), "events": [{k: v for k, v in e.items() if v not in (None, "")} for e in evs]})


def cmd_trades(a):
    cfg()
    res = []
    for t in trades():
        if a.status and t.get("status") != a.status.upper():
            continue
        if a.strategy and t.get("strategy") != a.strategy:
            continue
        res.append({k: t.get(k) for k in ("trade_id", "status", "symbol", "strategy", "entry_date", "qty", "entry_price",
                                           "current_stop", "target", "last_price", "unrealized_pnl", "exit_date",
                                           "exit_price", "exit_reason", "net_pnl", "r_multiple", "outcome")})
    out({"count": len(res), "trades": res})


def cmd_weekly(a):
    c = cfg()
    if a.week:
        y, w = a.week.split("-W")
        ws = dt.date.fromisocalendar(int(y), int(w), 1)
    else:
        ws = week_start(dt.date.today())
    we = ws + dt.timedelta(days=6)
    daily = rows(DAILY)
    prev = [r for r in daily if r["date"] < ws.isoformat()]
    inweek = [r for r in daily if ws.isoformat() <= r["date"] <= we.isoformat()]
    start_eq = float(prev[-1]["equity"]) if prev else float(c["capital"])
    end_eq = float(inweek[-1]["equity"]) if inweek else start_eq
    all_closed = closed_trades()
    closed = [t for t in all_closed if ws.isoformat() <= t["exit_date"] <= we.isoformat()]
    opened = [t for t in trades() if ws.isoformat() <= t.get("entry_date", "") <= we.isoformat()]
    decs = [d for d in rows(DECISIONS) if ws.isoformat() <= d["date"] <= we.isoformat()]
    pnl = lambda t: num(t.get("net_pnl")) or 0.0  # noqa: E731
    wins = [t for t in all_closed if pnl(t) > 0]
    rs = [num(t.get("r_multiple")) for t in all_closed if num(t.get("r_multiple")) is not None]
    caps = [num(t.get("captured_pct_of_best")) for t in all_closed if num(t.get("captured_pct_of_best")) is not None]
    days = [num(t.get("calendar_days_held")) for t in all_closed if num(t.get("calendar_days_held")) is not None]
    week_charges = sum(num(t.get("entry_charges")) or 0 for t in opened) + sum(num(t.get("exit_charges")) or 0 for t in closed)
    peak, mdd = float(c["capital"]), 0.0
    for r in daily:
        e = float(r["equity"])
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    short = ("trade_id", "symbol", "strategy", "entry_date", "entry_price", "exit_date", "exit_price", "exit_reason",
             "net_pnl", "return_pct", "r_multiple", "best_pct", "worst_pct", "captured_pct_of_best", "calendar_days_held")
    res = {
        "week": f"{ws.isocalendar()[0]}-W{ws.isocalendar()[1]:02d}", "from": ws.isoformat(), "to": we.isoformat(),
        "mode": c["mode"], "strategy": sid_version()[0], "version": sid_version()[1], "capital": c["capital"],
        "week_start_equity": round(start_eq, 2), "week_end_equity": round(end_eq, 2),
        "week_pnl": round(end_eq - start_eq, 2), "week_pnl_pct": round(100 * (end_eq / start_eq - 1), 2),
        "since_start_pnl": round(end_eq - float(c["capital"]), 2),
        "since_start_pct": round(100 * (end_eq / float(c["capital"]) - 1), 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "nifty500_week": {"start": num(prev[-1].get("nifty500_close")) if prev else None,
                          "end": num(inweek[-1].get("nifty500_close")) if inweek else None},
        "opened_this_week": [{k: t.get(k) for k in ("trade_id", "symbol", "entry_date", "qty", "entry_price",
                                                    "initial_stop", "target", "risk_amount")} for t in opened],
        "closed_this_week": [{k: t.get(k) for k in short} for t in closed],
        "charges_this_week": round(week_charges, 2),
        "decisions_this_week": {k: sum(1 for d in decs if d["decision"] == k) for k in sorted({d["decision"] for d in decs})},
        "vetoes_this_week": [d for d in decs if d["decision"] == "vetoed"],
        "open_trades": [{k: t.get(k) for k in ("trade_id", "symbol", "entry_date", "qty", "entry_price", "current_stop",
                                               "target", "last_price", "unrealized_pnl", "unrealized_pct", "best_pct",
                                               "worst_pct", "sessions_held")}
                        for t in trades() if t.get("status") == "OPEN"],
        "by_strategy": {s: {"trades": sum(1 for t in all_closed if t["strategy"] == s),
                            "net_pnl": round(sum(pnl(t) for t in all_closed if t["strategy"] == s), 2)}
                        for s in sorted({t["strategy"] for t in all_closed})},
        "all_time": {"trades": len(all_closed),
                     "win_rate_pct": round(100 * len(wins) / len(all_closed), 1) if all_closed else None,
                     "net_pnl": round(sum(pnl(t) for t in all_closed), 2),
                     "charges": round(sum(num(t.get("total_charges")) or 0 for t in all_closed), 2),
                     "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
                     "avg_days_held": round(sum(days) / len(days), 1) if days else None,
                     "avg_captured_pct_of_best": round(sum(caps) / len(caps), 1) if caps else None,
                     "best": {k: v for k, v in max(all_closed, key=pnl).items() if k in short} if all_closed else None,
                     "worst": {k: v for k, v in min(all_closed, key=pnl).items() if k in short} if all_closed else None},
        "equity_curve": [{"date": r["date"], "equity": float(r["equity"])} for r in daily],
    }
    (HOME / "weekly").mkdir(parents=True, exist_ok=True)
    (HOME / "weekly" / f"{res['week']}.json").write_text(json.dumps(res, indent=1, default=str))
    out(res)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--capital", type=float, default=100000)
    p.add_argument("--mode", default="PAPER", choices=["PAPER", "LIVE", "paper", "live"])
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("status")
    p.add_argument("--prices")
    p = sub.add_parser("decision")
    p.add_argument("--symbol", required=True)
    p.add_argument("--decision", required=True,
                   choices=["taken", "vetoed", "skipped_gap", "skipped_slots", "skipped_cash", "unfilled", "blocked_regime",
                            "blocked_limits", "blocked_strategy_rule"])
    p.add_argument("--reason", required=True)
    p.add_argument("--strategy", help="default: the active strategy")
    p.add_argument("--scan", help="scan file the signal came from (default: the latest one listing the symbol)")
    p.add_argument("--date")
    p = sub.add_parser("open")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", type=int, required=True)
    p.add_argument("--price", type=float, required=True, help="average fill price")
    p.add_argument("--stop", type=float, help="required unless the strategy's risk allows no broker stop")
    p.add_argument("--target", type=float)
    p.add_argument("--strategy", help="default: the active strategy")
    p.add_argument("--gtt-id")
    p.add_argument("--order-id")
    p.add_argument("--time", help="fill time HH:MM:SS (from the order's exchange timestamp)")
    p.add_argument("--day-open", type=float, help="today's open from the quote")
    p.add_argument("--note", help="the judgment checks done before buying, in one line")
    p.add_argument("--scan", help="scan file the signal came from (default: the latest one listing the symbol)")
    p.add_argument("--rank", type=int)
    p.add_argument("--score", type=float)
    p.add_argument("--date")
    p = sub.add_parser("event")
    p.add_argument("--symbol", required=True)
    p.add_argument("--event", required=True, choices=EVENT_NAMES)
    p.add_argument("--price", type=float)
    p.add_argument("--qty", type=int)
    p.add_argument("--order-id")
    p.add_argument("--gtt-id")
    p.add_argument("--old")
    p.add_argument("--new")
    p.add_argument("--note")
    p.add_argument("--date")
    p.add_argument("--time")
    p = sub.add_parser("stop")
    p.add_argument("--symbol", required=True)
    p.add_argument("--stop", type=float, required=True)
    p.add_argument("--gtt-id")
    p.add_argument("--reason", default="trail", help="why the stop moved: trail, breakeven, the strategy's rule name, ...")
    p.add_argument("--highest-close", type=float)
    p.add_argument("--date")
    p = sub.add_parser("close")
    p.add_argument("--symbol", required=True)
    p.add_argument("--price", type=float, required=True, help="average exit fill price")
    p.add_argument("--reason", required=True)
    p.add_argument("--order-id")
    p.add_argument("--via", choices=["gtt_stop", "gtt_target", "market_sell", "paper"])
    p.add_argument("--trigger-price", type=float, help="the GTT trigger or the price the exit was planned at")
    p.add_argument("--time", help="exit fill time HH:MM:SS")
    p.add_argument("--note")
    p.add_argument("--date")
    p = sub.add_parser("snapshot")
    p.add_argument("--prices")
    p.add_argument("--scan", help="today's scan file (default: the latest)")
    p.add_argument("--date")
    p = sub.add_parser("trade")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--symbol")
    g.add_argument("--id")
    p = sub.add_parser("trades")
    p.add_argument("--status", choices=["OPEN", "CLOSED", "open", "closed"])
    p.add_argument("--strategy")
    p = sub.add_parser("weekly")
    p.add_argument("--week")
    a = ap.parse_args()
    if a.cmd != "init":
        cfg()
        ensure_journal()
    {"init": cmd_init, "status": cmd_status, "decision": cmd_decision, "open": cmd_open, "event": cmd_event,
     "stop": cmd_stop, "close": cmd_close, "snapshot": cmd_snapshot, "trade": cmd_trade, "trades": cmd_trades,
     "weekly": cmd_weekly}[a.cmd](a)


if __name__ == "__main__":
    main()
