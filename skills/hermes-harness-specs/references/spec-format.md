# OpenSpec Change Artifact Format

Use this reference when drafting or reviewing OpenSpec-compatible change artifacts for Hermes-managed work.

## Minimal Artifact Tree

```text
changes/<change-id>/
  proposal.md
  tasks.md
  specs/<capability>/spec.md
```

Add `design.md` only when the change needs architecture, sequencing, migration, tradeoff, or compatibility detail.

## proposal.md

Keep `proposal.md` short and decision-oriented:

```markdown
# Change: Preserve transcript linkage during provider conversion

## Why
Provider transcript conversion can drop or reorder tool linkage, making replay and validation unreliable.

## What Changes
- Preserve assistant tool call IDs during conversion.
- Preserve corresponding tool result linkage and ordering.
- Add regression coverage for replay conversion.

## Impact
- Affected specs: transcript-conversion
- Affected code: provider transcript conversion and tests

## Non-Goals
- No changes to provider API routing.
```

Required sections:

- `Why`: the problem or user-visible need.
- `What Changes`: concrete behavior or artifact changes.
- `Impact`: affected capabilities, files, systems, tests, or users when known.

Optional sections:

- `Non-Goals`: boundaries that prevent scope creep.
- `Open Questions`: only when the user must decide before implementation.

## specs/<capability>/spec.md

Write specs as Requirement blocks. Each Requirement should describe observable behavior and include at least one Scenario.

The file MUST start with `# Capability: <Title>`. The first non-empty line immediately after every `### Requirement:` heading MUST be one physical line containing `SHALL` or `MUST`; do not wrap that first normative sentence across lines.

```markdown
# Capability: Transcript Conversion

## ADDED Requirements

### Requirement: Preserve tool result linkage
The system SHALL preserve each tool result's `tool_call_id` when converting provider transcripts.

#### Scenario: Linked tool result survives conversion
- GIVEN an assistant message with a tool call id `call_123`
- WHEN the transcript is converted for replay
- THEN the corresponding tool result MUST retain `tool_call_id` `call_123`
```

Use these section headers when relevant:

- `## ADDED Requirements`
- `## MODIFIED Requirements`
- `## REMOVED Requirements`

### Requirement Guidance

Good Requirements:

- use `SHALL` for required system behavior
- use `MUST` for required constraints, validation, compatibility, or safety properties
- put the normative `SHALL` or `MUST` sentence on the first physical line immediately after the `### Requirement:` heading
- identify the actor, system, condition, and observable outcome
- avoid bundling unrelated behavior into one Requirement
- name exact preservation requirements for ordering, IDs, metadata, content, or freshness

Weak:

```markdown
### Requirement: Better transcript handling
The system SHALL handle transcripts robustly.
```

Better:

```markdown
### Requirement: Preserve replay message order
The system SHALL preserve the relative order of assistant tool calls and tool results when preparing replay transcripts.

#### Scenario: Tool result follows its call
- GIVEN a transcript containing an assistant tool call followed by its tool result
- WHEN the transcript is prepared for replay
- THEN the tool result MUST appear after the assistant tool call
- AND the tool result MUST reference the original tool call ID
```

### Scenario Guidance

Use GIVEN/WHEN/THEN bullets:

- `GIVEN`: initial state, input, config, or prerequisite.
- `WHEN`: the action under test.
- `THEN`: required observable outcome.
- `AND`: extra required observations under the same scenario.

Keep scenarios concrete enough that a reviewer can map them to tests, logs, screenshots, docs, or manual reproduction steps.

## tasks.md

Use `tasks.md` for implementation tasks, validation tasks, and Hermes-specific completion evidence.

```markdown
# Tasks

## Implementation
- [ ] Update transcript conversion to preserve tool call IDs.
- [ ] Add regression coverage for replay conversion.

## Validation
- [ ] Run `scripts/run_tests.sh tests/agent/test_transcript_conversion.py`.
- [ ] Review changed files against `changes/preserve-transcript-linkage/specs/transcript-conversion/spec.md`.

## Completion Gate
- [ ] Every Requirement scenario has matching fresh evidence.
- [ ] Focused tests pass after the last relevant code mutation.
- [ ] Changed files are reviewed against the OpenSpec artifacts.
- [ ] Unresolved failures, blocked commands, or unverified Requirements are reported as not complete.
- [ ] Final response lists changed files and validation result.
```

Completion Gate rules:

- Keep Hermes-specific evidence and finalization policy here, not in YAML.
- Require executable verification for code changes unless blocked or inappropriate.
- For docs-only, visual, interactive, or environmental work, require concrete review evidence such as rendered docs, screenshots, logs, or manual reproduction.
- Evidence must be fresh after the last mutation that could affect the covered Requirement. If impact is unclear, treat evidence as stale.
- Do not mark complete when tools, tests, sensors, or approvals failed and were not resolved.

## design.md

Use `design.md` only when useful:

```markdown
# Design

## Context
Current transcript conversion passes through provider-specific adapters before replay.

## Decisions
- Store original tool call IDs through the conversion boundary.
- Validate ordering at the replay-preparation layer.

## Alternatives Considered
- Reconstruct IDs during replay. Rejected because it breaks exact linkage validation.

## Risks
- Provider-specific adapters may normalize message structures differently.
```

Good reasons to include `design.md`:

- multiple components or ownership boundaries are affected
- migration, rollback, compatibility, or data shape needs explanation
- implementation order matters
- rejected alternatives affect review

## Validation Checklist

Before handing off artifacts:

- Artifact paths follow `changes/<change-id>/...`.
- `proposal.md` has `Why`, `What Changes`, and `Impact`.
- Every affected capability has a `specs/<capability>/spec.md`.
- Every spec starts with `# Capability: <Title>`.
- Requirement blocks use SHALL/MUST language for required behavior.
- Every Requirement has `SHALL` or `MUST` on the first physical line after its heading.
- Every Requirement has at least one GIVEN/WHEN/THEN Scenario.
- Scenarios are observable without trusting model prose.
- `tasks.md` has implementation and validation tasks appropriate to the request.
- `tasks.md` includes a `Completion Gate` with Hermes-specific evidence and finalization requirements.
- Completion evidence is not duplicated into a separate Hermes YAML source format.
- `design.md` is present only when it adds useful architecture or tradeoff context.
- The artifacts do not tell Hermes how to operate unless the user explicitly requested orchestration details.

## Post-Completion Validation Checklist

After work completes, validate against the OpenSpec artifacts:

- List changed files, diffs, commands, logs, screenshots, command history, and review notes used as evidence when available and relevant.
- Mark each Requirement complete only if fresh evidence covers its required behavior and scenarios.
- Treat evidence before the last relevant mutation as stale; when mutation impact is unclear, stale the evidence.
- Treat unresolved failed tools, tests, sensors, approvals, or policy blocks as blocking.
- Use `complete` when every required Requirement and Completion Gate item has fresh evidence and no unresolved blocker remains.
- Use `partial` when at least one required item is freshly complete and at least one required item is missing, stale, or unresolved.
- Use `blocked` when progress is stopped by a concrete unresolved blocker.
- Use `unverified` when the work may be done but evidence is not fresh or sufficient enough to trust.
