# claude_usage

A **Claude Code** usage summary: for each conversation (session) it computes the cost in dollars, lines of code written, files touched, prompts and tokens, by reading your local history (`~/.claude/projects/*.jsonl`).

The cost calculation mirrors the prices Anthropic applies (input, output and the three cache modes), so the totals match [`ccusage`](https://github.com/ryoppippi/ccusage).

> **Privacy:** the script runs entirely on your machine. It only **reads** local files and prints to the terminal — it never sends anything anywhere. It shows your project names and the **number** of files touched, never the file contents or paths.

## Requirements

- Python 3 (standard library only — nothing to install)
- You've used Claude Code on this machine (so that `~/.claude/projects` exists)

## Usage

```bash
python3 claude_usage.py
```

With no flags, in a real terminal, it opens an **interactive menu** (arrow keys to change the period and the sort order, `q` to quit).

### Text mode (with flags)

```bash
python3 claude_usage.py              # sorted by cost (descending)
python3 claude_usage.py --by lines   # sorted by lines written
python3 claude_usage.py --by date    # sorted by date
python3 claude_usage.py --top 10     # only the first 10
python3 claude_usage.py --project hub  # filter projects containing "hub"
```

### Time filters

```bash
python3 claude_usage.py --today      # today only
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

- Reads every `.jsonl` under `~/.claude/projects` once.
- Deduplicates by billing key (`message.id`, `requestId`) so the same message is never counted twice.
- Derives the project name from the Claude Code folder name, computing the current user's home — so it's **portable** across machines.
- Lines counted come from the `Write`, `Edit`, `MultiEdit` and `NotebookEdit` tools.

## Notes

- Prices live in the `PRICES` dict inside the script; update them if Anthropic changes them.
- Costs are an estimate based on the locally recorded token usage and may differ slightly from your official billing.

## License

[MIT](LICENSE)
