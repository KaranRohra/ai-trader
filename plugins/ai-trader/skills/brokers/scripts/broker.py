#!/usr/bin/env python3
"""The broker the AI Trader trades through (Python standard library only). Every command prints JSON.

A broker profile says which MCP server to use, which of its tools does each job the bot needs (quote, cash,
holdings, buy, cancel, order status, stop order), how stops are kept (protection level), whether orders are
placed by the MCP or by the user (Manual mode), and the broker's charges.

  Ready-made:  skills/brokers/library/<id>.json (+ <id>.md: exact tool use)        - Zerodha (tested), Dhan
  Catalog:     skills/brokers/catalog.json - every Indian broker with an official MCP (URL, login, what it can do)
  Yours:       ~/ai-trader/brokers/<id>.json (+ <id>.md), drafts in ~/ai-trader/brokers/drafts/
  Active:      ~/ai-trader/config.json -> "broker" (missing = zerodha)

Commands
  catalog                                   brokers with an official MCP + Manual mode
  connect-cmd --id X [--alt]                the exact `claude mcp add` command for a catalog broker
  list                                      ready-made and connected profiles, and the active one
  draft --id X [--manual] [--name N] [--url U] [--transport http|sse] [--server S]
                                            start (or resume) a profile draft, seeded from the catalog
  set --id X --json '{...}'                 merge fields into the draft (tools, protection, charges, checks, ...)
  check --id X                              what's missing or wrong; the review needs a clean check
  review --id X                             the review screen (lines to show the user)
  confirm --id X [--no-use]                 save the reviewed draft and make it the active broker
  use --id X                                switch the active broker (only with no open bot positions)
  show [--id X]                             one profile in full, with its check
  capability                                the active broker for the other skills: tools, protection, login, notes
  first-order-done                          record that the first LIVE order on an untested broker went through
"""
import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scanner" / "scripts"))
import engine  # noqa: E402

HOME = engine.HOME
LIB = engine.BROKER_LIBRARY
USER = engine.USER_BROKERS
DRAFTS = USER / "drafts"
CATALOG = Path(__file__).resolve().parents[1] / "catalog.json"
POSITIONS = HOME / "positions.json"

LEVELS = ["two_leg", "single", "day_sl", "bot_watch"]  # best first
JOBS = {  # tool slot -> what it's for
    "profile": "who is logged in (connection check)",
    "quote": "live quote: last price, today's open, previous close, circuit limits",
    "ltp": "last price only (several symbols)",
    "funds": "free cash",
    "holdings": "shares held (to reconcile the bot's positions and to skip stocks you hold yourself)",
    "orders": "today's orders",
    "order_status": "one order's status, filled qty and average price",
    "place_order": "place a buy / sell order (LIMIT, MARKET and, for day stop-losses, SL)",
    "cancel_order": "cancel an open order",
    "protect_list": "list GTT / OCO orders",
    "protect_place": "place a GTT / OCO order",
    "protect_modify": "change a GTT / OCO order",
    "protect_cancel": "delete a GTT / OCO order",
}
REQUIRED_MCP = ["place_order", "cancel_order", "holdings"]
CHARGE_KEYS = ["brokerage_per_order", "dp_per_sell"]
READ_CHECKS = ["profile", "funds", "holdings", "quote", "orders"]


def out(o):
    print(json.dumps(o, indent=1, ensure_ascii=False, default=str))


def fail(msg, **extra):
    out(dict({"error": msg}, **extra))
    sys.exit(1)


def today():
    return dt.date.today().isoformat()


def catalog():
    return engine.read_json(CATALOG, {"brokers": []})


def catalog_entry(bid):
    return next((b for b in catalog()["brokers"] if b["id"] == bid), None)


def _public(d):
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _fp(d):
    return hashlib.sha1(json.dumps(_public(d), sort_keys=True, default=str).encode()).hexdigest()[:12]


def _draft_path(bid):
    return DRAFTS / f"{bid}.json"


def _notes_path(prof, draft=False):
    if prof.get("_path", "").startswith(str(LIB)):
        return str(LIB / (prof.get("notes_file") or f"{prof['id']}.md"))
    return str((DRAFTS if draft else USER) / f"{prof['id']}.md")


def load_draft(bid):
    d = engine.read_json(_draft_path(bid))
    if d is None:
        fail(f"no draft for '{bid}'. Start one: broker.py draft --id {bid}")
    return d


def save_draft(d):
    engine.write_json(_draft_path(d["id"]), d)


def merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict) and k != "tools":
            merge(a[k], v)
        elif isinstance(v, dict) and k == "tools":
            a.setdefault("tools", {}).update(v)
        else:
            a[k] = v
    return a


def config():
    return engine.config() or {}


def open_positions():
    return (engine.read_json(POSITIONS, {}) or {}).get("positions", [])


def position_broker(p):
    return p.get("broker") or engine.DEFAULT_BROKER


# ---------------------------------------------------------------- checks

def check_profile(d):
    errors, warnings = [], []
    ex = d.get("execution")
    if ex not in ("mcp", "manual"):
        errors.append("execution must be 'mcp' (the MCP places orders) or 'manual' (the user places them)")
    prot = d.get("protection")
    if prot not in LEVELS:
        errors.append(f"protection must be one of {LEVELS}")
    tools = d.get("tools") or {}
    unknown = [k for k in tools if k not in JOBS]
    if unknown:
        errors.append(f"unknown tool slots {unknown}; use {list(JOBS)}")
    if ex == "mcp":
        if not d.get("mcp_server"):
            errors.append("mcp_server: the MCP server's name as it appears in /mcp")
        for k in REQUIRED_MCP:
            if not tools.get(k):
                errors.append(f"tools.{k} is missing ({JOBS[k]}). Without it the bot can't trade through this MCP: "
                              "use Manual mode instead")
        if not (tools.get("order_status") or tools.get("orders")):
            errors.append("tools.order_status or tools.orders is needed to see whether an order filled")
        if prot in ("two_leg", "single"):
            for k in ("protect_place", "protect_cancel", "protect_list"):
                if not tools.get(k):
                    errors.append(f"protection {prot} needs tools.{k} ({JOBS[k]}); otherwise pick a lower level")
            if not tools.get("protect_modify"):
                warnings.append("no GTT modify tool: a trailing stop is moved by deleting and placing a new GTT")
        if prot == "day_sl" and not d.get("sl_order_args"):
            errors.append("protection day_sl: write sl_order_args (how place_order takes a stop-loss SELL order, "
                          "e.g. order_type SL + trigger_price) after checking the tool's schema")
        checks = {c.get("check"): c for c in d.get("checks") or []}
        missing = [c for c in ("holdings",) if not (checks.get(c) or {}).get("ok")]
        if missing and not d.get("tested"):
            errors.append("run the read-only checks first and record them with set --json "
                          "'{\"checks\": [{\"check\": \"holdings\", \"ok\": true, \"note\": \"...\"}, ...]}'")
        for c in READ_CHECKS:
            if c in checks and not checks[c].get("ok"):
                warnings.append(f"read-only check '{c}' failed: {checks[c].get('note', '')}")
    if not (tools.get("quote") or tools.get("ltp")):
        warnings.append("no live quote tool: fills in PAPER and the gap / circuit checks use the scan's prices "
                        "(the LIMIT price still caps what a buy can pay)")
    if not tools.get("funds"):
        warnings.append("no cash tool: free cash comes from the bot's own books")
    if ex == "manual":
        warnings.append("Manual mode: you place every order and stop the bot lists, then tell it the fill")
    ch = d.get("charges") or {}
    for k in CHARGE_KEYS:
        if not isinstance(ch.get(k), (int, float)):
            errors.append(f"charges.{k} is not answered (ask the user; Zerodha's are 0 and 15.34)")
    if prot in ("day_sl", "bot_watch"):
        warnings.append("stops are not kept at the broker between runs: strategies whose risk says "
                        "'stop at broker: Required' can't open LIVE positions on this broker")
    if not d.get("tested"):
        warnings.append("not tested by the plugin's author: the first LIVE order on it is confirmed with you first")
    return errors, warnings


def protection_text(d):
    words = engine.PROTECTION_WORDS.get(d.get("protection"), "")
    if d.get("protect_order_name"):
        words = words.replace("a GTT stop order", "a " + d["protect_order_name"] + " (stop)")
    if d.get("execution") == "manual" and d.get("protection") != "bot_watch":
        words = "you place it in the broker's app: " + words
    return words


def review_lines(d):
    tools = d.get("tools") or {}
    ch = d.get("charges") or {}
    rows = [("Broker", d.get("name")),
            ("Orders placed by", "the MCP (" + (d.get("mcp_server") or "") + ")" if d["execution"] == "mcp"
             else "you, in the broker's app (Manual mode)"),
            ("Login", (d.get("login") or {}).get("how") or "none needed"),
            ("Stop-loss", f"{d.get('protection')}: {protection_text(d)}"),
            ("Brokerage", f"₹{ch.get('brokerage_per_order')} per order"),
            ("DP charge", f"₹{ch.get('dp_per_sell')} per stock sold"),
            ("Tested by the author", "yes" if d.get("tested") else "no")]
    tool_rows = [(k, tools.get(k) or "-", JOBS[k]) for k in JOBS]
    checks = [(c.get("check"), "ok" if c.get("ok") else "FAILED", c.get("note", "")) for c in d.get("checks") or []]
    return rows, tool_rows, checks


# ---------------------------------------------------------------- commands

def cmd_catalog(a):
    cat = catalog()
    act = engine.active_broker_id() if engine.CONFIG.exists() else None
    out({"checked": cat.get("checked"), "active": act,
         "brokers": [dict({k: b.get(k) for k in ("id", "name", "can_trade", "auth", "protection_known",
                                                 "prerequisites", "notes", "tested", "docs")},
                          ready_made=(LIB / f"{b['id']}.json").exists(),
                          bundled=bool((b.get("connect") or {}).get("bundled")),
                          connector=(b.get("connect") or {}).get("connector"))
                     for b in cat["brokers"]],
         "manual": cat.get("manual"),
         "other": "A broker that isn't listed but has an MCP: draft --id <name> --url <its MCP URL>"})


def cmd_connect_cmd(a):
    b = catalog_entry(a.id)
    if not b:
        fail(f"'{a.id}' isn't in the catalog. For another MCP: claude mcp add --transport http <name> <url> --scope user")
    if (b.get("connect") or {}).get("connector"):
        c = b["connect"]
        out({"broker": a.id, "bundled": False, "command": None, "connector": True, "url": c["url"],
             "say": f"{b['short_name']}'s sign-in doesn't accept Claude Code's own sign-in, so it's added as a "
                    "claude.ai connector instead (once). It then shows up in Claude Code too.",
             "after": ["On claude.ai: Settings → Connectors → Add custom connector, URL " + c["url"] +
                       ", then Connect and sign in",
                       "Restart Claude Code (signed in with the same claude.ai account)",
                       "Then say 'continue broker setup'"],
             "auth": b.get("auth"), "prerequisites": b.get("prerequisites")})
        return
    if (b.get("connect") or {}).get("bundled"):
        srv = b["mcp_server"]
        out({"broker": a.id, "bundled": True, "command": None,
             "say": f"{b['short_name']}'s MCP comes with the plugin: nothing to add and no restart.",
             "after": [f"Run /mcp, pick plugin:ai-trader:{srv} (or a {b['short_name']} claude.ai connector if you "
                       "already have one) and Authenticate if it asks",
                       "Then say 'continue broker setup'"] if a.id != "zerodha" else [],
             "auth": b.get("auth"), "prerequisites": b.get("prerequisites")})
        return
    c = b.get("connect_alt") if a.alt and b.get("connect_alt") else b["connect"]
    cmd = f"claude mcp add --transport {c['transport']} --scope user {b['mcp_server']} {c['url']}"
    if c.get("header"):
        cmd += f' --header "{c["header"]}"'
    out({"broker": a.id, "command": cmd, "auth": b.get("auth"), "prerequisites": b.get("prerequisites"),
         "after": ["Restart Claude Code (the new server loads at start-up)",
                   f"Run /mcp, pick {b['mcp_server']} and Authenticate if it asks",
                   "Then say 'continue broker setup'"],
         "check_added": "claude mcp list"})


def cmd_list(a):
    act = engine.active_broker_id() if engine.CONFIG.exists() else engine.DEFAULT_BROKER
    profiles = []
    for root, kind in ((LIB, "ready-made"), (USER, "connected")):
        for p in sorted(root.glob("*.json")) if root.exists() else []:
            d = engine.read_json(p)
            if kind == "ready-made" and (USER / p.name).exists():
                continue  # the user's connected copy is listed instead
            k = "ready-made (connect to use)" if kind == "ready-made" and not d.get("confirmed") else kind
            profiles.append({"id": d["id"], "name": d.get("name"), "kind": k, "execution": d.get("execution"),
                             "protection": d.get("protection"), "tested": d.get("tested", False),
                             "confirmed": d.get("confirmed"), "active": d["id"] == act})
    drafts = sorted(p.stem for p in DRAFTS.glob("*.json")) if DRAFTS.exists() else []
    out({"active": act, "profiles": profiles, "drafts": drafts})


def cmd_draft(a):
    path = _draft_path(a.id)
    if path.exists() and not a.restart:
        d = engine.read_json(path)
        err, warn = check_profile(d)
        out({"status": "resumed", "draft": d, "errors": err, "warnings": warn, "notes_path": _notes_path(d, True)})
        return
    base = engine.read_json(LIB / f"{a.id}.json")
    cat = catalog_entry(a.id)
    if base:
        d = dict(base)
        if cat and not base.get("tested"):
            d["_catalog_hints"] = {k: cat.get(k) for k in ("prerequisites", "notes", "docs")}
        lib_notes = LIB / (base.get("notes_file") or f"{a.id}.md")
        if lib_notes.exists():
            DRAFTS.mkdir(parents=True, exist_ok=True)
            (DRAFTS / f"{a.id}.md").write_text(lib_notes.read_text())
    elif cat:
        known = cat.get("known_tools") if isinstance(cat.get("known_tools"), dict) else {}
        d = {"id": a.id, "name": cat["name"], "short_name": cat.get("short_name"), "mcp_server": cat["mcp_server"],
             "connect": cat["connect"], "login": {"tool": None, "check_tool": None, "daily": None, "how": cat.get("auth")},
             "execution": "mcp" if cat.get("can_trade") else "manual",
             "tools": {k: known.get(k) for k in JOBS},
             "protection": None, "protection_available": cat.get("protection_known") or [],
             "supports_tag": None, "charges": {}, "checks": [], "tested": False,
             "_catalog_hints": {k: cat.get(k) for k in ("protection_to_check", "prerequisites", "notes", "docs")}}
    else:
        d = {"id": a.id, "name": a.name or a.id.title(), "short_name": a.name or a.id.title(),
             "mcp_server": a.server or (a.id if a.url else None),
             "connect": {"transport": a.transport, "url": a.url} if a.url else None,
             "login": {"tool": None, "check_tool": None, "daily": None, "how": None},
             "execution": "mcp" if a.url else "manual", "tools": {k: None for k in JOBS},
             "protection": None, "protection_available": [], "supports_tag": None, "charges": {}, "checks": [],
             "tested": False}
    if a.manual:
        d["execution"] = "manual"
    if a.name:
        d["name"] = d["short_name"] = a.name
    d.pop("confirmed", None)
    d.pop("_path", None)
    save_draft(d)
    err, warn = check_profile(d)
    ready = bool(base)
    out({"status": "draft started", "ready_made": ready, "draft": d, "errors": err, "warnings": warn,
         "notes_path": _notes_path(d, True),
         "next": ("ready-made: have the user sign in (connect-cmd), check that every tool in the profile is in the "
                  "server's tool list (if one is missing or its inputs differ from the notes, match the tools "
                  "instead), run the read-only checks, confirm the pre-filled charges, then check -> review -> "
                  "confirm") if ready else
                 "connect the MCP if needed (connect-cmd), match its tools to the slots, run the read-only checks, "
                 "pick the protection level, ask the charges, then check -> review -> confirm"})


def cmd_set(a):
    d = load_draft(a.id)
    try:
        patch = json.loads(a.json)
    except json.JSONDecodeError as e:
        fail(f"--json is not valid JSON: {e}")
    if "id" in patch and patch["id"] != a.id:
        fail("the id can't change; start a new draft instead")
    merge(d, patch)
    d.pop("_reviewed", None)
    save_draft(d)
    err, warn = check_profile(d)
    out({"status": "saved", "errors": err, "warnings": warn})


def cmd_check(a):
    d = load_draft(a.id)
    err, warn = check_profile(d)
    out({"ok": not err, "errors": err, "warnings": warn, "notes_path": _notes_path(d, True),
         "notes_written": Path(_notes_path(d, True)).exists()})


def cmd_review(a):
    d = load_draft(a.id)
    err, warn = check_profile(d)
    if err:
        fail("fix these first", errors=err)
    notes = Path(_notes_path(d, True))
    if d["execution"] == "mcp" and not notes.exists():
        fail(f"write the exact tool use for this broker to {notes} first (arguments for each tool, from its schema)")
    rows, tool_rows, checks = review_lines(d)
    d["_reviewed"] = _fp(d)
    save_draft(d)
    out({"title": f"Broker: {d['name']}", "settings": rows, "tools": tool_rows, "read_only_checks": checks,
         "warnings": warn, "next": "show this to the user; on Confirm run: broker.py confirm --id " + a.id})


def _switch_blocked(new_id):
    held = [p["symbol"] for p in open_positions() if position_broker(p) != new_id]
    return held


def _activate(d, c):
    """Make d the active broker. An untested broker starts in PAPER."""
    res = {}
    c["broker"] = d["id"]
    if c.get("mode") == "LIVE" and not d.get("tested") and not d.get("first_live_order"):
        c["mode"] = "PAPER"
        res["mode_changed"] = "LIVE -> PAPER: a newly connected, untested broker starts in PAPER. " \
                              "Switch to LIVE with the trading-setup skill when you're ready."
    engine.write_json(engine.CONFIG, c)
    return res


def cmd_confirm(a):
    d = load_draft(a.id)
    err, _ = check_profile(d)
    if err:
        fail("fix these first", errors=err)
    if d.get("_reviewed") != _fp(d):
        fail("show the review screen first (broker.py review), then confirm")
    d = _public(d)
    d["confirmed"] = today()
    USER.mkdir(parents=True, exist_ok=True)
    engine.write_json(USER / f"{a.id}.json", d)
    notes = DRAFTS / f"{a.id}.md"
    if notes.exists():
        notes.replace(USER / f"{a.id}.md")
        d["notes_file"] = f"{a.id}.md"
        engine.write_json(USER / f"{a.id}.json", d)
    _draft_path(a.id).unlink()
    res = {"status": "confirmed", "broker": a.id, "profile": str(USER / f"{a.id}.json")}
    if not a.no_use:
        c = config()
        if not c:
            res["active"] = False
            res["note"] = "not set up yet: journal.py init --broker " + a.id
        else:
            held = _switch_blocked(a.id)
            if held:
                res["active"] = False
                res["note"] = f"saved, but not made active: the bot still has open positions at " \
                              f"{engine.active_broker_id()} ({', '.join(held)}). Switch when they're closed."
            else:
                res.update(_activate(d, c), active=True)
    out(res)


def cmd_use(a):
    d = engine.broker_profile(a.id)
    if not d or not d.get("confirmed"):
        fail(f"no confirmed profile '{a.id}'. Connect it first: broker.py draft --id {a.id}"
             + (" (ready-made: no tool matching needed)" if d else ""))
    c = config()
    if not c:
        fail("not set up yet: run the trading-setup skill")
    held = _switch_blocked(a.id)
    if held:
        fail(f"the bot has open positions at {engine.active_broker_id()} ({', '.join(held)}); their stops and "
             "orders live there. Close them (or let them exit) before switching brokers.")
    out(dict({"status": "active", "broker": a.id, "name": d.get("name")}, **_activate(d, c)))


def _summary(d):
    err, warn = check_profile(d)
    return {"broker": d["id"], "name": d.get("name"), "short_name": d.get("short_name"),
            "execution": d.get("execution"), "mcp_server": d.get("mcp_server"),
            "protection": d.get("protection"), "protection_words": protection_text(d),
            "kept_at_broker": d.get("protection") in engine.KEPT_AT_BROKER,
            "protection_expires_days": d.get("protection_expires_days"),
            "login": d.get("login"), "tools": d.get("tools"), "supports_tag": d.get("supports_tag"),
            "sl_order_args": d.get("sl_order_args"),
            "notes_file": _notes_path(d), "tested": d.get("tested", False), "confirmed": d.get("confirmed"),
            "first_live_order_pending": not d.get("tested") and not d.get("first_live_order")
            and d.get("execution") == "mcp",
            "charges": d.get("charges"), "problems": err, "warnings": warn}


def cmd_show(a):
    d = engine.broker_profile(a.id) if a.id else engine.broker_profile()
    if not d:
        if a.id and _draft_path(a.id).exists():
            return cmd_check(a)
        fail(f"no broker profile '{a.id or engine.active_broker_id()}'")
    out(dict(_summary(d), profile=_public(d)))


def cmd_capability(a):
    bid = engine.active_broker_id()
    d = engine.broker_profile(bid)
    if not d:
        out({"broker": bid, "ok": False, "block_reason": "broker_not_confirmed",
             "say": f"The active broker '{bid}' has no confirmed profile. Run the brokers skill to connect it."})
        return
    s = _summary(d)
    s["ok"] = not s["problems"] and bool(d.get("confirmed"))
    out(s)


def cmd_first_order_done(a):
    bid = engine.active_broker_id()
    path = USER / f"{bid}.json"
    d = engine.read_json(path)
    if not d:
        fail("only a connected (not ready-made) broker records this")
    d["first_live_order"] = today()
    engine.write_json(path, d)
    out({"status": "recorded", "broker": bid})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("catalog")
    p = sub.add_parser("connect-cmd")
    p.add_argument("--id", required=True)
    p.add_argument("--alt", action="store_true", help="the catalog's other connection route (e.g. an access-token header)")
    sub.add_parser("list")
    p = sub.add_parser("draft")
    p.add_argument("--id", required=True)
    p.add_argument("--manual", action="store_true")
    p.add_argument("--name")
    p.add_argument("--url")
    p.add_argument("--transport", default="http", choices=["http", "sse"])
    p.add_argument("--server", help="the MCP server's name in /mcp (default: the id)")
    p.add_argument("--restart", action="store_true", help="throw the old draft away")
    p = sub.add_parser("set")
    p.add_argument("--id", required=True)
    p.add_argument("--json", required=True)
    for name in ("check", "review"):
        sub.add_parser(name).add_argument("--id", required=True)
    p = sub.add_parser("confirm")
    p.add_argument("--id", required=True)
    p.add_argument("--no-use", action="store_true")
    sub.add_parser("use").add_argument("--id", required=True)
    sub.add_parser("show").add_argument("--id")
    sub.add_parser("capability")
    sub.add_parser("first-order-done")
    a = ap.parse_args()
    {"catalog": cmd_catalog, "connect-cmd": cmd_connect_cmd, "list": cmd_list, "draft": cmd_draft, "set": cmd_set,
     "check": cmd_check, "review": cmd_review, "confirm": cmd_confirm, "use": cmd_use, "show": cmd_show,
     "capability": cmd_capability, "first-order-done": cmd_first_order_done}[a.cmd](a)


if __name__ == "__main__":
    main()
