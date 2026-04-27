"""OpenSpec change-folder reader for harness tasks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .task import HarnessTask, HarnessTaskError, parse_task


_DELTA_SECTION_RE = re.compile(
    r"^##\s+(?P<delta>ADDED|MODIFIED|REMOVED)\s+Requirements\s*$"
    r"(?P<body>.*?)(?=^##\s+(?:ADDED|MODIFIED|REMOVED)\s+Requirements\s*$|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_REQUIREMENT_RE = re.compile(
    r"^###\s+Requirement:\s*(?P<title>.+?)\s*$"
    r"(?P<body>.*?)(?=^###\s+Requirement:|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_SCENARIO_RE = re.compile(
    r"^####\s+Scenario:\s*(?P<title>.+?)\s*$"
    r"(?P<body>.*?)(?=^####\s+Scenario:|^###\s+Requirement:|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_COMPLETION_GATE_RE = re.compile(
    r"^- \[(?P<state>[ xX])\]\s+(?P<id>[A-Za-z0-9_.:-]+)\s*:\s*(?P<hint>.+?)\s*$"
)
_CHECKLIST_RE = re.compile(r"^- \[(?P<state>[ xX])\]\s+(?P<text>.+?)\s*$")
_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$", re.MULTILINE)
_NORMATIVE_RE = re.compile(r"(?P<sentence>[^.\n]*(?:SHALL|MUST)[^.\n]*(?:\.|$))", re.IGNORECASE)
_SCENARIO_STEP_RE = re.compile(r"^\s*(Given|When|Then|And|But)\b", re.IGNORECASE)
_SENSOR_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_DEFAULT_COMPLETION_GATE_SENSOR = "openspec.completion_gate"


def load_openspec_change(path: str | Path) -> HarnessTask:
    """Load an OpenSpec change folder as a provider-independent harness task."""

    change_path = Path(path)
    errors: list[str] = []

    proposal_path = change_path / "proposal.md"
    tasks_path = change_path / "tasks.md"
    specs_path = change_path / "specs"
    if not proposal_path.is_file():
        errors.append("missing proposal.md")
    if not tasks_path.is_file():
        errors.append("missing tasks.md")
    if not specs_path.is_dir():
        errors.append("missing specs directory")

    capability_files: list[Path] = []
    if specs_path.is_dir():
        capability_files = sorted(specs_path.glob("*/spec.md"))
        if not capability_files:
            errors.append("missing specs/<capability>/spec.md files")

    if errors:
        raise HarnessTaskError("; ".join(errors))

    proposal_text = _read_text(proposal_path)
    tasks_text = _read_text(tasks_path)
    design_path = change_path / "design.md"
    design_text = _read_text(design_path) if design_path.is_file() else None

    requirements, raw_requirements = _parse_requirements(capability_files)
    if not requirements:
        raise HarnessTaskError("OpenSpec change has no requirements")

    gate_hints = _parse_completion_gate(tasks_text)
    acceptance_ids = {item["id"] for item in requirements}
    unknown_gate_ids = [gate_id for gate_id in gate_hints if gate_id not in acceptance_ids]
    if unknown_gate_ids:
        raise HarnessTaskError(
            "completion gate ids must map to acceptance ids: "
            + ", ".join(sorted(unknown_gate_ids))
        )

    acceptance_matrix = []
    for requirement in requirements:
        gate_hint = gate_hints.get(requirement["id"])
        item = {
            "id": requirement["id"],
            "criterion": requirement["criterion"],
            "required_evidence": [],
        }
        if gate_hint:
            item["required_evidence"] = [
                {
                    "sensor": gate_hint["sensor"],
                    "freshness": "after_last_mutation",
                },
            ]
        acceptance_matrix.append(item)

    task_items = _parse_checklist_tasks(tasks_text)
    task_data: dict[str, Any] = {
        "version": "1",
        "id": change_path.name,
        "objective": {"summary": _objective_summary(proposal_text, change_path.name)},
        "intent": {"latest_user_request": _intent_summary(proposal_text, change_path.name)},
        "acceptance_matrix": acceptance_matrix,
        "finalization_policy": {"complete_requires": [item["id"] for item in requirements]},
        "source_format": "openspec",
        "change_id": change_path.name,
        "openspec": {
            "requirements": raw_requirements,
            "completion_gate": list(gate_hints.values()),
            "tasks": task_items,
        },
        "paths": {
            "proposal": str(proposal_path),
            "tasks": str(tasks_path),
            "design": str(design_path) if design_path.is_file() else None,
        },
        "capability_files": {
            capability.parent.name: str(capability)
            for capability in capability_files
        },
    }
    if design_text is not None:
        task_data["design_excerpt"] = _first_content_line(design_text)

    return parse_task(task_data)


def _parse_requirements(capability_files: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    requirements: list[dict[str, Any]] = []
    raw_requirements: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec_file in capability_files:
        capability = spec_file.parent.name
        spec_text = _read_text(spec_file)
        for section in _DELTA_SECTION_RE.finditer(spec_text):
            delta_type = section.group("delta").upper()
            for match in _REQUIREMENT_RE.finditer(section.group("body")):
                raw_requirement = _parse_requirement_match(capability, delta_type, match)
                raw_requirements.append(raw_requirement)
                if delta_type == "REMOVED":
                    continue

                requirement_id = raw_requirement["id"]
                if requirement_id in seen:
                    raise HarnessTaskError(f"acceptance id must be unique: {requirement_id}")
                seen.add(requirement_id)
                criterion = f"{raw_requirement['title']}: {raw_requirement['normative_sentence']}"
                scenario_summaries = [
                    scenario["summary"]
                    for scenario in raw_requirement["scenarios"]
                ]
                if scenario_summaries:
                    criterion = f"{criterion} " + "; ".join(scenario_summaries)
                requirements.append({"id": requirement_id, "criterion": criterion})
    return requirements, raw_requirements


def _parse_requirement_match(capability: str, delta_type: str, match: re.Match[str]) -> dict[str, Any]:
    title = match.group("title").strip()
    requirement_id = _slug(f"{capability}-{title}")
    body = match.group("body")
    normative_sentence = _normative_sentence(body)
    if delta_type != "REMOVED" and normative_sentence is None:
        raise HarnessTaskError(
            f"requirement {requirement_id} must contain SHALL or MUST normative text"
        )
    return {
        "id": requirement_id,
        "capability": capability,
        "title": title,
        "delta_type": delta_type,
        "normative_sentence": normative_sentence,
        "scenarios": _parse_scenarios(body),
    }


def _parse_scenarios(requirement_body: str) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for match in _SCENARIO_RE.finditer(requirement_body):
        title = match.group("title").strip()
        steps = [
            line.strip()
            for line in match.group("body").splitlines()
            if line.strip() and _SCENARIO_STEP_RE.match(line)
        ]
        step_kinds = {step.split(maxsplit=1)[0].lower() for step in steps}
        if steps and not {"given", "when", "then"}.issubset(step_kinds):
            raise HarnessTaskError(f"scenario {title} must include Given, When, and Then steps")
        body = " ".join(steps)
        scenarios.append(
            {
                "title": title,
                "steps": steps,
                "summary": f"{title} - {body}" if body else title,
            }
        )
    return scenarios


def _parse_completion_gate(tasks_text: str) -> dict[str, dict[str, Any]]:
    gate_text = _section_text(tasks_text, "Completion Gate")
    if gate_text is None:
        return {}

    hints: dict[str, dict[str, Any]] = {}
    for line in gate_text.splitlines():
        match = _COMPLETION_GATE_RE.match(line.strip())
        if not match:
            continue
        gate_id = match.group("id")
        if gate_id in hints:
            raise HarnessTaskError(f"completion gate id must be unique: {gate_id}")
        hint = match.group("hint").strip()
        hints[gate_id] = {
            "id": gate_id,
            "checked": match.group("state").lower() == "x",
            "text": hint,
            "sensor": _completion_gate_sensor(hint),
        }
    return hints


def _parse_checklist_tasks(tasks_text: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for line in tasks_text.splitlines():
        match = _CHECKLIST_RE.match(line.strip())
        if not match:
            continue
        tasks.append(
            {
                "checked": match.group("state").lower() == "x",
                "text": match.group("text").strip(),
            }
        )
    return tasks


def _normative_sentence(requirement_body: str) -> str | None:
    body_without_scenarios = _SCENARIO_RE.sub("", requirement_body)
    match = _NORMATIVE_RE.search(body_without_scenarios)
    if not match:
        return None
    return " ".join(match.group("sentence").strip().split())


def _completion_gate_sensor(hint: str) -> str:
    if hint.startswith("sensor:"):
        candidate = hint.removeprefix("sensor:").strip()
        if _SENSOR_ID_RE.match(candidate):
            return candidate
    return _DEFAULT_COMPLETION_GATE_SENSOR


def _objective_summary(proposal_text: str, fallback: str) -> str:
    return _section_first_line(proposal_text, "What") or _section_first_line(proposal_text, "Why") or fallback


def _intent_summary(proposal_text: str, fallback: str) -> str:
    return _section_first_line(proposal_text, "Why") or _first_content_line(proposal_text) or fallback


def _section_first_line(markdown: str, title: str) -> str | None:
    section = _section_text(markdown, title)
    if section is None:
        return None
    return _first_content_line(section)


def _section_text(markdown: str, title: str) -> str | None:
    headings = list(_HEADING_RE.finditer(markdown))
    for index, match in enumerate(headings):
        if match.group("title").strip().lower() != title.lower():
            continue
        level = len(match.group("marks"))
        start = match.end()
        end = len(markdown)
        for next_match in headings[index + 1:]:
            if len(next_match.group("marks")) <= level:
                end = next_match.start()
                break
        return markdown[start:end].strip()
    return None


def _first_content_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped.lstrip("- ").strip()
    return ""


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "requirement"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HarnessTaskError(f"could not read OpenSpec file {path}: {exc}") from exc
