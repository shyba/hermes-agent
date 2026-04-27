---
name: hermes-harness-specs
description: "Write, review, and refine OpenSpec-compatible change artifacts for Hermes-managed work, including proposal.md, tasks.md, capability specs, and optional design.md. Use when Codex is asked to draft a harness spec, decompose a user request into requirements and completion checks, or validate completed work against a spec."
---

# Hermes Harness Specs

## Purpose

Write high-signal OpenSpec-compatible change artifacts for work the user will run through Hermes. Codex should define the change, requirements, task checklist, and completion validation; the user operates Hermes.

Use `references/spec-format.md` when you need artifact structure, examples, Requirement block guidance, or a validation checklist.

## Workflow

1. Restate the requested change as a concise objective.
2. Choose a short kebab-case change ID and one or more capability names.
3. Draft `proposal.md` with the problem, proposed change, impact, and explicit non-goals when helpful.
4. Draft `specs/<capability>/spec.md` using Requirement blocks with parser-compatible normative SHALL/MUST language and scenario examples.
5. Draft `tasks.md` as an implementation and validation checklist. Include the Hermes-specific evidence and finalization rules as a `Completion Gate` section.
6. Add `design.md` only when architecture, migration, data model, sequencing, or tradeoffs need more detail than the proposal can carry.
7. Validate the artifacts against the checklist in `references/spec-format.md`.

## Artifact Rules

OpenSpec artifacts are the source format. Do not produce Hermes `task.yaml` unless the user explicitly asks for legacy YAML.

Expected output tree:

```text
changes/<change-id>/
  proposal.md
  tasks.md
  specs/<capability>/spec.md
  design.md (optional)
```

`proposal.md` should be brief and decision-oriented. Include `Why`, `What Changes`, and `Impact`. Add `Non-Goals` when it prevents scope creep.

`specs/<capability>/spec.md` should start with `# Capability: <Title>` and contain Requirement blocks. Each Requirement must describe observable behavior and include at least one Scenario that can be checked after implementation.

`tasks.md` should list concrete work items and validation items. Keep Hermes-specific evidence, final response expectations, and completion status rules under `## Completion Gate`; do not create separate YAML for them.

`design.md` is optional. Use it for complex changes only, especially when multiple systems, migrations, compatibility concerns, or rejected alternatives matter.

## Requirement Blocks

Use concise normative language:

- `SHALL` for required behavior the implementation must provide.
- `MUST` for constraints, compatibility, validation, or safety requirements.
- Avoid vague verbs such as "support", "handle", or "improve" unless the observable outcome is named.
- The first non-empty line immediately after every `### Requirement:` heading MUST be a single physical line containing `SHALL` or `MUST`.
- Do not wrap the first normative sentence across multiple lines. Some Hermes/OpenSpec validators inspect only the immediate line after the heading.
- Prefer `The system SHALL ...` as the first sentence unless another actor is clearer.

Use GIVEN/WHEN/THEN scenarios:

```markdown
### Requirement: Preserve tool result linkage
The system SHALL preserve each tool result's `tool_call_id` when converting provider transcripts.

#### Scenario: Linked tool result survives conversion
- GIVEN an assistant message with a tool call id `call_123`
- WHEN the transcript is converted for replay
- THEN the corresponding tool result MUST retain `tool_call_id` `call_123`
```

Prefer several small Requirements over one broad Requirement. If a behavior depends on ordering, identity, freshness, or exact content, name that explicitly in the Requirement and Scenario.

## Completion Gate

In `tasks.md`, include a `## Completion Gate` section that says what evidence must exist before the work can be called complete.

The gate should require:

- changed files reviewed against the OpenSpec artifacts
- focused executable verification when code changed, using the repo's prescribed test command
- docs, screenshots, logs, or manual evidence when the task is docs-only, visual, interactive, or environmental
- evidence freshness after the last relevant mutation
- unresolved failures, blocked commands, or unverified Requirements reported as not complete
- final response includes changed files and validation result when the user asks for that

Do not encode Hermes agent orchestration, subagent trees, or task ownership mechanics unless the user explicitly requested those details.

## Completion Validation

When asked to validate completed work, compare actual artifacts and evidence to the OpenSpec Requirements and `tasks.md` Completion Gate.

Check:

- every Requirement with required behavior has matching evidence
- every required task and validation checklist item is complete or explicitly marked blocked
- evidence was produced after the last mutation that could affect the covered behavior; when impact is unclear, treat it as stale
- failed tools, failed tests, failed sensors, or blocked work remain blocking unless rerun or resolved
- claimed completion matches diffs, tests, logs, screenshots, command output, or review notes

Report validation as:

```text
status: complete|partial|blocked|unverified
complete: [requirements/tasks]
missing: [requirements/tasks]
blocking: [event-or-reason ids]
evidence: short list of commands/files/logs checked
```

Use `complete` only when all required Requirements and Completion Gate items have fresh supporting evidence and no unresolved blocker remains.

Use `partial` when at least one required item is complete and at least one required item is missing, stale, or unresolved.

Use `blocked` when a concrete unresolved failure, unavailable dependency, policy issue, or missing capability prevents completion.

Use `unverified` when the work may be done but evidence is not fresh or sufficient enough to trust.

## Output Shape

When drafting artifacts, output Markdown file contents with clear filenames. Keep explanation short unless the user asks for rationale.

When reviewing artifacts, lead with blocking issues, then provide corrected snippets only for the affected sections.

## Parser Compatibility Checks

Before handing off artifacts, run a local shape check when possible:

```sh
git diff --check changes/<change-id>
awk '/^### Requirement:/{req=$0; getline; if ($0 !~ /(SHALL|MUST)/) print req " -> " $0}' changes/<change-id>/specs/*/spec.md
```

The `awk` command should print nothing. If it prints any Requirement, rewrite that Requirement so the first line after the heading contains the normative `SHALL`/`MUST` sentence.

Hermes harness runs are created from the change root, for example `/harness run changes/<change-id>`, not from a nested `specs/<capability>` directory.
