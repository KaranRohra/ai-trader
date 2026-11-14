"""Replays any strategy file over NSE history exactly like the live bot (standard library only).

- Universe: a point-in-time proxy for the strategy's index, rebuilt every month from 6-month average traded
  value of listed stocks, ETFs excluded (NIFTY 500 = top 500, NIFTY 50 = top 50, Midcap 150 = ranks 101-250, ...). Historical index
  membership isn't published as data; this is the closest proxy without hindsight. Delisted stocks are
  included, so there is no survivorship bias in prices.
- Signals at the close of day D; orders on day D+1 like the live bot: LIMIT at signal close + buffer,
  skipped if the open gaps more than max_gap_pct above the signal close.
- Stops and targets act like broker GTTs: if the day's low touches the stop, exit at the stop (or at the open
  if it gapped below). Trailing stops, exit rules and time exits update at each close and sell the next open.
- The weekly loss limit and drawdown halt from the risk block are simulated (turn off with --no-limits).
- Costs: Zerodha delivery (0 brokerage) ~0.12% per side for STT/stamp/exchange/GST + ₹16 DP charge per sell.
"""
import csv
import datetime as dt
import gzip
import json
import math
from collections import deque

import engine
import scan
from engine import Ctx, IndexData, ok
from scan import EQ_DIR, HOME, Panel, assign_ranks, log, update

COST_SIDE = 0.0012
DP_CHARGE = 16.0
DELIST_DAYS = 20
WEEKLY_LEVELS = [3, 4, 5, 6, 8, 10, 12, 15, 20]


def _candidates(start, end, top=700, window=126):
    """Pass 1: every ISIN/symbol that was ever in the top `top` by 6-month traded value at a month start."""
    files = sorted(f for f in EQ_DIR.glob("*.csv.gz")
                   if start.strftime("%Y%m%d") <= f.name[:8] <= end.strftime("%Y%m%d"))
    win, tot, last_seen, names = {}, {}, {}, {}
    out, month = set(), None
    for k, f in enumerate(files):
        m = f.name[:6]
        if m != month and k > window:
            month = m
            live = [(tot[key] / window, key) for key, q in win.items() if len(q) == window and k - last_seen[key] <= 5]
            live.sort(reverse=True)
            for _, key in live[:top]:
                out.add(key)
                out.update(names.get(key, ()))
        with gzip.open(f, "rt") as fh:
            rd = csv.reader(fh)
            next(rd)
            for sym, isin, ser, o, h, l, c, v, t in rd:
                key = isin or sym
                q = win.get(key)
                if q is None:
                    q = win[key] = deque(maxlen=window)
                    tot[key] = 0.0
                if len(q) == window:
                    tot[key] -= q[0]
                tv = scan._f(t)
                q.append(tv)
                tot[key] += tv
                last_seen[key] = k
                names.setdefault(key, set()).add(sym)
    return out


def _universe(panel, k, lo, hi, window=126):
    """Series ranked lo..hi by average traded value over the `window` sessions before date index k."""
    ranked = []
    for key, s in panel.series.items():
        if s.isin.startswith("INF"):
            continue  # ETFs / mutual-fund units (ISIN INF...) aren't in any stock index
        i = s.row(k - 1) if k > 0 else None
        if i is None or i < 252:
            continue
        ranked.append((sum(s.t[i - window + 1:i + 1]) / window, key))
    ranked.sort(reverse=True)
    return {key for _, key in ranked[lo - 1:hi]}


def _weeks(dates, equity):
    """Weekly returns (ISO weeks), each vs the previous week's last equity."""
    last = {}
    for d, e in zip(dates, equity):
        last[d.isocalendar()[:2]] = (d, e)
    out, prev = [], equity[0]
    for wk in sorted(last):
        d, e = last[wk]
        out.append((d, e / prev - 1))
        prev = e
    return out


def _stats(dates, equity, trades, bench=None):
    if len(equity) < 2:
        return {}
    yrs = (dates[-1] - dates[0]).days / 365.25
    peak, mdd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    cagr = (equity[-1] / equity[0]) ** (1 / yrs) - 1 if yrs > 0 else 0
    months, prev_m, prev_e = {}, None, equity[0]
    for d, e in zip(dates, equity):
        m = d.strftime("%Y-%m")
        if m != prev_m and prev_m is not None:
            prev_e = months[prev_m][1]
        months.setdefault(m, [prev_e, e])[1] = e
        prev_m = m
    mret = {m: v[1] / v[0] - 1 for m, v in months.items()}
    worst = min(mret.items(), key=lambda x: x[1]) if mret else (None, 0)
    wk = _weeks(dates, equity)
    worst_w = min(wk, key=lambda x: x[1]) if wk else (None, 0)
    wins = [t for t in trades if t["pnl"] > 0]
    rs = [t["r"] for t in trades if t["r"] is not None]
    out = {
        "from": dates[0].isoformat(), "to": dates[-1].isoformat(), "years": round(yrs, 2),
        "start_equity": round(equity[0]), "end_equity": round(equity[-1]),
        "total_return_pct": round((equity[-1] / equity[0] - 1) * 100, 1),
        "cagr_pct": round(cagr * 100, 2), "max_drawdown_pct": round(mdd * 100, 2),
        "cagr_to_maxdd": round(cagr / -mdd, 2) if mdd < 0 else None,
        "worst_month": worst[0], "worst_month_pct": round(worst[1] * 100, 2),
        "worst_week_ending": worst_w[0].isoformat() if worst_w[0] else None, "worst_week_pct": round(worst_w[1] * 100, 2),
        "positive_months_pct": round(100 * sum(1 for r in mret.values() if r > 0) / max(len(mret), 1), 1),
        "trades": len(trades), "win_rate_pct": round(100 * len(wins) / len(trades), 1) if trades else None,
        "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
        "avg_win_pct": round(sum(t["ret_pct"] for t in wins) / len(wins), 2) if wins else None,
        "avg_loss_pct": round(sum(t["ret_pct"] for t in trades if t["pnl"] <= 0) / max(len(trades) - len(wins), 1), 2) if trades else None,
        "avg_hold_days": round(sum(t["hold_days"] for t in trades) / len(trades), 1) if trades else None,
        "exit_reasons": {r: sum(1 for t in trades if t["exit_reason"] == r) for r in sorted({t["exit_reason"] for t in trades})},
    }
    if bench and bench[0] and bench[-1]:
        bpeak, bdd = bench[0], 0.0
        for b in bench:
            if b:
                bpeak = max(bpeak, b)
                bdd = min(bdd, b / bpeak - 1)
        out["nifty500_cagr_pct"] = round(((bench[-1] / bench[0]) ** (1 / yrs) - 1) * 100, 2) if yrs > 0 else None
        out["nifty500_max_drawdown_pct"] = round(bdd * 100, 2)
    return out


def suggest_risk(dates, equity, full):
    """Starting points for the user's risk answers, computed from this run. The user decides every one."""
    wk = _weeks(dates, equity)
    yrs = full["years"]
    table = {f"-{lvl}%": sum(1 for _, r in wk if r * 100 <= -lvl) for lvl in WEEKLY_LEVELS}
    allowed = max(1, round(yrs / 5))
    weekly = next((lvl for lvl in WEEKLY_LEVELS[2:] if table[f"-{lvl}%"] <= allowed), None)
    mdd = -full["max_drawdown_pct"]
    halt = 5 * math.ceil(1.5 * mdd / 5) if mdd > 0 else None
    return {
        "note": "Suggestions only, from this backtest. The user answers every risk question; null (no limit) is a valid answer.",
        "weekly_loss_limit_pct": {
            "suggested": weekly,
            "weeks_at_or_below_each_loss": table,
            "worst_week_pct": full.get("worst_week_pct"),
            "why": (f"the tightest level that a {yrs:.0f}-year backtest hit at most {allowed} time(s); tighter levels "
                    "pause the strategy more often, looser ones only act in extreme weeks"),
        },
        "halt_drawdown_pct": {
            "suggested": halt,
            "backtest_max_drawdown_pct": full["max_drawdown_pct"],
            "why": "1.5x the backtest's worst drawdown: past that, the strategy is behaving worse than it ever did in testing",
        },
        "sizing_and_positions": {
            "avg_capital_invested_pct": full.get("avg_capital_invested_pct"),
            "signals_skipped": full.get("signals_skipped"),
            "why": ("if a lot of capital sits idle or many signals are skipped for slots/cash, compare sizes with "
                    "--sweep risk.sizing.pct=... or --sweep risk.max_positions=..."),
        },
    }


def _position_info(panel, key, p, k, close, sectors):
    """What the strategy's hooks see about an open position (nothing after day k)."""
    s = panel.series[key]
    px = close if close is not None else s.c[s.row(p["last_k"])]
    return {"symbol": s.sym, "sector": sectors.get(s.sym, ""), "entry_date": panel.dates[p["k"]],
            "entry_price": p["entry"], "qty": p["qty"], "stop": p["stop"], "target": p["target"],
            "highest_close": p["hc"], "days_held": k - p["k"], "close": px,
            "unrealized_pct": round((px / p["entry"] - 1) * 100, 2)}


def simulate(panel, st, prepared, index, k0, k1, capital, universes, use_limits=True):
    results, scores = prepared
    risk = st.risk
    cash = capital
    pos, pend_in, pend_out = {}, [], {}
    trades, eq_dates, eq_vals, bench = [], [], [], []
    regime_off_days = invested_days = halt_days = weekly_fired = 0
    exposure_sum = 0.0
    skipped = {"gap": 0, "unfilled": 0, "no_slot": 0, "cash": 0, "weekly_limit": 0, "halt": 0, "strategy_rule": 0}
    sectors = engine.sector_map() if st.has_hooks else {}
    regime = Ctx(None, st.params, index).vec(st.regime) if st.regime is not None else None
    passing = {}   # key -> bytearray: 1 if the script says buy and the universe filters pass on that row
    for key, res in results.items():
        s = res.s
        liq = engine.sma(s.t, 20) if st.min_turnover_cr is not None else None
        passing[key] = bytearray(
            1 if (res.buy[i] and (res.score is None or ok(res.score[i]))
                  and (st.min_price is None or s.c[i] >= st.min_price)
                  and (liq is None or (ok(liq[i]) and liq[i] / 1e7 >= st.min_turnover_cr))) else 0
            for i in range(len(s.c)))
    only_up = risk.get("stops_only_up", True)
    weekly_lim = risk.get("weekly_loss_limit_pct") if use_limits else None
    halt_lim = risk.get("halt_drawdown_pct") if use_limits else None
    max_pos = risk.get("max_positions")
    lo, hi = engine.UNIVERSES[st.universe][1]
    month, members = None, set()
    peak = capital
    week_base, cur_week, blocked_week = capital, None, None

    def sell(key, p, px, k, why):
        nonlocal cash
        value = p["qty"] * px
        cost = value * COST_SIDE + DP_CHARGE
        cash += value - cost
        pnl = value - cost - p["cost_basis"]
        trades.append({
            "symbol": panel.series[key].sym, "entry_date": panel.dates[p["k"]].isoformat(),
            "exit_date": panel.dates[k].isoformat(), "entry": round(p["entry"], 2), "exit": round(px, 2),
            "qty": p["qty"], "pnl": round(pnl, 2), "ret_pct": round((px / p["entry"] - 1) * 100, 2),
            "r": round((px - p["entry"]) / p["risk_ps"], 2) if p["risk_ps"] and p["risk_ps"] > 0 else None,
            "hold_days": k - p["k"], "exit_reason": why,
            "stop_pct": round(p["risk_ps"] / p["entry"] * 100, 2) if p["risk_ps"] else None,
            "entry_rank": p["rank"], "entry_score": p["score"],
        })

    for k in range(k0, k1 + 1):
        # ---- morning: exits first, then entries (same order as the live bot)
        for key in list(pend_out):
            s, p = panel.series[key], pos[key]
            i = s.row(k)
            if i is None:
                if k - p["last_k"] > DELIST_DAYS:
                    sell(key, p, s.c[s.row(p["last_k"])], k, "delisted_or_suspended")
                    del pos[key], pend_out[key]
                continue
            sell(key, p, s.o[i], k, pend_out.pop(key))
            del pos[key]
        for e in pend_in:
            s = panel.series[e["key"]]
            i = s.row(k)
            if i is None:
                skipped["unfilled"] += 1
                continue
            o, sc = s.o[i], e["close"]
            if o > sc * (1 + st.max_gap_pct / 100):
                skipped["gap"] += 1
                continue
            limit = sc * (1 + st.limit_buffer_pct / 100)
            fill = o if o <= limit else (limit if s.l[i] <= limit else None)
            if fill is None:
                skipped["unfilled"] += 1
                continue
            qty = min(e["qty"], int(cash / (fill * (1 + COST_SIDE))))
            if qty < 1:
                skipped["cash"] += 1
                continue
            cost = qty * fill * (1 + COST_SIDE)
            cash -= cost
            if st.stop is None:
                stop_px = None
            else:
                stop_px = e["level"] if e.get("level") is not None else fill - e["stop_dist"]
            pos[e["key"]] = {"qty": qty, "entry": fill, "k": k, "last_k": k, "stop": stop_px,
                             "risk_ps": fill - stop_px if stop_px is not None else None, "hc": s.c[i],
                             "cost_basis": cost, "target": st.target_price(fill, stop_px),
                             "rank": e["rank"], "score": e["score"]}
        pend_in = []
        # ---- intraday: broker-side stop / target (GTT)
        for key in list(pos):
            if key in pend_out:
                continue
            s, p = panel.series[key], pos[key]
            i = s.row(k)
            if i is None:
                continue
            if p["stop"] is not None and s.l[i] <= p["stop"]:
                px = p["stop"] if (p["k"] == k or s.o[i] > p["stop"]) else s.o[i]
                sell(key, p, px, k, "stop")
                del pos[key]
            elif p["target"] and s.h[i] >= p["target"]:
                px = p["target"] if (p["k"] == k or s.o[i] < p["target"]) else s.o[i]
                sell(key, p, px, k, "target")
                del pos[key]
        # ---- close: mark to market, trailing stops, exit rules, limits, new signals
        m = panel.dates[k].strftime("%Y-%m")
        if m != month:
            month = m
            members = universes.setdefault(m, _universe(panel, k, lo, hi))
        value = cash
        for key, p in pos.items():
            s = panel.series[key]
            i = s.row(k)
            if i is None:
                value += p["qty"] * s.c[s.row(p["last_k"])]
                if k - p["last_k"] > DELIST_DAYS and key not in pend_out:
                    pend_out[key] = "delisted_or_suspended"
                continue
            p["last_k"] = k
            value += p["qty"] * s.c[i]
            res = results[key]
            p["hc"] = max(p["hc"], s.c[i])
            new = st.trail_level(res, i, p["hc"])
            if new is not None:
                p["stop"] = new if p["stop"] is None else (max(p["stop"], new) if only_up else new)
            if key not in pend_out:
                if res.sell is not None and res.sell[i]:
                    pend_out[key] = res.sell[i] if isinstance(res.sell[i], str) else "sell_rule"
                if key not in pend_out and st.max_hold_days and k - p["k"] >= st.max_hold_days:
                    pend_out[key] = "time_stop"
                if key not in pend_out and st._manage:  # the strategy's own rules for an open position
                    act = st.manage_position(_position_info(panel, key, p, k, s.c[i], sectors),
                                             engine.Bars.from_series(s, panel.dates, i))
                    if "stop" in act:
                        p["stop"] = act["stop"] if (p["stop"] is None or not only_up) else max(p["stop"], act["stop"])
                    if "sell" in act:
                        pend_out[key] = act["sell"]
        eq_dates.append(panel.dates[k])
        eq_vals.append(value)
        exposure_sum += (value - cash) / value if value > 0 else 0
        bench.append(index.close[k] if ok(index.close[k]) else None)
        invested_days += bool(pos)
        # weekly loss limit: week P&L vs the last close of the previous week
        wk = panel.dates[k].isocalendar()[:2]
        if wk != cur_week:
            week_base = eq_vals[-2] if len(eq_vals) > 1 else capital
            cur_week = wk
        if weekly_lim and blocked_week != wk and (value / week_base - 1) * 100 <= -weekly_lim:
            blocked_week = wk
            weekly_fired += 1
        peak = max(peak, value)
        halted = bool(halt_lim) and (value / peak - 1) * 100 <= -halt_lim
        halt_days += halted
        if regime is not None and regime[k] != 1.0:
            regime_off_days += 1
            continue
        next_week = panel.dates[k + 1].isocalendar()[:2] if k + 1 < len(panel.dates) else None
        block = "weekly_limit" if (blocked_week is not None and next_week == blocked_week) else ("halt" if halted else None)
        free = (max_pos - (len(pos) - len(pend_out))) if max_pos else 10 ** 9
        cands = []
        for key in members:
            res = results.get(key)
            if res is None:
                continue
            i = res.s.row(k)
            if i is None or not passing[key][i]:
                continue
            r = res.rank[i] if res.rank is not None else float("nan")
            if st.max_rank is not None and not (ok(r) and r <= st.max_rank):
                continue
            cands.append((r if ok(r) else 1e9, key, i))
        cands.sort()
        for r, key, i in cands:
            if key in pos:
                continue
            if block:
                skipped[block] += 1
                continue
            if free <= 0:
                skipped["no_slot"] += 1
                continue
            res = results[key]
            sc = res.s.c[i]
            limit_px = sc * (1 + st.limit_buffer_pct / 100)
            level, stop_dist, skip = st.plan_stop(res, i, limit_px)
            if skip:
                continue
            if st._allow:  # the strategy's own portfolio-level entry rules
                sym = res.s.sym
                book = {"date": panel.dates[k], "equity": value, "cash": cash,
                        "positions": [_position_info(panel, pk, pp, k, None, sectors) for pk, pp in pos.items()
                                      if pk not in pend_out]
                        + [{"symbol": panel.series[e["key"]].sym, "sector": sectors.get(panel.series[e["key"]].sym, ""),
                            "pending": True} for e in pend_in],
                        "recent_trades": trades[-50:]}
                cand = {"symbol": sym, "sector": sectors.get(sym, ""), "date": panel.dates[k], "close": sc,
                        "score": scores[key][i], "rank": int(r) if r < 1e9 else None,
                        "stop_pct": round(stop_dist / limit_px * 100, 2) if stop_dist else None}
                if st.allow(cand, book):
                    skipped["strategy_rule"] += 1
                    continue
            if st.stop and st.stop["type"] in ("atr", "pct"):
                level = None  # ATR/% stops are measured from the actual fill
            qty = st.size(value, limit_px, stop_dist)
            if qty < 1:
                continue
            score = scores[key][i]
            pend_in.append({"key": key, "close": sc, "qty": qty, "stop_dist": stop_dist, "level": level,
                            "rank": int(r) if r < 1e9 else None,
                            "score": round(score, 3) if score is not None and ok(score) else None})
            free -= 1
    extra = {"regime_off_days_pct": round(100 * regime_off_days / max(len(eq_vals), 1), 1),
             "invested_days_pct": round(100 * invested_days / max(len(eq_vals), 1), 1),
             "avg_capital_invested_pct": round(100 * exposure_sum / max(len(eq_vals), 1), 1),
             "weekly_limit_fired": weekly_fired if weekly_lim else None,
             "halt_days": halt_days if halt_lim else None,
             "signals_skipped": skipped, "open_positions_at_end": len(pos)}
    return eq_dates, eq_vals, bench, trades, extra

# ---------------------------------------------------------------- running


def _set_path(data, dotted, value):
    keys = dotted.split(".")
    if keys[0] == "risk" and not ((data.get("risk") or {}).get("confirmed")):
        keys[0] = "risk_suggested"  # an unconfirmed strategy is tested with its suggested risk block
    node = data
    for k in keys[:-1]:
        if node.get(k) is None:
            node[k] = {}
        node = node[k]
    node[keys[-1]] = value


def _parse_value(v):
    try:
        return json.loads(v)
    except ValueError:
        return v


def _variant(base, sets):
    data = json.loads(json.dumps(base.data))
    for kv in sets:
        key, _, val = kv.partition("=")
        _set_path(data, key.strip(), _parse_value(val.strip()))
    return engine.Strategy(data, base.folder, base.script)


def _split(vals):
    """Split sweep values on top-level commas, so JSON values like {"type": "pct", "value": 8} stay whole."""
    out, depth, quote, cur = [], 0, False, ""
    for ch in vals:
        if ch == '"':
            quote = not quote
        elif not quote and ch in "{[":
            depth += 1
        elif not quote and ch in "}]":
            depth -= 1
        if ch == "," and depth == 0 and not quote:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return out + [cur.strip()] if cur.strip() else out


def _signal_key(st):
    d = st.data
    return json.dumps([d.get("universe"), d.get("params"), st.script], sort_keys=True)


def _prepare(panel, st, index, k0, k1, universes):
    """Run the strategy script on every stock once, then rank them day by day within the universe."""
    results = {key: st.run_series(s, panel, index) for key, s in panel.series.items()}
    lo, hi = engine.UNIVERSES[st.universe][1]

    def members_for(k):
        m = panel.dates[k].strftime("%Y-%m")
        if m not in universes:
            universes[m] = _universe(panel, k, lo, hi)
        return universes[m]
    scores = assign_ranks(panel, results, members_for, range(k0, k1 + 1))
    return results, scores


def _report(st, dates, eq, bench, trades, extra, split, use_limits):
    summary = {"strategy": st.id, "strategy_name": st.name, "strategy_version": st.version,
               "risk_used": st.risk_source, "limits_simulated": use_limits,
               "costs": f"{COST_SIDE * 100:.2f}%/side + ₹{DP_CHARGE:.0f}/sell",
               "universe": f"{st.universe}: monthly proxy by 6-month traded value (point-in-time, incl. delisted)",
               "full": dict(_stats(dates, eq, trades, bench), **extra)}
    if split and dates[0] < split < dates[-1]:
        j = next(n for n, d in enumerate(dates) if d >= split)
        summary["in_sample"] = _stats(dates[:j + 1], eq[:j + 1], [t for t in trades if t["entry_date"] < split.isoformat()], bench[:j + 1])
        summary["out_of_sample"] = _stats(dates[j:], eq[j:], [t for t in trades if t["entry_date"] >= split.isoformat()], bench[j:])
    by_year = {}
    for y in sorted({d.year for d in dates}):
        idx = [n for n, d in enumerate(dates) if d.year == y]
        a, b = idx[0], idx[-1]
        base = eq[a - 1] if a > 0 else eq[a]
        bbase = bench[a - 1] if a > 0 else bench[a]
        by_year[y] = {"return_pct": round((eq[b] / base - 1) * 100, 1),
                      "nifty500_pct": round((bench[b] / bbase - 1) * 100, 1) if bench[b] and bbase else None}
    summary["by_year"] = by_year
    summary["risk_suggestions"] = suggest_risk(dates, eq, summary["full"])
    return summary


def run_backtest(args):
    base = engine.load(args.strategy)
    st = _variant(base, args.set)
    if st.risk is None:
        raise engine.StrategyError("no risk settings to test with: confirm the strategy's risk block or pass "
                                   "--set risk.sizing='{\"type\": \"risk\", \"pct\": 1}' etc.")
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    warm = start - dt.timedelta(days=max(430, int(st.history * 1.5) + 60))
    if not args.no_update:
        update(warm, end)
    log("backtest: pass 1 (liquid-stock candidates)...")
    cands = _candidates(warm, end)
    log(f"backtest: loading {len(cands)} candidate series...")
    panel = Panel(start=warm, end=end, symbols=cands, quiet=False)
    panel.series = {key: s for key, s in panel.series.items() if len(s.c) > 252}
    index = IndexData(panel.index)
    k0 = next(k for k, d in enumerate(panel.dates) if d >= start)
    k1 = len(panel.dates) - 1
    use_limits = not args.no_limits
    split = dt.date.fromisoformat(args.split) if args.split else None
    universes = {}

    variants = [(None, st)]
    if args.sweep:
        key, _, vals = args.sweep.partition("=")
        variants = [(v, _variant(base, args.set + [f"{key}={v}"])) for v in _split(vals)]
    results, prepared = [], {}
    for val, vst in variants:
        sk = _signal_key(vst)
        if sk not in prepared:
            log(f"backtest: computing {vst.name} rules on {len(panel.series)} series...")
            prepared = {sk: _prepare(panel, vst, index, k0, k1, universes)}
        log(f"backtest: simulating {panel.dates[k0]} .. {panel.dates[k1]}" + (f" ({args.sweep.partition('=')[0]}={val})" if val else ""))
        dates, eq, bench, trades, extra = simulate(panel, vst, prepared[sk], index, k0, k1, args.capital, universes, use_limits)
        results.append((val, vst, dates, eq, bench, trades, extra))

    stamp = f"{st.id}_{st.version}_{start}_{results[0][2][-1]}"
    out = scan.Path(args.out) if args.out else HOME / "backtests" / stamp
    out.mkdir(parents=True, exist_ok=True)
    if args.sweep:
        rows = []
        for val, vst, dates, eq, bench, trades, extra in results:
            s = _report(vst, dates, eq, bench, trades, extra, split, use_limits)
            f = s["full"]
            rows.append({args.sweep.partition("=")[0]: _parse_value(val), "cagr_pct": f["cagr_pct"],
                         "max_drawdown_pct": f["max_drawdown_pct"], "cagr_to_maxdd": f["cagr_to_maxdd"],
                         "worst_week_pct": f["worst_week_pct"], "trades": f["trades"], "win_rate_pct": f["win_rate_pct"],
                         "avg_capital_invested_pct": f["avg_capital_invested_pct"],
                         "is_cagr_to_maxdd": s.get("in_sample", {}).get("cagr_to_maxdd"),
                         "oos_cagr_to_maxdd": s.get("out_of_sample", {}).get("cagr_to_maxdd"),
                         "signals_skipped": f["signals_skipped"]})
        res = {"strategy": st.id, "sweep": args.sweep, "settings": args.set, "limits_simulated": use_limits,
               "nifty500_cagr_pct": f.get("nifty500_cagr_pct"), "nifty500_max_drawdown_pct": f.get("nifty500_max_drawdown_pct"),
               "results": rows, "output_folder": str(out)}
        (out / "sweep.json").write_text(json.dumps(res, indent=1, default=str))
        print(json.dumps(res, indent=1, default=str))
        return
    val, vst, dates, eq, bench, trades, extra = results[0]
    summary = _report(vst, dates, eq, bench, trades, extra, split, use_limits)
    summary["settings_overridden"] = args.set
    summary["strategy_file"] = vst.data
    summary["fingerprint"] = engine.fingerprint(vst.data, vst.script) if not args.set and use_limits else None
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    with (out / "trades.csv").open("w", newline="") as fh:
        if trades:
            w = csv.DictWriter(fh, fieldnames=list(trades[0]))
            w.writeheader()
            w.writerows(trades)
    with (out / "equity.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "equity", "nifty500"])
        w.writerows((d.isoformat(), round(e, 2), b) for d, e, b in zip(dates, eq, bench))
    summary["output_folder"] = str(out)
    print(json.dumps({k: v for k, v in summary.items() if k != "strategy_file"}, indent=1, default=str))
