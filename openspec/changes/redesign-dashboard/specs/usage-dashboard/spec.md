## ADDED Requirements

### Requirement: Spend is the headline
The page SHALL show, above everything else, the API-equivalent cost of the selected period, the change against the preceding period of equal length when the period has a start date, the daily average, the current month to date and a projection for the end of the current month.

#### Scenario: Thirty-day period
- **WHEN** the selected period is the last 30 days
- **THEN** the page shows the cost of those 30 days and the difference from the 30 days before them

#### Scenario: All-time period
- **WHEN** the selected period has no start date
- **THEN** no comparison with a previous period is shown

### Requirement: Uncertainty is stated where it applies
The page SHALL state next to the headline how much of the cost comes from estimated events, how much from unverified prices and how many tokens had no price, and SHALL omit each statement when its amount is zero.

#### Scenario: Period with unpriced usage
- **WHEN** the selected period includes tokens from a model without a price
- **THEN** the page states the number of unpriced tokens and names the models

#### Scenario: Period with nothing uncertain
- **WHEN** the selected period has no estimated events, unverified prices or unpriced tokens
- **THEN** no uncertainty statement is shown

### Requirement: Spend is broken down by day, tool, account, project, model and token category
The page SHALL show daily cost stacked by tool and ranked cost by tool, account, project, model and token category, each with its amount and share of the period total.

#### Scenario: Breakdowns add up
- **WHEN** the overview for a period is requested
- **THEN** the costs by tool sum to the period total and the costs by token category sum to the period total

#### Scenario: Day without usage
- **WHEN** a day inside the period has no usage
- **THEN** the daily chart shows that day with no bar

### Requirement: Sessions are ranked by cost
The page SHALL list the sessions of the period with tool, account, project, model, last activity, duration, responses, tokens and cost, sorted by cost by default with an option to sort by recency, and SHALL mark sessions built from estimated events.

#### Scenario: Default order
- **WHEN** the page loads
- **THEN** the most expensive session of the period is first

### Requirement: Filters are applied by the server
The data endpoint SHALL accept a date range, a tool, an account and a list of models and SHALL return totals and breakdowns for exactly that selection; the page SHALL NOT compute totals from unfiltered data.

#### Scenario: Tool filter
- **WHEN** the overview is requested for the tool `codex`
- **THEN** every total, breakdown and session in the response covers Codex usage only

#### Scenario: Filter values stay available
- **WHEN** the overview is requested with a filter
- **THEN** the response still lists every tool, account and model available for filtering

### Requirement: Days are local calendar days
The system SHALL assign each event to the local calendar date of its timestamp for date filtering and daily totals.

#### Scenario: Late evening usage
- **WHEN** an event happens at 23:30 local time on the 5th in a timezone behind UTC
- **THEN** it is counted on the 5th

### Requirement: The page shows data freshness and can trigger a scan
The page SHALL show when the database was last updated and SHALL provide a control that runs a scan and reloads the data.

#### Scenario: Scan from the page
- **WHEN** the user activates the scan control
- **THEN** a scan runs and the page reloads its data when it finishes
