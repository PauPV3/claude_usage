#!/usr/bin/env python3
"""
claude_usage.py — Claude Code usage summary

For each PROJECT it shows:
  - Cost in dollars (what this traffic would have cost on the API)
  - Active model usage time (prompt -> last reply, per turn; idle time excluded)
  - Models actually used
  - Lines of code written/edited (Write + Edit + MultiEdit + NotebookEdit)
  - Files touched (distinct, across the whole selection)
  - Number of prompts (your messages)
  - Total tokens

And a grand TOTAL at the end.

Usage:
  python3 claude_usage.py              # sorted by cost (descending)
  python3 claude_usage.py --by time    # sorted by active usage time
  python3 claude_usage.py --by lines   # sorted by lines written
  python3 claude_usage.py --by date    # sorted by date
  python3 claude_usage.py --top 10     # only the first 10
  python3 claude_usage.py --project hub  # filter projects containing "hub"
  python3 claude_usage.py --models     # per-model breakdown with the rates used
  python3 claude_usage.py --prices     # print the price table and exit

Time filters:
  python3 claude_usage.py --today      # today only
  python3 claude_usage.py --yesterday  # yesterday only
  python3 claude_usage.py --week       # last 7 days
  python3 claude_usage.py --month      # last 30 days
  python3 claude_usage.py --days 3     # last N days
  python3 claude_usage.py --since 2026-06-15            # from a date
  python3 claude_usage.py --until 2026-06-18            # until a date
  python3 claude_usage.py --since 2026-06-15 --until 2026-06-18  # range
"""

import datetime
import json
import os
import re
import sys
from collections import defaultdict

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

# ---- colors (disabled when not a terminal) ----
if sys.stdout.isatty():
    C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m",
         "c": "\033[36m", "r": "\033[31m", "x": "\033[0m"}
else:
    C = {k: "" for k in ["b", "d", "g", "y", "c", "r", "x"]}


# =====================  PRICES  =====================
#
# Source: Anthropic's pricing page, read 2026-07-30.
#
# Two rules make this table safe, and both exist because the previous version
# of this script got them wrong:
#
# 1. THERE IS NO DEFAULT. It used to end with "…otherwise it's a Sonnet", so
#    `claude-fable-5` — which contains neither "opus" nor "haiku" — was billed
#    at $3/$15 instead of $10/$50. A model we cannot name now costs 0 and is
#    REPORTED as unpriced, because a visible hole is recoverable and an
#    invented number is not.
# 2. THE CACHE RATES ARE DERIVED, not typed out. One base price plus three
#    published multipliers is three fewer chances of a typo per row.
CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0
CACHE_READ = 0.1
GEO_US_MULTIPLIER = 1.1          # inference_geo: "us"
WEB_SEARCH_USD_PER_1K = 10.0     # billed per search, on top of tokens
PRICES_UPDATED = "2026-07-30"

# model id -> (label, {speed: ((since, until, input_per_mtok, output_per_mtok), ...)})
# `since`/`until` are inclusive ISO days, or None for "no bound".
MODELS = {
    "claude-fable-5":        ("Fable 5",   {"standard": ((None, None, 10.0, 50.0),)}),
    "claude-mythos-5":       ("Mythos 5",  {"standard": ((None, None, 10.0, 50.0),)}),
    "claude-mythos-preview": ("Mythos Pv", {"standard": ((None, None, 10.0, 50.0),)}),
    # Fast mode is a premium tier, not a flag: Opus 5 and 4.8 double on both
    # sides with `speed: "fast"`. Ignoring it halves a /fast session.
    "claude-opus-5":         ("Opus 5",    {"standard": ((None, None, 5.0, 25.0),),
                                            "fast":     ((None, None, 10.0, 50.0),)}),
    "claude-opus-4-8":       ("Opus 4.8",  {"standard": ((None, None, 5.0, 25.0),),
                                            "fast":     ((None, None, 10.0, 50.0),)}),
    "claude-opus-4-7":       ("Opus 4.7",  {"standard": ((None, None, 5.0, 25.0),)}),
    "claude-opus-4-6":       ("Opus 4.6",  {"standard": ((None, None, 5.0, 25.0),)}),
    "claude-opus-4-5":       ("Opus 4.5",  {"standard": ((None, None, 5.0, 25.0),)}),
    "claude-opus-4-1":       ("Opus 4.1",  {"standard": ((None, None, 15.0, 75.0),)}),
    "claude-opus-4-0":       ("Opus 4",    {"standard": ((None, None, 15.0, 75.0),)}),
    # Sonnet 5 is introductory-priced through 2026-08-31. The rate therefore
    # depends on the day the tokens were billed, not on today.
    "claude-sonnet-5":       ("Sonnet 5",  {"standard": ((None, "2026-08-31", 2.0, 10.0),
                                                         ("2026-09-01", None, 3.0, 15.0))}),
    "claude-sonnet-4-6":     ("Sonnet 4.6", {"standard": ((None, None, 3.0, 15.0),)}),
    "claude-sonnet-4-5":     ("Sonnet 4.5", {"standard": ((None, None, 3.0, 15.0),)}),
    "claude-sonnet-4-0":     ("Sonnet 4",  {"standard": ((None, None, 3.0, 15.0),)}),
    "claude-haiku-4-5":      ("Haiku 4.5", {"standard": ((None, None, 1.0, 5.0),)}),
    "claude-haiku-3-5":      ("Haiku 3.5", {"standard": ((None, None, 0.8, 4.0),)}),
}

# Bare names Claude Code writes into the transcript, plus the pre-4.6 dotted
# forms. Declared, never inferred — `"opus" in name` is the bug this replaces.
ALIASES = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
    "fable": "claude-fable-5",
    "mythos": "claude-mythos-5",
    "claude-opus-4-5-20251101": "claude-opus-4-5",
    "claude-opus-4-1-20250805": "claude-opus-4-1",
    "claude-opus-4-20250514": "claude-opus-4-0",
    "claude-sonnet-4-5-20250929": "claude-sonnet-4-5",
    "claude-sonnet-4-20250514": "claude-sonnet-4-0",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "claude-3-5-haiku-20241022": "claude-haiku-3-5",
}

# Claude Code fabricates these locally — an interrupt notice, a hook's output.
# Nobody bills them. Zero on purpose, and NOT "unknown": a warning that is
# always on screen is a warning nobody reads.
FREE_MODELS = frozenset(["<synthetic>", "synthetic", ""])

# `claude-opus-4-5-20251101` -> `claude-opus-4-5`, but only after the two exact
# lookups above have failed, and only for a trailing 8-digit date.
_DATED = re.compile(r"^(?P<base>.+)-\d{8}$")

EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def parse_ts(raw):
    """`2026-07-30T13:18:24.590Z` -> datetime, or None."""
    if not raw:
        return None
    raw = raw.replace("Z", "")
    fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in raw else "%Y-%m-%dT%H:%M:%S"
    try:
        return datetime.datetime.strptime(raw[:26], fmt)
    except Exception:
        return None


def format_time(seconds):
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%dh %dm" % (hours, minutes)
    if minutes:
        return "%dm %ds" % (minutes, secs)
    return "%ds" % secs


def resolve_price(model, speed="standard", day="9999-12-31"):
    """(label, speed, input_per_mtok, output_per_mtok, known, free).

    `day` is the day the tokens were billed — not today — because a price can
    change underneath history (Sonnet 5's introductory rate does).
    """
    name = (model or "").strip()
    if name in FREE_MODELS:
        return ("synthetic", "standard", 0.0, 0.0, True, True)

    lowered = name.lower()
    canonical = lowered if lowered in MODELS else ALIASES.get(lowered)
    if canonical is None:
        dated = _DATED.match(lowered)
        if dated:
            base = dated.group("base")
            canonical = base if base in MODELS else ALIASES.get(base)
    if canonical is None or canonical not in MODELS:
        # Deliberate dead end. Adding a guess here is the whole bug.
        return (name or "?", speed or "standard", 0.0, 0.0, False, False)

    label, tiers = MODELS[canonical]
    want = (speed or "standard").lower()
    # A speed the model does not sell falls back to its standard tier rather
    # than to `unknown`: Opus 4.6 accepts `speed: "fast"` and bills it standard.
    if want not in tiers:
        want = "standard"
    for since, until, price_in, price_out in tiers[want]:
        if since and day < since:
            continue
        if until and day > until:
            continue
        return (label, want, price_in, price_out, True, False)
    # The model exists but we cannot say what it cost then. Same rule as an
    # unknown model: say so, do not extrapolate.
    return (label, want, 0.0, 0.0, False, False)


def bucket_cost(counters, model, speed, day):
    """USD for one (day, model, speed) bucket, split by what it was spent on."""
    label, speed, price_in, price_out, known, free = resolve_price(model, speed, day)
    if not known:
        return {"total": 0.0, "input": 0.0, "output": 0.0,
                "cache_write": 0.0, "cache_read": 0.0, "web": 0.0}

    geo = GEO_US_MULTIPLIER if counters["geo"] == "us" else 1.0
    per_token = geo / 1_000_000
    price_5m = price_in * CACHE_WRITE_5M
    price_1h = price_in * CACHE_WRITE_1H
    price_read = price_in * CACHE_READ

    out = {
        "input": counters["input"] * price_in * per_token,
        "output": counters["output"] * price_out * per_token,
        "cache_write": (counters["cw5"] * price_5m + counters["cw1h"] * price_1h) * per_token,
        "cache_read": counters["cread"] * price_read * per_token,
        # Billed per search, not per token: geo does not apply.
        "web": counters["searches"] * WEB_SEARCH_USD_PER_1K / 1000,
    }
    out["total"] = sum(out.values())
    return out


# =====================  READING THE TRANSCRIPTS  =====================

def count_lines(text):
    if not isinstance(text, str) or text == "":
        return 0
    return text.count("\n") + 1


# Home directory prefix, "mangled" the same way Claude Code does (/ -> -).
# Computed on the fly for EACH user/machine, so the script is portable.
_HOME_MANGLED = os.path.expanduser("~").replace(os.sep, "-")


def demangle(dirname):
    """Turn a Claude Code folder name into a readable project name."""
    name = dirname
    if name.startswith(_HOME_MANGLED):
        name = name[len(_HOME_MANGLED):]
    name = name.lstrip("-")
    return name or dirname.lstrip("-")


def find_transcripts():
    """Every transcript, with the project and session it belongs to.

    THIS RECURSES, AND THAT IS THE POINT. This used to be a one-level glob of
    `~/.claude/projects/*/*.jsonl`, which misses every subagent and workflow
    transcript — they live at
    `<project>/<session>/subagents/[workflows/<wf>/]agent-*.jsonl`, two to four
    levels down. Measured on one real history: 25 files seen, 124 missed;
    775M tokens counted, 1157M actually spent. A third of the tokens and more
    than half the messages were invisible, and the gap grows with every fan-out.

    A subagent is attributed to the session that SPAWNED it: its cost is part
    of the conversation you started, and a list of anonymous `agent-a7bc89…`
    rows answers no question anyone has.
    """
    found = []
    if not os.path.isdir(PROJECTS_DIR):
        return found
    for project_dir in sorted(os.listdir(PROJECTS_DIR)):
        project_path = os.path.join(PROJECTS_DIR, project_dir)
        if not os.path.isdir(project_path):
            continue
        for dirpath, dirnames, filenames in os.walk(project_path):
            dirnames.sort()
            rel = os.path.relpath(dirpath, project_path)
            for filename in sorted(filenames):
                if not filename.endswith(".jsonl"):
                    continue
                full = os.path.join(dirpath, filename)
                if rel == ".":
                    session, kind = os.path.splitext(filename)[0], "main"
                else:
                    session, kind = rel.split(os.sep)[0], "subagent"
                found.append((full, project_dir, session, kind))
    return found


def new_counters():
    return {"input": 0, "output": 0, "cread": 0, "cw5": 0, "cw1h": 0,
            "messages": 0, "searches": 0, "geo": ""}


def new_day():
    # `usage` is keyed by (model, speed, kind) so a conversation that used two
    # models is priced as two, instead of being collapsed to whichever one won
    # a substring test. That collapsing was the "different models add up as the
    # same model" complaint.
    #
    # `kind` ("main"/"subagent") is part of the KEY, not a flag on the side: a
    # main turn and a subagent turn on the same model, same day, share every
    # other coordinate, so anything coarser cannot tell their costs apart. A
    # first attempt tagged the bucket instead and charged the whole thing to
    # subagents — 52% where the real figure is 34%.
    return {"usage": defaultdict(new_counters), "prompts": 0, "lines": 0,
            "files": set(), "time": 0.0}


def parse_file(path, seen, days, kind):
    """Read one .jsonl into `days`, deduplicating against the shared `seen`."""
    # Active usage time: a turn opens on a user-side message and closes on the
    # assistant reply, so the measure is prompt -> answer. Time spent reading,
    # typing or away lands BETWEEN turns and is never counted.
    #
    # This runs after the dedup check on purpose. A forked session copies its
    # history forward, so timing the raw stream would re-count hours that were
    # already spent in the original conversation — the same double count the
    # billing keys exist to stop.
    turn_start = None
    turn_seconds = 0.0
    turn_day = None

    try:
        handle = open(path, "r", errors="ignore")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue

            # Deduplicate by billing key. This has to be GLOBAL across files:
            # a resumed or forked session copies its history forward, so the
            # same message really does appear in more than one file. Measured
            # on one real history: 5116 billable records, 2532 distinct keys.
            mid, rid = msg.get("id"), obj.get("requestId")
            key = (mid, rid) if mid and rid else obj.get("uuid")
            if key:
                if key in seen:
                    continue
                seen.add(key)

            stamp = obj.get("timestamp") or ""
            day_key = stamp[:10]
            if not day_key:
                continue
            day = days[day_key]

            role = msg.get("role")
            kind_of = obj.get("type")
            content = msg.get("content")

            stamped = parse_ts(stamp)
            if stamped is not None:
                if kind_of == "user" or role == "user":
                    if turn_start is not None and turn_day is not None:
                        days[turn_day]["time"] += turn_seconds
                    turn_start, turn_day, turn_seconds = stamped, day_key, 0.0
                elif (kind_of == "assistant" or role == "assistant") and turn_start is not None:
                    elapsed = (stamped - turn_start).total_seconds()
                    if elapsed > 0:
                        # The LAST reply of the turn, not the sum of the replies:
                        # they overlap in wall-clock time.
                        turn_seconds = max(turn_seconds, elapsed)
            if role == "user" and obj.get("type") != "tool_result":
                if isinstance(content, str) and content.strip():
                    day["prompts"] += 1
                elif isinstance(content, list) and any(
                        isinstance(c, dict) and c.get("type") == "text" for c in content):
                    day["prompts"] += 1

            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue

            model = msg.get("model") or ""
            speed = usage.get("speed") or "standard"
            counters = day["usage"][(model, speed, kind)]
            counters["messages"] += 1
            counters["input"] += usage.get("input_tokens", 0) or 0
            counters["output"] += usage.get("output_tokens", 0) or 0
            counters["cread"] += usage.get("cache_read_input_tokens", 0) or 0
            geo = usage.get("inference_geo") or ""
            if geo and geo != "not_available":
                counters["geo"] = geo

            # The 5m/1h split matters: a 1-hour cache write is 2x base input
            # and a 5-minute one is 1.25x. Without the breakdown the only safe
            # assumption is the cheaper one, which understates.
            creation = usage.get("cache_creation")
            if isinstance(creation, dict) and creation:
                counters["cw5"] += creation.get("ephemeral_5m_input_tokens", 0) or 0
                counters["cw1h"] += creation.get("ephemeral_1h_input_tokens", 0) or 0
            else:
                counters["cw5"] += usage.get("cache_creation_input_tokens", 0) or 0

            tools = usage.get("server_tool_use")
            if isinstance(tools, dict):
                counters["searches"] += tools.get("web_search_requests", 0) or 0

            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict) or part.get("type") != "tool_use":
                        continue
                    name = part.get("name")
                    if name not in EDIT_TOOLS:
                        continue
                    payload = part.get("input") or {}
                    if not isinstance(payload, dict):
                        continue
                    if name == "Write":
                        day["lines"] += count_lines(payload.get("content", ""))
                    elif name == "Edit":
                        day["lines"] += count_lines(payload.get("new_string", ""))
                    elif name == "MultiEdit":
                        for edit in payload.get("edits") or []:
                            if isinstance(edit, dict):
                                day["lines"] += count_lines(edit.get("new_string", ""))
                    elif name == "NotebookEdit":
                        day["lines"] += count_lines(payload.get("new_source", ""))
                    target = payload.get("file_path") or payload.get("notebook_path")
                    if target:
                        # The PATH, not a count: counting distinct files
                        # requires holding the files. Summing per-session
                        # counts reported a file edited in three sessions as
                        # three files.
                        day["files"].add(target)

    # The turn still open when the file ends is real time too.
    if turn_start is not None and turn_day is not None:
        days[turn_day]["time"] += turn_seconds


def load_all():
    """Read every transcript once. Returns a list of {project, sid, days}."""
    seen = set()
    grouped = {}
    order = []
    for path, project_dir, session, kind in find_transcripts():
        key = (project_dir, session)
        if key not in grouped:
            grouped[key] = defaultdict(new_day)
            order.append(key)
        parse_file(path, seen, grouped[key], kind)

    sessions = []
    for key in order:
        days = grouped[key]
        if days:
            sessions.append({"project": demangle(key[0]), "sid": key[1], "days": days})
    return sessions


# =====================  AGGREGATION  =====================

def compute(sessions, since=None, until=None, proj_filter=None, sort_by="cost"):
    """Aggregate within [since, until]. Returns (rows, totals, models, unknown).

    One row per PROJECT: every session of the same project is summed, because
    «how much has this project cost me» is the question, and a project worked on
    across nine conversations was nine rows that had to be added up by eye.
    """
    def in_range(day):
        if since and day < since:
            return False
        if until and day > until:
            return False
        return True

    rows = []
    all_files = set()
    projects = {}
    per_model = defaultdict(lambda: {"cost": 0.0, "tokens": 0, "messages": 0,
                                     "rate": (0.0, 0.0), "known": True, "free": False})
    unknown = defaultdict(lambda: {"tokens": 0, "messages": 0})
    parts = {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0, "web": 0.0}
    subagent_cost = 0.0

    for session in sessions:
        if proj_filter and proj_filter not in session["project"].lower():
            continue
        row = projects.get(session["project"])
        if row is None:
            row = projects[session["project"]] = {
                "project": session["project"], "cost": 0.0, "tokens": 0, "lines": 0,
                "prompts": 0, "messages": 0, "time": 0.0, "files": set(),
                "labels": {}, "date": "", "sessions": 0,
            }
        cost = tokens = lines = prompts = messages = 0
        seconds = 0.0
        files = set()
        labels = row["labels"]
        last = ""

        for day_key, day in session["days"].items():
            if not in_range(day_key):
                continue
            lines += day["lines"]
            prompts += day["prompts"]
            seconds += day["time"]
            files |= day["files"]
            if day_key > last:
                last = day_key

            for (model, speed, kind), counters in day["usage"].items():
                label, tier, price_in, price_out, known, free = resolve_price(
                    model, speed, day_key)
                money = bucket_cost(counters, model, speed, day_key)
                bucket_tokens = (counters["input"] + counters["output"]
                                 + counters["cread"] + counters["cw5"] + counters["cw1h"])
                cost += money["total"]
                tokens += bucket_tokens
                messages += counters["messages"]
                for part in parts:
                    parts[part] += money[part]
                if kind == "subagent":
                    subagent_cost += money["total"]

                shown = label if tier == "standard" else label + " fast"
                if not free:
                    labels[shown] = None
                entry = per_model[shown]
                entry["cost"] += money["total"]
                entry["tokens"] += bucket_tokens
                entry["messages"] += counters["messages"]
                entry["rate"] = (price_in, price_out)
                entry["known"] = known
                entry["free"] = free
                if not known and not free:
                    unknown[model or "?"]["tokens"] += bucket_tokens
                    unknown[model or "?"]["messages"] += counters["messages"]

        if not last:  # no activity within the range
            continue
        all_files |= files
        row["cost"] += cost
        row["tokens"] += tokens
        row["lines"] += lines
        row["prompts"] += prompts
        row["messages"] += messages
        row["time"] += seconds
        row["files"] |= files
        row["sessions"] += 1
        if last > row["date"]:
            row["date"] = last

    for row in projects.values():
        if not row["date"]:
            continue
        rows.append({"project": row["project"], "cost": row["cost"], "tokens": row["tokens"],
                     "lines": row["lines"], "files": len(row["files"]),
                     "prompts": row["prompts"], "messages": row["messages"],
                     "time": row["time"], "date": row["date"],
                     "sessions": row["sessions"],
                     "model": " + ".join(sorted(row["labels"])) or "-"})

    keymap = {"cost": "cost", "time": "time", "lines": "lines", "date": "date",
              "tokens": "tokens", "files": "files", "prompts": "prompts"}
    rows.sort(key=lambda r: r[keymap.get(sort_by, "cost")], reverse=(sort_by != "date"))

    totals = {
        "cost": sum(r["cost"] for r in rows),
        "lines": sum(r["lines"] for r in rows),
        # DISTINCT across every session in the selection, which is why the
        # paths are kept rather than a per-session count.
        "files": len(all_files),
        "prompts": sum(r["prompts"] for r in rows),
        "tokens": sum(r["tokens"] for r in rows),
        "messages": sum(r["messages"] for r in rows),
        "time": sum(r["time"] for r in rows),
        "sessions": sum(r["sessions"] for r in rows),
        "n": len(rows),
        "parts": parts,
        "subagent_cost": subagent_cost,
    }
    models = sorted(per_model.items(), key=lambda kv: -kv[1]["cost"])
    return rows, totals, models, dict(unknown)


# =====================  TEXT MODE  =====================

def project_width(rows, term_w, fixed):
    longest = max((len(r["project"]) for r in rows), default=0)
    avail = max(10, term_w - 2 - fixed)
    return max(len("PROJECT"), min(longest, avail))


def model_width(rows, cap=30):
    longest = max((len(r["model"]) for r in rows), default=0)
    return max(len("MODELS"), min(longest, cap))


def fit(text, width):
    return text if len(text) <= width else text[:width - 1] + "…"


def render_models(models, unknown):
    print(C["b"] + "  PER MODEL" + C["x"]
          + C["d"] + "   (rates per million tokens, as of %s)" % PRICES_UPDATED + C["x"])
    print(C["d"] + "  " + "-" * 66 + C["x"])
    print("  " + C["b"] + "%-16s %14s %14s %16s" % ("MODEL", "IN/OUT", "TOKENS", "COST") + C["x"])
    for label, entry in models:
        if entry["free"]:
            rate = "free"
        elif not entry["known"]:
            rate = "UNPRICED"
        else:
            rate = "$%g/$%g" % entry["rate"]
        colour = C["r"] if not entry["known"] and not entry["free"] else ""
        reset = C["x"] if colour else ""
        print("  %s%-16s %14s %14s %16s%s" % (
            colour, fit(label, 16), rate, "{:,}".format(entry["tokens"]),
            "${:,.2f}".format(entry["cost"]), reset))
    print()
    if unknown:
        print(C["r"] + "  These models have NO known price. Their tokens are NOT in the total:" + C["x"])
        for name, entry in sorted(unknown.items()):
            print("    %s — %s tokens, %d messages" % (
                name, "{:,}".format(entry["tokens"]), entry["messages"]))
        print(C["d"] + "    Add them to MODELS in this file to price them." + C["x"])
        print()


def render_text(rows, totals, models, unknown, period_label, show_models):
    import shutil
    term_w = shutil.get_terminal_size((120, 24)).columns
    mw = model_width(rows)
    pw = project_width(rows, term_w, 11 + mw + 10 + 9 + 8 + 5 + 8 + 13 + 8)
    hdr = ("%-*s %-11s %-*s %9s %9s %8s %5s %8s %13s"
           % (pw, "PROJECT", "DATE", mw, "MODELS", "COST", "TIME", "LINES", "FILES",
              "PROMPTS", "TOKENS"))
    print()
    print(C["b"] + "  CLAUDE CODE USAGE PER PROJECT" + C["x"]
          + C["d"] + "   (%s)" % period_label + C["x"])
    print(C["d"] + "  " + "-" * len(hdr) + C["x"])
    print("  " + C["b"] + hdr + C["x"])
    print(C["d"] + "  " + "-" * len(hdr) + C["x"])
    for r in rows:
        colour = C["g"] if r["cost"] < 10 else (C["y"] if r["cost"] < 50 else C["r"])
        print("  %-*s %-11s %-*s %s%9s%s %9s %8s %5d %8d %s%13s%s" % (
            pw, fit(r["project"], pw), r["date"], mw, fit(r["model"], mw),
            colour, "${:,.2f}".format(r["cost"]), C["x"], format_time(r["time"]),
            "{:,}".format(r["lines"]), r["files"], r["prompts"],
            C["d"], "{:,}".format(r["tokens"]), C["x"]))
    print(C["d"] + "  " + "-" * len(hdr) + C["x"])
    print("  %-*s %-11s %-*s %s%9s%s %s%9s%s %s%8s%s %5d %8d %13s" % (
        pw, fit("TOTAL (%d projects, %d conversations)" % (
            totals["n"], totals["sessions"]), pw), "", mw, "",
        C["b"], "${:,.2f}".format(totals["cost"]), C["x"],
        C["b"], format_time(totals["time"]), C["x"],
        C["b"], "{:,}".format(totals["lines"]), C["x"],
        totals["files"], totals["prompts"], "{:,}".format(totals["tokens"])))
    print()

    parts = totals["parts"]
    print(C["b"] + "  OVERALL SUMMARY" + C["x"])
    print("    Cost if this had been API  %s%s%s" % (
        C["r"], "${:,.2f}".format(totals["cost"]), C["x"]))
    if totals["cost"] > 0:
        # Where the money actually goes. On a real history cache reads are
        # around two thirds of the bill, which a single total cannot say.
        for name, key in (("cache reads", "cache_read"), ("cache writes", "cache_write"),
                          ("output", "output"), ("input", "input"), ("web search", "web")):
            value = parts[key]
            if value <= 0:
                continue
            print("      %-24s %12s  (%.0f%%)" % (
                name, "${:,.2f}".format(value), 100 * value / totals["cost"]))
        if totals["subagent_cost"] > 0:
            print("    Of which subagents ....... %s  (%.0f%%)" % (
                "${:,.2f}".format(totals["subagent_cost"]),
                100 * totals["subagent_cost"] / totals["cost"]))
    print("    Active usage time ........ %s%s%s" % (
        C["c"], format_time(totals["time"]), C["x"]))
    if totals["time"] > 0:
        print("    Cost per hour ............ %s" % "${:,.2f}".format(
            totals["cost"] / (totals["time"] / 3600)))
    print("    Lines of code written .... %s%s%s" % (
        C["c"], "{:,}".format(totals["lines"]), C["x"]))
    print("    Files touched (distinct) . %s" % "{:,}".format(totals["files"]))
    print("    Prompts (yours) .......... %s" % "{:,}".format(totals["prompts"]))
    print("    Messages ................. %s" % "{:,}".format(totals["messages"]))
    print("    Total tokens ............. %s" % "{:,}".format(totals["tokens"]))
    if totals["lines"]:
        print("    Cost per 1,000 lines ..... %s" % "${:,.2f}".format(
            totals["cost"] / totals["lines"] * 1000))
    print()

    if show_models or unknown:
        render_models(models, unknown)


def render_prices():
    print()
    print(C["b"] + "  PRICE TABLE" + C["x"] + C["d"] + "   (per million tokens, as of %s)" % PRICES_UPDATED + C["x"])
    print(C["d"] + "  cache write 5m = %gx input · 1h = %gx · cache read = %gx · web search $%g/1k"
          % (CACHE_WRITE_5M, CACHE_WRITE_1H, CACHE_READ, WEB_SEARCH_USD_PER_1K) + C["x"])
    print()
    print("  " + C["b"] + "%-26s %-9s %8s %8s %10s %10s %10s"
          % ("MODEL", "SPEED", "INPUT", "OUTPUT", "CW-5M", "CW-1H", "CREAD") + C["x"])
    for model_id, (label, tiers) in MODELS.items():
        for speed, windows in tiers.items():
            for since, until, price_in, price_out in windows:
                window = ""
                if since or until:
                    window = "  [%s..%s]" % (since or "", until or "")
                print("  %-26s %-9s %8s %8s %10s %10s %10s%s" % (
                    model_id, speed, "$%g" % price_in, "$%g" % price_out,
                    "$%g" % (price_in * CACHE_WRITE_5M), "$%g" % (price_in * CACHE_WRITE_1H),
                    "$%g" % (price_in * CACHE_READ), C["d"] + window + C["x"]))
    print()
    print(C["d"] + "  Anything not listed here is reported as UNPRICED, never guessed." + C["x"])
    print()


def resolve_period(args, today):
    since = until = None
    label = "all history"
    if "--since" in args:
        since = args[args.index("--since") + 1]
    if "--until" in args:
        until = args[args.index("--until") + 1]
    if "--today" in args:
        since = until = today.isoformat()
        label = "today"
    elif "--yesterday" in args:
        # Bounded on BOTH ends. A "yesterday" that leaks into today is the
        # classic off-by-one here, and it is invisible: the number just looks
        # high.
        day = (today - datetime.timedelta(days=1)).isoformat()
        since = until = day
        label = "yesterday"
    elif "--week" in args:
        since = (today - datetime.timedelta(days=6)).isoformat()
        label = "last 7 days"
    elif "--month" in args:
        since = (today - datetime.timedelta(days=29)).isoformat()
        label = "last 30 days"
    elif "--days" in args:
        n = int(args[args.index("--days") + 1])
        since = (today - datetime.timedelta(days=n)).isoformat()
        label = "last %d days" % n
    if "--since" in args or "--until" in args:
        label = "%s -> %s" % (since or "start", until or "today")
    return since, until, label


# =====================  INTERACTIVE MODE  =====================

def run_tui(sessions, today):
    import curses

    yesterday = (today - datetime.timedelta(days=1)).isoformat()
    periods = [
        ("All history",  None, None),
        ("Today",        today.isoformat(), today.isoformat()),
        ("Yesterday",    yesterday, yesterday),
        ("Last 7 days",  (today - datetime.timedelta(days=6)).isoformat(), None),
        ("Last 30 days", (today - datetime.timedelta(days=29)).isoformat(), None),
        ("This month",   today.replace(day=1).isoformat(), None),
        ("This year",    today.replace(month=1, day=1).isoformat(), None),
    ]
    sorts = [("cost", "cost"), ("time", "time"), ("lines", "lines"), ("date", "date"),
             ("tokens", "tokens"), ("prompts", "prompts")]

    FIRST_ROW = 6

    def draw(stdscr, pi, si, rows, t, models, unknown, sel, show_models):
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

        sort_label, _ = sorts[si]
        put(0, 2, "CLAUDE CODE USAGE", A | CY)
        put(0, 22, "<- ->  period  |  ^ v  row  |  s  sort  |  m  models  |  q  quit", DM)
        x = 2
        for i, (name, _, _) in enumerate(periods):
            chip = " %s " % name
            if x + len(chip) + 2 >= w:
                break
            put(1, x, chip, (curses.A_REVERSE | A) if i == pi else DM)
            x += len(chip) + 1
        put(2, 2, "Sorted by: %s" % sort_label, YE | A)

        if show_models:
            put(4, 2, "%-18s %14s %14s %14s" % ("MODEL", "IN/OUT", "TOKENS", "COST"), A)
            put(5, 2, "-" * 64, DM)
            for idx, (label, entry) in enumerate(models[:max(1, h - 12)]):
                if entry["free"]:
                    rate = "free"
                elif not entry["known"]:
                    rate = "UNPRICED"
                else:
                    rate = "$%g/$%g" % entry["rate"]
                attr = RE if (not entry["known"] and not entry["free"]) else 0
                put(FIRST_ROW + idx, 2, "%-18s %14s %14s %14s" % (
                    fit(label, 18), rate, "{:,}".format(entry["tokens"]),
                    "${:,.2f}".format(entry["cost"])), attr)
            put(h - 2, 2, "m  back to conversations", DM)
            stdscr.refresh()
            return

        mw = model_width(rows, 18)
        pw = project_width(rows, w, 11 + mw + 10 + 9 + 7 + 5 + 7 + 13 + 8)
        hdr = ("%-*s %-11s %-*s %9s %9s %7s %5s %7s %13s"
               % (pw, "PROJECT", "DATE", mw, "MODELS", "COST", "TIME", "LINES",
                  "FILES", "PROMPTS", "TOKENS"))
        put(4, 2, hdr, A)
        put(5, 2, "-" * len(hdr), DM)
        maxrows = max(1, h - 13)
        shown = rows[:maxrows]
        for idx, r in enumerate(shown):
            col = GR if r["cost"] < 10 else (YE if r["cost"] < 50 else RE)
            line = ("%-*s %-11s %-*s %9s %9s %7s %5d %7d %13s"
                    % (pw, fit(r["project"], pw), r["date"], mw, fit(r["model"], mw),
                       "${:,.2f}".format(r["cost"]), format_time(r["time"]),
                       "{:,}".format(r["lines"]), r["files"], r["prompts"],
                       "{:,}".format(r["tokens"])))
            put(FIRST_ROW + idx, 2, line, (curses.A_REVERSE | A) if idx == sel else col)
        yb = FIRST_ROW + len(shown) + 1
        put(yb, 2, "-" * len(hdr), DM)
        put(yb + 1, 2, "%-*s %-11s %-*s %9s %9s %7s %5d %7d %13s" % (
            pw, fit("TOTAL (%d projects)" % t["n"], pw), "", mw, "",
            "${:,.2f}".format(t["cost"]), format_time(t["time"]),
            "{:,}".format(t["lines"]), t["files"], t["prompts"],
            "{:,}".format(t["tokens"])), A)
        cpl = (t["cost"] / t["lines"] * 1000) if t["lines"] else 0
        sub = ""
        if t["cost"] > 0 and t["subagent_cost"] > 0:
            sub = "    |    %.0f%% in subagents" % (100 * t["subagent_cost"] / t["cost"])
        put(yb + 3, 2, "Cost if API ${:,.2f}    |    {} active    |    {:,} lines    |    ${:,.2f} per 1,000 lines{}".format(
            t["cost"], format_time(t["time"]), t["lines"], cpl, sub), CY | A)
        if unknown:
            put(yb + 4, 2, "%d model(s) with NO known price — press m" % len(unknown), RE | A)
        if shown:
            put(h - 1, 2, ("Selected:  " + shown[sel]["project"])[:max(0, w - 3)], YE | A)
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
        mouse_ok = False
        try:
            curses.mousemask(curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED)
            mouse_ok = True
        except curses.error:
            pass
        pi = si = sel = 0
        show_models = False
        while True:
            h, _w = stdscr.getmaxyx()
            since, until = periods[pi][1], periods[pi][2]
            rows, t, models, unknown = compute(sessions, since, until, None, sorts[si][1])
            nshown = min(len(rows), max(1, h - 13))
            sel = 0 if nshown == 0 else max(0, min(sel, nshown - 1))

            draw(stdscr, pi, si, rows, t, models, unknown, sel, show_models)
            k = stdscr.getch()

            if k in (ord("q"), ord("Q"), 27):
                break
            elif k in (ord("m"), ord("M")):
                show_models = not show_models
            elif k in (curses.KEY_DOWN, ord("j")):
                if nshown:
                    sel = (sel + 1) % nshown
            elif k in (curses.KEY_UP, ord("k")):
                if nshown:
                    sel = (sel - 1) % nshown
            elif k in (curses.KEY_RIGHT, ord("l")):
                pi = (pi + 1) % len(periods); sel = 0
            elif k in (curses.KEY_LEFT, ord("h")):
                pi = (pi - 1) % len(periods); sel = 0
            elif k in (ord("s"), ord("S")):
                si = (si + 1) % len(sorts); sel = 0
            elif k == curses.KEY_MOUSE and mouse_ok:
                try:
                    _, _mx, my, _, _ = curses.getmouse()
                except Exception:
                    continue
                idx = my - FIRST_ROW
                if 0 <= idx < nshown and not show_models:
                    sel = idx

    curses.wrapper(loop)


def main():
    args = sys.argv[1:]
    today = datetime.date.today()

    if "--help" in args or "-h" in args:
        print(__doc__)
        return
    if "--prices" in args:
        render_prices()
        return

    interactive = (not args or "--menu" in args) and sys.stdout.isatty()

    print("  Reading Claude Code history...", file=sys.stderr)
    sessions = load_all()
    if not sessions:
        print("  Nothing found under %s" % PROJECTS_DIR, file=sys.stderr)
        return

    if interactive:
        try:
            run_tui(sessions, today)
        except Exception as exc:
            print("  (Could not open the interactive menu: %s. Showing text.)" % exc,
                  file=sys.stderr)
            rows, totals, models, unknown = compute(sessions, sort_by="cost")
            render_text(rows, totals, models, unknown, "all history", False)
        return

    sort_by = args[args.index("--by") + 1] if "--by" in args else "cost"
    proj_filter = args[args.index("--project") + 1].lower() if "--project" in args else None
    top = int(args[args.index("--top") + 1]) if "--top" in args else None
    since, until, label = resolve_period(args, today)
    rows, totals, models, unknown = compute(sessions, since, until, proj_filter, sort_by)
    if top:
        # `--top` narrows the LIST, so the totals must be recomputed from the
        # rows that survive — otherwise the footer describes a table nobody is
        # looking at. Files can only be summed here, so the distinct guarantee
        # does not hold for a truncated list; it is labelled as such.
        rows = rows[:top]
        totals = dict(totals,
                      cost=sum(r["cost"] for r in rows),
                      lines=sum(r["lines"] for r in rows),
                      files=sum(r["files"] for r in rows),
                      prompts=sum(r["prompts"] for r in rows),
                      messages=sum(r["messages"] for r in rows),
                      tokens=sum(r["tokens"] for r in rows),
                      time=sum(r["time"] for r in rows),
                      sessions=sum(r["sessions"] for r in rows),
                      n=len(rows))
    render_text(rows, totals, models, unknown, label, "--models" in args)


if __name__ == "__main__":
    main()
