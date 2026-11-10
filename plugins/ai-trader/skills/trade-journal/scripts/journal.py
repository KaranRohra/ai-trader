#!/usr/bin/env python3
"""Bot bookkeeping (Python standard library only). The bot keeps its OWN books, separate from
the rest of the Zerodha account, so equity/P&L only ever include trades the bot made.

Files in ~/ai-trader/
  config.json            mode, capital, limits, strategy version
  positions.json         open bot positions (symbol, qty, entry, stop, GTT id, ...)
  journal/trades.csv     every closed trade
  journal/decisions.csv  every signal decision: taken / vetoed / skipped_* (with the reason)
  journal/daily.csv      equity snapshot per run

Commands (all print JSON)
  init --capital 100000 [--mode PAPER|LIVE]
  status [--prices '{"SYM": 123.4}']     equity, open P&L, week P&L, risk-limit flags
  decision --symbol X --decision taken|vetoed|skipped_gap|skipped_slots|skipped_cash|unfilled --reason "..."
  open --symbol X --qty N --price P --stop S [--target T] [--gtt-id ID] [--order-id ID] [--rank R] [--score S]
  stop --symbol X --stop S [--gtt-id ID]
  close --symbol X --price P --reason "..." [--order-id ID]
  snapshot [--prices '{...}']            append today's equity to daily.csv
  weekly [--week 2026-W45]               weekly stats (also writes weekly/<week>.json)
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
from pathlib import Path

HOME = Path(os.environ.get("AI_TRADER_HOME", Path.home() / "ai-trader"))
CONFIG = HOME / "config.json"
POSITIONS = HOME / "positions.json"
J = HOME / "journal"
TRADES = J / "trades.csv"
DECISIONS = J / "decisions.csv"
DAILY = J / "daily.csv"
COST_SIDE = 0.0012   # STT + stamp + exchange + GST, approx, delivery
DP_CHARGE = 16.0     # per sell (CDSL + Zerodha)

TRADE_COLS = ["symbol", "mode", "version", "entry_date", "entry", "qty", "initial_stop", "exit_date", "exit",
              "exit_reason", "pnl", "ret_pct", "r", "hold_days", "entry_rank", "entry_score", "order_ids"]
DEC_COLS = ["date", "time", "symbol", "decision", "reason", "version", "mode"]
DAILY_COLS = ["date", "equity", "cash", "invested", "open_positions", "realized_total", "mode", "version"]

DEFAULT_CONFIG = {
    "version": "v1.0",
    "mode": "PAPER",
    "capital": 100000,
    "order_tag": "CLAUDEBOT",
    "limits": {"weekly_loss_pct": 8.0, "halt_drawdown_pct": 30.0},
    "strategy": {},
}


def out(obj):
    print(json.dumps(obj, indent=1, default=str))


def cfg():
    if not CONFIG.exists():
        sys.exit("not set up yet - run the trading-setup skill (journal.py init)")
    return json.loads(CONFIG.read_text())


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
        return list(csv.DictReader(fh))


def append(path, cols, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def today():
    return dt.date.today().isoformat()


def realized_total():
    return sum(float(t["pnl"]) for t in rows(TRADES))


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


def week_start(d):
    return d - dt.timedelta(days=d.weekday())


# ---------------------------------------------------------------- commands

def cmd_init(a):
    HOME.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists() and not a.force:
        c = json.loads(CONFIG.read_text())
        out({"status": "already set up", "config": c})
        return
    c = dict(DEFAULT_CONFIG, capital=a.capital, mode=a.mode.upper(), created=today())
    CONFIG.write_text(json.dumps(c, indent=1))
    for d in ("journal", "learning/configs", "weekly", "runs"):
        (HOME / d).mkdir(parents=True, exist_ok=True)
    (HOME / "learning" / "configs" / "v1.0.json").write_text(json.dumps(c, indent=1))
    if not POSITIONS.exists():
        save_positions([])
    out({"status": "created", "home": str(HOME), "config": c})


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
    # week P&L = equity now vs the last snapshot before this week (or capital)
    ws = week_start(dt.date.today()).isoformat()
    before = [r for r in rows(DAILY) if r["date"] < ws]
    week_base = float(before[-1]["equity"]) if before else float(c["capital"])
    week_pnl = equity - week_base
    lim = c.get("limits", DEFAULT_CONFIG["limits"])
    weekly_block = week_pnl <= -week_base * lim["weekly_loss_pct"] / 100
    # halt = equity has fallen halt_drawdown_pct below its highest level (start capital or any daily snapshot)
    peak = max([float(c["capital"]), equity] + [float(r["equity"]) for r in rows(DAILY) if r.get("equity")])
    drawdown_pct = 100 * (equity / peak - 1)
    halt = drawdown_pct <= -lim.get("halt_drawdown_pct", 30.0)
    out({
        "mode": c["mode"], "version": c["version"], "capital": c["capital"],
        "equity": round(equity, 2), "cash": round(cash, 2), "invested": round(invested, 2),
        "realized_total": round(realized_total(), 2),
        "week_start": ws, "week_pnl": round(week_pnl, 2), "week_pnl_pct": round(100 * week_pnl / week_base, 2),
        "peak_equity": round(peak, 2), "drawdown_pct": round(drawdown_pct, 2),
        "open_positions": pos, "slots_free": c.get("strategy", {}).get("max_positions", 10) - len(pos),
        "new_entries_blocked": weekly_block or halt,
        "block_reasons": [r for r, f in (("weekly_loss_limit", weekly_block), ("drawdown_halt", halt)) if f],
        "prices_missing_for": missing,
    })


def cmd_decision(a):
    c = cfg()
    append(DECISIONS, DEC_COLS, {"date": a.date or today(), "time": dt.datetime.now().strftime("%H:%M"),
                                 "symbol": a.symbol, "decision": a.decision, "reason": a.reason,
                                 "version": c["version"], "mode": c["mode"]})
    out({"logged": a.symbol, "decision": a.decision})


def cmd_open(a):
    c = cfg()
    pos = positions()
    if any(p["symbol"] == a.symbol for p in pos):
        sys.exit(f"{a.symbol} is already an open bot position")
    cost = a.qty * a.price * (1 + COST_SIDE)
    pos.append({"symbol": a.symbol, "qty": a.qty, "entry_price": a.price, "entry_date": a.date or today(),
                "stop": a.stop, "initial_stop": a.stop, "target": a.target, "highest_close": a.price, "gtt_id": a.gtt_id,
                "order_ids": [x for x in [a.order_id] if x], "cost_basis": round(cost, 2),
                "rank": a.rank, "score": a.score, "mode": c["mode"], "version": c["version"]})
    save_positions(pos)
    append(DECISIONS, DEC_COLS, {"date": a.date or today(), "time": dt.datetime.now().strftime("%H:%M"),
                                 "symbol": a.symbol, "decision": "taken",
                                 "reason": f"filled {a.qty} @ {a.price}, stop {a.stop}",
                                 "version": c["version"], "mode": c["mode"]})
    out({"opened": a.symbol, "qty": a.qty, "price": a.price, "stop": a.stop, "target": a.target, "cost": round(cost, 2)})


def cmd_stop(a):
    pos = positions()
    for p in pos:
        if p["symbol"] == a.symbol:
            if a.stop < p["stop"]:
                sys.exit(f"refused: stops only move up ({p['stop']} -> {a.stop})")
            p["stop"] = a.stop
            if a.gtt_id:
                p["gtt_id"] = a.gtt_id
            if a.highest_close:
                p["highest_close"] = max(p.get("highest_close") or 0, a.highest_close)
            save_positions(pos)
            out({"symbol": a.symbol, "stop": a.stop})
            return
    sys.exit(f"{a.symbol} is not an open bot position")


def cmd_close(a):
    c = cfg()
    pos = positions()
    p = next((x for x in pos if x["symbol"] == a.symbol), None)
    if not p:
        sys.exit(f"{a.symbol} is not an open bot position")
    value = p["qty"] * a.price
    pnl = value - value * COST_SIDE - DP_CHARGE - p["cost_basis"]
    risk_ps = p["entry_price"] - p["initial_stop"]
    exit_date = a.date or today()
    row = {"symbol": p["symbol"], "mode": p.get("mode", c["mode"]), "version": p.get("version", c["version"]),
           "entry_date": p["entry_date"], "entry": p["entry_price"], "qty": p["qty"], "initial_stop": p["initial_stop"],
           "exit_date": exit_date, "exit": a.price, "exit_reason": a.reason, "pnl": round(pnl, 2),
           "ret_pct": round((a.price / p["entry_price"] - 1) * 100, 2),
           "r": round((a.price - p["entry_price"]) / risk_ps, 2) if risk_ps > 0 else None,
           "hold_days": (dt.date.fromisoformat(exit_date) - dt.date.fromisoformat(p["entry_date"])).days,
           "entry_rank": p.get("rank"), "entry_score": p.get("score"),
           "order_ids": ";".join(p.get("order_ids", []) + ([a.order_id] if a.order_id else []))}
    append(TRADES, TRADE_COLS, row)
    save_positions([x for x in pos if x["symbol"] != a.symbol])
    out({"closed": row})


def cmd_snapshot(a):
    c = cfg()
    prices = json.loads(a.prices) if a.prices else {}
    pos = positions()
    for p in pos:
        if p["symbol"] in prices:
            p["last_price"] = prices[p["symbol"]]
    save_positions(pos)
    cash, invested, equity, missing = equity_now(prices)
    d = a.date or today()
    existing = [r for r in rows(DAILY) if r["date"] != d]
    new = {"date": d, "equity": round(equity, 2), "cash": round(cash, 2), "invested": round(invested, 2),
           "open_positions": len(pos), "realized_total": round(realized_total(), 2),
           "mode": c["mode"], "version": c["version"]}
    J.mkdir(parents=True, exist_ok=True)
    with DAILY.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=DAILY_COLS)
        w.writeheader()
        w.writerows(existing + [new])
    out(dict(new, prices_missing_for=missing))


def cmd_weekly(a):
    c = cfg()
    if a.week:
        y, w = a.week.split("-W")
        ws = dt.date.fromisocalendar(int(y), int(w), 1)
    else:
        t = dt.date.today()
        ws = week_start(t) if t.weekday() < 5 else week_start(t)
    we = ws + dt.timedelta(days=6)
    daily = rows(DAILY)
    prev = [r for r in daily if r["date"] < ws.isoformat()]
    inweek = [r for r in daily if ws.isoformat() <= r["date"] <= we.isoformat()]
    start_eq = float(prev[-1]["equity"]) if prev else float(c["capital"])
    end_eq = float(inweek[-1]["equity"]) if inweek else start_eq
    closed = [t for t in rows(TRADES) if ws.isoformat() <= t["exit_date"] <= we.isoformat()]
    decs = [d for d in rows(DECISIONS) if ws.isoformat() <= d["date"] <= we.isoformat()]
    all_closed = rows(TRADES)
    wins = [t for t in all_closed if float(t["pnl"]) > 0]
    peak, mdd = float(c["capital"]), 0.0
    for r in daily:
        e = float(r["equity"])
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    res = {
        "week": f"{ws.isocalendar()[0]}-W{ws.isocalendar()[1]:02d}", "from": ws.isoformat(), "to": we.isoformat(),
        "mode": c["mode"], "version": c["version"], "capital": c["capital"],
        "week_start_equity": round(start_eq, 2), "week_end_equity": round(end_eq, 2),
        "week_pnl": round(end_eq - start_eq, 2), "week_pnl_pct": round(100 * (end_eq / start_eq - 1), 2),
        "since_start_pnl": round(end_eq - float(c["capital"]), 2),
        "since_start_pct": round(100 * (end_eq / float(c["capital"]) - 1), 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "closed_this_week": closed,
        "decisions_this_week": {k: sum(1 for d in decs if d["decision"] == k) for k in sorted({d["decision"] for d in decs})},
        "vetoes_this_week": [d for d in decs if d["decision"] == "vetoed"],
        "open_positions": positions(),
        "all_time": {"trades": len(all_closed), "win_rate_pct": round(100 * len(wins) / len(all_closed), 1) if all_closed else None,
                     "avg_r": round(sum(float(t["r"] or 0) for t in all_closed) / len(all_closed), 2) if all_closed else None,
                     "best": max(all_closed, key=lambda t: float(t["pnl"]), default=None),
                     "worst": min(all_closed, key=lambda t: float(t["pnl"]), default=None)},
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
                   choices=["taken", "vetoed", "skipped_gap", "skipped_slots", "skipped_cash", "unfilled", "blocked_regime", "blocked_limits"])
    p.add_argument("--reason", required=True)
    p.add_argument("--date")
    p = sub.add_parser("open")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", type=int, required=True)
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--stop", type=float, required=True)
    p.add_argument("--target", type=float)
    p.add_argument("--gtt-id")
    p.add_argument("--order-id")
    p.add_argument("--rank", type=int)
    p.add_argument("--score", type=float)
    p.add_argument("--date")
    p = sub.add_parser("stop")
    p.add_argument("--symbol", required=True)
    p.add_argument("--stop", type=float, required=True)
    p.add_argument("--gtt-id")
    p.add_argument("--highest-close", type=float)
    p = sub.add_parser("close")
    p.add_argument("--symbol", required=True)
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--order-id")
    p.add_argument("--date")
    p = sub.add_parser("snapshot")
    p.add_argument("--prices")
    p.add_argument("--date")
    p = sub.add_parser("weekly")
    p.add_argument("--week")
    a = ap.parse_args()
    {"init": cmd_init, "status": cmd_status, "decision": cmd_decision, "open": cmd_open, "stop": cmd_stop,
     "close": cmd_close, "snapshot": cmd_snapshot, "weekly": cmd_weekly}[a.cmd](a)


if __name__ == "__main__":
    main()
