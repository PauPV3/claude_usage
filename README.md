# claude_usage

A **Claude Code** usage summary: for each project it computes what that traffic
*would have cost on the API*, plus active usage time, lines of code written,
files touched, prompts and tokens — by reading your local history
(`~/.claude/projects`).

If you're on a subscription your actual outlay is zero. The number here is the
comparison, not a bill.

> **Privacy:** the script runs entirely on your machine. It only **reads** local
> files and prints to the terminal — it never sends anything anywhere. It shows
> your project names and the **number** of files touched, never the file
> contents or paths.

## Requirements

- Python 3 (standard library only — nothing to install)
- You've used Claude Code on this machine (so that `~/.claude/projects` exists)

## Usage

```bash
python3 claude_usage.py
```

With no flags, in a real terminal, it opens an **interactive menu**:

- `←` / `→` — change the time period
- `↑` / `↓` — select a row; the full project name of the selected row is shown
  at the bottom (handy when a long name is truncated in the table)
- `s` — cycle the sort order (cost, time, lines, date, tokens, prompts)
- `m` — toggle the per-model breakdown, with the rate applied to each
- mouse click — select a row
- `q` (or `Esc`) — quit

The `PROJECT` column width adapts to the longest name and the terminal width, so
names are shown in full whenever they fit.

### Text mode (with flags)

```bash
python3 claude_usage.py              # sorted by cost (descending)
python3 claude_usage.py --by time    # sorted by active usage time
python3 claude_usage.py --by lines   # sorted by lines written
python3 claude_usage.py --by date    # sorted by date
python3 claude_usage.py --top 10     # only the first 10
python3 claude_usage.py --project hub  # filter projects containing "hub"
python3 claude_usage.py --models     # per-model breakdown with the rates used
python3 claude_usage.py --prices     # print the price table and exit
```

### Time filters

```bash
python3 claude_usage.py --today
python3 claude_usage.py --yesterday
python3 claude_usage.py --week       # last 7 days
python3 claude_usage.py --month      # last 30 days
python3 claude_usage.py --days 3     # last N days
python3 claude_usage.py --since 2026-06-15                     # from a date
python3 claude_usage.py --until 2026-06-18                     # until a date
python3 claude_usage.py --since 2026-06-15 --until 2026-06-18  # range
```

Full help: `python3 claude_usage.py --help`

### Optional: shell alias

```bash
echo 'alias claude_usage="python3 /path/to/claude_usage.py"' >> ~/.zshrc
source ~/.zshrc
```

## How it works

- Walks **every** `.jsonl` under `~/.claude/projects`, at any depth. Subagent and
  workflow transcripts are attributed to the session that spawned them.
- Deduplicates by billing key (`message.id`, `requestId`) **globally across
  files**, so a resumed or forked session is never counted twice.
- Prices each `(day, model, speed)` bucket separately, so a conversation that
  used two models is billed as two.
- One row per **project**: every session of the same project is summed.
- **Active usage time** is measured per turn, from your prompt to the last reply
  that answers it. Time spent reading, typing or away lands *between* turns and
  is never counted.
- Derives the project name from the Claude Code folder name, computing the
  current user's home — so it's **portable** across machines.
- Lines counted come from the `Write`, `Edit`, `MultiEdit` and `NotebookEdit`
  tools; files touched are counted **distinct** across the whole selection.

## What changed, and why it matters

An earlier version of this script looked right and was **68% low** on a real
history. Three separate bugs, all measured against one 149-file history:

**1. The glob only looked one level deep.** `~/.claude/projects/*/*.jsonl` never
sees a subagent or workflow transcript — those live at
`<project>/<session>/subagents/[workflows/<wf>/]agent-*.jsonl`. That was **124 of
149 files**, a third of the tokens and more than half the messages, invisible.
The gap grows with every fan-out. Worth **$312.82** on that history.

**2. Pricing had a default.** Model matching ended with *"…otherwise it's a
Sonnet"*, so `claude-fable-5` — which contains neither `opus` nor `haiku` — was
billed at $3/$15 instead of $10/$50, across 1,803 messages. Worth **$108.51**.

The fix is not "add a row". There is now **no default at all**: matching is exact
id, then a declared alias table, then a dated-snapshot suffix, and anything else
is reported as `UNPRICED`, by name, with its token count. A visible hole is
recoverable; an invented number is not.

**3. Fast mode was ignored.** `usage.speed` is in every record, and `speed:
"fast"` on Opus 5 / Opus 4.8 costs double. A `/fast` session was billed at half.

Also fixed along the way:

- A conversation using two models reported only one of them.
- "Files touched" summed per-session counts, so a file edited in three sessions
  counted as three files. It is now distinct across the selection.
- Active usage time was measured before deduplication, so a forked session
  re-counted hours already spent in the original.
- Web search ($10 / 1,000 searches) and `inference_geo: "us"` (×1.1) were not
  counted at all.
- Sonnet 5's introductory rate ends 2026-08-31, so the price depends on the day
  the tokens were billed — not on today.

Cross-checked against an independent implementation of the same accounting
(inside [Andromeda HUB](https://github.com/PauPV3/Andromeda_HUB)): per-model
totals, lines, distinct files and the subagent share agree to the cent.

## Notes

- **Supported models:** Opus (4 → 5), Sonnet (4 → 5, including the introductory
  pricing transition), Haiku, Fable 5 and Mythos 5. Anything else is reported as
  `UNPRICED` rather than guessed.
- Prices live in `MODELS` near the top of the script, with the cache rates
  **derived** from the published multipliers (5-minute write ×1.25, 1-hour write
  ×2, cache read ×0.1) rather than typed out per model. `--prices` prints the
  whole table.
- Rates are from Anthropic's pricing page as of the `PRICES_UPDATED` date in the
  file. Update `MODELS` when they change; every past day re-prices instantly.
- Costs are an estimate based on the locally recorded token usage and may differ
  from official billing.

## License

[MIT](LICENSE)
