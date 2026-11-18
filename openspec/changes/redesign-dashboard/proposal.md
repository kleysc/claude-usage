## Why

The dashboard page still leads with token counts (seven stat cards, a stacked token chart, a model doughnut) and makes the reader work out the answer to the question they came with: how much am I spending, on what, and is it going up. The numbers underneath are now trustworthy (`unify-usage-events`), so the page can be rebuilt around money.

## What Changes

- Replace the page with a money-first layout: headline spend for the selected period with the change against the previous period, daily average, month to date and a month-end projection; daily spend chart in dollars stacked by tool; ranked spend by tool, account, project and model; spend by token category; a sessions table sorted by cost.
- Move the page out of the Python string in `dashboard.py` into `web/index.html`, `web/style.css` and `web/app.js`, served by the same standard-library server.
- **BREAKING**: `/api/data` returns a period overview computed on the server for the requested filters (dates, tool, account, models) instead of the full unfiltered dataset; the browser no longer filters or aggregates.
- Days are local calendar days instead of UTC days.
- Add a "Scan now" button (`POST /api/scan`) and show when the data was last updated.
- Compute each event's cost once per request instead of once per grouping.
- Not included: subscription price comparison and Codex rate-limit display.

## Capabilities

### New Capabilities
- `usage-dashboard`: what the dashboard page shows and how its filters and data endpoint behave.

### Modified Capabilities

None.

## Impact

- Code: `dashboard.py` (template removed, static file serving, scan endpoint), `queries.py` (overview, cost annotated once, local days), `pricing.py` (cost per token category), new `web/` directory, tests.
- API: `/api/data` response shape changes; `/api/session` and `/api/session_preview` are unchanged.
- Dependencies: none added. Chart.js is still loaded from the CDN.
