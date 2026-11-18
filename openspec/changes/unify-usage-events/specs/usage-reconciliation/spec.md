## ADDED Requirements

### Requirement: Stored totals are checked against the raw logs
The system SHALL compare, for each log file, the totals stored for that file with totals computed from the file itself, and SHALL report every file where they differ.

#### Scenario: Codex file matches its cumulative totals
- **WHEN** the stored input plus cache read tokens and the stored output tokens for a Codex file equal the sum of the last cumulative usage of each thread in that file
- **THEN** the file is reported as matching

#### Scenario: Gemini file matches its message count
- **WHEN** the number of stored events for a Gemini file equals the number of distinct ids among its usage records
- **THEN** the file is reported as matching

#### Scenario: Claude file matches its distinct responses
- **WHEN** the number of stored events for a Claude transcript equals the number of distinct `message.id` and `requestId` pairs with usage in it
- **THEN** the file is reported as matching

#### Scenario: A file does not match
- **WHEN** stored totals for a file differ from the totals computed from the file
- **THEN** the report names the file, the source, and both figures

### Requirement: Verification runs after every scan
The system SHALL run the check for the files read during a scan and print the number of files checked and the number of mismatches.

#### Scenario: Scan with no mismatches
- **WHEN** a scan finishes and every file read matches
- **THEN** the scan output states the number of files checked and zero mismatches

### Requirement: Verification is available as a command
The system SHALL provide a command that checks every log file on disk and exits with a non-zero status when any file does not match.

#### Scenario: Full verification passes
- **WHEN** the verify command is run and all files match
- **THEN** it exits with status zero

#### Scenario: Full verification fails
- **WHEN** the verify command is run and at least one file does not match
- **THEN** it lists the mismatches and exits with a non-zero status

### Requirement: Estimated events are excluded from the check
The system SHALL NOT check events marked `estimated`, because their source files no longer exist, and SHALL report how many were skipped.

#### Scenario: Database with legacy events
- **WHEN** the verify command is run on a database that contains `estimated` events
- **THEN** those events are not compared and the output states their count
