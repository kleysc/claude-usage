## Context

`dashboard.py` holds a 970-line HTML/CSS/JavaScript string. `/api/data` sends every session and every day/model row (about 1 MB) and the browser filters and sums them. The page is organised around token categories. Cost totals are already computed in Python by `queries.py`.

## Goals / Non-Goals

**Goals:**
- A reader sees period spend, its trend and where it goes without scrolling.
- Every number on the page comes from the server already aggregated.
- The page is three plain static files; no build step, no new dependency.

**Non-Goals:**
- Subscription comparison, rate limits, transcript preview for Codex and Gemini.
- Changing ingestion, pricing rules or the database.

## Decisions

### 1. Server-side filtering, one overview call
`GET /api/data?start=&end=&source=&account=&models=` returns the overview for exactly that selection: totals, previous period of equal length, month to date with projection, daily rows by tool, rankings, sessions, and the available filter values. Changing a filter refetches. Alternative: keep client-side filtering over fine-grained rows. Rejected: two implementations of grouping, a large payload, and previous-period and projection logic in JavaScript.

### 2. Cost computed once per event per request
Events are priced once (with a per-model-and-date lookup cache) and the result is reused by every grouping. Cost is split per token category so the page can show where the money goes.

### 3. Local calendar days
A day is the local date of the event's timestamp, for filtering and for the daily chart. Price selection still uses the UTC date of the event. Alternative: UTC days. Rejected: "today" on the page would not match the user's day.

### 4. Month projection
Month to date divided by days elapsed, times days in the month, using the same tool, account and model filters. It is labelled a projection and is independent of the selected date range.

### 5. Forms and colour
- Headline spend is a number, not a chart.
- Daily spend is a stacked bar chart by tool: at most three series, one axis, dollars.
- Tool, account, project, model and token-category breakdowns are ranked horizontal bars with the amount and share written out; no doughnut.
- Colour carries one thing only, the tool (Claude, Codex, Gemini), with fixed colours that do not change with filters. All other bars are neutral. Palette values are the validated defaults of the data-visualisation guide, with separate light and dark steps.
- Uncertainty (estimated history, unverified prices, unpriced tokens) is stated in words under the headline and marked per session, only where it applies.

### 6. Static files
`web/` is served from a fixed allow-list of three files. `POST /api/scan` runs an incremental scan and returns its counts; the server only listens on localhost.

## Risks / Trade-offs

- [Each filter change is a request of a few hundred milliseconds] → Acceptable on localhost; the page shows a loading state.
- [Projection is misleading early in the month] → Labelled as a projection, with the days elapsed shown.
- [Chart.js CDN unavailable offline] → Numbers, rankings and tables do not depend on it; only the daily chart is missing, and a table view of the daily data exists.
