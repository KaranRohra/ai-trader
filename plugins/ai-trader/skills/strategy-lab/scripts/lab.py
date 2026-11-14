#!/usr/bin/env python3
"""Self-improvement bookkeeping (Python standard library only).

Nothing here changes how the bot trades unless the user picks "Apply" for a suggestion.
Everything works on one strategy at a time (default: the active one; --strategy ID for another).

Commands (all print JSON)
  pending                                  suggestions waiting for the user's decision
  propose --type lesson|retire_lesson|parameter|execution|risk|rollback --title "..." --change '{...}' --evidence "..."
  resolve --id N --action apply|reject|later [--reason "..."]
  features [--min-n 15]                    shadow-book outcome stats by feature bucket (hypothesis source)
  veto-report                              did Claude's vetoes beat the signals it took?
  trial-log --scan-json PATH               record what the trial config would have signalled today
  trial-report                             forward results: trial config vs current config signals
  rollback-check                           live drawdown vs the current version's backtest max drawdown
  bounds                                   which parameters the strategy lets the lab tune, and their ranges
  record-backtest --summary PATH           store a version's backtest max drawdown (used by rollback-check)
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scanner" / "scripts"))
import engine  # noqa: E402

HOME = engine.HOME
L = HOME / "learning"
PENDING = L / "pending.json"
LESSONS = L / "lessons.md"
CHANGELOG = L / "changelog.md"
SHADOW = L / "shadow_book.csv"
TRIAL = L / "trial"
CONFIG = HOME / "config.json"
SCAN_DIR = Path(__file__).resolve().parents[2] / "scanner" / "scripts"

# Parameters: each strategy file lists what the lab may tune ("tunable") and the ranges.
# Risk settings: the lab may only SUGGEST new answers (--type risk); they apply when the user picks Apply.
EXECUTION_BOUNDS = {"limit_buffer_pct": (0.25, 2.0), "max_gap_pct": (1.0, 5.0)}
NEVER = ["capital", "mode", "bot-only positions (never touch holdings outside the bot)"]
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
    major, _, minor = version.lstrip("v").partition(".")
    return f"v{major}.{int(minor or 0) + 1}"


def strat(sid=None):
    try:
        return engine.load(sid)
    except engine.StrategyError as e:
        sys.exit(f"strategy problem: {e}")


def configs_dir(sid):
    return L / "configs" / sid


def archive(data, script):
    """Keep every version (settings + script) for rollback."""
    p = configs_dir(data["id"]) / f"{data['version']}.json"
    rec = jload(p, {})
    rec.update(strategy_file=data, script=script)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=1, ensure_ascii=False))


def save_new_version(st, data, why, script=None):
    """Write a changed strategy as a new version (old one archived for rollback)."""
    script = st.script if script is None else script
    archive(st.data, st.script)
    data["version"] = bump(st.version)
    data["updated"] = today()
    engine.save_user_strategy(data, script)  # validates
    archive(data, script)
    changelog(f"- {today()} {st.id} {st.version} -> {data['version']}: {why}")
    return data["version"]


def changelog(line):
    L.mkdir(parents=True, exist_ok=True)
    new = not CHANGELOG.exists()
    with CHANGELOG.open("a") as fh:
        if new:
            fh.write("# Bot changelog\n\nEvery change here was approved by the user.\n\n")
        fh.write(line + "\n")


def validate_change(kind, change, st):
    if kind == "risk":
        bad = [k for k in change if k not in engine.RISK_FIELDS]
        if bad:
            sys.exit(f"refused: {bad} are not risk settings. Risk settings: {sorted(engine.RISK_FIELDS)}")
        data = json.loads(json.dumps(st.data))
        data["risk"] = dict(data.get("risk") or {}, **change)
        try:
            engine.Strategy(data, None, st.script)
        except engine.StrategyError as e:
            sys.exit(f"refused: {e}")
        return
    bounds = st.tunable if kind == "parameter" else EXECUTION_BOUNDS
    for k, v in change.items():
        if k not in bounds:
            sys.exit(f"refused: '{k}' is not tunable for {st.id}. Tunable: {sorted(bounds)}")
        lo, hi = bounds[k]
        if not (lo <= float(v) <= hi):
            sys.exit(f"refused: {k}={v} outside allowed range {lo}..{hi}")


# ---------------------------------------------------------------- queue

def cmd_pending(a):
    its = [i for i in items() if i["status"] == "pending"]
    trial = [i for i in items() if i["status"] == "trial"]
    out({"waiting_for_user": its, "in_trial": [{"id": i["id"], "strategy": i.get("strategy", engine.LEGACY_ID),
                                                  "title": i["title"], "trial_start": i.get("trial_start"),
                                                  "ends": i.get("trial_end")} for i in trial]})


def cmd_propose(a):
    its = items()
    st = strat(a.strategy)
    change = json.loads(a.change) if a.change else {}
    if a.type in ("parameter", "execution", "risk"):
        validate_change(a.type, change, st)
    if a.type == "risk":
        change = {"from": {k: (st.data.get("risk") or {}).get(k) for k in change}, "to": change}
    for i in its:
        same = i["title"].strip().lower() == a.title.strip().lower() or (change and i.get("change") == change)
        if same and i["status"] in ("pending", "trial"):
            sys.exit(f"already queued as #{i['id']}")
        if same and i["status"] == "rejected" and not a.new_evidence:
            sys.exit(f"refused: the user rejected this as #{i['id']} ({i.get('reason')}); pass --new-evidence only if the evidence changed")
    if a.type == "lesson" and a.cases is not None and a.cases < MIN_LESSON_CASES:
        sys.exit(f"refused: a lesson needs at least {MIN_LESSON_CASES} supporting cases (got {a.cases})")
    if a.type == "parameter" and sum(1 for i in its if i["type"] == "parameter" and i.get("strategy", engine.LEGACY_ID) == st.id
                                     and i["created"][:7] == today()[:7]) >= 1:
        sys.exit("refused: at most one parameter proposal per strategy per month")
    item = {"id": max([i["id"] for i in its], default=0) + 1, "created": today(), "strategy": st.id,
            "strategy_version": st.version, "type": a.type, "title": a.title,
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
    if engine.config() is None:
        sys.exit("not set up")
    st = strat(item.get("strategy", engine.LEGACY_ID))
    if item["type"] == "lesson":
        new = not LESSONS.exists()
        with LESSONS.open("a") as fh:
            if new:
                fh.write("# Lessons (approved by the user)\n\nThe analyst reads these before every veto decision.\n\n")
            fh.write(f"## L{item['id']}: {item['title']}\n- Rule: {item['lesson']}\n- Evidence: {item['evidence']}\n"
                     f"- Learned from: {st.id} | Added: {today()} | status: active\n\n")
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
        trial = json.loads(json.dumps(st.data))
        trial["params"].update(item["change"])
        trial["version"] = bump(st.version) + "-trial"
        engine.Strategy(trial, None, st.script)
        (TRIAL / engine.SETTINGS_FILE).write_text(json.dumps(trial, indent=1, ensure_ascii=False))
        (TRIAL / engine.SCRIPT_FILE).write_text(st.script)  # scan with: --strategy ~/ai-trader/learning/trial
        for f in ("signals.csv",):
            if (TRIAL / f).exists():
                (TRIAL / f).unlink()
        item.update(status="trial", stage="go_live", trial_start=today(),
                    trial_end=(dt.date.today() + dt.timedelta(weeks=TRIAL_WEEKS)).isoformat())
        changelog(f"- {today()} TRIAL STARTED #{item['id']} {item['title']} {item['change']} (until {item['trial_end']})")
        result["note"] = f"paper trial runs alongside the live config for {TRIAL_WEEKS} weeks; you'll be asked again before it goes live"
    elif item["type"] in ("parameter", "execution", "risk", "rollback"):
        data, script = json.loads(json.dumps(st.data)), st.script
        if item["type"] == "rollback":
            rec = jload(configs_dir(st.id) / f"{item['change']['to_version']}.json", {})
            prev = rec.get("strategy_file")
            if not prev:
                sys.exit("previous version not found")
            data = json.loads(json.dumps(prev))
            script = rec.get("script") or st.script
            data["risk"] = dict(data.get("risk") or {}, confirmed=today())
        elif item["type"] == "parameter":
            data["params"].update(item["change"])
        elif item["type"] == "execution":
            data["execution"].update(item["change"])
        else:
            data["risk"] = dict(data.get("risk") or {}, **item["change"]["to"], confirmed=today())
        new_v = save_new_version(st, data, f"{item['title']} {item['change']} | evidence: {item['evidence']}", script)
        if (TRIAL / engine.SETTINGS_FILE).exists() and item["type"] == "parameter":
            (TRIAL / engine.SETTINGS_FILE).rename(TRIAL / f"done_{item['id']}_strategy.json")
        item["status"] = "applied"
        result["new_version"] = new_v
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


def shadow_rows(sid):
    return [r for r in rows(SHADOW) if (r.get("strategy") or engine.LEGACY_ID) == sid]


def cmd_features(a):
    st = strat(a.strategy)
    sh = shadow_rows(st.id)
    done = [r for r in sh if r.get("ret20")]
    report = {"strategy": st.id, "rows_total": len(sh), "rows_with_20d_outcome": len(done),
              "metric": "forward 20-session return %, from signal close"}
    # signals, and near misses that failed exactly one rule
    base = [r for r in done if r["passed"] == "True" or (r["failed"] and ";" not in r["failed"])]
    report["passed_all_rules"] = _summ([r for r in done if r["passed"] == "True"])
    report["near_misses_by_failed_rule"] = {}
    for rule in sorted({r["failed"] for r in base if r["failed"]}):
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
    sh = {(r["date"], r["symbol"]): r for r in rows(SHADOW)
          if not a.strategy or (r.get("strategy") or engine.LEGACY_ID) == a.strategy}

    def outcome(d):
        # the decision is made the morning after the signal date; match the latest shadow row before it
        cands = [(k, r) for k, r in sh.items() if k[1] == d["symbol"] and k[0] < d["date"]]
        return max(cands)[1] if cands else None

    groups = {"taken": [], "vetoed": []}
    for d in decs:
        if a.strategy and (d.get("strategy") or engine.LEGACY_ID) != a.strategy:
            continue
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
    import scan  # noqa: F401
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
    sid = item.get("strategy", engine.LEGACY_ID)
    base = [{"date": r["date"], "symbol": r["symbol"]} for r in shadow_rows(sid) if r["passed"] == "True" and r["date"] >= start]
    t_fwd, b_fwd = _forward(trial), _forward(base)
    finished = today() >= item["trial_end"]
    res = {"id": item["id"], "strategy": sid, "title": item["title"], "change": item["change"], "trial_start": start,
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
    st = strat(a.strategy)
    v = st.version
    bt = jload(configs_dir(st.id) / f"{v}.json", {}).get("backtest_max_drawdown_pct")
    daily = rows(HOME / "journal" / "daily.csv")
    vd = [float(r["equity"]) for r in daily if r.get("version") == v and (r.get("strategy") or engine.LEGACY_ID) == st.id]
    if not vd or bt is None:
        out({"strategy": st.id, "version": v, "check": "skipped",
             "why": "no backtest drawdown recorded for this version or no live data"})
        return
    peak, mdd = vd[0], 0.0
    for e in vd:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    breach = mdd * 100 < bt
    out({"strategy": st.id, "version": v, "live_max_drawdown_pct": round(mdd * 100, 2), "backtest_max_drawdown_pct": bt, "breach": breach,
         "action": "propose a rollback to the previous version (user must approve)" if breach else "none"})


def cmd_record_backtest(a):
    summ = json.loads(Path(a.summary).read_text())
    st = strat(summ.get("strategy") or a.strategy)
    v = a.version or summ.get("strategy_version") or st.version
    p = configs_dir(st.id) / f"{v}.json"
    rec = jload(p, {"strategy_file": st.data, "script": st.script})
    full = summ["full"]
    rec.update(backtest_max_drawdown_pct=full["max_drawdown_pct"], backtest_cagr_pct=full["cagr_pct"],
               backtest_period=f"{full['from']}..{full['to']}", backtest_summary=str(a.summary))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=1))
    out({"strategy": st.id, "version": v, "backtest_max_drawdown_pct": full["max_drawdown_pct"], "backtest_cagr_pct": full["cagr_pct"]})


def cmd_bounds(a):
    st = strat(a.strategy)
    out({"strategy": st.id, "version": st.version, "tunable_params": st.tunable, "current_params": st.data.get("params"),
         "execution": EXECUTION_BOUNDS,
         "risk": "may be SUGGESTED with --type risk (every risk setting); applies only if the user picks Apply",
         "never_changed_by_the_lab": NEVER, "trial_weeks": TRIAL_WEEKS,
         "min_lesson_cases": MIN_LESSON_CASES, "max_parameter_proposals_per_strategy_per_month": 1})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pending")
    p = sub.add_parser("propose")
    p.add_argument("--type", required=True, choices=["lesson", "retire_lesson", "parameter", "execution", "risk", "rollback"])
    p.add_argument("--strategy", help="default: the active strategy")
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
    p.add_argument("--strategy")
    p = sub.add_parser("veto-report")
    p.add_argument("--strategy")
    p = sub.add_parser("trial-log")
    p.add_argument("--scan-json", required=True)
    sub.add_parser("trial-report")
    p = sub.add_parser("rollback-check")
    p.add_argument("--strategy")
    p = sub.add_parser("bounds")
    p.add_argument("--strategy")
    p = sub.add_parser("record-backtest")
    p.add_argument("--summary", required=True)
    p.add_argument("--strategy")
    p.add_argument("--version")
    a = ap.parse_args()
    {"pending": cmd_pending, "propose": cmd_propose, "resolve": cmd_resolve, "features": cmd_features,
     "veto-report": cmd_veto_report, "trial-log": cmd_trial_log, "trial-report": cmd_trial_report,
     "rollback-check": cmd_rollback_check, "bounds": cmd_bounds, "record-backtest": cmd_record_backtest}[a.cmd](a)


if __name__ == "__main__":
    main()
