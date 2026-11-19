## ADDED Requirements

### Requirement: One event per API response
The system SHALL store exactly one event for each API response found in the logs of Claude Code, Codex CLI and Gemini CLI, identified by a key that is unique within its source.

#### Scenario: Claude response written as several records
- **WHEN** a Claude transcript contains three assistant records with the same `message.id` and `requestId`
- **THEN** exactly one event is stored for them

#### Scenario: Gemini message written twice
- **WHEN** a Gemini chat file contains two records with the same id
- **THEN** one event is stored, with the values of the last record

#### Scenario: Codex file with token usage records
- **WHEN** a Codex rollout file contains `token_usage_record` lines
- **THEN** one event is stored per `response_id` and the file's `token_count` events are not counted

#### Scenario: Codex file in the older format
- **WHEN** a Codex rollout file contains only `token_count` events, some repeated with an unchanged cumulative total
- **THEN** one event is stored per distinct cumulative total and events with null info are skipped

### Requirement: Scanning is idempotent
The system SHALL produce the same stored events no matter how many times the same logs are scanned.

#### Scenario: Second scan with no changes
- **WHEN** a scan is run twice in a row with no change to the logs
- **THEN** the second scan adds zero events and changes no totals

#### Scenario: A log file grows
- **WHEN** new responses are appended to a file that was already scanned and a scan is run
- **THEN** only the new responses are added

#### Scenario: Unchanged files are skipped
- **WHEN** a file's modification time and size are the same as at the previous scan
- **THEN** the file is not read again

### Requirement: Token categories are stored separately
The system SHALL store, for each event, uncached input tokens, output tokens, cache read tokens, 5-minute cache write tokens and 1-hour cache write tokens as separate values, together with the model, timestamp, account, session, project, speed and sidechain flag.

#### Scenario: Claude response with both cache write kinds
- **WHEN** a Claude record reports `ephemeral_5m_input_tokens` of 1000 and `ephemeral_1h_input_tokens` of 4000
- **THEN** the event stores 1000 as 5-minute cache write tokens and 4000 as 1-hour cache write tokens

#### Scenario: Codex input includes cached tokens
- **WHEN** a Codex usage record reports `input_tokens` of 10000 and `cached_input_tokens` of 9000
- **THEN** the event stores 1000 input tokens and 9000 cache read tokens

#### Scenario: Fast mode response
- **WHEN** a Claude record reports `speed` as `fast`
- **THEN** the event is stored with speed `fast`

### Requirement: Sessions are derived from events
The system SHALL compute session totals and daily totals from the stored events at query time and SHALL NOT keep separately accumulated counters.

#### Scenario: Session total matches its events
- **WHEN** a session's totals are requested
- **THEN** each total equals the sum of that value over the session's events

### Requirement: Missing tools do not cause errors
The system SHALL complete a scan when the directory of any supported tool does not exist.

#### Scenario: Gemini is not installed
- **WHEN** `~/.gemini` does not exist and a scan is run
- **THEN** the scan finishes without error and reports zero Gemini files

### Requirement: Legacy history is kept as estimates
The system SHALL import usage rows whose transcripts are no longer on disk as events marked `estimated`, with duplicated rows collapsed, and SHALL NOT import a legacy row for a session that was rebuilt from a transcript.

#### Scenario: Session exists only in the old table
- **WHEN** migration finds a session in the old `turns` table with no transcript on disk
- **THEN** its rows are imported as `estimated` events after duplicate copies are collapsed

#### Scenario: Session exists in both places
- **WHEN** migration finds a session in the old `turns` table and also on disk
- **THEN** only the events built from the transcript are stored for it

### Requirement: Migration cannot lose data
The system SHALL migrate the live database by building and verifying a copy before replacing the live file, SHALL keep a backup of the file as it was, and SHALL leave the existing tables in place.

#### Scenario: Verification fails during migration
- **WHEN** the reconciliation check reports a mismatch on the working copy
- **THEN** the live database is not modified and the command exits with a non-zero status

#### Scenario: Migration succeeds
- **WHEN** the working copy passes verification
- **THEN** a timestamped backup exists, the live file is replaced, and a before and after summary by month and source is printed

### Requirement: Scheduled scan is opt-in
The system SHALL provide a command that installs a daily scan and SHALL NOT install any scheduled job unless that command is run.

#### Scenario: User installs the schedule
- **WHEN** the user runs the install command
- **THEN** a daily scan job is installed and the command prints its location and how to remove it

### Requirement: Each log format version is covered by a test
The system SHALL include automated tests, runnable with the Python standard library, that parse a synthetic sample of every supported log format version.

#### Scenario: Running the test suite
- **WHEN** `python3 -m unittest` is run in the repository
- **THEN** tests for the Claude format, both Codex formats and the Gemini format run and pass
