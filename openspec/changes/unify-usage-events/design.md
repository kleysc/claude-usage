## Context

Current state (branch `feature/codex-gemini-sources`, uncommitted):

```
raw logs                  scanner.py (630 lines)            ~/.claude/usage.db
~/.claude*/projects ──▶ Claude: 1 row per record     ──▶ turns    (about 2.1x overcount)
~/.codex/sessions   ──▶ Codex:  1 row per response   ──▶ turns    (correct, unique source_key)
~/.gemini/tmp       ──▶ Gemini: 1 row per message id ──▶ turns    (correct, unique source_key)
                                                          sessions (accumulated counters)
                                                              │
                  cli.py (347)        ◀── Python prices ──────┤
                  dashboard.py (1444) ◀── JavaScript prices ──┘
```

Facts that constrain the design:

- A Claude API response is written to the transcript as several assistant records with the same `message.id`. The current scanner stores each one.
- Claude ingestion is incremental by line count per file, with no unique key. Codex and Gemini are keyed by `(source, source_key)`.
- `sessions` is updated by adding to its counters, so a partial re-scan shifts it silently.
- Old transcripts are pruned from disk. June and July 2026 are gone, April and May are sparse. The database is the only copy of March to May, and those rows are double counted and carry no message id.
- Cost exists only as a calculation: once in `cli.py` (Opus at $15/$75, cache at 0.10x/1.25x of input), once in the page JavaScript (4.x list prices times 1.23, no Fable entry).
- No tests. Python standard library only, by choice.
- The live database is the only copy of the older history, so a backup is taken before any migration.

## Goals / Non-Goals

**Goals:**

- One row per API response for every tool, enforced by a unique key.
- One price table and one cost calculation, in Python.
- Numbers that can be checked against the raw logs by a command.
- The old March to May history kept, visibly marked as an estimate.
- A migration of the live database that cannot lose data.
- Tests that pin each log format version.

**Non-Goals:**

- Redesigning the dashboard page (follow-up change).
- Showing what was actually billed. Subscriptions are flat; cost here is the API-equivalent value of the usage.
- Session preview for Codex and Gemini.
- Dropping the old tables.
- Adding dependencies or a build step.

## Decisions

### 1. One `events` table, one row per API response

```
events
  source            claude | codex | gemini
  event_key         unique within source
  account           default | <claude account name> | codex | gemini
  session_id        namespaced for non-Claude sources, as today
  timestamp         UTC ISO 8601
  model
  project, cwd, git_branch
  input_tokens          uncached input only
  output_tokens         includes reasoning/thinking
  cache_read_tokens
  cache_write_5m_tokens
  cache_write_1h_tokens
  reasoning_tokens      informational, already inside output_tokens
  speed             standard | fast
  is_sidechain      0 | 1
  quality           exact | estimated
  source_path
  UNIQUE (source, event_key)
```

Event keys:

| Source | Key |
|---|---|
| Claude | `message.id` + `requestId` |
| Codex, new format | `response_id` |
| Codex, before 2026-09-09 | hash of the cumulative `total_token_usage` plus thread id, as today |
| Gemini | message `id` (last copy wins) |
| Legacy import | `legacy:<turns.id>` of the surviving row |

Alternative considered: keep `turns` and add a unique index for Claude. Rejected because existing Claude rows have no message id to key on, the table lacks the cache split and fast-mode columns, and fixing it in place means rewriting the only copy of the old history.

For Claude records that share a key, the stored values are those of the last copy written. Measured on 2026-11-18 over all transcripts on the author's disk: about 72% of responses have more than one copy, about 0.1% of those have copies that differ, the difference is always in `output_tokens`, and in every such case the last copy equals the field-wise maximum. No `message.id` appears with more than one `requestId`.

### 2. Sessions and daily totals are queries, not stored counters

Session totals come from a `GROUP BY session_id` over `events`, exposed as a persistent SQL view named `event_sessions`. The name `sessions` is taken by the legacy table, which is kept unchanged, and SQLite cannot hold a table and a view with the same name. At 20k to 60k rows this is instant in SQLite. This removes the class of bug where counters drift from the rows.

Alternative considered: keep a materialized `sessions` table and rebuild it after each scan. Rejected as unnecessary at this size.

### 3. Incremental scanning by file fingerprint, upsert by key

For each log file store `(path, mtime, size)`. If unchanged, skip. If changed, re-read the whole file and upsert by `(source, event_key)`. Correctness comes from the key, so a re-read can never double count; the fingerprint is only a speed-up.

Alternative considered: resume from a byte offset. Rejected: Gemini rewrites earlier records and Codex files hold several threads, so partial reads need per-format state for little gain.

### 4. Prices live in one data file with dates and provenance

`prices.json`, read by `pricing.py`:

```
{ "model": "claude-opus-5-5", "match": "prefix",
  "effective_from": "2026-01-01",
  "input": 4.00, "output": 20.00, "cache_read": 0.20,
  "cache_write_5m": 5.00, "cache_write_1h": 8.00,
  "fast": { "input": 8.00, "output": 40.00 },
  "source": "anthropic pricing page", "verified": true, "checked": "2026-11-18" }
```

- Lookup: the row for the model with the latest `effective_from` not after the event date.
- No row means cost is null. There is no default rate.
- Claude rows are verified. `gpt-5.4` (2.50 input / 0.25 cached / 15 output) and `gpt-5.4-mini` (0.75 / 0.075 / 4.50) were read from OpenAI's model pages on 2026-11-18 and are verified. `gpt-6-astra` is 10 / 1 / 50 with cache write 12.50: every source found agrees, including reports of OpenAI's launch post, but the vendor page could not be fetched, so it is marked `verified: false`. The other Codex and Gemini rows come from third-party aggregators and are marked `verified: false`.
- OpenAI reprices requests above 272K input tokens (2x input, 1.5x output). In the author's stored Codex data no request exceeds that size, so the surcharge is not modelled.
- Cost is computed when queried, not stored, so a corrected price applies to history without a migration.

Alternative considered: a prices table in SQLite. Rejected: a file in the repository is reviewable in a diff and versioned by git; the database is not.

### 5. Cost is computed only in Python

`queries.py` returns tokens and cost together. `cli.py` and `/api/data` call the same functions. Every aggregate returns three things: the priced total, the number of tokens that had no price, and the share that is `estimated`. The page JavaScript only formats.

### 6. Legacy rows are imported as estimates

Sessions that exist in `turns` but have no transcript on disk are imported with `quality = 'estimated'`.

Deduplication heuristic: inside a session, consecutive legacy rows with the same model and the same `(input_tokens, cache_read_tokens, cache_creation_tokens)` are treated as copies of one response and collapsed into one. Copies of a response share their input-side counts, and two different responses having an identical cache-read count is rare.

Within a collapsed group the largest `output_tokens` is kept.

Validated on 2026-11-18 against every session that exists both in `turns` and on disk with the same record count, where the true answer is known: the heuristic reproduced the truth exactly (input, output, cache read and cache creation all at 0.00% error, every session exact). It is adopted; no fallback is needed. Applied to the sessions that exist only in `turns`, it removes about 43% of their rows. The rows stay labelled `estimated` because the heuristic cannot be checked on them directly and they lack the cache split.

Found during implementation: a response copied into another session by a resume or fork repeats all five values (model, input, output, cache read, cache creation). The import therefore also drops a legacy row whose five values match an exact event or an earlier legacy row, unless the row has no cache activity (too generic to fingerprint). With this second step, collapsing the old rows of every session that still has a transcript gives 0.27% fewer events than the true count, with cache read tokens exact. For the same reason the upsert keeps, for a Claude key seen in two files, the copy with the larger output.

Legacy rows have no 5-minute/1-hour split; all cache writes are treated as 5-minute, which makes the estimate a lower bound.

Alternative considered: leave the old months out. Rejected for now because it erases the only record of that period; it stays as an option in Open Questions.

### 7. Module layout

```
collectors/claude.py   parse one transcript file  → events
collectors/codex.py    parse one rollout file     → events
collectors/gemini.py   parse one chat file        → events
store.py               DB path (one definition), schema, upsert, views
pricing.py             load prices.json, price one event
prices.json
queries.py             totals, daily, by model/project/session, with cost
reconcile.py           raw-log totals vs stored totals
cli.py                 scan | today | stats | verify | migrate | install-schedule | dashboard
dashboard.py           HTTP handler + page template (layout unchanged)
tests/                 unittest, tests/fixtures/ synthetic samples
```

Collectors are pure functions from a file path to a list of event dicts: no database access, which is what makes them testable. `scanner.py` remains as a thin module that exposes `scan()` for compatibility.

### 8. Migration builds a copy and swaps

`cli.py migrate`:

1. Copy the live database to a timestamped backup.
2. Copy it again to a working file.
3. In the working file: create `events`, scan all logs on disk, import legacy rows.
4. Run reconciliation on the working file. Stop on any mismatch.
5. Print a before/after summary by month and source.
6. Replace the live file atomically (`os.replace`).

The old tables are left in place and no longer written. Rollback is restoring the backup.

Alternative considered: `ALTER`/create in place on the live file. Rejected: a failure halfway leaves a mixed state in the only copy of the old history.

### 9. Reconciliation

For each log file, compute totals independently of the collector's row logic where the format allows it:

- Codex: sum of the last cumulative `thread_token_usage` (or `total_token_usage`) per thread. This is a genuinely independent figure.
- Gemini: count of distinct ids and sum of `tokens.total`.
- Claude: transcripts carry no cumulative total, and one response can appear in several transcripts (a resumed or forked session copies earlier history into the new file), so per-file totals cannot be compared. The check is by key: every distinct `(message.id, requestId)` with usage in the file must be stored once, with the same input-side counts and an output count not smaller than the file's copy. This detects stale or partial ingestion, not a flaw in the dedup rule itself; the dedup rule is covered by unit tests instead.

A file whose size or modification time differs from its stored fingerprint is still being written (or is new); it is reported as "changed since last scan" and not as a mismatch.

Runs at the end of every scan for the files read in that scan, and for everything with `cli.py verify`, which exits non-zero on a mismatch.

### 10. Scheduled scan is opt-in

`cli.py install-schedule` writes a launchd agent that runs `cli.py scan` daily and prints what it wrote and how to remove it. Nothing is installed unless the user runs that command.

### 11. Tests

`unittest` from the standard library. Fixtures are small synthetic logs written for the tests, one per format version (Claude with duplicated message ids and both cache-write kinds, Codex before and after 2026-09-09 including a multi-thread file, Gemini with a rewritten id). No real transcript content goes into the repository.

## Risks / Trade-offs

- [Migration damages the live database] → Work on a copy, verify, then atomic replace; timestamped backup plus the existing 2026-11-18 backup.
- [The legacy heuristic is wrong on old data] → It was exact on every session with known truth; every legacy row is still labelled `estimated` in output.
- [The Claude last-copy rule stops holding] → Measured on real data (about 0.1% of multi-copy responses differ, last copy always the largest); a unit test pins the rule.
- [Totals drop by about half and look like a bug] → `migrate` prints the before/after table and the README explains the correction.
- [Codex and Gemini prices are unverified] → Flag carried into the output; the user can correct `prices.json` without code changes.
- [A tool changes its log format again] → Collectors are isolated per tool, fixtures are per version, and reconciliation fails loudly instead of producing quiet wrong totals.
- [Full re-read of changed files is slower than line offsets] → Only changed files are re-read; measured in task 3.5.
- [Divergence from upstream] → Accepted; stated in the proposal.

## Migration Plan

1. Land the modules and tests with the new pipeline writing to a temporary database only (`CLAUDE_USAGE_DB`).
2. Run `migrate` against a copy of the live database and review the summary.
3. Run `migrate` for real.
4. Rollback at any point: restore the backup file; the previous code still reads the old tables.

## Open Questions

Resolved on 2026-11-18:

- Legacy months are imported as estimates; the heuristic was exact on every session where it could be checked.
- `gpt-5.4` and `gpt-5.4-mini` have vendor prices and are priced.
- `gpt-6-astra` uses 10 / 1 / 50; the 8 / 0.40 / 40 figure was not found in any other source.

Still open, and not blocking:

- Whether sidechain (subagent) usage is shown separately on the page. The column is captured either way, so this is decided in the redesign change.
- Resolved: the 60 rows dated 2026-11 were sessions whose responses had all been copied into, and attributed to, another transcript. The five-value match above drops them.
