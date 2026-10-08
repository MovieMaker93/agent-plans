# SPDX-License-Identifier: Apache-2.0
"""Artifact manifests and Accuracy attestations.

A task may be `done` only when attestations/<task_id>.yaml records verdict
pass from reviewer accuracy, and the sha256 values match
artifacts/<task_id>/manifest.yaml.

Git cannot prove which bot wrote the file. This check is enforcement
inside the tool chain: validate and CI reject an unattested done.
Signatures are a later phase. The signature field is stored and not checked.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from planner.model import Plan, Task
from planner.util import parse_datetime
from planner.yamlio import YamlError, dump_yaml, load_yaml

REVIEWER = "accuracy"
_SHA = re.compile(r"^[0-9a-f]{64}$")

MANIFEST_HEADER = (
    "# Artifact manifest. Hashes are inputs to the done-lock.\n"
    "# Written by `python -m planner record-artifact`.\n"
)
ATTEST_HEADER = (
    "# Accuracy attestation. `status: done` requires verdict pass and reviewer accuracy.\n"
    "# Written by `python -m planner attest-done`. signature is not verified yet.\n"
)


@dataclass
class ArtifactRecord:
    artifact: str
    sha256: str
    size: int
    uri: str


@dataclass
class Manifest:
    plan_id: str
    task_id: str
    producer: str
    attempt: int
    artifacts: dict[str, ArtifactRecord] = field(default_factory=dict)


@dataclass
class Criterion:
    text: str
    met: bool


@dataclass
class Attestation:
    plan_id: str
    task_id: str
    verdict: str
    reviewer: str
    timestamp: str
    notes: str
    attempt: int
    criteria: list[Criterion]
    artifacts: dict[str, ArtifactRecord]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_path(plan: Plan, task_id: str) -> Path:
    if plan.directory is None:
        raise ValueError("plan has no directory")
    return plan.directory / "artifacts" / task_id / "manifest.yaml"


def attestation_path(plan: Plan, task_id: str) -> Path:
    if plan.directory is None:
        raise ValueError("plan has no directory")
    return plan.directory / "attestations" / f"{task_id}.yaml"


def _record_from(raw, where: str) -> ArtifactRecord:
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be a mapping")
    name = str(raw.get("artifact") or "").strip()
    digest = str(raw.get("sha256") or "").strip().lower()
    uri = str(raw.get("uri") or "").strip()
    size = raw.get("size", 0)
    if not name:
        raise ValueError(f"{where}.artifact must be set")
    if not _SHA.match(digest):
        raise ValueError(f"{where}.sha256 must be 64 lowercase hex characters")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError(f"{where}.size must be an integer >= 0")
    if not uri:
        raise ValueError(f"{where}.uri must be set")
    return ArtifactRecord(artifact=name, sha256=digest, size=size, uri=uri)


def load_manifest(path: Path) -> Manifest:
    try:
        data = load_yaml(path)
    except YamlError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a mapping")
    if data.get("schema_version") != 1:
        raise ValueError(f"{path} schema_version must be 1")
    attempt = data.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError(f"{path} attempt must be an integer >= 1")
    artifacts: dict[str, ArtifactRecord] = {}
    for index, row in enumerate(data.get("artifacts") or []):
        record = _record_from(row, f"artifacts[{index}]")
        artifacts[record.artifact] = record
    return Manifest(
        plan_id=str(data.get("plan_id") or ""),
        task_id=str(data.get("task_id") or ""),
        producer=str(data.get("producer") or ""),
        attempt=attempt,
        artifacts=artifacts,
    )


def render_manifest(manifest: Manifest) -> str:
    artifacts = []
    for name in sorted(manifest.artifacts):
        record = manifest.artifacts[name]
        artifacts.append(
            {
                "artifact": record.artifact,
                "sha256": record.sha256,
                "size": record.size,
                "uri": record.uri,
            }
        )
    body = {
        "schema_version": 1,
        "plan_id": manifest.plan_id,
        "task_id": manifest.task_id,
        "producer": manifest.producer,
        "attempt": manifest.attempt,
        "artifacts": artifacts,
    }
    return MANIFEST_HEADER + dump_yaml(body)


def write_manifest(path: Path, manifest: Manifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_manifest(manifest), encoding="utf-8")


def relative_uri(plan_dir: Path, file_path: Path) -> str:
    resolved = file_path.resolve()
    try:
        return resolved.relative_to(plan_dir.resolve()).as_posix()
    except ValueError:
        return file_path.as_posix()


def record_artifact(
    plan: Plan,
    task: Task,
    file_path: Path,
    *,
    name: str | None = None,
    producer: str | None = None,
    attempt: int | None = None,
) -> Manifest:
    if plan.directory is None:
        raise ValueError("plan has no directory")
    if not file_path.is_file():
        raise ValueError(f"file not found: {file_path}")
    artifact = (name or file_path.name).strip()
    known = {item.artifact for item in task.outputs}
    if artifact not in known:
        listed = ", ".join(sorted(known)) or "(none)"
        raise ValueError(f"{artifact} is not an output of {task.id} (outputs: {listed})")
    data = file_path.read_bytes()
    record = ArtifactRecord(
        artifact=artifact,
        sha256=sha256_bytes(data),
        size=len(data),
        uri=relative_uri(plan.directory, file_path),
    )
    path = manifest_path(plan, task.id)
    if path.is_file():
        manifest = load_manifest(path)
    else:
        manifest = Manifest(
            plan_id=plan.plan_id,
            task_id=task.id,
            producer=producer or task.assignee,
            attempt=attempt if attempt is not None else max(task.actuals.attempts, 1),
            artifacts={},
        )
    if producer:
        manifest.producer = producer
    if attempt is not None:
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        manifest.attempt = attempt
    manifest.plan_id = plan.plan_id
    manifest.task_id = task.id
    manifest.artifacts[artifact] = record
    write_manifest(path, manifest)
    return manifest


def load_attestation(path: Path) -> Attestation:
    try:
        data = load_yaml(path)
    except YamlError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a mapping")
    if data.get("schema_version") != 1:
        raise ValueError(f"{path} schema_version must be 1")
    verdict = str(data.get("verdict") or "").strip()
    if verdict not in {"pass", "fail"}:
        raise ValueError(f"{path} verdict must be pass or fail")
    reviewer = str(data.get("reviewer") or "").strip()
    timestamp = str(data.get("timestamp") or "").strip()
    if not timestamp:
        raise ValueError(f"{path} timestamp must be set")
    parse_datetime(timestamp, f"{path} timestamp")
    attempt = data.get("attempt", 1)
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError(f"{path} attempt must be an integer >= 1")
    criteria: list[Criterion] = []
    raw_criteria = data.get("criteria")
    if not isinstance(raw_criteria, list) or not raw_criteria:
        raise ValueError(f"{path} criteria must be a non-empty list")
    for index, row in enumerate(raw_criteria):
        if not isinstance(row, dict):
            raise ValueError(f"{path} criteria[{index}] must be a mapping")
        text = str(row.get("text") or "").strip()
        met = row.get("met")
        if not text:
            raise ValueError(f"{path} criteria[{index}].text must be set")
        if not isinstance(met, bool):
            raise ValueError(f"{path} criteria[{index}].met must be true or false")
        criteria.append(Criterion(text=text, met=met))
    artifacts: dict[str, ArtifactRecord] = {}
    for index, row in enumerate(data.get("artifacts") or []):
        if not isinstance(row, dict):
            raise ValueError(f"{path} artifacts[{index}] must be a mapping")
        name = str(row.get("artifact") or "").strip()
        digest = str(row.get("sha256") or "").strip().lower()
        uri = str(row.get("uri") or "").strip()
        if not name or not _SHA.match(digest):
            raise ValueError(f"{path} artifacts[{index}] needs artifact and sha256")
        size = row.get("size", 0)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            size = 0
        artifacts[name] = ArtifactRecord(artifact=name, sha256=digest, size=size, uri=uri or name)
    return Attestation(
        plan_id=str(data.get("plan_id") or ""),
        task_id=str(data.get("task_id") or ""),
        verdict=verdict,
        reviewer=reviewer,
        timestamp=timestamp,
        notes=str(data.get("notes") or ""),
        attempt=attempt,
        criteria=criteria,
        artifacts=artifacts,
    )


def render_attestation(attestation: Attestation) -> str:
    criteria = [{"text": item.text, "met": item.met} for item in attestation.criteria]
    artifacts = []
    for name in sorted(attestation.artifacts):
        record = attestation.artifacts[name]
        artifacts.append(
            {
                "artifact": record.artifact,
                "sha256": record.sha256,
                "uri": record.uri,
            }
        )
    body = {
        "schema_version": 1,
        "plan_id": attestation.plan_id,
        "task_id": attestation.task_id,
        "verdict": attestation.verdict,
        "reviewer": attestation.reviewer,
        "timestamp": attestation.timestamp,
        "notes": attestation.notes,
        "signature": None,
        "attempt": attestation.attempt,
        "criteria": criteria,
        "artifacts": artifacts,
    }
    return ATTEST_HEADER + dump_yaml(body)


def write_attestation(plan: Plan, attestation: Attestation) -> Path:
    path = attestation_path(plan, attestation.task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_attestation(attestation), encoding="utf-8")
    return path


def attest_task(
    plan: Plan,
    task: Task,
    *,
    verdict: str,
    timestamp: str,
    notes: str = "",
) -> Attestation:
    if verdict not in {"pass", "fail"}:
        raise ValueError("verdict must be pass or fail")
    if verdict == "fail" and not notes.strip():
        raise ValueError("a fail attestation needs --notes")
    parse_datetime(timestamp, "--at")
    artifacts: dict[str, ArtifactRecord] = {}
    attempt = 1
    path = manifest_path(plan, task.id)
    manifest = None
    if path.is_file():
        manifest = load_manifest(path)
        attempt = manifest.attempt
        if manifest.plan_id != plan.plan_id or manifest.task_id != task.id:
            raise ValueError(f"manifest plan_id/task_id does not match {plan.plan_id} {task.id}")
    if verdict == "pass":
        if manifest is None:
            raise ValueError(
                f"pass requires artifacts/{task.id}/manifest.yaml. "
                "Run record-artifact first."
            )
        missing = [item.artifact for item in task.outputs if item.artifact not in manifest.artifacts]
        if missing:
            raise ValueError(
                f"manifest for {task.id} is missing {', '.join(missing)}"
            )
        for item in task.outputs:
            artifacts[item.artifact] = manifest.artifacts[item.artifact]
    elif manifest is not None:
        for item in task.outputs:
            if item.artifact in manifest.artifacts:
                artifacts[item.artifact] = manifest.artifacts[item.artifact]
    met = verdict == "pass"
    attestation = Attestation(
        plan_id=plan.plan_id,
        task_id=task.id,
        verdict=verdict,
        reviewer=REVIEWER,
        timestamp=timestamp,
        notes=notes.strip(),
        attempt=attempt,
        criteria=[Criterion(text=item, met=met) for item in task.acceptance_criteria],
        artifacts=artifacts,
    )
    write_attestation(plan, attestation)
    return attestation


def done_lock_errors(plan: Plan, task: Task) -> list[str]:
    """Errors that keep `status: done` from validating. Empty when the lock holds."""
    label = task.id
    if plan.directory is None:
        return [f"{label}: status is done but the plan has no directory for attestations"]
    path = attestation_path(plan, task.id)
    rel = f"attestations/{task.id}.yaml"
    if not path.is_file():
        return [
            f"{label}: status is done but {rel} is missing. "
            "Accuracy must run attest-done, then the Planner runs complete."
        ]
    try:
        attestation = load_attestation(path)
    except ValueError as exc:
        return [f"{label}: {rel} is not a valid attestation ({exc})"]
    errors: list[str] = []
    if attestation.plan_id != plan.plan_id:
        errors.append(f"{label}: {rel} plan_id is {attestation.plan_id}, not {plan.plan_id}")
    if attestation.task_id != task.id:
        errors.append(f"{label}: {rel} task_id is {attestation.task_id}")
    if attestation.verdict != "pass":
        errors.append(
            f"{label}: {rel} verdict is {attestation.verdict}. "
            "A fail goes to failed or in_review, not done."
        )
    if attestation.reviewer != REVIEWER:
        errors.append(f"{label}: {rel} reviewer must be {REVIEWER}")
    texts = [item.text for item in attestation.criteria]
    if texts != list(task.acceptance_criteria):
        errors.append(f"{label}: {rel} criteria do not match the task acceptance criteria")
    elif any(not item.met for item in attestation.criteria):
        errors.append(f"{label}: {rel} is not a full pass")
    manifest_file = manifest_path(plan, task.id)
    manifest_rel = f"artifacts/{task.id}/manifest.yaml"
    if not manifest_file.is_file():
        errors.append(f"{label}: status is done but {manifest_rel} is missing")
        return errors
    try:
        manifest = load_manifest(manifest_file)
    except ValueError as exc:
        errors.append(f"{label}: {manifest_rel} is not a valid manifest ({exc})")
        return errors
    if manifest.plan_id != plan.plan_id or manifest.task_id != task.id:
        errors.append(f"{label}: {manifest_rel} does not match this plan and task")
    for item in task.outputs:
        attested = attestation.artifacts.get(item.artifact)
        recorded = manifest.artifacts.get(item.artifact)
        if attested is None:
            errors.append(f"{label}: {rel} has no sha256 for {item.artifact}")
            continue
        if recorded is None:
            errors.append(f"{label}: {manifest_rel} has no sha256 for {item.artifact}")
            continue
        if attested.sha256 != recorded.sha256:
            errors.append(
                f"{label}: attestation sha256 for {item.artifact} does not match {manifest_rel}"
            )
    return errors
