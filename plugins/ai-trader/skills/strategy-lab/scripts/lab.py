#!/usr/bin/env python3
"""Self-improvement bookkeeping (Python standard library only).

Nothing here changes how the bot trades unless the user picks "Apply" for a suggestion.

Commands (all print JSON)
  pending                                  suggestions waiting for the user's decision
  propose --type lesson|retire_lesson|parameter|execution|rollback --title "..." --change '{...}' --evidence "..."
  resolve --id N --action apply|reject|later [--reason "..."]
  features [--min-n 15]                    shadow-book outcome stats by feature bucket (hypothesis source)
  veto-report                              did Claude's vetoes beat the signals it took?
  trial-log --scan-json PATH               record what the trial config would have signalled today
  trial-report                             forward results: trial config vs current config signals
  rollback-check                           live drawdown vs the current version's backtest max drawdown
  bounds                                   which parameters may change, and their allowed ranges
  record-backtest --summary PATH           store a version's backtest max drawdown (used by rollback-check)
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
from pathlib import Path

HOME = Path(os.environ.get("AI_TRADER_HOME", Path.home() / "ai-trader"))
L = HOME / "learning"
PENDING = L / "pending.json"
LESSONS = L / "lessons.md"
CHANGELOG = L / "changelog.md"
SHADOW = L / "shadow_book.csv"
TRIAL = L / "trial"
CONFIG = HOME / "config.json"
SCAN_DIR = Path(__file__).resolve().parents[2] / "momentum-scan" / "scripts"

# What self-improvement may tune, and the allowed range. Everything else is locked.
BOUNDS = {
    "rsi_min": (50, 70), "rsi_max": (70, 90), "vol_mult": (1.0, 3.0), "max_ext_pct": (5, 20),
    "rank_entry": (20, 100), "target_r": (2.0, 4.0), "stop_pct": (8.0, 16.0),
    "mom_w_short": (0.0, 1.0), "mom_w_long": (0.0, 1.0),
}
EXECUTION_BOUNDS = {"limit_buffer_pct": (0.25, 2.0), "max_gap_pct": (1.0, 5.0)}
LOCKED = ["risk_pct", "max_positions", "max_position_pct", "limits", "regime_sma", "breakout_lookback", "stop_mode",
          "min_price", "min_turnover_cr", "capital", "mode", "universe (NIFTY 500)", "CNC only"]
TRIAL_WEEKS = 4
MIN_LESSON_CASES = 8


def out(o):
    print(json.dumps(o, indent=1, default=str))


def jload(p, d):
    return json.loads(p.read_text()) if p.exists() else d


def rows(p):
    if not p.exists():
        return []
    with p.open() as fh:
        return list(csv.DictReader(fh))


def today():
    return dt.date.today().isoformat()


def items():
    return jload(PENDING, [])


def save(its):
    L.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps(its, indent=1))


def bump(version):
    major, minor = version.lstrip("v").split(".")
    return f"v{major}.{int(minor) + 1}"


def changelog(line):
    L.mkdir(parents=True, exist_ok=True)
    new = not CHANGELOG.exists()
    with CHANGELOG.open("a") as fh:
        if new:
            fh.write("# Bot changelog\n\nEvery change here was approved by the user.\n\n")
        fh.write(line + "\n")


def validate_change(kind, change):
    bounds = BOUNDS if kind == "parameter" else EXECUTION_BOUNDS
    for k, v in change.items():
        if k not in bounds:
            sys.exit(f"refused: '{k}' is locked or unknown. Tunable: {sorted(bounds)}")
        lo, hi = bounds[k]
        if not (lo <= float(v) <= hi):
            sys.exit(f"refused: {k}={v} outside allowed range {lo}..{hi}")


# ---------------------------------------------------------------- queue

def cmd_pending(a):
    its = [i for i in items() if i["status"] == "pending"]
    trial = [i for i in items() if i["status"] == "trial"]
    out({"waiting_for_user": its, "in_trial": [{"id": i["id"], "title": i["title"], "trial_start": i.get("trial_start"),
                                                  "ends": i.get("trial_end")} for i in trial]})


def cmd_propose(a):
    its = items()
    change = json.loads(a.change) if a.change else {}
    if a.type in ("parameter", "execution"):
        validate_change(a.type, change)
    for i in its:
        same = i["title"].strip().lower() == a.title.strip().lower() or (change and i.get("change") == change)
        if same and i["status"] in ("pending", "trial"):
            sys.exit(f"already queued as #{i['id']}")
        if same and i["status"] == "rejected" and not a.new_evidence:
            sys.exit(f"refused: the user rejected this as #{i['id']} ({i.get('reason')}); pass --new-evidence only if the evidence changed")
    if a.type == "lesson" and a.cases is not None and a.cases < MIN_LESSON_CASES:
        sys.exit(f"refused: a lesson needs at least {MIN_LESSON_CASES} supporting cases (got {a.cases})")
    if a.type == "parameter" and sum(1 for i in its if i["type"] == "parameter" and i["created"][:7] == today()[:7]) >= 1:
        sys.exit("refused: at most one parameter proposal per month")
    item = {"id": max([i["id"] for i in its], default=0) + 1, "created": today(), "type": a.type, "title": a.title,
            "change": change, "lesson": a.lesson, "evidence": a.evidence, "cases": a.cases,
            "stage": "start_trial" if a.type == "parameter" else "apply", "status": "pending", "history": []}
    its.append(item)
    save(its)
    out({"queued": item})


def cmd_resolve(a):
    its = items()
    item = next((i for i in its if i["id"] == a.id), None)
    if not item or item["status"] != "pending":
        sys.exit(f"#{a.id} is not waiting for a decision")
    item["history"].append({"date": today(), "action": a.action, "reason": a.reason})
    result = {"id": item["id"], "action": a.action}
    if a.action == "later":
        save(its)
        out(dict(result, note="kept for the next run; today runs on the current version"))
        return
    if a.action == "reject":
        item["status"], item["reason"] = "rejected", a.reason
        save(its)
        changelog(f"- {today()} REJECTED #{item['id']} {item['title']} ({a.reason or 'no reason'})")
        out(result)
        return
    cfg = jload(CONFIG, None)
    if cfg is None:
        sys.exit("not set up")
    if item["type"] == "lesson":
        new = not LESSONS.exists()
        with LESSONS.open("a") as fh:
            if new:
                fh.write("# Lessons (approved by the user)\n\nThe analyst reads these before every veto decision.\n\n")
            fh.write(f"## L{item['id']}: {item['title']}\n- Rule: {item['lesson']}\n- Evidence: {item['evidence']}\n"
                     f"- Added: {today()} | status: active\n\n")
        item["status"] = "applied"
        changelog(f"- {today()} LESSON L{item['id']}: {item['title']}")
    elif item["type"] == "retire_lesson":
        txt = LESSONS.read_text() if LESSONS.exists() else ""
        tag = item["change"].get("lesson_id", "")
        LESSONS.write_text(txt.replace(f"## {tag}:", f"## [RETIRED {today()}] {tag}:"))
        item["status"] = "applied"
        changelog(f"- {today()} RETIRED lesson {tag}: {item['title']}")
    elif item["type"] == "parameter" and item["stage"] == "start_trial":
        TRIAL.mkdir(parents=True, exist_ok=True)
        trial_cfg = json.loads(json.dumps(cfg))
        trial_cfg.setdefault("strategy", {}).update(item["change"])
        trial_cfg["version"] = bump(cfg["version"]) + "-trial"
        (TRIAL / "config.json").write_text(json.dumps(trial_cfg, indent=1))
        for f in ("signals.csv",):
            if (TRIAL / f).exists():
                (TRIAL / f).unlink()
        item.update(status="trial", stage="go_live", trial_start=today(),
                    trial_end=(dt.date.today() + dt.timedelta(weeks=TRIAL_WEEKS)).isoformat())
        changelog(f"- {today()} TRIAL STARTED #{item['id']} {item['title']} {item['change']} (until {item['trial_end']})")
        result["note"] = f"paper trial runs alongside the live config for {TRIAL_WEEKS} weeks; you'll be asked again before it goes live"
    elif item["type"] in ("parameter", "execution", "rollback"):
        old_v = cfg["version"]
        if item["type"] == "rollback":
            prev = jload(L / "configs" / f"{item['change']['to_version']}.json", None)
            if not prev:
                sys.exit("previous config not found")
            cfg["strategy"] = prev.get("strategy", {})
        else:
            cfg.setdefault("strategy", {}).update(item["change"])
        cfg["version"] = bump(old_v)
        cfg["updated"] = today()
        CONFIG.write_text(json.dumps(cfg, indent=1))
        (L / "configs").mkdir(parents=True, exist_ok=True)
        (L / "configs" / f"{cfg['version']}.json").write_text(json.dumps(cfg, indent=1))
        if (TRIAL / "config.json").exists() and item["type"] == "parameter":
            (TRIAL / "config.json").rename(TRIAL / f"done_{item['id']}_config.json")
        item["status"] = "applied"
        changelog(f"- {today()} {old_v} -> {cfg['version']}: {item['title']} {item['change']} | evidence: {item['evidence']}")
        result["new_version"] = cfg["version"]
    save(its)
    out(result)


# ---------------------------------------------------------------- analysis

def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _bucket(v, edges):
    if v is None:
        return None
    for lo, hi in zip(edges, edges[1:]):
        if lo <= v < hi:
            return f"{lo}-{hi}"
    return f">={edges[-1]}" if v >= edges[-1] else f"<{edges[0]}"


def _summ(rs, key="ret20"):
    vals = [_f(r[key]) for r in rs if _f(r.get(key)) is not None]
    if not vals:
        return {"n": 0}
    vals.sort()
    return {"n": len(vals), "avg": round(sum(vals) / len(vals), 2), "median": round(vals[len(vals) // 2], 2),
            "win_pct": round(100 * sum(v > 0 for v in vals) / len(vals), 1)}


def cmd_features(a):
    sh = rows(SHADOW)
    done = [r for r in sh if r.get("ret20")]
    report = {"rows_total": len(sh), "rows_with_20d_outcome": len(done), "metric": "forward 20-session return %, from signal close"}
    # stocks that broke out to a 52w high and passed the trend rule, failing at most one other rule
    base = [r for r in done if "no_52w_breakout" not in r["failed"] and "trend" not in r["failed"]]
    report["passed_all_rules"] = _summ([r for r in done if r["passed"] == "True"])
    report["near_misses_by_failed_rule"] = {}
    for rule in ("rank", "volume", "rsi", "extended", "liquidity"):
        report["near_misses_by_failed_rule"][rule] = _summ([r for r in base if r["failed"] == rule])
    buckets = {"rsi": [50, 60, 70, 80, 90], "vol_ratio": [1.0, 1.5, 2.0, 3.0, 5.0], "ext_pct": [0, 5, 10, 15, 20],
               "rank": [1, 26, 51, 101, 201], "breakout_pct": [0, 1, 3, 5, 10]}
    for feat, edges in buckets.items():
        groups = {}
        for r in base:
            b = _bucket(_f(r.get(feat)), edges)
            if b:
                groups.setdefault(b, []).append(r)
        report[f"by_{feat}"] = {b: _summ(g) for b, g in sorted(groups.items()) if len(g) >= a.min_n}
    secs = {}
    for r in base:
        secs.setdefault(r.get("sector") or "?", []).append(r)
    report["by_sector"] = {s: _summ(g) for s, g in sorted(secs.items()) if len(g) >= a.min_n}
    report["note"] = ("Buckets with fewer than --min-n cases are hidden. A pattern is only a hypothesis: "
                      "it must still pass the full-history backtest gate before it can be proposed.")
    out(report)


def cmd_veto_report(a):
    decs = rows(HOME / "journal" / "decisions.csv")
    sh = {(r["date"], r["symbol"]): r for r in rows(SHADOW)}

    def outcome(d):
        # the decision is made the morning after the signal date; match the latest shadow row before it
        cands = [(k, r) for k, r in sh.items() if k[1] == d["symbol"] and k[0] < d["date"]]
        return max(cands)[1] if cands else None

    groups = {"taken": [], "vetoed": []}
    for d in decs:
        if d["decision"] in groups:
            r = outcome(d)
            if r and r.get("ret20"):
                groups[d["decision"]].append(dict(r, reason=d["reason"]))
    taken, vetoed = _summ(groups["taken"]), _summ(groups["vetoed"])
    verdict = "not enough data yet (need >= 10 vetoed signals with 20-day outcomes)"
    if vetoed["n"] >= 10 and taken["n"] >= 10:
        verdict = ("vetoes are helping (vetoed signals did worse than taken ones)" if vetoed["avg"] < taken["avg"]
                   else "vetoes are NOT helping (vetoed signals did as well or better) - consider narrowing veto reasons")
    reasons = {}
    for r in groups["vetoed"]:
        reasons.setdefault(r["reason"].split(":")[0][:40], []).append(r)
    out({"taken": taken, "vetoed": vetoed, "verdict": verdict,
         "vetoed_by_reason": {k: _summ(v) for k, v in reasons.items()}})


def cmd_trial_log(a):
    data = json.loads(Path(a.scan_json).read_text())
    TRIAL.mkdir(parents=True, exist_ok=True)
    p = TRIAL / "signals.csv"
    new = not p.exists()
    sigs = data.get("signals") or data.get("signals_blocked_by_regime") or []
    with p.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["date", "symbol", "close", "rank", "score", "regime_on"])
        for s in sigs:
            w.writerow([data["as_of"], s["symbol"], s["close"], s["rank"], s["score"], data["regime"]["new_entries_allowed"]])
    out({"logged": len(sigs), "as_of": data["as_of"]})


def _forward(sig_rows):
    sys.path.insert(0, str(SCAN_DIR))
    import scan  # noqa
    if not sig_rows:
        return []
    first = min(dt.date.fromisoformat(r["date"]) for r in sig_rows)
    panel = scan.Panel(start=first, symbols={r["symbol"] for r in sig_rows})
    dpos = {d: i for i, d in enumerate(panel.dates)}
    res = []
    for r in sig_rows:
        s = panel.by_sym.get(r["symbol"])
        k = dpos.get(dt.date.fromisoformat(r["date"]))
        i = s.row(k) if s and k is not None else None
        if i is None:
            continue
        o = dict(r)
        for h in (10, 20):
            o[f"ret{h}"] = round((s.c[i + h] / s.c[i] - 1) * 100, 2) if i + h < len(s.c) else None
        res.append(o)
    return res


def cmd_trial_report(a):
    item = next((i for i in items() if i["status"] == "trial"), None)
    if not item:
        out({"trial": None})
        return
    trial = rows(TRIAL / "signals.csv")
    start = item["trial_start"]
    base = [{"date": r["date"], "symbol": r["symbol"]} for r in rows(SHADOW) if r["passed"] == "True" and r["date"] >= start]
    t_fwd, b_fwd = _forward(trial), _forward(base)
    finished = today() >= item["trial_end"]
    res = {"id": item["id"], "title": item["title"], "change": item["change"], "trial_start": start,
           "trial_end": item["trial_end"], "finished": finished,
           "trial_signals": _summ(t_fwd, "ret10"), "current_signals": _summ(b_fwd, "ret10"),
           "metric": "forward 10-session return % of every signal (no fills/slots simulated)"}
    if finished:
        for i in (its := items()):
            if i["id"] == item["id"]:
                i["status"] = "pending"
                i["evidence"] = f"{i['evidence']} | 4-week trial: trial {res['trial_signals']} vs current {res['current_signals']}"
        save(its)
        res["note"] = "trial finished - it is now waiting for the user's go-live decision"
    out(res)


def cmd_rollback_check(a):
    cfg = jload(CONFIG, {})
    v = cfg.get("version")
    bt = jload(L / "configs" / f"{v}.json", {}).get("backtest_max_drawdown_pct")
    daily = rows(HOME / "journal" / "daily.csv")
    vd = [float(r["equity"]) for r in daily if r.get("version") == v]
    if not vd or bt is None:
        out({"version": v, "check": "skipped", "why": "no backtest drawdown recorded for this version or no live data"})
        return
    peak, mdd = vd[0], 0.0
    for e in vd:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    breach = mdd * 100 < bt
    out({"version": v, "live_max_drawdown_pct": round(mdd * 100, 2), "backtest_max_drawdown_pct": bt, "breach": breach,
         "action": "propose a rollback to the previous version (user must approve)" if breach else "none"})


def cmd_record_backtest(a):
    summ = json.loads(Path(a.summary).read_text())
    cfg = jload(CONFIG, {})
    v = a.version or cfg.get("version")
    p = L / "configs" / f"{v}.json"
    rec = jload(p, dict(cfg))
    full = summ["full"]
    rec.update(backtest_max_drawdown_pct=full["max_drawdown_pct"], backtest_cagr_pct=full["cagr_pct"],
               backtest_period=f"{full['from']}..{full['to']}", backtest_summary=str(a.summary))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=1))
    out({"version": v, "backtest_max_drawdown_pct": full["max_drawdown_pct"], "backtest_cagr_pct": full["cagr_pct"]})


def cmd_bounds(a):
    out({"tunable": BOUNDS, "execution": EXECUTION_BOUNDS, "locked": LOCKED, "trial_weeks": TRIAL_WEEKS,
         "min_lesson_cases": MIN_LESSON_CASES, "max_parameter_proposals_per_month": 1})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pending")
    p = sub.add_parser("propose")
    p.add_argument("--type", required=True, choices=["lesson", "retire_lesson", "parameter", "execution", "rollback"])
    p.add_argument("--title", required=True)
    p.add_argument("--change", help="JSON, e.g. '{\"rsi_max\": 75}'")
    p.add_argument("--lesson", help="lesson text (for --type lesson)")
    p.add_argument("--evidence", required=True)
    p.add_argument("--cases", type=int, help="number of supporting cases")
    p.add_argument("--new-evidence", action="store_true")
    p = sub.add_parser("resolve")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--action", required=True, choices=["apply", "reject", "later"])
    p.add_argument("--reason", default="")
    p = sub.add_parser("features")
    p.add_argument("--min-n", type=int, default=15)
    sub.add_parser("veto-report")
    p = sub.add_parser("trial-log")
    p.add_argument("--scan-json", required=True)
    sub.add_parser("trial-report")
    sub.add_parser("rollback-check")
    sub.add_parser("bounds")
    p = sub.add_parser("record-backtest")
    p.add_argument("--summary", required=True)
    p.add_argument("--version")
    a = ap.parse_args()
    {"pending": cmd_pending, "propose": cmd_propose, "resolve": cmd_resolve, "features": cmd_features,
     "veto-report": cmd_veto_report, "trial-log": cmd_trial_log, "trial-report": cmd_trial_report,
     "rollback-check": cmd_rollback_check, "bounds": cmd_bounds, "record-backtest": cmd_record_backtest}[a.cmd](a)


if __name__ == "__main__":
    main()
