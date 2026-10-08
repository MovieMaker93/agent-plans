# SPDX-License-Identifier: Apache-2.0
"""Surgical edits to hand-written plan.yaml and log.md.

Generated files go through yamlio.dump_yaml. plan.yaml keeps its comments,
so status, actuals, calibrated hours, and version are patched in place.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from planner.util import fmt_hours

_TASK_ID = re.compile(r"^  - id: (\S+)\s*$")
_VERSION = re.compile(r"^version:\s*(\d+)\s*$")


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _join(lines: list[str]) -> str:
    return "".join(lines)


def task_spans(lines: list[str]) -> list[tuple[str, int, int]]:
    """(task id, start line, end line) with end exclusive."""
    starts: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        match = _TASK_ID.match(line.rstrip("\n"))
        if match:
            starts.append((match.group(1), index))
    spans = []
    for index, (task_id, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(lines)
        spans.append((task_id, start, end))
    return spans


def _span(lines: list[str], task_id: str) -> tuple[int, int]:
    for found, start, end in task_spans(lines):
        if found == task_id:
            return start, end
    raise ValueError(f"task {task_id} not found in plan.yaml")


def _yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, Decimal):
        return fmt_hours(value)
    text = str(value).strip()
    if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", text) and text.lower() not in {
        "true",
        "false",
        "null",
        "yes",
        "no",
    }:
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def read_version(text: str) -> int:
    for line in text.splitlines():
        match = _VERSION.match(line.strip())
        if match and not line.startswith(" "):
            return int(match.group(1))
    raise ValueError("plan.yaml is missing a top-level version")


def set_version(text: str, version: int) -> str:
    lines = _lines(text)
    for index, line in enumerate(lines):
        if _VERSION.match(line.rstrip("\n")) and not line.startswith(" "):
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"version: {version}{newline}"
            return _join(lines)
    raise ValueError("plan.yaml is missing a top-level version")


def set_status(text: str, task_id: str, status: str) -> str:
    lines = _lines(text)
    start, end = _span(lines, task_id)
    for index in range(start, end):
        if lines[index].startswith("    status:"):
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = f"    status: {status}{newline}"
            return _join(lines)
    raise ValueError(f"{task_id} is missing status")


def set_actuals(
    text: str,
    task_id: str,
    *,
    start: str | None = None,
    end: str | None = None,
    hours: Decimal | None = None,
    attempts: int | None = None,
    set_start: bool = False,
    set_end: bool = False,
    set_hours: bool = False,
    set_attempts: bool = False,
) -> str:
    """Replace actuals fields. A flag must be set for the value to be written, so null is writable."""
    lines = _lines(text)
    span_start, span_end = _span(lines, task_id)
    actual_at = None
    for index in range(span_start, span_end):
        if lines[index].startswith("    actuals:"):
            actual_at = index
            break
    if actual_at is None:
        raise ValueError(f"{task_id} is missing actuals")
    block_end = actual_at + 1
    while block_end < span_end:
        line = lines[block_end]
        if line.strip() == "" or line.startswith("      "):
            block_end += 1
            continue
        break
    values = {
        "start": _yaml_scalar(start) if set_start else None,
        "end": _yaml_scalar(end) if set_end else None,
        "hours": _yaml_scalar(hours) if set_hours else None,
        "attempts": _yaml_scalar(attempts) if set_attempts else None,
    }
    seen = set()
    for index in range(actual_at + 1, block_end):
        for key, rendered in values.items():
            if rendered is None:
                continue
            if lines[index].lstrip().startswith(f"{key}:"):
                newline = "\n" if lines[index].endswith("\n") else ""
                lines[index] = f"      {key}: {rendered}{newline}"
                seen.add(key)
    missing = [key for key, rendered in values.items() if rendered is not None and key not in seen]
    if missing:
        raise ValueError(f"{task_id} actuals is missing {', '.join(missing)}")
    return _join(lines)


def set_calibrated(text: str, task_id: str, hours: Decimal) -> str:
    lines = _lines(text)
    start, end = _span(lines, task_id)
    estimate_at = None
    for index in range(start, end):
        if lines[index].startswith("    estimate_hours:"):
            estimate_at = index
            break
    if estimate_at is None:
        raise ValueError(f"{task_id} is missing estimate_hours")
    block_end = estimate_at + 1
    while block_end < end:
        line = lines[block_end]
        if line.strip() == "" or line.startswith("      "):
            block_end += 1
            continue
        break
    rendered = _yaml_scalar(hours)
    new_line = f"      calibrated: {rendered}\n"
    for index in range(estimate_at + 1, block_end):
        if lines[index].lstrip().startswith("calibrated:"):
            lines[index] = new_line
            return _join(lines)
    insert_at = block_end
    for index in range(estimate_at + 1, block_end):
        if lines[index].lstrip().startswith("pessimistic:"):
            insert_at = index + 1
            break
    if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"
    lines.insert(insert_at, new_line)
    return _join(lines)


def append_log(path: Path, entry: str) -> None:
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
    else:
        existing = (
            "# Decision log\n\n"
            "Append-only. Add new entries at the bottom. Do not edit earlier entries.\n"
        )
    if not existing.endswith("\n"):
        existing += "\n"
    if not existing.endswith("\n\n"):
        existing += "\n"
    path.write_text(existing + entry.rstrip() + "\n", encoding="utf-8")


def log_entry(
    *,
    version: int,
    at: str,
    kind: str,
    reason: str,
    change: str,
    frozen: list[str],
    assignee_frozen: list[str] | None = None,
    bypass: str | None = None,
) -> str:
    date = at[:10] if len(at) >= 10 else at
    frozen_text = ", ".join(frozen) if frozen else "none"
    lines = [
        f"## {date} v{version}",
        "",
        f"- Kind: {kind}",
        f"- At: {at}",
        f"- Reason: {reason}",
        f"- Change: {change}",
        f"- Frozen: {frozen_text}",
    ]
    if assignee_frozen is not None:
        listed = ", ".join(assignee_frozen) if assignee_frozen else "none"
        lines.append(f"- Assignee-frozen: {listed}")
    if bypass:
        lines.append(f"- Bypass: {bypass}")
    return "\n".join(lines) + "\n"
