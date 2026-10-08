# SPDX-License-Identifier: Apache-2.0
"""Small shared helpers. No I/O."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

HOUR_QUANTUM = Decimal("0.0001")
RATIO_QUANTUM = Decimal("0.0001")
PLANS_ENV = "AGENT_PLANS_DIR"


def repo_root() -> Path:
    """Directory that holds roster.yaml and plans/."""
    here = Path(__file__).resolve().parents[1]
    candidates = [here, Path.cwd().resolve()]
    candidates.extend(Path.cwd().resolve().parents)
    for path in candidates:
        if (path / "roster.yaml").is_file() and (path / "plans").is_dir():
            return path
    return here


def as_decimal(value, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise ValueError(f"{where} must be a number")
    try:
        number = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except Exception as exc:
        raise ValueError(f"{where} must be a number") from exc
    if not number.is_finite():
        raise ValueError(f"{where} must be a finite number")
    return number


def as_int(value, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where} must be an integer")
    return value


def fmt_hours(value: Decimal) -> str:
    """Stable hour text. Trailing zeros are stripped after 4 decimal places."""
    quantum = value.quantize(HOUR_QUANTUM, rounding=ROUND_HALF_UP)
    if quantum == 0:
        return "0"
    text = format(quantum, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def fmt_ratio(value: Decimal) -> str:
    quantum = value.quantize(RATIO_QUANTUM, rounding=ROUND_HALF_UP)
    return format(quantum, "f")


def fmt_percent(done: int, total: int) -> str:
    if total == 0:
        return "0.0"
    pct = (Decimal(done) / Decimal(total) * Decimal(100)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return format(pct, "f")


def parse_datetime(value: str, where: str) -> datetime:
    text = value.strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{where} must be an ISO-8601 datetime") from exc


def hours_between(start: datetime, end: datetime) -> Decimal:
    """Hours from start to end. Both values must be aware, or both naive."""
    if (start.tzinfo is None) != (end.tzinfo is None):
        raise ValueError("datetimes must both include a timezone offset, or both omit it")
    delta = end - start
    seconds = (
        Decimal(delta.days) * Decimal(86400)
        + Decimal(delta.seconds)
        + (Decimal(delta.microseconds) / Decimal(1_000_000))
    )
    return seconds / Decimal(3600)


def add_hours(origin: datetime, hours: Decimal) -> datetime:
    micros = (hours * Decimal(3600) * Decimal(1_000_000)).to_integral_value(
        rounding=ROUND_HALF_UP
    )
    return origin + timedelta(microseconds=int(micros))


def repo_of(directory: Path | None) -> Path | None:
    """Walk parents until a directory holds roster.yaml and plans/.

    A plan that lives in ``AGENT_PLANS_DIR`` outside the checkout still
    resolves to the repository that owns the roster.
    """
    if directory is None:
        return None
    current = directory.resolve()
    for parent in [current, *current.parents]:
        if (parent / "roster.yaml").is_file() and (parent / "plans").is_dir():
            return parent
    root = repo_root()
    live = plans_dir(root).resolve()
    try:
        current.relative_to(live)
    except ValueError:
        return None
    if (root / "roster.yaml").is_file() and (root / "plans").is_dir():
        return root
    return None


def bundled_plans_dir(root: Path) -> Path:
    """The ``plans/`` directory shipped in the checkout.

    Templates stay here. Live plans use the same directory unless
    ``AGENT_PLANS_DIR`` is set.
    """
    return root / "plans"


def plans_dir(root: Path) -> Path:
    """Directory scanned for live plans.

    ``AGENT_PLANS_DIR`` overrides the location. A relative value is resolved
    from ``root``. An absolute value is used as given. Unset means ``<root>/plans``.
    """
    raw = os.environ.get(PLANS_ENV, "").strip()
    if not raw:
        return bundled_plans_dir(root)
    path = Path(raw)
    if not path.is_absolute():
        path = (root / path).resolve()
    return path


def plan_directory_label(directory: Path | None, plan_id: str) -> str:
    """Path shown for a plan. In-checkout plans stay ``plans/<name>``."""
    if directory is None:
        return f"plans/{plan_id}"
    root = repo_of(directory)
    if root is not None:
        try:
            return directory.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return directory.name


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def resolve_plan_reference(root: Path, ref: str) -> Path | None:
    """Resolve a subplan reference to a directory (or a plan.yaml file).

    With ``AGENT_PLANS_DIR`` unset, ``plans/<id>`` is ``<root>/plans/<id>``.
    When the variable is set, a ``plans/`` prefix is resolved inside that
    directory so a later move of live plans still finds the child. Any other
    relative reference stays under the repository root. The result must stay
    inside the repository or inside the configured plans directory.
    """
    raw = os.environ.get(PLANS_ENV, "").strip()
    if raw and (ref == "plans" or ref.startswith("plans/")):
        rest = ref[len("plans") :].lstrip("/")
        path = (plans_dir(root) / rest).resolve()
    else:
        path = (root / ref).resolve()
    if _is_relative_to(path, root) or _is_relative_to(path, plans_dir(root)):
        return path
    return None


def template_library_dirs(root: Path) -> list[Path]:
    """plans/_templates/<kind>/ directories. Not picked up by plan_dirs.

    Templates stay in the checkout even when live plans are overridden.
    """
    base = bundled_plans_dir(root) / "_templates"
    if not base.is_dir():
        return []
    found = []
    for path in sorted(base.iterdir()):
        if path.is_dir() and (path / "plan.yaml").is_file():
            found.append(path)
    return found


def _scan_plan_dirs(base: Path, include_templates: bool) -> list[Path]:
    if not base.is_dir():
        return []
    found = []
    for path in sorted(base.iterdir()):
        if not path.is_dir() or not (path / "plan.yaml").is_file():
            continue
        if not include_templates and path.name.startswith("_"):
            continue
        found.append(path)
    return found


def plan_dirs(root: Path, include_templates: bool = True) -> list[Path]:
    live = plans_dir(root)
    found = _scan_plan_dirs(live, include_templates)
    bundled = bundled_plans_dir(root)
    if include_templates and live.resolve() != bundled.resolve():
        seen = {path.resolve() for path in found}
        for path in _scan_plan_dirs(bundled, include_templates=True):
            if path.name.startswith("_") and path.resolve() not in seen:
                found.append(path)
    return found


def resolve_plan_dir(arg: str) -> Path:
    path = Path(arg)
    if path.is_file() and path.name == "plan.yaml":
        return path.parent
    if (path / "plan.yaml").is_file():
        return path
    raise SystemExit(f"not a plan directory: {arg}")
