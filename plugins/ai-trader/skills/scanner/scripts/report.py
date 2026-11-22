#!/usr/bin/env python3
"""Backtest report for the AI Trader: one self-contained HTML file (Python standard library only).

  python3 report.py <backtest folder>      (re)build <folder>/report.html from summary.json, equity.csv, trades.csv

Tabs: Overview (equity, drawdown, years, halves, months) · Performance metrics (Sharpe, Sortino, Calmar, beta ...)
· Trade analysis (return distribution, streaks, time patterns, exits) · All trades (filters) · Strategy & method.
Older runs get the newer numbers worked out from their CSV files.

The page needs no internet: charts are drawn as SVG by a small inline script from the data embedded in the page.
"""
import csv
import html
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import metrics  # noqa: E402  (sibling file)

RISK_SOURCE_WORDS = {
    "user": "your confirmed risk rules",
    "suggested": "the strategy's suggested risk rules (you haven't set this strategy up yet)",
    "draft answers + suggestions": "your answers so far, not yet confirmed on the review screen (suggestions fill any unanswered rule)",
    "sizing answers only (limits not applied yet)": "your sizing answers only (the other limits aren't answered yet, so not applied)",
}
EXIT_WORDS = {"stop": "Stop-loss hit", "target": "Target hit", "trail": "Trailing stop hit", "sell_rule": "Sell rule",
              "time_stop": "Time exit", "delisted_or_suspended": "Delisted / suspended"}
SKIP_WORDS = {"gap": "opened too far above the signal", "unfilled": "limit price not reached",
              "no_slot": "all position slots full", "cash": "not enough cash", "weekly_limit": "weekly loss limit active",
              "halt": "drawdown halt active", "strategy_rule": "blocked by a portfolio rule"}


def words_module():
    """The plain-words helpers of the strategies skill (same wording as the review screen)."""
    path = HERE.parents[1] / "strategies" / "scripts" / "strategy.py"
    spec = importlib.util.spec_from_file_location("aitrader_strategy_words", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rupees(x):
    s = f"{abs(x):.0f}"
    head, tail = s[:-3], s[-3:]
    while len(head) > 2:
        tail = head[-2:] + "," + tail
        head = head[:-2]
    return ("-" if x < 0 else "") + "₹" + (head + "," + tail if head else tail)


def _monthly(equity):
    """{year: {month: return %}} from the daily equity rows."""
    out, prev_end, cur, last = {}, None, None, None
    for d, e, _ in equity:
        m = d[:7]
        if m != cur:
            if cur is not None:
                prev_end = last
            cur = m
            start = prev_end if prev_end is not None else e
        last = e
        y, mo = int(m[:4]), int(m[5:7])
        out.setdefault(y, {})[mo] = round((e / start - 1) * 100, 1)
    return out


def _rules(summary):
    data = summary.get("strategy_file") or {}
    risk = summary.get("risk_rules") or {}
    cap = (summary.get("full") or {}).get("start_equity") or 100000
    try:
        w = words_module()
        return {"buys": w.buys_words(data), "sells": w.sells_words(data), "orders": w.orders_words(data),
                "risk": [[a, b, c] for a, b, c in w.risk_words(risk, cap)] if risk else []}
    except Exception:  # never fail a report over wording
        return {"buys": [], "sells": [], "orders": [], "risk": []}


def _load(folder):
    folder = Path(folder)
    summary = json.loads((folder / "summary.json").read_text())
    equity = []
    with (folder / "equity.csv").open() as fh:
        for r in csv.DictReader(fh):
            b = r.get("benchmark", r.get("nifty500"))  # older runs named the column nifty500
            equity.append((r["date"], float(r["equity"]), float(b) if b not in (None, "", "None") else None))
    trades = []
    if (folder / "trades.csv").exists():
        with (folder / "trades.csv").open() as fh:
            for r in csv.DictReader(fh):
                num = lambda k: float(r[k]) if r.get(k) not in (None, "", "None") else None  # noqa: E731
                trades.append({"symbol": r["symbol"], "entry_date": r["entry_date"], "exit_date": r["exit_date"],
                               "entry": float(r["entry"]), "exit": float(r["exit"]), "qty": int(float(r["qty"])),
                               "pnl": float(r["pnl"]), "ret_pct": float(r["ret_pct"]), "r": num("r"),
                               "hold_days": int(float(r["hold_days"])), "exit_reason": r["exit_reason"],
                               "charges": num("charges")})
    return folder, summary, equity, trades


def _compat(summary):
    """Older runs always compared with NIFTY 500 and named their keys nifty500_*."""
    if "benchmark" not in summary:
        summary["benchmark"] = {"id": "nifty500", "name": "NIFTY 500"}

    def ren(d):
        for k in [k for k in d if k.startswith("nifty500_")]:
            d.setdefault("benchmark_" + k[9:], d.pop(k))
    for part in ("full", "in_sample", "out_of_sample"):
        if isinstance(summary.get(part), dict):
            ren(summary[part])
    for v in (summary.get("by_year") or {}).values():
        ren(v)
    perf = (summary.get("full") or {}).get("performance") or {}
    if "nifty500" in perf:
        perf["benchmark"] = perf.pop("nifty500")
    if "vs_nifty500" in perf:
        perf["vs_benchmark"] = perf.pop("vs_nifty500")


def _fill_missing(summary, equity, trades):
    """Reports of older runs: work out the newer numbers from equity.csv / trades.csv."""
    _compat(summary)
    f = summary.setdefault("full", {})
    dates = [d for d, _, _ in equity]
    eq = [e for _, e, _ in equity]
    bench = [b for _, _, b in equity]
    rf = summary.get("risk_free_pct", metrics.RISK_FREE_PCT)
    if "performance" not in f:
        f["performance"] = {"risk_free_pct": rf, "strategy": metrics.perf(dates, eq, rf),
                            "benchmark": metrics.perf(dates, bench, rf),
                            "vs_benchmark": metrics.relative(dates, eq, bench, rf)}
    if "trade_stats" not in f:
        f["trade_stats"] = metrics.trade_stats(trades, f.get("years"))
    if "analysis" not in summary:
        summary["analysis"] = metrics.analysis(dates, eq, trades)


def build(folder):
    folder, summary, equity, trades = _load(folder)
    _fill_missing(summary, equity, trades)
    f = summary.get("full") or {}
    cap = f.get("start_equity") or (equity[0][1] if equity else 100000)
    i0 = next((i for i, (_, _, b) in enumerate(equity) if b), None)
    b0 = equity[i0][2] if i0 is not None else None
    e0 = (cap if i0 == 0 else equity[i0][1]) if i0 is not None else cap   # a later-starting index starts at the strategy's value
    bm = summary.get("benchmark") or {}
    bn = bm.get("name")
    payload = {
        "dates": [d for d, _, _ in equity],
        "eq": [round(e) for _, e, _ in equity],
        "bench": [round(b / b0 * e0) if b and b0 else None for _, _, b in equity],
        "bname": bn,
        "years": summary.get("by_year") or {},
        "monthly": _monthly(equity),
        "trades": [[t["symbol"], t["entry_date"], t["exit_date"], t["entry"], t["exit"], t["qty"], t["pnl"],
                    t["ret_pct"], t["r"], t["hold_days"], EXIT_WORDS.get(t["exit_reason"], t["exit_reason"].replace("_", " ")),
                    t["charges"]] for t in trades],
        "analysis": summary.get("analysis") or {},
    }
    page = TEMPLATE
    title = f"{summary.get('strategy_name', summary.get('strategy'))} ({summary.get('strategy_version', '')})"
    rules = _rules(summary)
    fill = {
        "TITLE": html.escape(title),
        "PERIOD": f"{f.get('from', '')} to {f.get('to', '')} · {f.get('years', '')} years",
        "CAPITAL": rupees(cap),
        "RISK_SOURCE": html.escape(RISK_SOURCE_WORDS.get(summary.get("risk_used"), summary.get("risk_used") or "")),
        "COSTS": html.escape(summary.get("costs", "")),
        "UNIVERSE": html.escape(summary.get("universe", "")),
        "LIMITS": "simulated" if summary.get("limits_simulated") else "not simulated (turned off for this run)",
        "BN": html.escape(bn or ""),
        "BODYCLASS": "" if bn else "nobm",
        "BNOTE": (f"<p class='note'>{html.escape(bn)} values start {bm['from']}; before that there is nothing to compare with, "
                  f"and its numbers cover {bm['from']} onward.</p>") if bn and bm.get("partial") else
                 (f"<p class='note'>No {html.escape(bn)} values were found for this period.</p>" if bn and bm.get("missing") else ""),
        "KPIS": _kpis(f, bn),
        "SPLIT_TITLE": _split_title(summary),
        "SPLIT": _split_table(summary, bn),
        "PERF": _perf_table(f, bn),
        "RF": f.get("performance", {}).get("risk_free_pct", metrics.RISK_FREE_PCT),
        "EXITS": _exits(f),
        "SKIPS": _skips(f),
        "RULES": _rules_html(rules),
        "DATA": json.dumps(payload, separators=(",", ":")).replace("</", "<\\/"),
    }
    for k, v in fill.items():
        page = page.replace("{{" + k + "}}", str(v))
    out = folder / "report.html"
    out.write_text(page)
    return out


def _pct(v, sign=True):
    if v is None:
        return "–"
    return (f"{v:+.1f}%" if sign else f"{v:.1f}%").replace("-", "−")


def _num(v, d=2):
    if v is None:
        return "–"
    t = f"{v:.{d}f}"
    return t.lstrip("-") if float(t) == 0 else t.replace("-", "−")


def _kpis(f, bn=None):
    def tile(label, val, sub="", cls=""):
        return (f'<div class="kpi {cls}"><div class="kl">{label}</div><div class="kv">{val}</div>'
                f'<div class="ks">{sub}</div></div>')
    perf = f.get("performance") or {}
    ps, pn = perf.get("strategy") or {}, perf.get("benchmark") or {}
    vs = (lambda v: f"{bn}: {v}") if bn else (lambda v: "")  # noqa: E731
    ts = f.get("trade_stats") or {}
    exp = ts.get("expectancy")
    return "".join([
        tile("Final value", rupees(f.get("end_equity", 0)), f"from {rupees(f.get('start_equity', 0))} · {_pct(f.get('total_return_pct'))}"),
        tile("CAGR (per year)", _pct(f.get("cagr_pct"), False), vs(_pct(f.get('benchmark_cagr_pct'), False))),
        tile("Max drawdown", _pct(f.get("max_drawdown_pct"), False), vs(_pct(f.get('benchmark_max_drawdown_pct'), False)), "neg"),
        tile("Sharpe ratio", _num(ps.get("sharpe")), vs(_num(pn.get('sharpe')))),
        tile("Sortino ratio", _num(ps.get("sortino")), vs(_num(pn.get('sortino')))),
        tile("Trades", f.get("trades", 0), f"won {_pct(f.get('win_rate_pct'), False)} · avg hold {f.get('avg_hold_days') or '–'} days"),
        tile("Profit factor", _num(ts.get("profit_factor")), f"avg {rupees(exp) if exp is not None else '–'} a trade"),
        tile("Worst month", _pct(f.get("worst_month_pct")), f"{f.get('worst_month') or '–'}", "neg"),
    ])


def _split_title(s):
    if s.get("split_date"):
        return "First half vs second half"
    if s.get("in_sample"):
        return f"Before vs after {s['out_of_sample'].get('from', '')[:7]}"
    return "First half vs second half"


def _split_table(s, bn=None):
    rows = [("Whole period", s.get("full"))]
    if s.get("in_sample"):
        rows.append(("First half" if s.get("split_date") else "Before", s["in_sample"]))
    if s.get("out_of_sample"):
        rows.append(("Second half" if s.get("split_date") else "After", s["out_of_sample"]))
    body = "".join(
        f"<tr><td>{n}</td><td>{x.get('from', '')[:7]} → {x.get('to', '')[:7]}</td><td>{_pct(x.get('cagr_pct'), False)}</td>"
        f"<td>{_pct(x.get('max_drawdown_pct'), False)}</td><td>{_num(x.get('sharpe'))}</td><td>{x.get('trades', 0)}</td>"
        f"<td>{_pct(x.get('win_rate_pct'), False)}</td><td>{_num(x.get('profit_factor'))}</td>"
        + (f"<td>{_pct(x.get('benchmark_cagr_pct'), False)}</td><td>{_pct(x.get('benchmark_max_drawdown_pct'), False)}</td>"
           f"<td>{_num(x.get('benchmark_sharpe'))}</td>" if bn else "") + "</tr>"
        for n, x in rows if x)
    note = "" if len(rows) > 1 else "<p class='note'>The period is too short to split.</p>"
    return ("<table><thead><tr><th>Period</th><th>Dates</th><th>CAGR</th><th>Max DD</th><th>Sharpe</th><th>Trades</th>"
            "<th>Won</th><th>Profit factor</th>"
            + (f"<th>{bn} CAGR</th><th>{bn} max DD</th><th>{bn} Sharpe</th>" if bn else "")
            + "</tr></thead><tbody>" + body + "</tbody></table>" + note)


def _days(v):
    return "–" if v is None else f"{v:,} days"


def _perf_table(f, bn=None):
    perf = f.get("performance") or {}
    s, n, v = perf.get("strategy") or {}, perf.get("benchmark") or {}, perf.get("vs_benchmark") or {}
    B = bn or "the index"
    t = f.get("trade_stats") or {}
    rf = perf.get("risk_free_pct", metrics.RISK_FREE_PCT)
    P = lambda x: _pct(x, False)  # noqa: E731
    S = lambda x: _pct(x, True)   # noqa: E731

    def dd_when(x):
        if not x.get("max_drawdown_peak"):
            return ""
        rec = f"recovered {x['max_drawdown_recovered']}" if x.get("max_drawdown_recovered") else "not recovered by the end"
        return f"<div class='mini'>{x['max_drawdown_peak']} → {x['max_drawdown_trough']} · {rec}</div>"

    def underwater(x):
        if not x.get("longest_underwater_from"):
            return "–"
        to = x.get("longest_underwater_to") or "end"
        return f"{_days(x.get('longest_underwater_days'))}<div class='mini'>{x['longest_underwater_from']} → {to}</div>"

    def rec(x):
        if x.get("max_drawdown_recovery_days") is not None:
            return _days(x["max_drawdown_recovery_days"])
        return "not recovered" if x.get("max_drawdown_peak") else "–"

    def trade(x):
        if not x:
            return "–"
        return f"{rupees(x['pnl'])} ({S(x['ret_pct'])})<div class='mini'>{html.escape(x['symbol'])} · sold {x['exit_date']}</div>"
    yrs = lambda x: f"{x.get('positive_years', 0)} of {x.get('years_counted', 0)}" if x else "–"  # noqa: E731
    money = lambda x: rupees(x) if x is not None else "–"  # noqa: E731
    sections = [
        ("Returns", [
            ("Total return", S(s.get("total_return_pct")), S(n.get("total_return_pct")), "Gain on the starting capital over the whole period."),
            ("CAGR", P(s.get("cagr_pct")), P(n.get("cagr_pct")), "Compound growth per year."),
            ("Best year / worst year", f"{S(s.get('best_year_pct'))} / {S(s.get('worst_year_pct'))}",
             f"{S(n.get('best_year_pct'))} / {S(n.get('worst_year_pct'))}", "Calendar years (the first and last can be partial)."),
            ("Years with a gain", yrs(s), yrs(n), "Calendar years that ended above where they started."),
            ("Best month / worst month", f"{S(s.get('best_month_pct'))} / {S(s.get('worst_month_pct'))}",
             f"{S(n.get('best_month_pct'))} / {S(n.get('worst_month_pct'))}", ""),
            ("Average month", S(s.get("avg_month_pct")), S(n.get("avg_month_pct")), "Simple average of all monthly returns."),
            ("Months with a gain", P(s.get("positive_months_pct")), P(n.get("positive_months_pct")), ""),
            ("Best day / worst day", f"{S(s.get('best_day_pct'))} / {S(s.get('worst_day_pct'))}",
             f"{S(n.get('best_day_pct'))} / {S(n.get('worst_day_pct'))}", ""),
            ("Days with a gain", P(s.get("positive_days_pct")), P(n.get("positive_days_pct")), "Days the value closed higher than the day before."),
        ]),
        ("Risk", [
            ("Volatility (per year)", P(s.get("volatility_pct")), P(n.get("volatility_pct")), "How much daily value swings, scaled to a year (standard deviation)."),
            ("Downside volatility", P(s.get("downside_volatility_pct")), P(n.get("downside_volatility_pct")), "Volatility counting only days below the risk-free return."),
            ("Max drawdown", P(s.get("max_drawdown_pct")) + dd_when(s), P(n.get("max_drawdown_pct")) + dd_when(n), "Biggest fall from a peak, peak → lowest point."),
            ("Recovery from max drawdown", rec(s), rec(n), "Calendar days from the lowest point back to the old peak."),
            ("Longest time below a peak", underwater(s), underwater(n), "Longest stretch before a new high was made."),
            ("Drawdowns deeper than 10%", s.get("drawdowns_over_10pct", "–"), n.get("drawdowns_over_10pct", "–"), "Separate falls of 10% or more from a peak."),
            ("Ulcer index", _num(s.get("ulcer_index")), _num(n.get("ulcer_index")), "Depth and length of drawdowns together (root-mean-square of the daily drawdown %)."),
            ("1-day value at risk (95%)", S(s.get("var_95_daily_pct")), S(n.get("var_95_daily_pct")), "On 1 day in 20 the value fell by this much or more."),
            ("Worst week", S(f.get("worst_week_pct")) + (f"<div class='mini'>week ending {f['worst_week_ending']}</div>" if f.get("worst_week_ending") else ""), "–", ""),
        ]),
        ("Risk-adjusted return", [
            ("Sharpe ratio", _num(s.get("sharpe")), _num(n.get("sharpe")), f"Yearly return above a {rf:g}% risk-free rate, divided by volatility."),
            ("Sortino ratio", _num(s.get("sortino")), _num(n.get("sortino")), f"Like Sharpe, but divides by downside volatility only ({rf:g}% risk-free rate)."),
            ("Calmar ratio", _num(s.get("calmar")), _num(n.get("calmar")), "CAGR divided by the max drawdown."),
            ("Omega ratio", _num(s.get("omega")), _num(n.get("omega")), "Sum of daily gains divided by sum of daily losses."),
        ]),
        (f"Compared with {B}", [
            ("Beta", _num(v.get("beta")), "1.00", f"How much the strategy moved per 1% move of {B}."),
            ("Alpha (per year)", S(v.get("alpha_pct")), "–", f"Yearly return not explained by beta to {B} (Jensen's alpha)."),
            ("Correlation", _num(v.get("correlation")), "1.00", f"How closely daily moves line up with {B} (−1 to 1)."),
            ("Up capture", P(v.get("up_capture_pct")), "100.0%", f"Average strategy move on {B}'s up days, as a share of the index move."),
            ("Down capture", P(v.get("down_capture_pct")), "100.0%", f"Same on {B}'s down days."),
            ("Tracking error", P(v.get("tracking_error_pct")), "–", f"Volatility of the daily difference from {B}."),
            ("Information ratio", _num(v.get("information_ratio")), "–", f"Average yearly difference from {B} divided by the tracking error."),
            (f"Days beating {B}", P(v.get("days_beating_index_pct")), "–", ""),
        ]) if bn else None,
        ("Trades", [
            ("Closed trades", f"{t.get('trades', 0)}<div class='mini'>{_num(t.get('trades_per_year'), 1)} a year</div>", "–", ""),
            ("Win rate", P(t.get("win_rate_pct")) + f"<div class='mini'>{t.get('winners', 0)} won · {t.get('losers', 0)} lost</div>", "–", "Trades that closed with a profit after costs."),
            ("Profit factor", _num(t.get("profit_factor")), "–", "Total profit of winners divided by total loss of losers."),
            ("Expectancy per trade", f"{money(t.get('expectancy'))} ({S(t.get('expectancy_pct'))})", "–", "Average result of one trade, in rupees and %."),
            ("Median trade", S(t.get("median_return_pct")), "–", "Half the trades did better than this, half worse."),
            ("Average win / average loss", f"{money(t.get('avg_win'))} / {money(t.get('avg_loss'))}<div class='mini'>{S(t.get('avg_win_pct'))} / {S(t.get('avg_loss_pct'))}</div>", "–", ""),
            ("Payoff ratio", _num(t.get("payoff_ratio")), "–", "Average win divided by average loss, in rupees."),
            ("Average R", _num(t.get("avg_r")), "–", "Average result in units of the risk taken at entry (entry − stop)."),
            ("Largest win", trade(t.get("largest_win")), "–", ""),
            ("Largest loss", trade(t.get("largest_loss")), "–", ""),
            ("Average holding", f"{_num(t.get('avg_hold_days'), 1)} days<div class='mini'>winners {_num(t.get('avg_hold_days_winners'), 1)} · losers {_num(t.get('avg_hold_days_losers'), 1)}</div>", "–", "Trading days from buy to sell."),
            ("Gross profit / gross loss", f"{money(t.get('gross_profit'))} / {money(t.get('gross_loss'))}", "–", "After costs."),
            ("Charges paid", money(t.get("total_charges")), "–", "STT, stamp duty, exchange, GST and DP charges on closed trades."),
            ("Capital invested", P(f.get("avg_capital_invested_pct")), "100.0%", "Average share of the money in stocks; the rest sat as cash."),
        ]),
    ]
    cols = 4 if bn else 3
    out = ["<table class='perf'><thead><tr><th>Metric</th><th>Strategy</th>" + (f"<th>{bn}</th>" if bn else "")
           + "<th>What it measures</th></tr></thead><tbody>"]
    for sec in sections:
        if not sec:
            continue
        head, rows = sec
        out.append(f"<tr class='sec'><td colspan='{cols}'>{head}</td></tr>")
        out += [f"<tr><td>{a}</td><td class='v'>{b}</td>" + (f"<td class='v m'>{c}</td>" if bn else "")
                + f"<td class='d'>{d}</td></tr>" for a, b, c, d in rows]
    return "".join(out) + "</tbody></table>"


def _exits(f):
    ex = f.get("exit_reasons") or {}
    tot = sum(ex.values()) or 1
    rows = "".join(f"<tr><td>{EXIT_WORDS.get(k, k.replace('_', ' '))}</td><td>{v}</td><td>{100 * v / tot:.0f}%</td></tr>"
                   for k, v in sorted(ex.items(), key=lambda kv: -kv[1]))
    extra = f.get("open_positions_at_end")
    note = f"<p class='note'>{extra} position(s) still open at the end are valued at the last close.</p>" if extra else ""
    return "<h2>How trades ended</h2><table><thead><tr><th>Exit</th><th>Trades</th><th>Share</th></tr></thead><tbody>" + rows + "</tbody></table>" + note


def _skips(f):
    sk = {k: v for k, v in (f.get("signals_skipped") or {}).items() if v}
    lines = [f"<li>{v} signal(s): {SKIP_WORDS.get(k, k)}</li>" for k, v in sorted(sk.items(), key=lambda kv: -kv[1])]
    if f.get("regime_off_days_pct"):
        lines.append(f"<li>Market filter blocked new buys on {f['regime_off_days_pct']}% of days</li>")
    if f.get("weekly_limit_fired"):
        lines.append(f"<li>Weekly loss limit paused buying {f['weekly_limit_fired']} time(s)</li>")
    if f.get("halt_days"):
        lines.append(f"<li>Drawdown halt was on for {f['halt_days']} day(s)</li>")
    return "<ul>" + ("".join(lines) or "<li>No signals were skipped</li>") + "</ul>"


def _rules_html(r):
    def ul(items):
        return "<ul>" + "".join(f"<li>{html.escape(str(x))}</li>" for x in items) + "</ul>"
    risk = "".join(f"<tr><td>{html.escape(a)}</td><td><b>{html.escape(b)}</b></td><td>{html.escape(c)}</td></tr>" for a, b, c in r["risk"])
    return (f"<div class='cols'><div><h3>What it buys</h3>{ul(r['buys'])}<h3>Orders</h3>{ul(r['orders'])}</div>"
            f"<div><h3>When it sells</h3>{ul(r['sells'])}</div></div>"
            f"<h3>Risk rules used</h3><table class='risk'><tbody>{risk}</tbody></table>")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backtest · {{TITLE}}</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#14181f;--mute:#5b6573;--line:#e3e6eb;--grid:#eceef2;
--acc:#2f6fed;--bench:#8a939f;--pos:#13824a;--neg:#c9352b;--pos-bg:#d8f0e2;--neg-bg:#f8dcd9}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0d1117;--card:#151b23;--ink:#e6e9ee;--mute:#97a1ad;
--line:#262e39;--grid:#1f2630;--acc:#5b9bff;--bench:#7d8692;--pos:#3fbf7f;--neg:#ff6b5e;--pos-bg:#123524;--neg-bg:#3d1714}}
:root[data-theme="dark"]{--bg:#0d1117;--card:#151b23;--ink:#e6e9ee;--mute:#97a1ad;--line:#262e39;--grid:#1f2630;
--acc:#5b9bff;--bench:#7d8692;--pos:#3fbf7f;--neg:#ff6b5e;--pos-bg:#123524;--neg-bg:#3d1714}
*{box-sizing:border-box}
body.nobm .bm{display:none}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 16px 60px}
header{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:flex-start}
h1{font-size:26px;margin:0 0 4px}
h2{font-size:18px;margin:0 0 12px}
h3{font-size:14px;margin:14px 0 6px;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}
.sub{color:var(--mute);font-size:14px}
.tag{display:inline-block;font-size:12px;padding:2px 8px;border-radius:99px;background:var(--grid);color:var(--mute);margin-right:6px}
button,select,input{font:inherit;font-size:13px;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:5px 10px}
button{cursor:pointer}
button.on{border-color:var(--acc);color:var(--acc)}
.tabs{display:flex;gap:4px;margin-top:20px;border-bottom:1px solid var(--line);overflow-x:auto;scrollbar-width:none}
.tabs button{border:none;border-bottom:2px solid transparent;border-radius:0;background:none;padding:10px 14px;font-size:14px;color:var(--mute);white-space:nowrap}
.tabs button.on{color:var(--ink);border-bottom-color:var(--acc);font-weight:600}
.tab{display:none}.tab.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-top:16px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kl{font-size:12px;color:var(--mute)}
.kv{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums;margin:2px 0}
.kpi.neg .kv{color:var(--neg)}.kpi.pos .kv{color:var(--pos)}
.ks{font-size:12px;color:var(--mute)}
.head{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px}
.head h2{margin:0}
.legend{display:flex;gap:14px;font-size:13px;color:var(--mute);flex-wrap:wrap;align-items:center}
.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{border:none;border-radius:0;border-right:1px solid var(--line)}
.seg button:last-child{border-right:none}
.seg button.on{background:var(--grid);color:var(--ink);font-weight:600}
svg{display:block;width:100%;overflow:visible}
svg text{fill:var(--mute);font-size:11px}
svg text.mark{fill:var(--ink);paint-order:stroke;stroke:var(--card);stroke-width:4px;stroke-linejoin:round}
.tip{position:fixed;pointer-events:none;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 9px;
font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.15);display:none;z-index:5;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mute);font-weight:600;font-size:12px}
.wrap{overflow-x:auto}
.heat td,.heat th{text-align:center;padding:6px 4px;border:none}
.heat td{border-radius:4px}
.perf td.v{white-space:nowrap;font-weight:600}
.perf td.m{color:var(--mute);font-weight:500}
.perf td.d{color:var(--mute);font-size:12px;min-width:220px}
.perf tr.sec td{background:var(--grid);font-weight:650;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute);border-bottom:none}
.mini{font-size:11px;color:var(--mute);font-weight:400;white-space:nowrap}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:20px}
ul{margin:4px 0;padding-left:18px}
li{margin:3px 0}
.pos{color:var(--pos)}.neg{color:var(--neg)}
.note{color:var(--mute);font-size:13px}
.stats{display:grid;grid-template-columns:repeat(8,1fr);gap:8px;margin-top:12px}
.stat{border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.stat .kl{font-size:11px}.stat .kv{font-size:16px}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
#ttable th{cursor:pointer;user-select:none;white-space:nowrap}
#ttable td{white-space:nowrap}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
.grid2 .card{margin-top:0}
a{color:var(--acc)}
footer{color:var(--mute);font-size:12px;margin-top:22px}
@media (max-width:820px){.kpis{grid-template-columns:repeat(2,1fr)}.cols,.grid2,.tiles{grid-template-columns:1fr}.kv{font-size:19px}
.stats{grid-template-columns:repeat(4,1fr)}}
</style>
</head>
<body class="{{BODYCLASS}}">
<main>
<header>
 <div>
  <div class="sub">AI Trader · Backtest</div>
  <h1>{{TITLE}}</h1>
  <div class="sub">{{PERIOD}} · starting capital {{CAPITAL}}</div>
  <div style="margin-top:6px"><span class="tag">Risk: {{RISK_SOURCE}}</span></div>
 </div>
 <div><button id="theme" type="button">Light / dark</button></div>
</header>

<section class="kpis">{{KPIS}}</section>

<nav class="tabs" id="tabs">
 <button type="button" data-tab="overview">Overview</button>
 <button type="button" data-tab="performance">Performance metrics</button>
 <button type="button" data-tab="analysis">Trade analysis</button>
 <button type="button" data-tab="trades">All trades</button>
 <button type="button" data-tab="strategy">Strategy &amp; method</button>
</nav>

<!-- ============ OVERVIEW ============ -->
<div class="tab" id="tab-overview">
<section class="card">
 <div class="head"><h2>Equity<span class="bm"> vs {{BN}}</span></h2>
  <div class="legend"><span><i style="background:var(--acc)"></i>Strategy</span><span class="bm"><i style="background:var(--bench)"></i>{{BN}} (same starting capital)</span>
  <button id="logbtn" type="button">Log scale</button></div></div>
 <div id="eqchart"></div>{{BNOTE}}
</section>
<section class="card">
 <div class="head"><h2>Drawdown (fall from the previous peak)</h2>
  <div class="legend"><span><i style="background:var(--neg)"></i>Strategy</span><span class="bm"><i style="background:var(--bench)"></i>{{BN}}</span></div></div>
 <div id="ddchart"></div>
</section>
<section class="card">
  <div class="head"><h2>Year by year</h2>
   <div class="legend"><span><i style="background:var(--acc)"></i>Strategy</span><span class="bm"><i style="background:var(--bench)"></i>{{BN}}</span></div></div>
  <div id="yrchart"></div>
</section>
<section class="card"><h2>{{SPLIT_TITLE}}</h2>
 <p class="note" style="margin-top:0">The tested period cut into two halves. If a strategy only worked in one half, that shows here.</p>
 <div class="wrap">{{SPLIT}}</div></section>
<section class="card"><h2>Month by month (%)</h2><div class="wrap" id="heat"></div></section>
</div>

<!-- ============ PERFORMANCE ============ -->
<div class="tab" id="tab-performance">
<section class="card">
 <div class="head"><h2>Performance metrics</h2><span class="note">risk-free rate {{RF}}% a year · daily values · costs included</span></div>
 <p class="note" style="margin-top:0">Every number is worked out from the daily value of the strategy<span class="bm"> and of {{BN}} over the same days</span>, and from the closed trades.</p>
 <div class="wrap">{{PERF}}</div>
</section>
<section class="card">
 <div class="head"><h2>Rolling 1-year Sharpe ratio</h2>
  <div class="legend"><span><i style="background:var(--acc)"></i>Strategy</span><span class="bm"><i style="background:var(--bench)"></i>{{BN}}</span></div></div>
 <p class="note" style="margin-top:0">Sharpe ratio of the 252 trading days up to each date.</p>
 <div id="rollchart"></div>
</section>
</div>

<!-- ============ TRADE ANALYSIS ============ -->
<div class="tab" id="tab-analysis">
<section class="card">
 <div class="head"><h2>Trade returns distribution</h2>
  <div class="seg" id="distmode"><button type="button" data-v="return_pct" class="on">Return %</button><button type="button" data-v="r_multiple">R-multiple</button></div></div>
 <p class="note" style="margin-top:0">How many closed trades ended in each return band. Dashed line = average, dotted line = median.</p>
 <div id="distchart"></div>
 <div class="stats" id="diststats"></div>
</section>

<section class="card">
 <div class="head"><h2>Winning and losing streaks</h2><span class="note">trades in the order they were sold</span></div>
 <div class="tiles" id="streaktiles"></div>
 <h3>How often each streak length happened</h3>
 <div class="legend" style="margin-bottom:4px"><span><i style="background:var(--pos)"></i>Winning streaks</span><span><i style="background:var(--neg)"></i>Losing streaks</span></div>
 <div id="streakchart"></div>
 <div class="cols" style="margin-top:6px">
  <div><h3>Longest winning streaks</h3><div class="wrap" id="topwin"></div></div>
  <div><h3>Longest losing streaks</h3><div class="wrap" id="toploss"></div></div>
 </div>
</section>

<section class="card">
 <div class="head"><h2>Time patterns</h2>
  <div class="legend">
   <div class="seg" id="tpgroup">
    <button type="button" data-v="weekday" class="on">Day bought</button><button type="button" data-v="entry_month">Month bought</button>
    <button type="button" data-v="hold_period">Holding period</button><button type="button" data-v="entry_year">Year bought</button>
    <button type="button" data-v="calendar_month">Calendar month</button></div>
   <select id="tpmetric" aria-label="Measure"></select>
  </div></div>
 <p class="note" style="margin-top:0" id="tpnote"></p>
 <div id="tpchart"></div>
 <div class="wrap" id="tptable"></div>
</section>

<div class="grid2">
 <section class="card">{{EXITS}}</section>
 <section class="card"><h2>Signals not taken</h2>{{SKIPS}}</section>
</div>
</div>

<!-- ============ ALL TRADES ============ -->
<div class="tab" id="tab-trades">
<section class="card">
 <div class="head"><h2>All trades</h2><span class="note" id="tcount"></span></div>
 <div class="filters">
  <div class="seg" id="tside"><button type="button" data-v="all" class="on">All</button><button type="button" data-v="win">Winners</button><button type="button" data-v="loss">Losers</button></div>
  <select id="texit" aria-label="Exit"><option value="">Every exit</option></select>
  <select id="tyear" aria-label="Year"><option value="">Every year</option></select>
  <input id="tsearch" type="search" placeholder="Search stock" aria-label="Search stock" style="width:150px">
  <a href="trades.csv" download class="note">Download trades.csv</a>
 </div>
 <div class="wrap"><table id="ttable"><thead><tr>
  <th data-k="0">Stock</th><th data-k="1">Bought</th><th data-k="2">Sold</th><th data-k="3">Buy ₹</th><th data-k="4">Sell ₹</th>
  <th data-k="5">Qty</th><th data-k="6">P&amp;L ₹</th><th data-k="7">Return</th><th data-k="8">R</th><th data-k="9">Days</th><th data-k="11">Charges ₹</th><th data-k="10">Exit</th>
 </tr></thead><tbody></tbody><tfoot></tfoot></table></div>
</section>
</div>

<!-- ============ STRATEGY & METHOD ============ -->
<div class="tab" id="tab-strategy">
<section class="card"><h2>The strategy that was tested</h2>{{RULES}}</section>
<section class="card"><h2>How this backtest works</h2>
<ul>
 <li>Free official NSE end-of-day data. Prices adjusted for splits and bonuses.</li>
 <li>Stocks: {{UNIVERSE}}. NSE doesn't publish past index lists as data, so each month the most traded stocks stand in for the index. Delisted stocks are included.</li>
 <li>Signals use the day's close; the LIMIT buy is placed the next morning, exactly like the live bot. Trades skip if the stock opens too far up.</li>
 <li>Stops and targets act like stop / target orders waiting at the broker (GTT): they fill at the level, or at the open if the price gapped past it.</li>
 <li>Costs included: {{COSTS}}. Weekly loss limit and drawdown halt: {{LIMITS}}.</li>
 <li>Sharpe, Sortino and alpha use a {{RF}}% yearly risk-free rate and daily values (252 trading days a year).</li>
</ul>
<h3>Not modelled</h3>
<ul>
 <li>Slippage beyond the limit price, partial fills, and the effect of your own orders on the price.</li>
 <li>The real past index lists (a traded-value proxy is used).</li>
 <li>Your other holdings (the live bot never buys a stock you already hold outside it).</li>
 <li>Taxes on profits.</li>
</ul>
<p class="note">A backtest is history, not a forecast. This is not investment advice.</p>
</section>
</div>
<footer>Made by the AI Trader plugin · all data and charts are inside this file</footer>
</main>
<div class="tip" id="tip"></div>
<script>
const D={{DATA}};
const A=D.analysis||{};
const BM=s=>D.bname?[s]:[];   // the benchmark series, only when the strategy compares with an index
const $=s=>document.querySelector(s);
const NS="http://www.w3.org/2000/svg";
const inr=v=>(v<0?"−":"")+"₹"+Math.abs(Math.round(v)).toLocaleString("en-IN");
const pct=v=>v==null?"–":(v>0?"+":v<0?"−":"")+Math.abs(v).toFixed(1)+"%";
const num=(v,d=2)=>v==null?"–":(+v.toFixed(d)<0?"−":"")+Math.abs(v).toFixed(d);
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function el(t,a,p){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e}
const tip=$("#tip");
function showTip(ev,html){tip.innerHTML=html;tip.style.display="block";
 const x=Math.min(ev.clientX+14,innerWidth-tip.offsetWidth-8);tip.style.left=x+"px";tip.style.top=(ev.clientY+14)+"px"}
function hideTip(){tip.style.display="none"}
let logScale=false;

function niceTicks(lo,hi,n){const span=hi-lo||1,step0=span/n,mag=Math.pow(10,Math.floor(Math.log10(step0)));
 const step=[1,2,2.5,5,10].map(m=>m*mag).find(s=>s>=step0);const out=[];for(let v=Math.ceil(lo/step)*step;v<=hi+1e-9;v+=step)out.push(+v.toFixed(10));return out}

function lineChart(box,series,opts){
 box.innerHTML="";const W=box.clientWidth||900,H=opts.h||320,L=70,R=12,T=10,B=26;
 const svg=el("svg",{viewBox:`0 0 ${W} ${H}`,height:H},box);const n=D.dates.length;
 let vals=[];series.forEach(s=>s.v.forEach(v=>{if(v!=null)vals.push(v)}));
 if(!vals.length)return;
 let lo=Math.min(...vals),hi=Math.max(...vals);if(opts.zero){hi=Math.max(hi,0);lo=Math.min(lo,0)}
 const useLog=opts.log&&lo>0;const f=useLog?Math.log:(v=>v);
 const flo=f(lo),fhi=f(hi),pad=(fhi-flo)*0.04;
 const y=v=>T+(H-T-B)*(1-(f(v)-flo+pad)/(fhi-flo+2*pad));const x=i=>L+(W-L-R)*i/Math.max(n-1,1);
 let ticks=useLog?(()=>{const t=[];let m=Math.pow(10,Math.floor(Math.log10(lo)));for(let v=m;v<=hi*1.01;v*=10)[1,2,5].forEach(k=>{const z=v*k;if(z>=lo*0.97&&z<=hi*1.03)t.push(z)});return t})():niceTicks(lo,hi,5);
 ticks.forEach(t=>{const yy=y(t);el("line",{x1:L,x2:W-R,y1:yy,y2:yy,stroke:css("--grid")},svg);
  const tx=el("text",{x:L-8,y:yy+4,"text-anchor":"end"},svg);tx.textContent=opts.fmt(t)});
 if(opts.zero&&!useLog)el("line",{x1:L,x2:W-R,y1:y(0),y2:y(0),stroke:css("--mute"),opacity:.6},svg);
 let lastY="",lastX=-1e9;D.dates.forEach((d,i)=>{const yr=d.slice(0,4);if(yr!==lastY){lastY=yr;if(x(i)-lastX>=40){lastX=x(i);const t=el("text",{x:x(i),y:H-6,"text-anchor":"middle"},svg);t.textContent=yr}}});
 series.forEach(s=>{let p="",dn=false;s.v.forEach((v,i)=>{if(v==null){dn=false;return}p+=(dn?"L":"M")+x(i).toFixed(1)+","+y(v).toFixed(1);dn=true});
  if(s.fill){el("path",{d:p+`L${x(n-1)},${y(0)}L${x(0)},${y(0)}Z`,fill:s.color,opacity:.16},svg)}
  el("path",{d:p,fill:"none",stroke:s.color,"stroke-width":s.w||2,"stroke-linejoin":"round"},svg)});
 const cross=el("line",{y1:T,y2:H-B,stroke:css("--mute"),"stroke-dasharray":"3 3",opacity:0},svg);
 const dots=series.map(s=>el("circle",{r:4,fill:s.color,opacity:0},svg));
 const hit=el("rect",{x:L,y:T,width:W-L-R,height:H-T-B,fill:"transparent"},svg);
 hit.addEventListener("mousemove",ev=>{const r=svg.getBoundingClientRect();const i=Math.max(0,Math.min(n-1,Math.round(((ev.clientX-r.left)/r.width*W-L)/(W-L-R)*(n-1))));
  const xx=x(i);cross.setAttribute("x1",xx);cross.setAttribute("x2",xx);cross.setAttribute("opacity",1);
  let h=`<b>${D.dates[i]}</b>`;series.forEach((s,j)=>{const v=s.v[i];if(v==null){dots[j].setAttribute("opacity",0);return}
   dots[j].setAttribute("cx",xx);dots[j].setAttribute("cy",y(v));dots[j].setAttribute("opacity",1);h+=`<br><span style="color:${s.color}">●</span> ${s.name}: ${opts.fmt(v)}`});showTip(ev,h)});
 hit.addEventListener("mouseleave",()=>{hideTip();cross.setAttribute("opacity",0);dots.forEach(d=>d.setAttribute("opacity",0))});
}

/* grouped bar chart: labels on x, one or more series; colorFn(v,series) picks each bar's colour */
function barChart(box,labels,series,opts){
 box.innerHTML="";const W=box.clientWidth||900,H=opts.h||260,L=opts.L||52,R=8,T=10,B=opts.B||28;
 const svg=el("svg",{viewBox:`0 0 ${W} ${H}`,height:H},box);
 const all=[0];series.forEach(s=>s.v.forEach(v=>{if(v!=null)all.push(v)}));
 const lo=Math.min(...all),hi=Math.max(...all),yv=v=>T+(H-T-B)*(1-(v-lo)/((hi-lo)||1));
 niceTicks(lo,hi,5).forEach(t=>{el("line",{x1:L,x2:W-R,y1:yv(t),y2:yv(t),stroke:css("--grid")},svg);const tx=el("text",{x:L-6,y:yv(t)+4,"text-anchor":"end"},svg);tx.textContent=opts.fmt(t)});
 el("line",{x1:L,x2:W-R,y1:yv(0),y2:yv(0),stroke:css("--mute")},svg);
 const gw=(W-L-R)/Math.max(labels.length,1),ns=series.length,bw=Math.max(2,Math.min(42,gw*0.78/ns));
 const skip=Math.ceil(labels.length/Math.max(1,Math.floor((W-L-R)/(opts.labelW||46))));
 labels.forEach((lab,i)=>{const cx=L+gw*i+gw/2;
  series.forEach((s,j)=>{const v=s.v[i];if(v==null)return;const x0=cx-bw*ns/2+bw*j;
   const r=el("rect",{x:x0+0.5,y:Math.min(yv(v),yv(0)),width:bw-1,height:Math.max(1,Math.abs(yv(v)-yv(0))),fill:s.colorFn?s.colorFn(v,i):s.color,rx:2},svg);
   r.addEventListener("mousemove",ev=>showTip(ev,opts.tip?opts.tip(i,j,v):`<b>${lab}</b><br>${s.name}: ${opts.fmt(v)}`));r.addEventListener("mouseleave",hideTip)});
  if(i%skip===0){const t=el("text",{x:cx,y:H-8,"text-anchor":"middle"},svg);t.textContent=lab}});
 return {svg,L,W,R,T,B,H,gw,yv};
}

function drawdown(v){let peak=-Infinity;return v.map(x=>{if(x==null)return null;peak=Math.max(peak,x);return (x/peak-1)*100})}

function rollingSharpe(v,win){const rf=Math.pow(1+{{RF}}/100,1/252)-1;const r=v.map((x,i)=>i&&x!=null&&v[i-1]!=null?x/v[i-1]-1-rf:null);
 const out=new Array(v.length).fill(null);let s=0,s2=0,c=0;const q=[];
 for(let i=0;i<r.length;i++){const x=r[i];q.push(x);if(x!=null){s+=x;s2+=x*x;c++}
  if(q.length>win){const o=q.shift();if(o!=null){s-=o;s2-=o*o;c--}}
  if(q.length===win&&c>win*0.9){const m=s/c,sd=Math.sqrt(Math.max(0,(s2-c*m*m)/(c-1)));out[i]=sd>0?m/sd*Math.sqrt(252):null}}
 return out}

function yearChart(){
 const ys=Object.keys(D.years);
 barChart($("#yrchart"),ys.map(y=>ys.length>14?"'"+y.slice(2):y),
  [{name:"Strategy",v:ys.map(y=>D.years[y].return_pct),color:css("--acc")}].concat(D.bname?[{name:D.bname,v:ys.map(y=>D.years[y].benchmark_pct!==undefined?D.years[y].benchmark_pct:D.years[y].nifty500_pct),color:css("--bench")}]:[]),
  {fmt:v=>v+"%",h:280,tip:(i,j,v)=>`<b>${ys[i]}</b><br>${j?D.bname:"Strategy"}: ${pct(v)}`});
}

function heat(){
 const M=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
 let h="<table class='heat'><thead><tr><th></th>"+M.map(m=>`<th>${m}</th>`).join("")+"<th>Year</th></tr></thead><tbody>";
 Object.keys(D.monthly).forEach(y=>{h+=`<tr><th>${y}</th>`;for(let m=1;m<=12;m++){const v=D.monthly[y][m];
  if(v==null){h+="<td></td>";continue}const a=Math.min(1,Math.abs(v)/8);
  const bg=v>=0?`rgba(19,130,74,${0.12+0.55*a})`:`rgba(201,53,43,${0.12+0.55*a})`;h+=`<td style="background:${bg}">${v.toFixed(1)}</td>`}
  const yr=D.years[y];h+=`<td><b class="${yr&&yr.return_pct<0?"neg":"pos"}">${yr?pct(yr.return_pct):""}</b></td></tr>`});
 $("#heat").innerHTML=h+"</tbody></table>";
}

/* ---------- trade analysis ---------- */
let distMode="return_pct";
function distChart(){
 const d=(A.distribution||{})[distMode];const box=$("#distchart");
 if(!d||!d.bins||!d.bins.length){box.innerHTML="<p class='note'>No closed trades.</p>";$("#diststats").innerHTML="";return}
 const isR=distMode==="r_multiple",u=isR?"R":"%",f=v=>(v<0?"−":"")+Math.abs(+v.toFixed(2))+u,tot=d.bins.reduce((a,b)=>a+b.count,0);
 const lab=b=>b.open_low?"≤ "+f(b.to):b.open_high?"≥ "+f(b.from):f(b.from);
 const c=barChart(box,d.bins.map(lab),[{name:"Trades",v:d.bins.map(b=>b.count),colorFn:(v,i)=>{const b=d.bins[i];return b.to<=0?css("--neg"):b.from>=0?css("--pos"):css("--bench")}}],
  {fmt:v=>Math.round(v),h:280,L:40,labelW:52,tip:(i,j,v)=>{const b=d.bins[i];const r=b.open_low?`${f(b.to)} or lower`:b.open_high?`${f(b.from)} or higher`:`${f(b.from)} to ${f(b.to)}`;
   return `<b>${r}</b><br>${v} trade(s) · ${(100*v/tot).toFixed(1)}% of all`}});
 const s=d.stats,first=d.bins[0].from,w=d.width,n=d.bins.length;
 const xAt=v=>c.L+c.gw*Math.max(0,Math.min(n,(v-first)/w));
 [[s.mean,"6 4","Average"],[s.median,"2 3","Median"]].forEach(([v,da,nm])=>{if(v==null)return;const xx=xAt(v);
  el("line",{x1:xx,x2:xx,y1:c.T,y2:c.H-c.B,stroke:css("--ink"),"stroke-dasharray":da,opacity:.7},c.svg);
  const right=xx>c.W-c.R-130;const t=el("text",{x:right?xx-4:xx+4,y:c.T+10+(nm==="Median"?14:0),class:"mark","text-anchor":right?"end":"start"},c.svg);t.textContent=nm+" "+f(v)});
 const items=[["Average",f(s.mean)],["Median",f(s.median)],["Std deviation",f(s.std)],["Skew",num(s.skew)],["5th percentile",f(s.p5)],["95th percentile",f(s.p95)],["Best",f(s.max)],["Worst",f(s.min)]];
 $("#diststats").innerHTML=items.map(([a,b])=>`<div class="stat"><div class="kl">${a}</div><div class="kv">${b}</div></div>`).join("");
}
document.querySelectorAll("#distmode button").forEach(b=>b.addEventListener("click",()=>{distMode=b.dataset.v;document.querySelectorAll("#distmode button").forEach(x=>x.classList.toggle("on",x===b));distChart()}));

function streakTiles(){
 const S=(A.streaks||{}),t=S.trades||{},w=t.win||{},l=t.loss||{},cur=t.current,m=S.months||{};
 const top=x=>x&&x.top&&x.top[0];const tw=top(w),tl=top(l);
 const tile=(lab,val,sub,cls)=>`<div class="kpi ${cls||""}"><div class="kl">${lab}</div><div class="kv">${val}</div><div class="ks">${sub||""}</div></div>`;
 $("#streaktiles").innerHTML=[
  tile("Longest winning streak",(w.max||0)+" trades",tw?`${tw.from} → ${tw.to} · ${inr(tw.pnl)}`:"","pos"),
  tile("Longest losing streak",(l.max||0)+" trades",tl?`${tl.from} → ${tl.to} · ${inr(tl.pnl)}`:"","neg"),
  tile("Average streak",`${num(w.avg,1)} / ${num(l.avg,1)}`,"winning / losing trades in a row"),
  tile("Streak at the end",cur?`${cur.length} ${cur.kind==="win"?"win":"loss"}${cur.length>1?(cur.kind==="win"?"s":"es"):""}`:"–",cur?`since ${cur.from} · ${inr(cur.pnl)}`:""),
  tile("Most months up in a row",m.win?m.win.max+" months":"–",m.win&&m.win.from?`${m.win.from} → ${m.win.to} · ${pct(m.win.return_pct)}`:"","pos"),
  tile("Most months down in a row",m.loss?m.loss.max+" months":"–",m.loss&&m.loss.from?`${m.loss.from} → ${m.loss.to} · ${pct(m.loss.return_pct)}`:"","neg"),
 ].join("");
 const tbl=x=>!x||!x.top||!x.top.length?"<p class='note'>None.</p>":"<table><thead><tr><th>Trades</th><th>From</th><th>To</th><th>P&amp;L</th><th>Stocks</th></tr></thead><tbody>"+
  x.top.map(r=>`<tr><td><b>${r.length}</b></td><td style="white-space:nowrap">${r.from}</td><td style="white-space:nowrap">${r.to}</td><td style="white-space:nowrap" class="${r.pnl>=0?"pos":"neg"}">${inr(r.pnl)}</td><td class="note" style="white-space:normal">${r.symbols.map(esc).join(", ")}${r.length>r.symbols.length?" …":""}</td></tr>`).join("")+"</tbody></table>";
 $("#topwin").innerHTML=tbl(w);$("#toploss").innerHTML=tbl(l);
}
function streakChart(){
 const t=(A.streaks||{}).trades||{},w=(t.win||{}).lengths||{},l=(t.loss||{}).lengths||{};
 const mx=Math.max(1,...Object.keys(w).map(Number),...Object.keys(l).map(Number));const labs=[];for(let i=1;i<=mx;i++)labs.push(String(i));
 barChart($("#streakchart"),labs,[{name:"Winning streaks",v:labs.map(k=>w[k]||0),color:css("--pos")},{name:"Losing streaks",v:labs.map(k=>l[k]||0),color:css("--neg")}],
  {fmt:v=>Math.round(v),h:220,L:40,labelW:26,tip:(i,j,v)=>`<b>${labs[i]} ${j?"loss":"win"}${labs[i]==="1"?"":(j?"es":"s")} in a row</b><br>happened ${v} time(s)`});
}

const TP={
 weekday:{note:"Grouped by the weekday the stock was bought.",kind:"trades"},
 entry_month:{note:"Grouped by the month of the year the stock was bought (all years together).",kind:"trades"},
 hold_period:{note:"Grouped by how many trading days the position was held.",kind:"trades"},
 entry_year:{note:"Grouped by the year the stock was bought.",kind:"trades"},
 calendar_month:{note:"The strategy's whole-portfolio return in each calendar month, averaged over the years (from the daily value, not single trades).",kind:"months"}};
const TPM={trades:[["avg_return_pct","Average return per trade",v=>pct(v)],["win_rate_pct","Win rate",v=>v==null?"–":v.toFixed(0)+"%"],["pnl","Total P&L ₹",v=>inr(v)],["trades","Number of trades",v=>v]],
 months:[["avg_return_pct","Average month return",v=>pct(v)],["positive_pct","Share of years it was up",v=>v==null?"–":v.toFixed(0)+"%"],["best_pct","Best",v=>pct(v)],["worst_pct","Worst",v=>pct(v)]]};
let tpGroup="weekday",tpMetric={trades:"avg_return_pct",months:"avg_return_pct"};
function tpRender(){
 const cfg=TP[tpGroup],rows=((A.time_patterns||{})[tpGroup])||[],ms=TPM[cfg.kind];
 const sel=$("#tpmetric");sel.innerHTML=ms.map(([k,n])=>`<option value="${k}"${k===tpMetric[cfg.kind]?" selected":""}>${n}</option>`).join("");
 $("#tpnote").textContent=cfg.note;
 const m=ms.find(x=>x[0]===tpMetric[cfg.kind])||ms[0];
 const signed=m[0]!=="trades"&&m[0]!=="win_rate_pct"&&m[0]!=="positive_pct";
 barChart($("#tpchart"),rows.map(r=>r.label),[{name:m[1],v:rows.map(r=>r[m[0]]),colorFn:v=>signed?(v>=0?css("--pos"):css("--neg")):css("--acc")}],
  {fmt:v=>m[0]==="pnl"?(Math.abs(v)>=1e5?(v/1e5).toFixed(1)+"L":Math.round(v/1e3)+"k"):m[0]==="trades"?Math.round(v):(+v.toFixed(1))+"%",h:240,labelW:tpGroup==="hold_period"?78:40,
   tip:(i,j,v)=>`<b>${rows[i].label}</b><br>${m[1]}: ${m[2](v)}`+(cfg.kind==="trades"?`<br>${rows[i].trades} trade(s)`:`<br>${rows[i].years} year(s)`)});
 const head=cfg.kind==="trades"?["","Trades","Win rate","Avg return","Total P&L"]:["","Years","Average","Up in","Best","Worst"];
 $("#tptable").innerHTML="<table><thead><tr>"+head.map(h=>`<th>${h}</th>`).join("")+"</tr></thead><tbody>"+rows.map(r=>cfg.kind==="trades"?
  `<tr><td><b>${r.label}</b></td><td>${r.trades}</td><td>${r.win_rate_pct==null?"–":r.win_rate_pct.toFixed(0)+"%"}</td><td class="${(r.avg_return_pct||0)>=0?"pos":"neg"}">${pct(r.avg_return_pct)}</td><td class="${r.pnl>=0?"pos":"neg"}">${inr(r.pnl)}</td></tr>`:
  `<tr><td><b>${r.label}</b></td><td>${r.years}</td><td class="${(r.avg_return_pct||0)>=0?"pos":"neg"}">${pct(r.avg_return_pct)}</td><td>${r.positive_pct==null?"–":r.positive_pct.toFixed(0)+"% of years"}</td><td>${pct(r.best_pct)}</td><td>${pct(r.worst_pct)}</td></tr>`).join("")+"</tbody></table>";
}
document.querySelectorAll("#tpgroup button").forEach(b=>b.addEventListener("click",()=>{tpGroup=b.dataset.v;document.querySelectorAll("#tpgroup button").forEach(x=>x.classList.toggle("on",x===b));tpRender()}));
$("#tpmetric").addEventListener("change",e=>{tpMetric[TP[tpGroup].kind]=e.target.value;tpRender()});

/* ---------- all trades ---------- */
let sortK=2,sortDir=-1,tSide="all";
const exits=[...new Set(D.trades.map(t=>t[10]))].sort(),tyears=[...new Set(D.trades.map(t=>t[1].slice(0,4)))].sort();
$("#texit").innerHTML+=exits.map(e=>`<option>${esc(e)}</option>`).join("");
$("#tyear").innerHTML+=tyears.map(y=>`<option value="${y}">Bought in ${y}</option>`).join("");
function tradesTable(){
 const q=$("#tsearch").value.trim().toUpperCase(),ex=$("#texit").value,yr=$("#tyear").value;
 const rows=D.trades.filter(t=>(tSide==="all"||(tSide==="win"?t[6]>0:t[6]<=0))&&(!ex||t[10]===ex)&&(!yr||t[1].startsWith(yr))&&(!q||t[0].includes(q)))
  .sort((a,b)=>{const x=a[sortK],y=b[sortK];if(x==null)return 1;if(y==null)return -1;return (x>y?1:x<y?-1:0)*sortDir});
 $("#ttable tbody").innerHTML=rows.map(t=>`<tr><td><b>${esc(t[0])}</b></td><td>${t[1]}</td><td>${t[2]}</td><td>${t[3].toFixed(2)}</td><td>${t[4].toFixed(2)}</td><td>${t[5]}</td>`+
  `<td class="${t[6]>=0?"pos":"neg"}">${inr(t[6])}</td><td class="${t[7]>=0?"pos":"neg"}">${pct(t[7])}</td><td>${t[8]==null?"–":num(t[8])}</td><td>${t[9]}</td><td>${t[11]==null?"–":inr(t[11])}</td><td>${esc(t[10])}</td></tr>`).join("");
 const pnl=rows.reduce((a,t)=>a+t[6],0),w=rows.filter(t=>t[6]>0).length,ch=rows.reduce((a,t)=>a+(t[11]||0),0);
 $("#ttable tfoot").innerHTML=rows.length?`<tr><th colspan="6">${rows.length} trade(s) · ${(100*w/rows.length).toFixed(0)}% won</th><th class="${pnl>=0?"pos":"neg"}">${inr(pnl)}</th><th>${pct(rows.reduce((a,t)=>a+t[7],0)/rows.length)} avg</th><th></th><th></th><th>${inr(ch)}</th><th></th></tr>`:"";
 $("#tcount").textContent=`${rows.length} of ${D.trades.length} closed trades · click a column to sort`;
 document.querySelectorAll("#ttable thead th").forEach(th=>th.textContent=th.textContent.replace(/ [▲▼]$/,"")+(+th.dataset.k===sortK?(sortDir>0?" ▲":" ▼"):""));
}
document.querySelectorAll("#ttable thead th").forEach(th=>th.addEventListener("click",()=>{const k=+th.dataset.k;if(k===sortK)sortDir*=-1;else{sortK=k;sortDir=k<3||k===10?1:-1}tradesTable()}));
document.querySelectorAll("#tside button").forEach(b=>b.addEventListener("click",()=>{tSide=b.dataset.v;document.querySelectorAll("#tside button").forEach(x=>x.classList.toggle("on",x===b));tradesTable()}));
["#texit","#tyear"].forEach(s=>$(s).addEventListener("change",tradesTable));$("#tsearch").addEventListener("input",tradesTable);

/* ---------- tabs ---------- */
const DRAW={
 overview:()=>{lineChart($("#eqchart"),BM({name:D.bname,v:D.bench,color:css("--bench"),w:1.6}).concat([{name:"Strategy",v:D.eq,color:css("--acc"),w:2.2}]),{fmt:inr,log:logScale,h:340});
  lineChart($("#ddchart"),BM({name:D.bname,v:drawdown(D.bench),color:css("--bench"),w:1.4}).concat([{name:"Strategy",v:drawdown(D.eq),color:css("--neg"),w:1.8,fill:true}]),{fmt:v=>v.toFixed(0)+"%",zero:true,h:220});
  yearChart()},
 performance:()=>lineChart($("#rollchart"),BM({name:D.bname,v:rollingSharpe(D.bench,252),color:css("--bench"),w:1.4}).concat([{name:"Strategy",v:rollingSharpe(D.eq,252),color:css("--acc"),w:1.8}]),{fmt:v=>v.toFixed(1),zero:true,h:240}),
 analysis:()=>{distChart();streakChart();tpRender()},
 trades:()=>{}, strategy:()=>{}};
let cur="overview";
function show(t){if(!DRAW[t])t="overview";cur=t;document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("on",x.id==="tab-"+t));
 document.querySelectorAll("#tabs button").forEach(b=>b.classList.toggle("on",b.dataset.tab===t));DRAW[t]();
 if(location.hash!=="#"+t)history.replaceState(null,"","#"+t)}
document.querySelectorAll("#tabs button").forEach(b=>b.addEventListener("click",()=>show(b.dataset.tab)));
addEventListener("hashchange",()=>show(location.hash.slice(1)));
$("#logbtn").addEventListener("click",e=>{logScale=!logScale;e.target.classList.toggle("on",logScale);DRAW.overview()});
$("#theme").addEventListener("click",()=>{const r=document.documentElement;const dark=r.dataset.theme?r.dataset.theme==="dark":matchMedia("(prefers-color-scheme:dark)").matches;r.dataset.theme=dark?"light":"dark";DRAW[cur]()});
let rt;addEventListener("resize",()=>{clearTimeout(rt);rt=setTimeout(()=>DRAW[cur](),120)});
heat();streakTiles();tradesTable();show(location.hash.slice(1)||"overview");
</script>
</body>
</html>
"""

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(build(sys.argv[1]))
