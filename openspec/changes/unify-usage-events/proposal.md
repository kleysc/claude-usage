## Why

The numbers the tool reports cannot be trusted today. Claude usage is stored once per transcript record instead of once per API response, which overcounts tokens and cost by about 2.1x on the author's data. Cost is computed in three different places with three different price tables (`cli.py`, the JavaScript inside `dashboard.py`, and neither matches current rates), so the CLI and the dashboard never agree. Codex and Gemini already use a correct one-row-per-response model, so the codebase now has two ingestion designs side by side and no tests for either.

This has to be fixed before any redesign of the page: a better layout on top of wrong numbers is still wrong.

## What Changes

- Add one normalized `events` table: one row per API response for every tool (Claude, Codex, Gemini), with a unique key per source, so re-scanning can never double count.
- Move Claude ingestion onto that table, deduplicating by `message.id` + `requestId`, and capture the fields the current scanner drops: 5-minute and 1-hour cache writes, fast mode, sidechain (subagent) flag.
- Derive sessions and daily totals from `events` by query instead of maintaining accumulated counters in `sessions`.
- Add a single dated price table (one file, with the source and a verified/unverified flag for each price) and compute cost in Python only. The CLI and `/api/data` both use it. **BREAKING**: the `PRICING` table and `calcCost` in the dashboard JavaScript are removed; `/api/data` returns cost fields instead.
- Import the old rows that can no longer be rebuilt from transcripts (March to May 2026) as `estimated` events, deduplicated by a heuristic that was exact on every session where it could be checked, and labelled as estimates everywhere they are shown.
- Add a reconciliation check that compares stored totals with the raw logs after every scan, plus a `verify` command.
- Add a `migrate` command that rebuilds into a copy of the database, verifies it, and only then replaces the live file, keeping a backup.
- Add a command that installs a daily scheduled scan, because transcripts are pruned from disk and anything not scanned in time is lost.
- Split the code into modules (collectors, store, pricing, queries) and add a unit test suite with small synthetic log samples for each format version.
- Out of scope: the visual redesign of the dashboard page. The page keeps its current layout and only switches to server-computed cost. The redesign is a follow-up change.

## Capabilities

### New Capabilities
- `usage-ingestion`: reading Claude, Codex and Gemini logs into one row per API response, idempotently, including the legacy import, the migration and the scheduled scan.
- `usage-pricing`: the dated price table and the single cost calculation used by the CLI and the API, including how unpriced models and estimates are reported.
- `usage-reconciliation`: checking stored totals against the raw logs and reporting mismatches.

### Modified Capabilities

None. `openspec/specs/` is empty; no capability has been specified before.

## Impact

- Code: `scanner.py`, `cli.py` and `dashboard.py` are restructured; new modules `store.py`, `pricing.py`, `queries.py`, `reconcile.py`, `collectors/`, a `prices.json` data file and a `tests/` directory.
- Data: `~/.claude/usage.db` gains an `events` table. The old `turns`, `sessions` and `processed_files` tables are kept, unused, until a later change removes them. A backup is taken before migration; the existing backup `~/.claude/usage.db.bak-2026-11-18` is not touched.
- API: `/api/data` rows gain cost fields and a `quality` field; the JavaScript no longer prices anything.
- Reported numbers: Claude tokens and cost drop to roughly half of what the tool shows today. This is the correction, not a regression.
- Dependencies: none added. Python standard library and SQLite only.
- Upstream: this diverges from `phuryn/claude-usage`; merging future upstream changes will no longer be practical.
