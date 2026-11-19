## ADDED Requirements

### Requirement: One price table
The system SHALL read all prices from a single data file in which every entry states the model, the date it takes effect, the rate for each token category, where the price came from and whether it was verified against the vendor.

#### Scenario: Price changes over time
- **WHEN** a model has an entry effective 2026-01-01 and another effective 2026-09-01
- **THEN** an event dated 2026-08-15 is priced with the first entry and an event dated 2026-09-15 with the second

#### Scenario: Correcting a price
- **WHEN** a rate in the price file is changed and the totals are requested again
- **THEN** the new rate is applied to past events without re-scanning

### Requirement: One cost calculation
The system SHALL compute cost in a single Python function used by both the command line output and the HTTP API, and the dashboard page SHALL NOT contain prices.

#### Scenario: CLI and dashboard agree
- **WHEN** the total cost for the same date range and filters is requested from the CLI and from `/api/data`
- **THEN** both return the same amount

### Requirement: Cost follows the token category rates
The system SHALL price each event as the sum of its input, output, cache read, 5-minute cache write and 1-hour cache write tokens, each multiplied by its own rate, and SHALL use the fast-mode rates for events with speed `fast` when the model has them.

#### Scenario: Cache writes are priced by duration
- **WHEN** an event has 1,000,000 5-minute cache write tokens and 1,000,000 1-hour cache write tokens on a model with input at $4 per million
- **THEN** the cache write cost is $5.00 plus $8.00

#### Scenario: Fast mode
- **WHEN** an event with speed `fast` uses a model that has fast-mode rates
- **THEN** the fast-mode input and output rates are used

### Requirement: Unpriced usage is never given a guessed cost
The system SHALL report a null cost for an event whose model has no price entry, SHALL NOT apply a default rate, and SHALL still report its tokens.

#### Scenario: Model without a price
- **WHEN** an event uses a model that has no entry in the price file
- **THEN** its cost is null and its tokens appear in the token totals

#### Scenario: Total that includes unpriced usage
- **WHEN** a total covers both priced and unpriced events
- **THEN** the result contains the sum of the priced events and, separately, the number of tokens that had no price

### Requirement: Uncertainty is visible
The system SHALL report, with every cost total, how much of it comes from events marked `estimated` and how much from prices marked unverified.

#### Scenario: Total covering legacy months
- **WHEN** a total includes events marked `estimated`
- **THEN** the result states the amount that comes from estimated events

#### Scenario: Total covering unverified prices
- **WHEN** a total includes events priced with an unverified entry
- **THEN** the result states the amount that comes from unverified prices
