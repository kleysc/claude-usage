## 1. Safety and groundwork

- [x] 1.1 Confirm a backup of the live database exists and is readable; all development and tests use `CLAUDE_USAGE_DB` pointing at a temporary file
- [x] 1.2 Uncommitted work on `feature/codex-gemini-sources`: build on top without committing (no commit approval given); rollback point saved as `~/Downloads/claude-usage-pre-refactor.patch`
- [x] 1.3 Create `tests/` with a `unittest` skeleton and `tests/fixtures/`; confirm `python3 -m unittest` runs

## 2. Spikes on real data (read-only)

- [x] 2.1 Measure, across all Claude transcripts on disk, how often records sharing `message.id` + `requestId` carry different usage and in which fields; result: keep the last copy (design decision 1)
- [x] 2.2 Run the legacy dedup heuristic (design decision 6) on sessions present both in `turns` and on disk; result: exact on every session checked, heuristic adopted
- [x] 2.3 Record both results in `design.md`

## 3. Store and collectors

- [x] 3.1 Add `store.py`: the single DB path definition, `events` schema with `UNIQUE (source, event_key)`, file fingerprint table, upsert, and the `event_sessions` view (the legacy `sessions` table keeps its name)
- [x] 3.2 Add `collectors/claude.py` as a pure function from file path to events, with dedup, 5m/1h cache split, speed and sidechain; add fixture and tests
- [x] 3.3 Move Codex parsing to `collectors/codex.py`; add fixtures for both formats and a multi-thread file; add tests
- [x] 3.4 Move Gemini parsing to `collectors/gemini.py`; add fixture with a rewritten id; add tests
- [x] 3.5 Rewrite `scan()` to fingerprint files, call collectors and upsert; keep `scanner.py` as a thin compatibility module; test idempotency, file growth and missing tool directories; time a full and an incremental scan on real logs

## 4. Pricing

- [x] 4.1 Add `prices.json` with dated entries: Claude, `gpt-5.4` and `gpt-5.4-mini` rates verified; `gpt-6-astra` and the remaining Codex and Gemini rates marked unverified (values in design decision 4)
- [x] 4.2 Add `pricing.py`: load the file, look up by model and date, price one event including 5m/1h cache writes and fast mode, return null when there is no entry
- [x] 4.3 Add tests for date selection, cache write rates, fast mode and the unpriced case

## 5. Queries and outputs

- [x] 5.1 Add `queries.py`: totals, daily series, by model, by project, by session, each returning tokens, priced cost, unpriced tokens, estimated share and unverified share
- [x] 5.2 Switch `cli.py today` and `stats` to `queries.py`; remove the price table and fallback rate from `cli.py`
- [x] 5.3 Switch `/api/data` to `queries.py` with cost and `quality` fields; remove `PRICING` and `calcCost` from the page JavaScript and render the server values; keep the layout
- [x] 5.4 Add a test that the CLI total and the API total agree for the same filters

## 6. Reconciliation

- [x] 6.1 Add `reconcile.py` with the per-source checks from design decision 9
- [x] 6.2 Run it at the end of `scan()` for the files read; add `cli.py verify` with a non-zero exit on mismatch
- [x] 6.3 Add tests: matching file, mismatching file, estimated events skipped

## 7. Legacy import and migration

- [x] 7.1 Implement the legacy import using the heuristic validated in 2.2; first explain the 60 legacy-only rows dated 2026-11, writing `quality = 'estimated'`, skipping sessions rebuilt from disk
- [x] 7.2 Implement `cli.py migrate`: timestamped backup, working copy, build, verify, before/after summary, atomic replace; abort without touching the live file on any failure
- [x] 7.3 Test migration on a small fixture database, including the abort path
- [x] 7.4 Run `migrate` against a copy of the live database and show the user the summary
- [x] 7.5 With the user's go-ahead, run `migrate` on the live database and run `verify`

## 8. Schedule and documentation

- [x] 8.1 Add `cli.py install-schedule` that writes a launchd agent for a daily scan and prints how to remove it
- [x] 8.2 Update `README.md`: what cost means (API-equivalent, not billed), the correction to Claude totals, estimated months, unverified prices, new commands, how to edit `prices.json`
- [x] 8.3 Final check: `python3 -m unittest` passes, `verify` reports zero mismatches, CLI and dashboard show the same total
