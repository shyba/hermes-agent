# harness-openspec-reader Specification

## ADDED Requirements

### Requirement: Loads change folders
The system SHALL derive a HarnessTask from an OpenSpec change folder.

#### Scenario: Valid folder
Given proposal.md, tasks.md, and specs/harness-openspec-reader/spec.md
When the OpenSpec reader loads the folder
Then it returns a HarnessTask with acceptance criteria.

#### Scenario: Multiple scenarios
Given a spec with two Scenario blocks
When requirements are parsed
Then both scenarios are included in the criterion text.

### Requirement: Reports validation errors
The system SHALL reject malformed OpenSpec change folders.

#### Scenario: Missing specs
Given proposal.md and tasks.md but no specs directory
When the OpenSpec reader loads the folder
Then it raises a harness task validation error.
