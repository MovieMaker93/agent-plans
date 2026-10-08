# SPDX-License-Identifier: Apache-2.0
"""In-memory plan and roster.

Field names follow the words agents already use: assignee, depends_on,
and a three-point estimate of optimistic / likely / pessimistic hours.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from planner.util import as_decimal, as_int
from planner.yamlio import load_yaml

STATUSES = (
    "planned",
    "ready",
    "in_progress",
    "in_review",
    "done",
    "blocked",
    "failed",
    "cancelled",
)
RISKS = ("low", "medium", "high")
DEP_TYPES = ("FS", "SS", "FF", "SF")
NOT_STARTED = ("planned", "ready")
TERMINAL = ("done", "failed", "cancelled")

PLAN_KEYS = {
    "schema_version",
    "plan_id",
    "version",
    "title",
    "goal",
    "owner",
    "definition_of_done",
    "budget",
    "human_approval_policy",
    "constraints",
    "replan_policy",
    "priority",
    "template_kind",
    "calibration_override",
    "provided_inputs",
    "deliverables",
    "tasks",
}
PLAN_REQUIRED = PLAN_KEYS - {
    "title",
    "provided_inputs",
    "replan_policy",
    "priority",
    "template_kind",
    "calibration_override",
}
CONSTRAINT_KEYS = {"deadline", "max_parallel", "schedule_origin"}
BUDGET_KEYS = {"tokens", "hours", "usd"}
TASK_KEYS = {
    "id",
    "title",
    "section",
    "task_type",
    "objective",
    "inputs",
    "outputs",
    "depends_on",
    "estimate_hours",
    "required_capabilities",
    "assignee",
    "acceptance_criteria",
    "risk",
    "reversible",
    "needs_human",
    "budget",
    "max_retries",
    "status",
    "actuals",
    "subplan",
}
TASK_REQUIRED = TASK_KEYS - {"section", "subplan"}
ESTIMATE_KEYS = {"optimistic", "likely", "pessimistic", "calibrated"}
ESTIMATE_REQUIRED = {"optimistic", "likely", "pessimistic"}
REPLAN_KEYS = {"min_interval_minutes", "hysteresis_hours", "freeze_window_hours"}
ACTUAL_KEYS = {"start", "end", "hours", "attempts"}
INPUT_KEYS = {"artifact", "from_task"}
OUTPUT_KEYS = {"artifact", "type"}
DEP_KEYS = {"task", "type", "lag_hours"}
PROVIDED_KEYS = {"artifact", "description"}
ROSTER_KEYS = {"schema_version", "default_max_parallel", "bots"}
BOT_KEYS = {"name", "description", "capabilities", "capacity", "kind", "cost"}
BOT_REQUIRED = BOT_KEYS - {"kind", "cost"}
BOT_KINDS = ("bot", "cloud_agent", "human")
COST_KEYS = {"usd_per_hour", "tokens_per_hour"}
OVERRIDE_KEYS = {"factor", "by_pair", "by_task_type", "by_assignee"}


@dataclass
class Budget:
    tokens: int
    hours: Decimal
    usd: Decimal


@dataclass
class Estimate:
    optimistic: Decimal
    likely: Decimal
    pessimistic: Decimal
    calibrated: Decimal | None = None

    @property
    def expected(self) -> Decimal:
        """Author PERT expected hours. Ignores calibrated."""
        return (self.optimistic + 4 * self.likely + self.pessimistic) / Decimal(6)

    @property
    def std_dev(self) -> Decimal:
        return (self.pessimistic - self.optimistic) / Decimal(6)

    @property
    def scheduling(self) -> Decimal:
        """Hours the scheduler should use. Calibrated wins when it is set."""
        if self.calibrated is not None:
            return self.calibrated
        return self.expected


@dataclass
class Actuals:
    start: str | None
    end: str | None
    hours: Decimal | None
    attempts: int


@dataclass
class Artifact:
    artifact: str
    type: str | None = None
    from_task: str | None = None
    description: str | None = None


@dataclass
class Dependency:
    task: str
    type: str = "FS"
    lag_hours: Decimal = Decimal(0)


@dataclass
class Task:
    id: str
    title: str
    task_type: str
    objective: str
    inputs: list[Artifact]
    outputs: list[Artifact]
    depends_on: list[Dependency]
    estimate_hours: Estimate
    required_capabilities: list[str]
    assignee: str
    acceptance_criteria: list[str]
    risk: str
    reversible: bool
    needs_human: bool
    budget: Budget
    max_retries: int
    status: str
    actuals: Actuals
    section: str | None = None
    subplan: str | None = None


@dataclass
class ReplanPolicy:
    """Defaults match the documented control loop. A plan may override them."""

    min_interval_minutes: int = 60
    hysteresis_hours: Decimal = Decimal("0.5")
    freeze_window_hours: Decimal = Decimal("1")


@dataclass
class CalibrationOverride:
    """Per-plan factors. More specific keys win. See calibration.resolve_factor."""

    factor: Decimal | None = None
    by_pair: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    by_task_type: dict[str, Decimal] = field(default_factory=dict)
    by_assignee: dict[str, Decimal] = field(default_factory=dict)


@dataclass
class Plan:
    schema_version: int
    plan_id: str
    version: int
    goal: str
    owner: str
    definition_of_done: str
    budget: Budget
    human_approval_policy: str
    max_parallel: int
    tasks: list[Task]
    schedule_origin: str
    title: str | None = None
    deadline: str | None = None
    replan_policy: ReplanPolicy | None = None
    priority: int = 0
    template_kind: str | None = None
    calibration_override: CalibrationOverride | None = None
    provided_inputs: list[Artifact] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    path: Path | None = None

    @property
    def directory(self) -> Path | None:
        return self.path.parent if self.path else None


@dataclass
class CostModel:
    usd_per_hour: Decimal
    tokens_per_hour: Decimal


@dataclass
class Bot:
    name: str
    description: str
    capabilities: list[str]
    capacity: int
    kind: str = "bot"
    cost: CostModel | None = None


@dataclass
class Roster:
    schema_version: int
    default_max_parallel: int
    bots: list[Bot]
    path: Path | None = None

    def get(self, name: str) -> Bot | None:
        for bot in self.bots:
            if bot.name == name:
                return bot
        return None


class LoadError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _text(value, where: str, errors: list[str], allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        errors.append(f"{where} must be a string")
        return ""
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        errors.append(f"{where} must not be empty")
    return cleaned


def _mapping(value, where: str, errors: list[str]) -> dict | None:
    if not isinstance(value, dict):
        errors.append(f"{where} must be a mapping")
        return None
    return value


def _check_keys(value: dict, where: str, allowed: set[str], required: set[str], errors: list[str]):
    for key in value:
        if key not in allowed:
            errors.append(f"{where} has unknown field '{key}'")
    for key in sorted(required):
        if key not in value:
            errors.append(f"{where} is missing '{key}'")


def _replan_policy(value, errors: list[str]) -> ReplanPolicy | None:
    where = "plan.replan_policy"
    raw = _mapping(value, where, errors)
    if raw is None:
        return None
    _check_keys(raw, where, REPLAN_KEYS, set(), errors)
    policy = ReplanPolicy()
    if "min_interval_minutes" in raw:
        try:
            minutes = as_int(raw["min_interval_minutes"], f"{where}.min_interval_minutes")
        except ValueError as exc:
            errors.append(str(exc))
        else:
            if minutes < 0:
                errors.append(f"{where}.min_interval_minutes must be >= 0")
            else:
                policy.min_interval_minutes = minutes
    for key, attr in (
        ("hysteresis_hours", "hysteresis_hours"),
        ("freeze_window_hours", "freeze_window_hours"),
    ):
        if key not in raw:
            continue
        try:
            number = as_decimal(raw[key], f"{where}.{key}")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if number < 0:
            errors.append(f"{where}.{key} must be >= 0")
            continue
        setattr(policy, attr, number)
    return policy


def _positive_decimal(value, where: str, errors: list[str]) -> Decimal | None:
    try:
        number = as_decimal(value, where)
    except ValueError as exc:
        errors.append(str(exc))
        return None
    if number <= 0:
        errors.append(f"{where} must be > 0")
        return None
    return number


def _cost(value, where: str, errors: list[str]) -> CostModel | None:
    if value is None:
        return None
    raw = _mapping(value, where, errors)
    if raw is None:
        return None
    _check_keys(raw, where, COST_KEYS, COST_KEYS, errors)
    try:
        usd = as_decimal(raw.get("usd_per_hour", 0), f"{where}.usd_per_hour")
        tokens = as_decimal(raw.get("tokens_per_hour", 0), f"{where}.tokens_per_hour")
    except ValueError as exc:
        errors.append(str(exc))
        return None
    if usd < 0 or tokens < 0:
        errors.append(f"{where} values must be >= 0")
    return CostModel(usd_per_hour=usd, tokens_per_hour=tokens)


def _override_factor(value, where: str, errors: list[str]) -> Decimal | None:
    return _positive_decimal(value, where, errors)


def _calibration_override(value, errors: list[str]) -> CalibrationOverride | None:
    where = "plan.calibration_override"
    raw = _mapping(value, where, errors)
    if raw is None:
        return None
    _check_keys(raw, where, OVERRIDE_KEYS, set(), errors)
    override = CalibrationOverride()
    if "factor" in raw and raw["factor"] is not None:
        factor = _override_factor(raw["factor"], f"{where}.factor", errors)
        if factor is not None:
            override.factor = factor
    pairs = raw.get("by_pair", [])
    if pairs is None:
        pairs = []
    if not isinstance(pairs, list):
        errors.append(f"{where}.by_pair must be a list")
        pairs = []
    for index, item in enumerate(pairs):
        slot = f"{where}.by_pair[{index}]"
        mapped = _mapping(item, slot, errors)
        if mapped is None:
            continue
        _check_keys(mapped, slot, {"assignee", "task_type", "factor"}, {"assignee", "task_type", "factor"}, errors)
        assignee = _text(mapped.get("assignee", ""), f"{slot}.assignee", errors)
        task_type = _text(mapped.get("task_type", ""), f"{slot}.task_type", errors)
        factor = _override_factor(mapped.get("factor", 0), f"{slot}.factor", errors)
        if assignee and task_type and factor is not None:
            override.by_pair[(assignee, task_type)] = factor
    for key, dest in (("by_task_type", "task_type"), ("by_assignee", "assignee")):
        rows = raw.get(key, [])
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            errors.append(f"{where}.{key} must be a list")
            continue
        target = override.by_task_type if key == "by_task_type" else override.by_assignee
        for index, item in enumerate(rows):
            slot = f"{where}.{key}[{index}]"
            mapped = _mapping(item, slot, errors)
            if mapped is None:
                continue
            _check_keys(mapped, slot, {dest, "factor"}, {dest, "factor"}, errors)
            name = _text(mapped.get(dest, ""), f"{slot}.{dest}", errors)
            factor = _override_factor(mapped.get("factor", 0), f"{slot}.factor", errors)
            if name and factor is not None:
                target[name] = factor
    return override


def _budget(value, where: str, errors: list[str]) -> Budget | None:
    raw = _mapping(value, where, errors)
    if raw is None:
        return None
    _check_keys(raw, where, BUDGET_KEYS, BUDGET_KEYS, errors)
    try:
        tokens = as_int(raw.get("tokens", 0), f"{where}.tokens")
        hours = as_decimal(raw.get("hours", 0), f"{where}.hours")
        usd = as_decimal(raw.get("usd", 0), f"{where}.usd")
    except ValueError as exc:
        errors.append(str(exc))
        return None
    if tokens < 0 or hours < 0 or usd < 0:
        errors.append(f"{where} values must be >= 0")
    return Budget(tokens=tokens, hours=hours, usd=usd)


def _estimate(value, where: str, errors: list[str]) -> Estimate | None:
    raw = _mapping(value, where, errors)
    if raw is None:
        return None
    _check_keys(raw, where, ESTIMATE_KEYS, ESTIMATE_REQUIRED, errors)
    try:
        optimistic = as_decimal(raw.get("optimistic", 0), f"{where}.optimistic")
        likely = as_decimal(raw.get("likely", 0), f"{where}.likely")
        pessimistic = as_decimal(raw.get("pessimistic", 0), f"{where}.pessimistic")
    except ValueError as exc:
        errors.append(str(exc))
        return None
    if optimistic < 0 or likely < 0 or pessimistic < 0:
        errors.append(f"{where} values must be >= 0")
    if not (optimistic <= likely <= pessimistic):
        errors.append(
            f"{where} must have optimistic <= likely <= pessimistic"
        )
    calibrated = None
    if "calibrated" in raw and raw["calibrated"] is not None:
        try:
            calibrated = as_decimal(raw["calibrated"], f"{where}.calibrated")
        except ValueError as exc:
            errors.append(str(exc))
        else:
            if calibrated < 0:
                errors.append(f"{where}.calibrated must be >= 0")
    return Estimate(
        optimistic=optimistic,
        likely=likely,
        pessimistic=pessimistic,
        calibrated=calibrated,
    )


def _actuals(value, where: str, errors: list[str]) -> Actuals | None:
    raw = _mapping(value, where, errors)
    if raw is None:
        return None
    _check_keys(raw, where, ACTUAL_KEYS, ACTUAL_KEYS, errors)
    start = raw.get("start")
    end = raw.get("end")
    hours = raw.get("hours")
    if start is not None and not isinstance(start, str):
        errors.append(f"{where}.start must be a string or null")
        start = None
    if end is not None and not isinstance(end, str):
        errors.append(f"{where}.end must be a string or null")
        end = None
    parsed_hours = None
    if hours is not None:
        try:
            parsed_hours = as_decimal(hours, f"{where}.hours")
        except ValueError as exc:
            errors.append(str(exc))
        else:
            if parsed_hours < 0:
                errors.append(f"{where}.hours must be >= 0")
    try:
        attempts = as_int(raw.get("attempts", 0), f"{where}.attempts")
    except ValueError as exc:
        errors.append(str(exc))
        attempts = 0
    if attempts < 0:
        errors.append(f"{where}.attempts must be >= 0")
    return Actuals(
        start=start.strip() if isinstance(start, str) else None,
        end=end.strip() if isinstance(end, str) else None,
        hours=parsed_hours,
        attempts=attempts,
    )


def _string_list(value, where: str, errors: list[str], allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{where} must be a list")
        return []
    if not value and not allow_empty:
        errors.append(f"{where} must not be empty")
    out = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{where}[{index}] must be a non-empty string")
            continue
        out.append(item.strip())
    return out


def load_roster(path: Path) -> Roster:
    data = load_yaml(path)
    errors: list[str] = []
    raw = _mapping(data, "roster", errors)
    if raw is None:
        raise LoadError(errors)
    _check_keys(raw, "roster", ROSTER_KEYS, ROSTER_KEYS, errors)
    try:
        schema_version = as_int(raw.get("schema_version", 0), "roster.schema_version")
        default_parallel = as_int(
            raw.get("default_max_parallel", 0), "roster.default_max_parallel"
        )
    except ValueError as exc:
        errors.append(str(exc))
        schema_version = 0
        default_parallel = 0
    if schema_version != 1:
        errors.append("roster.schema_version must be 1")
    if default_parallel < 1:
        errors.append("roster.default_max_parallel must be >= 1")
    bots: list[Bot] = []
    seen: set[str] = set()
    entries = raw.get("bots")
    if not isinstance(entries, list) or not entries:
        errors.append("roster.bots must be a non-empty list")
        entries = []
    for index, entry in enumerate(entries):
        where = f"roster.bots[{index}]"
        body = _mapping(entry, where, errors)
        if body is None:
            continue
        _check_keys(body, where, BOT_KEYS, BOT_REQUIRED, errors)
        name = _text(body.get("name", ""), f"{where}.name", errors)
        description = _text(body.get("description", ""), f"{where}.description", errors)
        capabilities = _string_list(
            body.get("capabilities", []), f"{where}.capabilities", errors
        )
        try:
            capacity = as_int(body.get("capacity", 0), f"{where}.capacity")
        except ValueError as exc:
            errors.append(str(exc))
            capacity = 0
        if capacity < 1:
            errors.append(f"{where}.capacity must be >= 1")
        kind = "bot"
        if "kind" in body and body["kind"] is not None:
            kind = _text(body["kind"], f"{where}.kind", errors)
            if kind and kind not in BOT_KINDS:
                errors.append(f"{where}.kind must be one of {', '.join(BOT_KINDS)}")
        cost = _cost(body.get("cost"), f"{where}.cost", errors) if "cost" in body else None
        if name in seen:
            errors.append(f"roster has duplicate bot '{name}'")
        seen.add(name)
        if len(capabilities) != len(set(capabilities)):
            errors.append(f"{where}.capabilities has duplicates")
        bots.append(
            Bot(
                name=name,
                description=description,
                capabilities=capabilities,
                capacity=capacity,
                kind=kind,
                cost=cost,
            )
        )
    if errors:
        raise LoadError(errors)
    return Roster(
        schema_version=schema_version,
        default_max_parallel=default_parallel,
        bots=bots,
        path=path,
    )


def _dependency(value, where: str, errors: list[str]) -> Dependency | None:
    if isinstance(value, str):
        task_id = value.strip()
        if not task_id:
            errors.append(f"{where} must not be empty")
            return None
        return Dependency(task=task_id)
    raw = _mapping(value, where, errors)
    if raw is None:
        return None
    _check_keys(raw, where, DEP_KEYS, {"task"}, errors)
    task_id = _text(raw.get("task", ""), f"{where}.task", errors)
    dep_type = str(raw.get("type", "FS")).strip().upper()
    if dep_type not in DEP_TYPES:
        errors.append(f"{where}.type must be one of FS, SS, FF, SF")
        dep_type = "FS"
    try:
        lag = as_decimal(raw.get("lag_hours", 0), f"{where}.lag_hours")
    except ValueError as exc:
        errors.append(str(exc))
        lag = Decimal(0)
    return Dependency(task=task_id, type=dep_type, lag_hours=lag)


def _load_task(raw, index: int, errors: list[str]) -> Task | None:
    where = f"tasks[{index}]"
    body = _mapping(raw, where, errors)
    if body is None:
        return None
    _check_keys(body, where, TASK_KEYS, TASK_REQUIRED, errors)
    task_id = _text(body.get("id", ""), f"{where}.id", errors)
    label = task_id or where
    title = _text(body.get("title", ""), f"{label}.title", errors)
    task_type = _text(body.get("task_type", ""), f"{label}.task_type", errors)
    objective = _text(body.get("objective", ""), f"{label}.objective", errors)
    section = body.get("section")
    if section is not None:
        section = _text(section, f"{label}.section", errors)
    estimate = _estimate(body.get("estimate_hours"), f"{label}.estimate_hours", errors)
    budget = _budget(body.get("budget"), f"{label}.budget", errors)
    actuals = _actuals(body.get("actuals"), f"{label}.actuals", errors)
    capabilities = _string_list(
        body.get("required_capabilities", []),
        f"{label}.required_capabilities",
        errors,
    )
    criteria = _string_list(
        body.get("acceptance_criteria", []),
        f"{label}.acceptance_criteria",
        errors,
    )
    assignee = _text(body.get("assignee", ""), f"{label}.assignee", errors)
    risk = _text(body.get("risk", ""), f"{label}.risk", errors)
    if risk and risk not in RISKS:
        errors.append(f"{label}.risk must be one of low, medium, high")
    status = _text(body.get("status", ""), f"{label}.status", errors)
    if status and status not in STATUSES:
        errors.append(
            f"{label}.status must be one of {', '.join(STATUSES)}"
        )
    reversible = body.get("reversible")
    needs_human = body.get("needs_human")
    if not isinstance(reversible, bool):
        errors.append(f"{label}.reversible must be true or false")
        reversible = True
    if not isinstance(needs_human, bool):
        errors.append(f"{label}.needs_human must be true or false")
        needs_human = False
    try:
        retries = as_int(body.get("max_retries", -1), f"{label}.max_retries")
    except ValueError as exc:
        errors.append(str(exc))
        retries = 0
    if retries < 0:
        errors.append(f"{label}.max_retries must be >= 0")

    inputs: list[Artifact] = []
    raw_inputs = body.get("inputs", [])
    if not isinstance(raw_inputs, list):
        errors.append(f"{label}.inputs must be a list")
        raw_inputs = []
    for input_index, item in enumerate(raw_inputs):
        slot = f"{label}.inputs[{input_index}]"
        mapped = _mapping(item, slot, errors)
        if mapped is None:
            continue
        _check_keys(mapped, slot, INPUT_KEYS, {"artifact"}, errors)
        artifact = _text(mapped.get("artifact", ""), f"{slot}.artifact", errors)
        from_task = mapped.get("from_task")
        if from_task is not None:
            from_task = _text(from_task, f"{slot}.from_task", errors)
        inputs.append(Artifact(artifact=artifact, from_task=from_task))

    outputs: list[Artifact] = []
    raw_outputs = body.get("outputs", [])
    if not isinstance(raw_outputs, list) or not raw_outputs:
        errors.append(f"{label}.outputs must list at least one artifact")
        raw_outputs = []
    for output_index, item in enumerate(raw_outputs):
        slot = f"{label}.outputs[{output_index}]"
        mapped = _mapping(item, slot, errors)
        if mapped is None:
            continue
        _check_keys(mapped, slot, OUTPUT_KEYS, OUTPUT_KEYS, errors)
        artifact = _text(mapped.get("artifact", ""), f"{slot}.artifact", errors)
        artifact_type = _text(mapped.get("type", ""), f"{slot}.type", errors)
        outputs.append(Artifact(artifact=artifact, type=artifact_type))

    depends_on: list[Dependency] = []
    raw_deps = body.get("depends_on", [])
    if not isinstance(raw_deps, list):
        errors.append(f"{label}.depends_on must be a list")
        raw_deps = []
    for dep_index, item in enumerate(raw_deps):
        dep = _dependency(item, f"{label}.depends_on[{dep_index}]", errors)
        if dep is not None:
            depends_on.append(dep)

    subplan = None
    if "subplan" in body and body["subplan"] is not None:
        subplan = _text(body["subplan"], f"{label}.subplan", errors)
        if subplan and (subplan.startswith("/") or ".." in Path(subplan).parts):
            errors.append(f"{label}.subplan must be a relative path under plans/")

    if estimate is None or budget is None or actuals is None:
        return None
    return Task(
        id=task_id,
        title=title,
        task_type=task_type,
        objective=objective,
        inputs=inputs,
        outputs=outputs,
        depends_on=depends_on,
        estimate_hours=estimate,
        required_capabilities=capabilities,
        assignee=assignee,
        acceptance_criteria=criteria,
        risk=risk,
        reversible=reversible,
        needs_human=needs_human,
        budget=budget,
        max_retries=retries,
        status=status,
        actuals=actuals,
        section=section,
        subplan=subplan,
    )


def load_plan(path: Path) -> Plan:
    data = load_yaml(path)
    errors: list[str] = []
    raw = _mapping(data, "plan", errors)
    if raw is None:
        raise LoadError(errors)
    _check_keys(raw, "plan", PLAN_KEYS, PLAN_REQUIRED, errors)
    try:
        schema_version = as_int(raw.get("schema_version", 0), "plan.schema_version")
        version = as_int(raw.get("version", 0), "plan.version")
    except ValueError as exc:
        errors.append(str(exc))
        schema_version = 0
        version = 0
    if schema_version != 1:
        errors.append("plan.schema_version must be 1")
    if version < 1:
        errors.append("plan.version must be >= 1")
    plan_id = _text(raw.get("plan_id", ""), "plan.plan_id", errors)
    title = raw.get("title")
    if title is not None:
        title = _text(title, "plan.title", errors)
    goal = _text(raw.get("goal", ""), "plan.goal", errors)
    owner = _text(raw.get("owner", ""), "plan.owner", errors)
    definition = _text(
        raw.get("definition_of_done", ""), "plan.definition_of_done", errors
    )
    policy = _text(
        raw.get("human_approval_policy", ""), "plan.human_approval_policy", errors
    )
    budget = _budget(raw.get("budget"), "plan.budget", errors)
    constraints = _mapping(raw.get("constraints"), "plan.constraints", errors)
    deadline = None
    origin = ""
    max_parallel = 0
    if constraints is not None:
        _check_keys(
            constraints,
            "plan.constraints",
            CONSTRAINT_KEYS,
            {"max_parallel", "schedule_origin"},
            errors,
        )
        if "deadline" in constraints and constraints["deadline"] is not None:
            deadline = _text(constraints["deadline"], "plan.constraints.deadline", errors)
        origin = _text(
            constraints.get("schedule_origin", ""),
            "plan.constraints.schedule_origin",
            errors,
        )
        try:
            max_parallel = as_int(
                constraints.get("max_parallel", 0), "plan.constraints.max_parallel"
            )
        except ValueError as exc:
            errors.append(str(exc))
        if max_parallel < 1:
            errors.append("plan.constraints.max_parallel must be >= 1")

    replan_policy = None
    if "replan_policy" in raw and raw["replan_policy"] is not None:
        replan_policy = _replan_policy(raw["replan_policy"], errors)

    priority = 0
    if "priority" in raw and raw["priority"] is not None:
        try:
            priority = as_int(raw["priority"], "plan.priority")
        except ValueError as exc:
            errors.append(str(exc))
    template_kind = None
    if "template_kind" in raw and raw["template_kind"] is not None:
        template_kind = _text(raw["template_kind"], "plan.template_kind", errors)
    calibration_override = None
    if "calibration_override" in raw and raw["calibration_override"] is not None:
        calibration_override = _calibration_override(raw["calibration_override"], errors)

    provided: list[Artifact] = []
    raw_provided = raw.get("provided_inputs", [])
    if raw_provided is None:
        raw_provided = []
    if not isinstance(raw_provided, list):
        errors.append("plan.provided_inputs must be a list")
        raw_provided = []
    for index, item in enumerate(raw_provided):
        slot = f"plan.provided_inputs[{index}]"
        mapped = _mapping(item, slot, errors)
        if mapped is None:
            continue
        _check_keys(mapped, slot, PROVIDED_KEYS, {"artifact"}, errors)
        artifact = _text(mapped.get("artifact", ""), f"{slot}.artifact", errors)
        description = mapped.get("description")
        if description is not None:
            description = _text(description, f"{slot}.description", errors)
        provided.append(Artifact(artifact=artifact, description=description))

    deliverables = _string_list(
        raw.get("deliverables", []), "plan.deliverables", errors
    )
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        errors.append("plan.tasks must be a non-empty list")
        raw_tasks = []
    tasks = []
    for index, item in enumerate(raw_tasks):
        task = _load_task(item, index, errors)
        if task is not None and task.id:
            tasks.append(task)
    if budget is None or errors:
        raise LoadError(errors)
    return Plan(
        schema_version=schema_version,
        plan_id=plan_id,
        version=version,
        title=title,
        goal=goal,
        owner=owner,
        definition_of_done=definition,
        budget=budget,
        human_approval_policy=policy,
        max_parallel=max_parallel,
        tasks=tasks,
        schedule_origin=origin,
        deadline=deadline,
        replan_policy=replan_policy,
        priority=priority,
        template_kind=template_kind,
        calibration_override=calibration_override,
        provided_inputs=provided,
        deliverables=deliverables,
        path=path,
    )
