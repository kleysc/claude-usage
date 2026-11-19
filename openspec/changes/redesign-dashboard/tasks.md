## 1. Server

- [x] 1.1 `pricing.py`: cost per token category for one event
- [x] 1.2 `queries.py`: price each event once per request, local days, `overview()` with totals, previous period, month projection, daily by tool, rankings and sessions
- [x] 1.3 `dashboard.py`: remove the page string, serve `web/` from an allow-list, `/api/data` from `overview()`, `POST /api/scan`
- [x] 1.4 Tests: breakdowns sum to the total, tool filter, previous period, projection, local day, static files and scan endpoint

## 2. Page

- [x] 2.1 `web/index.html` and `web/style.css`: layout, light and dark themes
- [x] 2.2 `web/app.js`: filters in the URL, headline and tiles, daily chart with tooltip and table view, ranked bars, sessions table, session reader
- [x] 2.3 Validate the tool colours with the palette validator in both themes

## 3. Check

- [x] 3.1 Run the test suite and `verify`
- [x] 3.2 Open the page in a browser in light and dark themes and fix what looks wrong
- [x] 3.3 Update the README

## 4. Second pass after review of the first page

- [x] 4.1 Full-width layout in an editorial monochrome style (large display number, mono eyebrow labels, hairline grid), light and dark, agreed from a mockup with real data
- [x] 4.2 `overview()`: every day of the period listed, trend series and previous-period cost per tool, account, project and model, `project` filter
- [x] 4.3 Click to filter by day, tool, account, project or model, with removable active-filter chips
- [x] 4.4 Daily chart drawn as SVG (no chart library): average line, peak label, tooltip, table view
