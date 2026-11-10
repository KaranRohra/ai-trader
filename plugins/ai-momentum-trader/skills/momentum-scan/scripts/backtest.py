"""Replays the exact live rules of scan.py over NSE history (standard library only).

- Universe: point-in-time top 500 stocks by 6-month average traded value, rebuilt every month
  (the historical NIFTY 500 membership isn't published as data; this is the closest no-bias proxy).
  Delisted stocks are included, so there is no survivorship bias in prices.
- Signals at the close of day D; orders execute on day D+1 exactly like the live bot:
  LIMIT at signal close + 1%, skipped if the open is > 3% above the signal close.
- Stops act like the broker-side GTT: if the day's low touches the stop, exit at the stop
  (or at the open if it gapped below). Trailing stop and rule exits update at each close.
- Costs: Zerodha delivery (0 brokerage) ~0.12% per side for STT/stamp/exchange/GST + ₹16 DP charge per sell.
"""
import csv
import datetime as dt
import gzip
import json
import math
from collections import deque

import scan
from scan import (HOME, EQ_DIR, Ind, Panel, chart_stop_level, failed_rules, load_config, log, ok, rank_on,
                  regime_series, stop_distance, target_price, update)

COST_SIDE = 0.0012
DP_CHARGE = 16.0
UNIVERSE = 500
DELIST_DAYS = 20


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


def _universe(panel, k, window=126, size=UNIVERSE):
    """Top `size` series by average traded value over the `window` sessions before date index k."""
    ranked = []
    for key, s in panel.series.items():
        i = s.row(k - 1) if k > 0 else None
        if i is None or i < 252:
            continue
        ranked.append((sum(s.t[i - window + 1:i + 1]) / window, key))
    ranked.sort(reverse=True)
    return {key for _, key in ranked[:size]}


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
    wins = [t for t in trades if t["pnl"] > 0]
    out = {
        "from": dates[0].isoformat(), "to": dates[-1].isoformat(), "years": round(yrs, 2),
        "start_equity": round(equity[0]), "end_equity": round(equity[-1]),
        "total_return_pct": round((equity[-1] / equity[0] - 1) * 100, 1),
        "cagr_pct": round(cagr * 100, 2), "max_drawdown_pct": round(mdd * 100, 2),
        "cagr_to_maxdd": round(cagr / -mdd, 2) if mdd < 0 else None,
        "worst_month": worst[0], "worst_month_pct": round(worst[1] * 100, 2),
        "positive_months_pct": round(100 * sum(1 for r in mret.values() if r > 0) / max(len(mret), 1), 1),
        "trades": len(trades), "win_rate_pct": round(100 * len(wins) / len(trades), 1) if trades else None,
        "avg_r": round(sum(t["r"] for t in trades) / len(trades), 2) if trades else None,
        "avg_win_pct": round(sum(t["ret_pct"] for t in wins) / len(wins), 2) if wins else None,
        "avg_loss_pct": round(sum(t["ret_pct"] for t in trades if t["pnl"] <= 0) / max(len(trades) - len(wins), 1), 2) if trades else None,
        "avg_hold_days": round(sum(t["hold_days"] for t in trades) / len(trades), 1) if trades else None,
    }
    if bench and bench[0] and bench[-1]:
        bpeak, bdd = bench[0], 0.0
        for b in bench:
            bpeak = max(bpeak, b)
            bdd = min(bdd, b / bpeak - 1)
        out["nifty500_cagr_pct"] = round(((bench[-1] / bench[0]) ** (1 / yrs) - 1) * 100, 2) if yrs > 0 else None
        out["nifty500_max_drawdown_pct"] = round(bdd * 100, 2)
    return out


def simulate(panel, inds, cfg, k0, k1, capital, universes):
    reg, ivals, _ = regime_series(panel.index, cfg["regime_sma"])
    cash = capital
    pos, pend_in, pend_out = {}, [], {}
    trades, eq_dates, eq_vals, bench = [], [], [], []
    regime_off_days = invested_days = 0
    exposure_sum = 0.0
    skipped = {"gap": 0, "unfilled": 0, "no_slot": 0, "cash": 0}
    month, members = None, set()

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
            "r": round((px - p["entry"]) / p["risk_ps"], 2), "hold_days": k - p["k"], "exit_reason": why,
            "stop_pct": round(p["risk_ps"] / p["entry"] * 100, 2),
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
            if o > sc * (1 + cfg["max_gap_pct"] / 100):
                skipped["gap"] += 1
                continue
            limit = sc * (1 + cfg["limit_buffer_pct"] / 100)
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
            stop_px = e["level"] if e.get("level") is not None else fill - e["stop_dist"]
            risk_ps = fill - stop_px
            pos[e["key"]] = {"qty": qty, "entry": fill, "k": k, "last_k": k, "stop": stop_px,
                             "risk_ps": risk_ps, "hc": s.c[i], "cost_basis": cost,
                             "target": target_price(cfg, fill, risk_ps),
                             "rank": e["rank"], "score": e["score"]}
        pend_in = []
        # ---- intraday: GTT stops
        for key in list(pos):
            if key in pend_out:
                continue
            s, p = panel.series[key], pos[key]
            i = s.row(k)
            if i is None:
                continue
            if s.l[i] <= p["stop"]:
                px = p["stop"] if (p["k"] == k or s.o[i] > p["stop"]) else s.o[i]
                sell(key, p, px, k, "stop")
                del pos[key]
            elif p["target"] and s.h[i] >= p["target"]:
                px = p["target"] if (p["k"] == k or s.o[i] < p["target"]) else s.o[i]
                sell(key, p, px, k, "target")
                del pos[key]
        # ---- close: mark to market, trail stops, rule exits, new signals
        if k > k0 and cash > 0:
            in_etf = cfg.get("idle_in_index") and not (cfg.get("idle_only_regime_on") and reg[k - 1] is False)
            if in_etf and ivals[k] and ivals[k - 1]:
                cash *= ivals[k] / ivals[k - 1]  # uninvested money held in a NIFTY 500 index ETF (price return)
            elif cfg.get("cash_yield_pct"):
                cash *= (1 + cfg["cash_yield_pct"] / 100) ** (1 / 252)  # liquid fund / LIQUIDBEES
        m = panel.dates[k].strftime("%Y-%m")
        if m != month:
            month = m
            members = universes.setdefault(m, _universe(panel, k))
        ranks = rank_on(inds, k, members)
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
            ind = inds[key]
            p["hc"] = max(p["hc"], s.c[i])
            if cfg.get("trail_atr", 0) > 0 and ok(ind.atr[i]):
                p["stop"] = max(p["stop"], p["hc"] - cfg["trail_atr"] * ind.atr[i])
            rk = ranks.get(key)
            if key not in pend_out:
                if cfg.get("exit_on_50dma", True) and ok(ind.sma_f[i]) and s.c[i] < ind.sma_f[i]:
                    pend_out[key] = "close_below_50dma"
                elif cfg.get("exit_on_rank", True) and (rk is None or rk > cfg["rank_hold"]):
                    pend_out[key] = "rank_drop"
                elif cfg.get("max_hold_days", 0) and k - p["k"] >= cfg["max_hold_days"]:
                    pend_out[key] = "time_stop"
        eq_dates.append(panel.dates[k])
        eq_vals.append(value)
        exposure_sum += (value - cash) / value if value > 0 else 0
        bench.append(ivals[k])
        invested_days += bool(pos)
        if cfg.get("use_regime", True) and not reg[k]:
            regime_off_days += 1
            continue
        free = cfg["max_positions"] - (len(pos) - len(pend_out))
        top = sorted((r, key) for key, r in ranks.items() if key in members and r <= cfg["rank_entry"])
        for r, key in top:
            if key in pos:
                continue
            ind = inds[key]
            i = ind.row(k)
            if i is None or not ok(ind.atr[i]) or not ok(ind.hi[i]) or not ok(ind.vavg[i]):
                continue
            if failed_rules(ind, i, r, cfg):
                continue
            if free <= 0:
                skipped["no_slot"] += 1
                continue
            sc = ind.s.c[i]
            limit_px = sc * (1 + cfg["limit_buffer_pct"] / 100)
            level, stop_dist, skip = scan.plan_stop(cfg, panel, ind, i, limit_px)
            if skip:
                continue
            if cfg.get("stop_mode", "atr") in ("atr", "pct"):
                level = None  # ATR/% stops are measured from the actual fill
            if cfg.get("position_pct"):
                qty = int(value * cfg["position_pct"] / 100 // limit_px)
            else:
                qty = int(value * cfg["risk_pct"] / 100 // stop_dist)
            qty = min(qty, int(value * cfg["max_position_pct"] / 100 // (sc * (1 + cfg["limit_buffer_pct"] / 100))))
            if qty < 1:
                continue
            pend_in.append({"key": key, "close": sc, "qty": qty, "stop_dist": stop_dist, "level": level, "rank": r,
                            "score": round(ind.mom[i], 3)})
            free -= 1
    extra = {"regime_off_days_pct": round(100 * regime_off_days / max(len(eq_vals), 1), 1),
             "invested_days_pct": round(100 * invested_days / max(len(eq_vals), 1), 1),
             "avg_capital_invested_pct": round(100 * exposure_sum / max(len(eq_vals), 1), 1),
             "signals_skipped": skipped, "open_positions_at_end": len(pos)}
    return eq_dates, eq_vals, bench, trades, extra


def run_backtest(args):
    cfg = load_config(args.config)
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    warm = start - dt.timedelta(days=430)
    if not args.no_update:
        update(warm, end)
    log("backtest: pass 1 (liquid-stock candidates)...")
    cands = _candidates(warm, end)
    log(f"backtest: loading {len(cands)} candidate series...")
    panel = Panel(start=warm, end=end, symbols=cands, quiet=False)
    inds = {key: Ind(s, cfg) for key, s in panel.series.items() if len(s.c) > 252}
    panel.series = {key: s for key, s in panel.series.items() if key in inds}
    k0 = next(k for k, d in enumerate(panel.dates) if d >= start)
    k1 = len(panel.dates) - 1
    log(f"backtest: simulating {panel.dates[k0]} .. {panel.dates[k1]} on {len(inds)} series")
    dates, eq, bench, trades, extra = simulate(panel, inds, cfg, k0, k1, args.capital, {})

    split = dt.date.fromisoformat(args.split) if args.split else None
    summary = {"strategy_version": cfg["version"], "rules": cfg, "costs": f"{COST_SIDE*100:.2f}%/side + ₹{DP_CHARGE:.0f}/sell",
               "universe": "monthly top 500 by 6-month traded value (point-in-time, incl. delisted)",
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

    out = scan.Path(args.out) if args.out else HOME / "backtests" / f"{cfg['version']}_{start}_{dates[-1]}"
    out.mkdir(parents=True, exist_ok=True)
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
    print(json.dumps({k: v for k, v in summary.items() if k != "rules"}, indent=1, default=str))
