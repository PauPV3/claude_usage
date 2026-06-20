#!/usr/bin/env python3
"""
claude_usage.py — Claude Code usage summary

For each conversation (session) it shows:
  - Project
  - Cost in dollars (computed like ccusage, with correct cache prices)
  - Lines of code written/edited (Write + Edit + MultiEdit + NotebookEdit)
  - Files touched
  - Number of prompts (your messages)
  - Total tokens
  - Date of the last activity

And a grand TOTAL at the end.

Usage:
  python3 claude_usage.py              # sorted by cost (descending)
  python3 claude_usage.py --by lines   # sorted by lines written
  python3 claude_usage.py --by date    # sorted by date
  python3 claude_usage.py --top 10     # only the first 10
  python3 claude_usage.py --project hub  # filter projects containing "hub"

Time filters:
  python3 claude_usage.py --today      # today only
  python3 claude_usage.py --week       # last 7 days
  python3 claude_usage.py --month      # last 30 days
  python3 claude_usage.py --days 3     # last N days
  python3 claude_usage.py --since 2026-06-15            # from a date
  python3 claude_usage.py --until 2026-06-18            # until a date
  python3 claude_usage.py --since 2026-06-15 --until 2026-06-18  # range
"""

import json
import os
import sys
import glob
import datetime
from collections import defaultdict

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

# ---- colors (disabled when not a terminal) ----
if sys.stdout.isatty():
    C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m",
         "c": "\033[36m", "r": "\033[31m", "x": "\033[0m"}
else:
    C = {k: "" for k in ["b", "d", "g", "y", "c", "r", "x"]}


def count_lines(text):
    if not isinstance(text, str) or text == "":
        return 0
    return text.count("\n") + 1


# ---- prices per MILLION tokens (USD), as applied by Anthropic ----
#  in = input | out = output | cw5 = cache write 5min | cw1h = cache write 1h | cr = cache read
#  (Opus 4.8 is ~3x cheaper than classic Opus; validated against ccusage)
PRICES = {
    "opus":   {"in": 5.0,  "out": 25.0, "cw5": 6.25,  "cw1h": 10.0, "cr": 0.50},
    "sonnet": {"in": 3.0,  "out": 15.0, "cw5": 3.75,  "cw1h": 6.0,  "cr": 0.30},
    "haiku":  {"in": 1.0,  "out": 5.0,  "cw5": 1.25,  "cw1h": 2.0,  "cr": 0.10},
}


def price_for(model):
    if not model:
        return PRICES["opus"]
    m = model.lower()
    if "opus" in m:
        return PRICES["opus"]
    if "haiku" in m:
        return PRICES["haiku"]
    return PRICES["sonnet"]


def msg_cost(model, u):
    """Cost in USD of a single message, from its 'usage' block."""
    p = price_for(model)
    cc = u.get("cache_creation") or {}
    cw1h = cc.get("ephemeral_1h_input_tokens", 0)
    cw5 = cc.get("ephemeral_5m_input_tokens", 0)
    # if there's no breakdown, treat all cache creation as 5min
    if not cc:
        cw5 = u.get("cache_creation_input_tokens", 0)
    return (
        u.get("input_tokens", 0) * p["in"]
        + u.get("output_tokens", 0) * p["out"]
        + u.get("cache_read_input_tokens", 0) * p["cr"]
        + cw5 * p["cw5"]
        + cw1h * p["cw1h"]
    ) / 1_000_000


# home directory prefix, "mangled" the same way Claude Code does (/ -> -)
# Computed on the fly for EACH user/machine, so the script is portable.
_HOME_MANGLED = os.path.expanduser("~").replace(os.sep, "-")  # e.g. -Users-pau  or  -home-pau


def demangle(dirname):
    """Turn a Claude Code folder name into a readable project name.
    e.g. '-Users-pau-workspace-MY-APP' -> 'workspace/MY-APP' (or just 'MY-APP').
    Works on any machine because it derives the home of the current user."""
    name = dirname
    if name.startswith(_HOME_MANGLED):
        name = name[len(_HOME_MANGLED):]
    name = name.lstrip("-")
    return name or dirname.lstrip("-")


def parse_days(path, seen):
    """Read a .jsonl file and return data GROUPED BY DAY:
       { 'YYYY-MM-DD': {cost,tokens,lines,prompts, files:set, models:set} }
    'seen' is a shared set of (message.id, requestId) used for deduplication."""
    days = defaultdict(lambda: {"cost": 0.0, "tokens": 0, "lines": 0,
                                "prompts": 0, "files": set(), "models": set()})
    with open(path, "r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            msg = o.get("message") or {}
            if not isinstance(msg, dict):
                continue
            # dedup by billing key (message.id, requestId); fall back to uuid
            mid = msg.get("id")
            rid = o.get("requestId")
            dkey = (mid, rid) if mid and rid else o.get("uuid")
            if dkey:
                if dkey in seen:
                    continue
                seen.add(dkey)
            d = (o.get("timestamp") or "")[:10]
            if not d:
                continue
            day = days[d]
            role = msg.get("role")
            model = msg.get("model")
            if model:
                day["models"].add(model)
            u = msg.get("usage")
            if isinstance(u, dict):
                day["cost"] += msg_cost(model, u)
                day["tokens"] += (u.get("input_tokens", 0) + u.get("output_tokens", 0)
                                  + u.get("cache_read_input_tokens", 0)
                                  + u.get("cache_creation_input_tokens", 0))
            content = msg.get("content")
            if role == "user" and o.get("type") != "tool_result":
                if isinstance(content, str) and content.strip():
                    day["prompts"] += 1
                elif isinstance(content, list) and any(
                        isinstance(c, dict) and c.get("type") == "text" for c in content):
                    day["prompts"] += 1
            if isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict) or c.get("type") != "tool_use":
                        continue
                    name = c.get("name")
                    inp = c.get("input", {}) or {}
                    fp = inp.get("file_path") or inp.get("notebook_path")
                    if name == "Write":
                        day["lines"] += count_lines(inp.get("content", ""))
                    elif name == "Edit":
                        day["lines"] += count_lines(inp.get("new_string", ""))
                    elif name == "MultiEdit":
                        for e in inp.get("edits", []) or []:
                            day["lines"] += count_lines(e.get("new_string", ""))
                    elif name == "NotebookEdit":
                        day["lines"] += count_lines(inp.get("new_source", ""))
                    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit") and fp:
                        day["files"].add(fp)
    return days


def load_all():
    """Read ALL .jsonl files once. Returns a list of sessions:
       {project, sid, days}.  Deduplication is global across files."""
    seen = set()
    sessions = []
    paths = sorted(glob.glob(os.path.join(PROJECTS_DIR, "*", "*.jsonl")),
                   key=lambda p: os.path.getmtime(p))
    for path in paths:
        sid = os.path.splitext(os.path.basename(path))[0]
        project = demangle(os.path.basename(os.path.dirname(path)))
        days = parse_days(path, seen)
        if days:
            sessions.append({"project": project, "sid": sid, "days": days})
    return sessions


def compute(sessions, since=None, until=None, proj_filter=None, sort_by="cost"):
    """From the already-loaded sessions, aggregate each conversation within the
    [since, until] range and return (rows, totals)."""
    def in_range(d):
        if since and d < since:
            return False
        if until and d > until:
            return False
        return True

    rows = []
    for s in sessions:
        if proj_filter and proj_filter not in s["project"].lower():
            continue
        cost = tokens = lines = prompts = 0
        files = set()
        models = set()
        last = ""
        for d, v in s["days"].items():
            if not in_range(d):
                continue
            cost += v["cost"]; tokens += v["tokens"]; lines += v["lines"]
            prompts += v["prompts"]; files |= v["files"]; models |= v["models"]
            if d > last:
                last = d
        if not last:  # no activity within the range
            continue
        model = "opus" if any("opus" in m for m in models) else (
                "sonnet" if any("sonnet" in m for m in models) else "?")
        rows.append({"project": s["project"], "cost": cost, "tokens": tokens,
                     "lines": lines, "files": len(files), "prompts": prompts,
                     "date": last, "model": model})

    keymap = {"cost": "cost", "lines": "lines", "date": "date",
              "tokens": "tokens", "files": "files", "prompts": "prompts"}
    rows.sort(key=lambda r: r[keymap.get(sort_by, "cost")], reverse=(sort_by != "date"))
    totals = {
        "cost": sum(r["cost"] for r in rows),
        "lines": sum(r["lines"] for r in rows),
        "files": sum(r["files"] for r in rows),
        "prompts": sum(r["prompts"] for r in rows),
        "tokens": sum(r["tokens"] for r in rows),
        "n": len(rows),
    }
    return rows, totals


# =====================  TEXT MODE (with flags)  =====================
def render_text(rows, totals, period_label):
    hdr = (f"{'PROJECT':<26} {'DATE':<11} {'MODEL':<7} {'COST':>9} "
           f"{'LINES':>8} {'FILES':>5} {'PROMPTS':>8} {'TOKENS':>13}")
    print()
    print(C["b"] + "  CLAUDE CODE USAGE PER CONVERSATION" + C["x"]
          + C["d"] + f"   ({period_label})" + C["x"])
    print(C["d"] + "  " + "-" * len(hdr) + C["x"])
    print("  " + C["b"] + hdr + C["x"])
    print(C["d"] + "  " + "-" * len(hdr) + C["x"])
    for r in rows:
        cost_s = f"${r['cost']:.2f}"
        col = C["g"] if r["cost"] < 10 else (C["y"] if r["cost"] < 50 else C["r"])
        print(f"  {r['project'][:25]:<26} {r['date']:<11} {r['model']:<7} "
              f"{col}{cost_s:>9}{C['x']} {r['lines']:>8,} {r['files']:>5} "
              f"{r['prompts']:>8} {C['d']}{r['tokens']:>13,}{C['x']}")
    print(C["d"] + "  " + "-" * len(hdr) + C["x"])
    print(f"  {'TOTAL (' + str(totals['n']) + ' conversations)':<26} {'':<11} {'':<7} "
          f"{C['b']}${totals['cost']:>8.2f}{C['x']} {C['b']}{totals['lines']:>8,}{C['x']} "
          f"{totals['files']:>5} {totals['prompts']:>8} {totals['tokens']:>13,}")
    print()
    print(C["b"] + "  OVERALL SUMMARY" + C["x"])
    print(f"    Total cost ........... {C['r']}${totals['cost']:,.2f}{C['x']}")
    print(f"    Lines of code written  {C['c']}{totals['lines']:,}{C['x']}")
    print(f"    Files touched ........ {totals['files']:,}")
    print(f"    Prompts (yours) ...... {totals['prompts']:,}")
    print(f"    Total tokens ......... {totals['tokens']:,}")
    if totals["lines"]:
        print(f"    Cost per 1,000 lines   ${totals['cost'] / totals['lines'] * 1000:,.2f}")
    print()


def resolve_period(args, today):
    since = until = None
    label = "all history"
    if "--since" in args:
        since = args[args.index("--since") + 1]
    if "--until" in args:
        until = args[args.index("--until") + 1]
    if "--today" in args:
        since = today.isoformat(); label = "today"
    elif "--week" in args:
        since = (today - datetime.timedelta(days=7)).isoformat(); label = "last 7 days"
    elif "--month" in args:
        since = (today - datetime.timedelta(days=30)).isoformat(); label = "last 30 days"
    elif "--days" in args:
        n = int(args[args.index("--days") + 1])
        since = (today - datetime.timedelta(days=n)).isoformat(); label = f"last {n} days"
    if "--since" in args or "--until" in args:
        label = f"{since or 'start'} -> {until or 'today'}"
    return since, until, label


# =====================  INTERACTIVE MODE (arrow keys)  =====================
def run_tui(sessions, today):
    import curses

    periods = [
        ("All history",  None, None),
        ("Today",        today.isoformat(), None),
        ("Yesterday",    (today - datetime.timedelta(days=1)).isoformat(),
                         (today - datetime.timedelta(days=1)).isoformat()),
        ("Last 7 days",  (today - datetime.timedelta(days=7)).isoformat(), None),
        ("Last 30 days", (today - datetime.timedelta(days=30)).isoformat(), None),
        ("This month",   today.replace(day=1).isoformat(), None),
        ("This year",    today.replace(month=1, day=1).isoformat(), None),
    ]
    sorts = [("cost", "cost"), ("lines", "lines"), ("date", "date"),
             ("tokens", "tokens"), ("prompts", "prompts")]

    def draw(stdscr, pi, si):
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        A = curses.A_BOLD
        CY = curses.color_pair(1); GR = curses.color_pair(2)
        YE = curses.color_pair(3); RE = curses.color_pair(4); DM = curses.color_pair(5)

        def put(y, x, s, attr=0):
            if y < 0 or y >= h or x >= w:
                return
            try:
                stdscr.addstr(y, x, str(s)[:max(0, w - x - 1)], attr)
            except curses.error:
                pass

        plabel, since, until = periods[pi]
        sort_label, sort_key = sorts[si]
        rows, t = compute(sessions, since, until, None, sort_key)

        put(0, 2, "CLAUDE CODE USAGE", A | CY)
        put(0, 22, "<- ->  period   |   ^ v  sort   |   q  quit", DM)
        # period chips
        x = 2
        for i, (name, _, _) in enumerate(periods):
            chip = f" {name} "
            if x + len(chip) + 2 >= w:
                break
            put(1, x, chip, (curses.A_REVERSE | A) if i == pi else DM)
            x += len(chip) + 1
        put(2, 2, f"Sorted by: {sort_label}", YE | A)

        hdr = (f"{'PROJECT':<24} {'DATE':<11} {'MODEL':<6} {'COST':>9} "
               f"{'LINES':>7} {'FILES':>5} {'PROMPTS':>7} {'TOKENS':>13}")
        put(4, 2, hdr, A)
        put(5, 2, "-" * len(hdr), DM)
        maxrows = max(1, h - 12)
        for idx, r in enumerate(rows[:maxrows]):
            col = GR if r["cost"] < 10 else (YE if r["cost"] < 50 else RE)
            line = (f"{r['project'][:23]:<24} {r['date']:<11} {r['model']:<6} "
                    f"{('$%.2f' % r['cost']):>9} {r['lines']:>7,} {r['files']:>5} "
                    f"{r['prompts']:>7} {r['tokens']:>13,}")
            put(6 + idx, 2, line, col)
        yb = 6 + min(len(rows), maxrows) + 1
        put(yb, 2, "-" * len(hdr), DM)
        tot = (f"{'TOTAL (' + str(t['n']) + ' conversations)':<24} {'':<11} {'':<6} "
               f"{('$%.2f' % t['cost']):>9} {t['lines']:>7,} {t['files']:>5} "
               f"{t['prompts']:>7} {t['tokens']:>13,}")
        put(yb + 1, 2, tot, A)
        cpl = (t["cost"] / t["lines"] * 1000) if t["lines"] else 0
        put(yb + 3, 2,
            f"Total cost ${t['cost']:,.2f}    |    {t['lines']:,} lines written"
            f"    |    ${cpl:,.2f} per 1,000 lines", CY | A)
        stdscr.refresh()

    def loop(stdscr):
        curses.curs_set(0)
        if curses.has_colors():
            curses.start_color(); curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_RED, -1)
            curses.init_pair(5, curses.COLOR_WHITE, -1)
        pi = 0; si = 0
        while True:
            draw(stdscr, pi, si)
            k = stdscr.getch()
            if k in (ord("q"), ord("Q"), 27):
                break
            elif k in (curses.KEY_RIGHT, ord("l")):
                pi = (pi + 1) % len(periods)
            elif k in (curses.KEY_LEFT, ord("h")):
                pi = (pi - 1) % len(periods)
            elif k in (curses.KEY_DOWN, curses.KEY_UP, ord("s")):
                si = (si + 1) % len(sorts)

    curses.wrapper(loop)


def main():
    args = sys.argv[1:]
    today = datetime.date.today()

    # No flags and a real terminal -> interactive arrow-key menu
    interactive = (not args or "--menu" in args) and sys.stdout.isatty()

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    print("  Reading Claude Code history...", file=sys.stderr)
    sessions = load_all()

    if interactive:
        try:
            run_tui(sessions, today)
        except Exception as e:
            print(f"  (Could not open the interactive menu: {e}. Showing text.)",
                  file=sys.stderr)
            rows, totals = compute(sessions, sort_by="cost")
            render_text(rows, totals, "all history")
        return

    # ---- text mode (with flags) ----
    sort_by = args[args.index("--by") + 1] if "--by" in args else "cost"
    proj_filter = args[args.index("--project") + 1].lower() if "--project" in args else None
    top = int(args[args.index("--top") + 1]) if "--top" in args else None
    since, until, label = resolve_period(args, today)
    rows, totals = compute(sessions, since, until, proj_filter, sort_by)
    if top:
        rows = rows[:top]
        totals = {"cost": sum(r["cost"] for r in rows),
                  "lines": sum(r["lines"] for r in rows),
                  "files": sum(r["files"] for r in rows),
                  "prompts": sum(r["prompts"] for r in rows),
                  "tokens": sum(r["tokens"] for r in rows), "n": len(rows)}
    render_text(rows, totals, label)


if __name__ == "__main__":
    main()
