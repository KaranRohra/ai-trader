#!/usr/bin/env python3
"""Backtest report for the AI Trader: one self-contained HTML file (Python standard library only).

  python3 report.py <backtest folder>      (re)build <folder>/report.html from summary.json, equity.csv, trades.csv

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


def build(folder):
    folder = Path(folder)
    summary = json.loads((folder / "summary.json").read_text())
    equity = []
    with (folder / "equity.csv").open() as fh:
        for r in csv.DictReader(fh):
            b = r.get("nifty500")
            equity.append((r["date"], float(r["equity"]), float(b) if b not in (None, "", "None") else None))
    trades = []
    if (folder / "trades.csv").exists():
        with (folder / "trades.csv").open() as fh:
            for r in csv.DictReader(fh):
                trades.append([r["symbol"], r["entry_date"], r["exit_date"], float(r["entry"]), float(r["exit"]),
                               int(float(r["qty"])), float(r["pnl"]), float(r["ret_pct"]),
                               float(r["r"]) if r.get("r") not in (None, "", "None") else None,
                               int(float(r["hold_days"])), EXIT_WORDS.get(r["exit_reason"], r["exit_reason"].replace("_", " "))])
    f = summary.get("full") or {}
    cap = f.get("start_equity") or (equity[0][1] if equity else 100000)
    b0 = next((b for _, _, b in equity if b), None)
    payload = {
        "dates": [d for d, _, _ in equity],
        "eq": [round(e) for _, e, _ in equity],
        "bench": [round(b / b0 * cap) if b and b0 else None for _, _, b in equity],
        "years": summary.get("by_year") or {},
        "monthly": _monthly(equity),
        "trades": trades,
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
        "KPIS": _kpis(f),
        "SPLIT": _split_table(summary),
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


def _kpis(f):
    def tile(label, val, sub="", cls=""):
        return (f'<div class="kpi {cls}"><div class="kl">{label}</div><div class="kv">{val}</div>'
                f'<div class="ks">{sub}</div></div>')
    cagr_n = f.get("nifty500_cagr_pct")
    dd_n = f.get("nifty500_max_drawdown_pct")
    return "".join([
        tile("Final value", rupees(f.get("end_equity", 0)), f"from {rupees(f.get('start_equity', 0))} · {_pct(f.get('total_return_pct'))}"),
        tile("CAGR (per year)", _pct(f.get("cagr_pct"), False), f"NIFTY 500: {_pct(cagr_n, False)}"),
        tile("Max drawdown", _pct(f.get("max_drawdown_pct"), False), f"NIFTY 500: {_pct(dd_n, False)}", "neg"),
        tile("Trades", f.get("trades", 0), f"won {_pct(f.get('win_rate_pct'), False)} · avg hold {f.get('avg_hold_days') or '–'} days"),
        tile("Avg win / avg loss", f"{_pct(f.get('avg_win_pct'))} / {_pct(f.get('avg_loss_pct'))}", f"avg R {f.get('avg_r') if f.get('avg_r') is not None else '–'}"),
        tile("Worst week", _pct(f.get("worst_week_pct")), f"week ending {f.get('worst_week_ending') or '–'}", "neg"),
        tile("Worst month", _pct(f.get("worst_month_pct")), f"{f.get('worst_month') or '–'}", "neg"),
        tile("Capital invested", _pct(f.get("avg_capital_invested_pct"), False), "on average; the rest sat as cash"),
    ])


def _split_table(s):
    rows = [("Whole period", s.get("full"))]
    if s.get("in_sample"):
        rows.append(("Before 2021", s["in_sample"]))
    if s.get("out_of_sample"):
        rows.append(("2021 onward", s["out_of_sample"]))
    body = "".join(
        f"<tr><td>{n}</td><td>{x.get('from', '')[:7]} → {x.get('to', '')[:7]}</td><td>{_pct(x.get('cagr_pct'), False)}</td>"
        f"<td>{_pct(x.get('max_drawdown_pct'), False)}</td><td>{x.get('trades', 0)}</td><td>{_pct(x.get('win_rate_pct'), False)}</td>"
        f"<td>{_pct(x.get('nifty500_cagr_pct'), False)}</td><td>{_pct(x.get('nifty500_max_drawdown_pct'), False)}</td></tr>"
        for n, x in rows if x)
    return ("<table><thead><tr><th>Period</th><th>Dates</th><th>CAGR</th><th>Max DD</th><th>Trades</th><th>Won</th>"
            "<th>NIFTY 500 CAGR</th><th>NIFTY 500 max DD</th></tr></thead><tbody>" + body + "</tbody></table>")


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
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 16px 60px}
header{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:flex-start}
h1{font-size:26px;margin:0 0 4px}
h2{font-size:18px;margin:0 0 12px}
h3{font-size:14px;margin:14px 0 6px;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}
.sub{color:var(--mute);font-size:14px}
.tag{display:inline-block;font-size:12px;padding:2px 8px;border-radius:99px;background:var(--grid);color:var(--mute);margin-right:6px}
button{font:inherit;font-size:13px;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:5px 10px;cursor:pointer}
button.on{border-color:var(--acc);color:var(--acc)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-top:16px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kl{font-size:12px;color:var(--mute)}
.kv{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums;margin:2px 0}
.kpi.neg .kv{color:var(--neg)}
.ks{font-size:12px;color:var(--mute)}
.head{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px}
.legend{display:flex;gap:14px;font-size:13px;color:var(--mute)}
.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
svg{display:block;width:100%;overflow:visible}
svg text{fill:var(--mute);font-size:11px}
.tip{position:fixed;pointer-events:none;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 9px;
font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.15);display:none;z-index:5;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}
th{color:var(--mute);font-weight:600;font-size:12px}
.wrap{overflow-x:auto}
.heat td,.heat th{text-align:center;padding:6px 4px;border:none}
.heat td{border-radius:4px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:20px}
ul{margin:4px 0;padding-left:18px}
li{margin:3px 0}
.pos{color:var(--pos)}.neg{color:var(--neg)}
.note{color:var(--mute);font-size:13px}
#trades th{cursor:pointer;user-select:none;white-space:nowrap}
#trades td{white-space:nowrap}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.grid2 .card{margin-top:0}
footer{color:var(--mute);font-size:12px;margin-top:22px}
@media (max-width:820px){.kpis{grid-template-columns:repeat(2,1fr)}.cols,.grid2{grid-template-columns:1fr}.kv{font-size:19px}}
</style>
</head>
<body>
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

<section class="card">
 <div class="head"><h2>Equity vs NIFTY 500</h2>
  <div class="legend"><span><i style="background:var(--acc)"></i>Strategy</span><span><i style="background:var(--bench)"></i>NIFTY 500 (same starting capital)</span>
  <button id="logbtn" type="button">Log scale</button></div></div>
 <div id="eqchart"></div>
</section>

<section class="card">
 <div class="head"><h2>Drawdown (fall from the previous peak)</h2>
  <div class="legend"><span><i style="background:var(--neg)"></i>Strategy</span><span><i style="background:var(--bench)"></i>NIFTY 500</span></div></div>
 <div id="ddchart"></div>
</section>

<section class="card">
  <div class="head"><h2>Year by year</h2>
   <div class="legend"><span><i style="background:var(--acc)"></i>Strategy</span><span><i style="background:var(--bench)"></i>NIFTY 500</span></div></div>
  <div id="yrchart"></div>
</section>

<section class="card"><h2>Before 2021 vs 2021 onward</h2>
 <p class="note" style="margin-top:0">If a strategy only worked in one half, that shows here.</p>
 <div class="wrap">{{SPLIT}}</div></section>

<section class="card"><h2>Month by month (%)</h2><div class="wrap" id="heat"></div></section>

<div class="grid2" style="margin-top:16px">
 <section class="card">{{EXITS}}</section>
 <section class="card"><h2>Signals not taken</h2>{{SKIPS}}</section>
</div>

<section class="card"><h2>The strategy that was tested</h2>{{RULES}}</section>

<section class="card">
 <div class="head"><h2>Every trade</h2><span class="note" id="tcount"></span></div>
 <div class="wrap"><table id="trades"><thead><tr>
  <th data-k="0">Stock</th><th data-k="1">Bought</th><th data-k="2">Sold</th><th data-k="3">Buy ₹</th><th data-k="4">Sell ₹</th>
  <th data-k="5">Qty</th><th data-k="6">P&amp;L ₹</th><th data-k="7">Return</th><th data-k="8">R</th><th data-k="9">Days</th><th data-k="10">Exit</th>
 </tr></thead><tbody></tbody></table></div>
</section>

<section class="card"><h2>How this backtest works</h2>
<ul>
 <li>Free official NSE end-of-day data. Prices adjusted for splits and bonuses.</li>
 <li>Stocks: {{UNIVERSE}}. NSE doesn't publish past index lists as data, so each month the most traded stocks stand in for the index. Delisted stocks are included.</li>
 <li>Signals use the day's close; the LIMIT buy is placed the next morning, exactly like the live bot. Trades skip if the stock opens too far up.</li>
 <li>Stops and targets act like Zerodha GTT orders: they fill at the level, or at the open if the price gapped past it.</li>
 <li>Costs included: {{COSTS}}. Weekly loss limit and drawdown halt: {{LIMITS}}.</li>
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
<footer>Made by the AI Trader plugin · all data and charts are inside this file</footer>
</main>
<div class="tip" id="tip"></div>
<script>
const D={{DATA}};
const $=s=>document.querySelector(s);
const NS="http://www.w3.org/2000/svg";
const inr=v=>(v<0?"−":"")+"₹"+Math.abs(Math.round(v)).toLocaleString("en-IN");
const pct=v=>(v>0?"+":"")+v.toFixed(1)+"%";
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
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
 let lo=Math.min(...vals),hi=Math.max(...vals);if(opts.zero){hi=Math.max(hi,0)}
 const useLog=opts.log&&lo>0;const f=useLog?Math.log:(v=>v);
 const flo=f(lo),fhi=f(hi),pad=(fhi-flo)*0.04;
 const y=v=>T+(H-T-B)*(1-(f(v)-flo+pad)/(fhi-flo+2*pad));const x=i=>L+(W-L-R)*i/Math.max(n-1,1);
 let ticks=useLog?(()=>{const t=[];let m=Math.pow(10,Math.floor(Math.log10(lo)));for(let v=m;v<=hi*1.01;v*=10)[1,2,5].forEach(k=>{const z=v*k;if(z>=lo*0.97&&z<=hi*1.03)t.push(z)});return t})():niceTicks(lo,hi,5);
 ticks.forEach(t=>{const yy=y(t);el("line",{x1:L,x2:W-R,y1:yy,y2:yy,stroke:css("--grid")},svg);
  const tx=el("text",{x:L-8,y:yy+4,"text-anchor":"end"},svg);tx.textContent=opts.fmt(t)});
 let lastY="";D.dates.forEach((d,i)=>{const yr=d.slice(0,4);if(yr!==lastY){lastY=yr;if(i>0||true){const t=el("text",{x:x(i),y:H-6,"text-anchor":"middle"},svg);t.textContent=yr}}});
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

function drawdown(v){let peak=-Infinity;return v.map(x=>{if(x==null)return null;peak=Math.max(peak,x);return (x/peak-1)*100})}

function yearChart(){
 const box=$("#yrchart");box.innerHTML="";const ys=Object.keys(D.years);const W=box.clientWidth||900,H=280,L=44,R=8,T=10,B=26;
 const svg=el("svg",{viewBox:`0 0 ${W} ${H}`,height:H},box);
 const all=[];ys.forEach(y=>{all.push(D.years[y].return_pct);if(D.years[y].nifty500_pct!=null)all.push(D.years[y].nifty500_pct)});
 const lo=Math.min(0,...all),hi=Math.max(0,...all),yv=v=>T+(H-T-B)*(1-(v-lo)/((hi-lo)||1));
 niceTicks(lo,hi,5).forEach(t=>{el("line",{x1:L,x2:W-R,y1:yv(t),y2:yv(t),stroke:css("--grid")},svg);const tx=el("text",{x:L-6,y:yv(t)+4,"text-anchor":"end"},svg);tx.textContent=t+"%"});
 el("line",{x1:L,x2:W-R,y1:yv(0),y2:yv(0),stroke:css("--mute")},svg);
 const gw=(W-L-R)/ys.length,bw=Math.max(3,gw*0.34);
 ys.forEach((y,i)=>{const cx=L+gw*i+gw/2;[[D.years[y].return_pct,css("--acc"),-1,"Strategy"],[D.years[y].nifty500_pct,css("--bench"),1,"NIFTY 500"]].forEach(([v,c,side,nm])=>{
  if(v==null)return;const r=el("rect",{x:cx+(side<0?-bw-1:1),y:Math.min(yv(v),yv(0)),width:bw,height:Math.max(1,Math.abs(yv(v)-yv(0))),fill:c,rx:2},svg);
  r.addEventListener("mousemove",ev=>showTip(ev,`<b>${y}</b><br>${nm}: ${pct(v)}`));r.addEventListener("mouseleave",hideTip)});
  const t=el("text",{x:cx,y:H-6,"text-anchor":"middle"},svg);t.textContent=ys.length>14?"'"+y.slice(2):y});
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

let sortK=1,sortDir=-1;
function tradesTable(){
 const rows=D.trades.slice().sort((a,b)=>{const x=a[sortK],y=b[sortK];if(x==null)return 1;if(y==null)return -1;return (x>y?1:x<y?-1:0)*sortDir});
 $("#trades tbody").innerHTML=rows.map(t=>`<tr><td><b>${t[0]}</b></td><td>${t[1]}</td><td>${t[2]}</td><td>${t[3].toFixed(2)}</td><td>${t[4].toFixed(2)}</td><td>${t[5]}</td>`+
  `<td class="${t[6]>=0?"pos":"neg"}">${inr(t[6])}</td><td class="${t[7]>=0?"pos":"neg"}">${pct(t[7])}</td><td>${t[8]==null?"–":t[8].toFixed(2)}</td><td>${t[9]}</td><td>${t[10]}</td></tr>`).join("");
 $("#tcount").textContent=D.trades.length+" closed trades · click a column to sort";
 document.querySelectorAll("#trades th").forEach(th=>th.textContent=th.textContent.replace(/ [▲▼]$/,"")+(+th.dataset.k===sortK?(sortDir>0?" ▲":" ▼"):""));
}
document.querySelectorAll("#trades th").forEach(th=>th.addEventListener("click",()=>{const k=+th.dataset.k;if(k===sortK)sortDir*=-1;else{sortK=k;sortDir=k<3||k===10?1:-1}tradesTable()}));

function draw(){
 lineChart($("#eqchart"),[{name:"NIFTY 500",v:D.bench,color:css("--bench"),w:1.6},{name:"Strategy",v:D.eq,color:css("--acc"),w:2.2}],{fmt:inr,log:logScale,h:340});
 lineChart($("#ddchart"),[{name:"NIFTY 500",v:drawdown(D.bench),color:css("--bench"),w:1.4},{name:"Strategy",v:drawdown(D.eq),color:css("--neg"),w:1.8,fill:true}],{fmt:v=>v.toFixed(0)+"%",zero:true,h:220});
 yearChart();
}
$("#logbtn").addEventListener("click",e=>{logScale=!logScale;e.target.classList.toggle("on",logScale);draw()});
$("#theme").addEventListener("click",()=>{const r=document.documentElement;const dark=r.dataset.theme?r.dataset.theme==="dark":matchMedia("(prefers-color-scheme:dark)").matches;r.dataset.theme=dark?"light":"dark";draw()});
let rt;addEventListener("resize",()=>{clearTimeout(rt);rt=setTimeout(draw,120)});
draw();heat();tradesTable();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(build(sys.argv[1]))
