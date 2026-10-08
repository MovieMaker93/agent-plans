# SPDX-License-Identifier: Apache-2.0
"""Command line for the plan store.

Core loop (docs/core.md):

    python -m planner instantiate research-report --into plans/<slug> --plan-id ID
    python -m planner validate PLAN
    python -m planner schedule PLAN
    python -m planner gantt PLAN
    python -m planner ready
    python -m planner brief PLAN
    python -m planner record-artifact PLAN TASK --file PATH
    python -m planner attest-done PLAN TASK --verdict pass|fail
    python -m planner complete PLAN TASK --hours H --attempts N
    python -m planner replan PLAN --now --reason TEXT
    python -m planner check

When the work needs them:

    python -m planner calibrate [--apply]
    python -m planner estimate PLAN
    python -m planner apply-calibration PLAN
    python -m planner portfolio
    python -m planner portfolio schedule
    python -m planner simulate PLAN
    python -m planner status [PLAN ...]
    python -m planner dispatch-dry-run [PLAN ...]
    python -m planner dashboard --at 2026-10-08T09:00:00-05:00
    python -m planner digest --at 2026-10-08T09:00:00-05:00
    python -m planner digest --post
    python -m planner serve --host 127.0.0.1 --port 8787
"""

from __future__ import annotations

import argparse
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from planner import __version__
from planner.attest import attest_task, done_lock_errors, record_artifact
from planner.brief import brief_targets, write_briefs
from planner.calibration import (
    apply_calibration,
    build_calibration,
    calibration_for,
    load_calibration,
    render_calibration_yaml,
    render_estimate,
)
from planner.dispatch import render_dispatch
from planner.gantt import render_gantt, render_schedule_yaml
from planner.model import STATUSES, LoadError, Plan, load_plan, load_roster
from planner.planedit import append_log, log_entry, set_actuals, set_status, set_version
from planner.portfolio import plan_entry, render_portfolio_md, render_portfolio_yaml
from planner.replan import clock_from, replan_plan, schedule_from_now
from planner.reports import deadline_warning, render_calibration, render_ready, render_status, schedule_plan
from planner.schedule import ScheduleError
from planner.util import PLANS_ENV, fmt_hours, plan_dirs, repo_root, resolve_plan_dir
from planner.validate import validate_plan
from planner.yamlio import YamlError

EXAMPLES = """
examples:
  core loop (docs/core.md):
  python -m planner instantiate research-report --into plans/<YYYY-MM-DD>-<slug> --plan-id <id>
  python -m planner validate plans/<slug>
  python -m planner schedule plans/<slug>
  python -m planner gantt plans/<slug>
  python -m planner ready
  python -m planner brief plans/<slug>
  python -m planner record-artifact plans/<slug> T1 --file path/to/report.md
  python -m planner attest-done plans/<slug> T1 --verdict pass --at <ISO-8601>
  python -m planner complete plans/<slug> T1 --hours 2 --attempts 1 --at <ISO-8601>
  python -m planner replan plans/<slug> --now --reason "slipped" --at <ISO-8601>
  python -m planner check

  when the work needs them:
  python -m planner calibrate --apply
  python -m planner estimate plans/<slug>
  python -m planner apply-calibration plans/<slug>
  python -m planner portfolio
  python -m planner portfolio schedule
  python -m planner simulate plans/<slug>
  python -m planner dispatch-dry-run
  python -m planner dashboard --at 2026-10-08T09:00:00-05:00
  python -m planner digest --at 2026-10-08T09:00:00-05:00
  python -m planner serve --host 127.0.0.1 --port 8787
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="planner",
        description="Validate, schedule, and inspect agent project plans.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"planner {__version__}")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="repository root (default: directory with roster.yaml)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    parsers = {}
    for name, help_text in (
        ("validate", "check schema, roster fit, provenance, and cycles"),
        ("schedule", "write schedule.yaml (CPM slack and resource-constrained times)"),
        ("gantt", "write gantt.md from the plan"),
        ("ready", "list the dispatch queue and human gates that are waiting"),
        ("status", "summarize progress, critical path, variance, and blocks"),
        ("dispatch-dry-run", "show what a dispatcher would send, without sending it"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument(
            "plans",
            nargs="*",
            help="plan directory or plan.yaml (default: plans/, or AGENT_PLANS_DIR)",
        )
        parsers[name] = command

    for name in ("schedule", "gantt"):
        parsers[name].add_argument(
            "--from-now",
            action="store_true",
            help="anchor in-progress work and schedule the rest from --at (or the current time)",
        )
        parsers[name].add_argument(
            "--at",
            default=None,
            help="ISO-8601 timestamp used with --from-now (default: current time)",
        )

    calibrate = sub.add_parser(
        "calibrate",
        help="estimate-vs-actual ratios by task type and assignee",
    )
    calibrate.add_argument(
        "--apply",
        action="store_true",
        help="write calibration.yaml from done-task actuals (deterministic)",
    )

    for name, help_text in (
        ("estimate", "show raw PERT, the calibration factor, and preview hours"),
        ("apply-calibration", "write estimate_hours.calibrated; do not change optimistic/likely/pessimistic"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("plan", help="plan directory or plan.yaml")
        if name == "apply-calibration":
            command.add_argument("--at", default=None, help="ISO-8601 timestamp for the log entry")

    portfolio = sub.add_parser(
        "portfolio",
        help="write the portfolio summary, or level open plans on one roster",
    )
    portfolio.add_argument(
        "view",
        nargs="?",
        default="summary",
        choices=["summary", "schedule"],
        help="summary writes portfolio.md; schedule levels open plans",
    )
    sub.add_parser("check", help="validate every plan, then run the unit tests")

    simulate = sub.add_parser(
        "simulate",
        help="Monte Carlo finish percentiles. Prints a report and writes nothing",
    )
    simulate.add_argument("plan")
    simulate.add_argument("--seed", type=int, default=1)
    simulate.add_argument("--runs", type=int, default=200)
    simulate.add_argument(
        "--add-capacity",
        action="append",
        default=[],
        help="name=N adds N to that assignee's concurrency for this run",
    )
    simulate.add_argument("--omit", action="append", default=[], help="drop a task id")
    simulate.add_argument(
        "--omit-deliverable",
        action="append",
        default=[],
        help="drop the task that produces this deliverable",
    )
    simulate.add_argument(
        "--deadline",
        default=None,
        help="compare p50 slack against this ISO-8601 deadline. Does not edit the plan",
    )

    instantiate = sub.add_parser("instantiate", help="copy plans/_templates/<kind> into a new plan directory")
    instantiate.add_argument("kind")
    instantiate.add_argument("--into", required=True, type=Path)
    instantiate.add_argument("--plan-id", required=True)
    instantiate.add_argument("--title", default=None)

    dashboard = sub.add_parser("dashboard", help="write dashboard/index.md and index.html from live plans")
    dashboard.add_argument("--at", default=None, help="ISO-8601 timestamp (default: current time)")
    digest = sub.add_parser(
        "digest",
        help="print a meeting-prep summary and write a Slack payload. Posts only with --post",
    )
    digest.add_argument("--at", default=None, help="ISO-8601 timestamp (default: current time)")
    digest.add_argument(
        "--post",
        action="store_true",
        help="POST the payload to SLACK_DIGEST_WEBHOOK. Dry run when omitted",
    )
    serve_cmd = sub.add_parser(
        "serve",
        help="serve the generated dashboard on localhost for Tailscale",
    )
    serve_cmd.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    serve_cmd.add_argument("--port", type=int, default=8787)
    serve_cmd.add_argument(
        "--refresh",
        type=int,
        default=0,
        help="regenerate views every N seconds and ask the browser to reload",
    )
    serve_cmd.add_argument(
        "--at",
        default=None,
        help="pin regenerate to this ISO-8601 timestamp (default: current time)",
    )

    brief = sub.add_parser("brief", help="write a self-contained markdown brief for a task")
    brief.add_argument("plan", help="plan directory or plan.yaml")
    brief.add_argument("task", nargs="?", default=None, help="task id (default: every dispatchable task)")

    record = sub.add_parser("record-artifact", help="hash an output into artifacts/<task>/manifest.yaml")
    record.add_argument("plan")
    record.add_argument("task")
    record.add_argument("--file", required=True, type=Path, help="file to hash")
    record.add_argument("--name", default=None, help="output artifact name (default: file name)")
    record.add_argument("--producer", default=None, help="roster name (default: task assignee)")
    record.add_argument("--attempt", type=int, default=None)

    def add_attest(name: str, help_text: str):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("plan")
        command.add_argument("task")
        command.add_argument("--verdict", required=True, choices=["pass", "fail"])
        command.add_argument("--notes", default="")
        command.add_argument("--at", default=None, help="ISO-8601 timestamp (default: current time)")

    add_attest("attest-done", "write attestations/<task>.yaml (Accuracy). Does not edit plan.yaml.")
    add_attest("attest", "alias of attest-done")

    complete = sub.add_parser(
        "complete",
        help="set done when a pass attestation from accuracy exists",
    )
    complete.add_argument("plan")
    complete.add_argument("task")
    complete.add_argument("--hours", required=True, help="actual hours")
    complete.add_argument("--attempts", required=True, type=int)
    complete.add_argument("--start", default=None)
    complete.add_argument("--end", default=None)
    complete.add_argument("--at", default=None, help="ISO-8601 timestamp used when start or end is omitted")

    status = sub.add_parser(
        "set-status",
        help="set a status other than done, and optionally record actuals",
    )
    status.add_argument("plan")
    status.add_argument("task")
    status.add_argument("status", choices=list(STATUSES))
    status.add_argument("--hours", default=None)
    status.add_argument("--attempts", type=int, default=None)
    status.add_argument("--start", default=None)
    status.add_argument("--end", default=None)
    status.add_argument("--at", default=None)

    replan = sub.add_parser(
        "replan",
        help="freeze in-progress work, reschedule from now, regenerate views, append log.md",
    )
    replan.add_argument("plan")
    replan.add_argument("--now", action="store_true", required=True, help="required: schedule from --at")
    replan.add_argument("--reason", required=True)
    replan.add_argument("--at", default=None, help="ISO-8601 timestamp for now (default: current time)")
    replan.add_argument("--force", action="store_true", help="bypass the rate limit and hysteresis")
    replan.add_argument(
        "--human",
        action="store_true",
        help="a person asked for this replan; bypass the rate limit only",
    )
    return parser


def _root(args) -> Path:
    return args.root.resolve() if args.root else repo_root()


def _selected(args, include_templates: bool) -> list[Path]:
    if getattr(args, "plans", None):
        return [resolve_plan_dir(item) for item in args.plans]
    return plan_dirs(_root(args), include_templates=include_templates)


def _print_findings(path: Path, findings) -> None:
    label = path / "plan.yaml"
    for warning in findings.warnings:
        print(f"warning: {label}: {warning}")
    for error in findings.errors:
        print(f"error: {label}: {error}")


def _open_plan(directory: Path):
    try:
        return load_plan(directory / "plan.yaml")
    except (LoadError, YamlError) as exc:
        messages = exc.errors if isinstance(exc, LoadError) else [str(exc)]
        print(f"{directory / 'plan.yaml'}:")
        for message in messages:
            print(f"  - {message}")
        return None


def _open_roster(root: Path):
    path = root / "roster.yaml"
    try:
        return load_roster(path)
    except (LoadError, YamlError) as exc:
        messages = exc.errors if isinstance(exc, LoadError) else [str(exc)]
        print(f"{path}:")
        for message in messages:
            print(f"  - {message}")
        return None


def _validated(directory: Path, roster):
    plan = _open_plan(directory)
    if plan is None:
        return None
    findings = validate_plan(plan, roster)
    _print_findings(directory, findings)
    if not findings.ok:
        return None
    return plan


def cmd_validate(args) -> int:
    from planner.util import template_library_dirs

    roster = _open_roster(_root(args))
    if roster is None:
        return 1
    directories = _selected(args, include_templates=True)
    if not getattr(args, "plans", None):
        directories = directories + template_library_dirs(_root(args))
    if not directories:
        print("no plans found")
        return 1
    failed = False
    for directory in directories:
        plan = _validated(directory, roster)
        if plan is None:
            failed = True
            continue
        print(f"ok: {directory / 'plan.yaml'} ({len(plan.tasks)} tasks)")
    return 1 if failed else 0


def _scheduled(directory: Path, roster, at=None):
    plan = _validated(directory, roster)
    if plan is None:
        return None, None
    try:
        if at is None:
            result = schedule_plan(plan, roster)
            notes: list[str] = []
        else:
            result, notes = schedule_from_now(plan, roster, at)
    except (ScheduleError, ValueError) as exc:
        print(f"error: {directory / 'plan.yaml'}: {exc}")
        return None, None
    for note in notes:
        print(f"note: {plan.plan_id}: {note}")
    warning = deadline_warning(plan, result)
    if warning:
        print(f"warning: {warning}")
    return plan, result


def _write_schedule(directory: Path, plan, result) -> None:
    target = directory / "schedule.yaml"
    target.write_text(render_schedule_yaml(plan, result), encoding="utf-8")
    path = " -> ".join(result.critical_path) or "(none)"
    label = "from-now" if result.mode == "from-now" else "origin"
    print(
        f"wrote {target}: makespan {fmt_hours(result.makespan)}h, "
        f"critical path {path} ({label})"
    )


def _write_gantt(directory: Path, plan, result) -> None:
    target = directory / "gantt.md"
    target.write_text(render_gantt(plan, result), encoding="utf-8")
    print(f"wrote {target}")


def _clock(args):
    try:
        return clock_from(getattr(args, "at", None))
    except ValueError as exc:
        print(f"error: {exc}")
        return None


def cmd_schedule(args) -> int:
    roster = _open_roster(_root(args))
    if roster is None:
        return 1
    directories = _selected(args, include_templates=True)
    if not directories:
        print("no plans found")
        return 1
    at = None
    if args.from_now:
        at = _clock(args)
        if at is None:
            return 1
    failed = False
    for directory in directories:
        plan, result = _scheduled(directory, roster, at)
        if plan is None or result is None:
            failed = True
            continue
        _write_schedule(directory, plan, result)
    return 1 if failed else 0


def cmd_gantt(args) -> int:
    roster = _open_roster(_root(args))
    if roster is None:
        return 1
    directories = _selected(args, include_templates=True)
    if not directories:
        print("no plans found")
        return 1
    at = None
    if args.from_now:
        at = _clock(args)
        if at is None:
            return 1
    failed = False
    for directory in directories:
        plan, result = _scheduled(directory, roster, at)
        if plan is None or result is None:
            failed = True
            continue
        _write_gantt(directory, plan, result)
    return 1 if failed else 0


def _live_blocks(args, render, roster=None) -> int:
    if roster is None:
        roster = _open_roster(_root(args))
    if roster is None:
        return 1
    directories = _selected(args, include_templates=False)
    if not directories:
        print("no plans found")
        return 1
    failed = False
    blocks = []
    for directory in directories:
        plan, result = _scheduled(directory, roster)
        if plan is None or result is None:
            failed = True
            continue
        blocks.append(render(plan, result))
    if blocks:
        print("\n\n".join(blocks))
    return 1 if failed else 0


def cmd_ready(args) -> int:
    return _live_blocks(args, render_ready)


def cmd_status(args) -> int:
    return _live_blocks(args, render_status)


def cmd_dispatch(args) -> int:
    roster = _open_roster(_root(args))
    if roster is None:
        return 1

    def render(plan, result, bound=roster):
        return render_dispatch(plan, result, bound)

    return _live_blocks(args, render, roster)


def _live_plans(root: Path, roster):
    plans = []
    failed = False
    for directory in plan_dirs(root, include_templates=False):
        plan = _validated(directory, roster)
        if plan is None:
            failed = True
            continue
        plans.append(plan)
    return plans, failed


def cmd_calibrate(args) -> int:
    roster = _open_roster(_root(args))
    if roster is None:
        return 1
    plans, failed = _live_plans(_root(args), roster)
    print(render_calibration(plans))
    if args.apply and not failed:
        text = render_calibration_yaml(build_calibration(plans))
        target = _root(args) / "calibration.yaml"
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target}")
    return 1 if failed else 0


def _one_plan(args):
    roster = _open_roster(_root(args))
    if roster is None:
        return None, None
    try:
        directory = resolve_plan_dir(args.plan)
    except SystemExit as exc:
        print(exc)
        return None, None
    plan = _validated(directory, roster)
    if plan is None:
        return None, None
    return roster, plan


def _calibration_source(plan: Plan, root: Path) -> tuple[object, str]:
    path = root / "calibration.yaml"
    if path.is_file():
        try:
            return load_calibration(path), str(path)
        except ValueError as exc:
            print(f"error: {exc}")
            return None, ""
    return calibration_for(plan), "none (factor 1 until calibrate --apply)"


def cmd_estimate(args) -> int:
    roster, plan = _one_plan(args)
    if plan is None or roster is None:
        return 1
    cal, source = _calibration_source(plan, _root(args))
    if cal is None:
        return 1
    print(render_estimate(plan, cal, source=source))
    return 0


def cmd_apply_calibration(args) -> int:
    roster, plan = _one_plan(args)
    if plan is None or roster is None:
        return 1
    path = _root(args) / "calibration.yaml"
    if not path.is_file():
        print("error: calibration.yaml is missing. Run calibrate --apply first.")
        return 1
    try:
        cal = load_calibration(path)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    at = _clock(args)
    if at is None:
        return 1
    try:
        _changed, message = apply_calibration(plan, cal, at=at.isoformat())
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(message)
    return 0


def cmd_portfolio(args) -> int:
    if getattr(args, "view", "summary") == "schedule":
        return cmd_portfolio_schedule(args)
    roster = _open_roster(_root(args))
    if roster is None:
        return 1
    entries = []
    failed = False
    directories = plan_dirs(_root(args), include_templates=False)
    if not directories:
        print("no plans found")
        return 1
    for directory in directories:
        plan, result = _scheduled(directory, roster)
        if plan is None or result is None:
            failed = True
            continue
        entries.append(plan_entry(plan, result, roster))
    if failed:
        return 1
    root = _root(args)
    md = root / "portfolio.md"
    yml = root / "portfolio.yaml"
    md.write_text(render_portfolio_md(entries), encoding="utf-8")
    yml.write_text(render_portfolio_yaml(entries), encoding="utf-8")
    print(f"wrote {md}")
    print(f"wrote {yml}")
    return 0


def cmd_portfolio_schedule(args) -> int:
    from planner.leveling import is_open, level_plans, render_portfolio_schedule
    from planner.util import fmt_hours

    roster = _open_roster(_root(args))
    if roster is None:
        return 1
    plans, failed = _live_plans(_root(args), roster)
    if failed:
        return 1
    open_plans = [plan for plan in plans if is_open(plan)]
    try:
        document = level_plans(open_plans, roster)
    except ScheduleError as exc:
        print(f"error: {exc}")
        return 1
    root = _root(args)
    target = root / "portfolio-schedule.yaml"
    target.write_text(render_portfolio_schedule(document), encoding="utf-8")
    over = document["overallocated"]
    print(f"wrote {target}")
    print(f"origin: {document['origin']}")
    print(f"plans: {len(document['plans'])}")
    print(f"makespan: {fmt_hours(document['makespan_hours'])}h")
    print("overallocated: " + (", ".join(over) if over else "(none)"))
    return 0


def _surface_plans(args):
    roster = _open_roster(_root(args))
    if roster is None:
        return None, None, None
    plans = []
    results = {}
    directories = plan_dirs(_root(args), include_templates=False)
    if not directories:
        print("no plans found")
        return None, None, None
    for directory in directories:
        plan, result = _scheduled(directory, roster)
        if plan is None or result is None:
            return None, None, None
        plans.append(plan)
        results[plan.plan_id] = result
    return roster, plans, results


def cmd_simulate(args) -> int:
    from planner.simulate import omit_ids, parse_capacity_adds, render_simulation

    roster, plan = _one_plan(args)
    if plan is None or roster is None:
        return 1
    if args.runs < 1:
        print("error: --runs must be >= 1")
        return 1
    try:
        additions = parse_capacity_adds(args.add_capacity)
        omit = omit_ids(plan, args.omit, args.omit_deliverable)
        if args.deadline:
            from planner.util import parse_datetime

            parse_datetime(args.deadline, "--deadline")
        text = render_simulation(
            plan,
            roster,
            seed=args.seed,
            runs=args.runs,
            additions=additions,
            omit=omit,
            deadline=args.deadline,
        )
    except (ValueError, ScheduleError) as exc:
        print(f"error: {exc}")
        return 1
    print(text)
    return 0


def cmd_instantiate(args) -> int:
    from planner.library import instantiate

    root = _root(args)
    try:
        path = instantiate(root, args.kind, args.into, args.plan_id, args.title)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(f"wrote {path}")
    return 0


def cmd_dashboard(args) -> int:
    from planner.dashboard import write_dashboard

    at = _clock(args)
    if at is None:
        return 1
    roster, plans, results = _surface_plans(args)
    if roster is None or plans is None or results is None:
        return 1
    md_path, html_path = write_dashboard(_root(args), plans, roster, results, at)
    print(f"wrote {md_path}")
    print(f"wrote {html_path}")
    return 0


def cmd_digest(args) -> int:
    import os

    from planner.dashboard import write_digest
    from planner.slack import WEBHOOK_ENV, post_webhook, slack_payload, write_payload

    at = _clock(args)
    if at is None:
        return 1
    roster, plans, results = _surface_plans(args)
    if roster is None or plans is None or results is None:
        return 1
    root = _root(args)
    path, text = write_digest(root, plans, roster, results, at)
    payload_path = write_payload(root, text)
    print(text)
    print(f"wrote {path}")
    print(f"wrote {payload_path}")
    if not args.post:
        print("slack: dry run (pass --post to send)")
        return 0
    url = os.environ.get(WEBHOOK_ENV, "").strip()
    if not url:
        print(f"error: --post requires {WEBHOOK_ENV}")
        return 1
    try:
        status = post_webhook(url, slack_payload(text))
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    except OSError as exc:
        print(f"error: slack post failed: {exc}")
        return 1
    print(f"slack: posted ({status})")
    return 0


def cmd_serve(args) -> int:
    from planner.serve import serve

    if args.port < 1 or args.port > 65535:
        print("error: --port must be between 1 and 65535")
        return 1
    if args.refresh < 0:
        print("error: --refresh must be >= 0")
        return 1
    at = None
    if args.at:
        at = _clock(args)
        if at is None:
            return 1
    if not args.host.strip():
        print("error: --host must not be empty")
        return 1
    try:
        serve(
            _root(args),
            args.host.strip(),
            args.port,
            refresh_seconds=args.refresh or None,
            at=at,
        )
    except OSError as exc:
        print(f"error: {exc}")
        return 1
    return 0


def cmd_brief(args) -> int:
    roster, plan = _one_plan(args)
    if plan is None or roster is None:
        return 1
    result = None
    if not args.task:
        try:
            result = schedule_plan(plan, roster)
        except ScheduleError as exc:
            print(f"error: {exc}")
            return 1
    try:
        tasks = brief_targets(plan, result, args.task)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    if not tasks:
        print("no dispatchable tasks")
        return 0
    written = write_briefs(plan, tasks)
    for path in written:
        print(f"wrote {path}")
    return 0


def _task(plan: Plan, task_id: str):
    for task in plan.tasks:
        if task.id == task_id:
            return task
    print(f"error: unknown task {task_id}")
    return None


def cmd_record(args) -> int:
    _roster, plan = _one_plan(args)
    if plan is None:
        return 1
    task = _task(plan, args.task)
    if task is None:
        return 1
    try:
        manifest = record_artifact(
            plan,
            task,
            args.file,
            name=args.name,
            producer=args.producer,
            attempt=args.attempt,
        )
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(
        f"recorded {args.name or args.file.name} for {task.id} "
        f"({len(manifest.artifacts)} artifacts in the manifest)"
    )
    return 0


def cmd_attest(args) -> int:
    _roster, plan = _one_plan(args)
    if plan is None:
        return 1
    task = _task(plan, args.task)
    if task is None:
        return 1
    at = _clock(args)
    if at is None:
        return 1
    try:
        attestation = attest_task(
            plan,
            task,
            verdict=args.verdict,
            timestamp=at.isoformat(),
            notes=args.notes,
        )
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    print(
        f"wrote attestations/{attestation.task_id}.yaml "
        f"verdict {attestation.verdict} reviewer {attestation.reviewer}"
    )
    if args.verdict == "fail":
        print("rework: set-status to failed or in_review. complete will refuse this task.")
    return 0


def _refresh_origin(directory: Path, roster) -> int:
    plan = _validated(directory, roster)
    if plan is None:
        return 1
    try:
        result = schedule_plan(plan, roster)
    except ScheduleError as exc:
        print(f"error: {exc}")
        return 1
    _write_schedule(directory, plan, result)
    _write_gantt(directory, plan, result)
    return 0


def _decimal_hours(text: str, where: str):
    from decimal import Decimal

    try:
        number = Decimal(text)
    except Exception:
        print(f"error: {where} must be a number")
        return None
    if number < 0:
        print(f"error: {where} must be >= 0")
        return None
    return number


def cmd_complete(args) -> int:
    roster, plan = _one_plan(args)
    if plan is None or roster is None:
        return 1
    task = _task(plan, args.task)
    if task is None:
        return 1
    if args.attempts < 1:
        print("error: --attempts must be >= 1")
        return 1
    hours = _decimal_hours(args.hours, "--hours")
    if hours is None:
        return 1
    at = _clock(args)
    if at is None:
        return 1
    probe = replace(task, status="done")
    problems = done_lock_errors(plan, probe)
    if problems:
        for problem in problems:
            print(f"error: {problem}")
        print("refusing to set done without a passing attestation from accuracy.")
        return 1
    stamp = at.isoformat()
    start = args.start or task.actuals.start or stamp
    end = args.end or stamp
    if plan.path is None or plan.directory is None:
        print("error: plan has no directory")
        return 1
    text = plan.path.read_text(encoding="utf-8")
    text = set_status(text, task.id, "done")
    text = set_actuals(
        text,
        task.id,
        start=start,
        end=end,
        hours=hours,
        attempts=args.attempts,
        set_start=True,
        set_end=True,
        set_hours=True,
        set_attempts=True,
    )
    new_version = plan.version + 1
    text = set_version(text, new_version)
    plan.path.write_text(text, encoding="utf-8")
    append_log(
        plan.directory / "log.md",
        log_entry(
            version=new_version,
            at=stamp,
            kind="complete",
            reason=f"Accuracy passed {task.id}.",
            change=f"Set {task.id} to done. actuals.hours {fmt_hours(hours)}, attempts {args.attempts}.",
            frozen=[item.id for item in plan.tasks if item.status in {"in_progress", "in_review"} and item.id != task.id],
        ),
    )
    print(f"set {task.id} done at version {new_version}")
    return _refresh_origin(plan.directory, roster)


def cmd_set_status(args) -> int:
    if args.status == "done":
        print("refusing to set done. Run attest-done, then complete.")
        return 1
    roster, plan = _one_plan(args)
    if plan is None or roster is None:
        return 1
    task = _task(plan, args.task)
    if task is None:
        return 1
    at = _clock(args)
    if at is None:
        return 1
    hours = None
    if args.hours is not None:
        hours = _decimal_hours(args.hours, "--hours")
        if hours is None:
            return 1
    if plan.path is None or plan.directory is None:
        print("error: plan has no directory")
        return 1
    text = plan.path.read_text(encoding="utf-8")
    text = set_status(text, task.id, args.status)
    text = set_actuals(
        text,
        task.id,
        start=args.start,
        end=args.end,
        hours=hours,
        attempts=args.attempts,
        set_start=args.start is not None,
        set_end=args.end is not None,
        set_hours=args.hours is not None,
        set_attempts=args.attempts is not None,
    )
    new_version = plan.version + 1
    text = set_version(text, new_version)
    plan.path.write_text(text, encoding="utf-8")
    stamp = at.isoformat()
    append_log(
        plan.directory / "log.md",
        log_entry(
            version=new_version,
            at=stamp,
            kind="set-status",
            reason=f"Set {task.id} to {args.status}.",
            change=f"{task.id} status is {args.status}.",
            frozen=[item.id for item in plan.tasks if item.status in {"in_progress", "in_review"}],
        ),
    )
    print(f"set {task.id} {args.status} at version {new_version}")
    if args.status == "failed":
        print("rework path: failed. A later replan may bypass the rate limit while a task is failed.")
    return _refresh_origin(plan.directory, roster)


def cmd_replan(args) -> int:
    if not args.now:
        print("error: replan requires --now")
        return 1
    roster, plan = _one_plan(args)
    if plan is None or roster is None:
        return 1
    at = _clock(args)
    if at is None:
        return 1
    outcome = replan_plan(
        plan,
        roster,
        reason=args.reason,
        at=at,
        force=args.force,
        human=args.human,
    )
    print(outcome.message)
    return outcome.code


def cmd_check(args) -> int:
    """Validate every plan, then run the unit tests against this checkout.

    The validate half honors ``AGENT_PLANS_DIR``. The unit-test half clears
    that variable so snapshot tests keep comparing the plans and generated
    views committed in this repository. ``pytest`` clears it the same way
    when the test package is imported.
    """
    code = cmd_validate(args)
    root = _root(args)
    saved = os.environ.pop(PLANS_ENV, None)
    try:
        suite = unittest.defaultTestLoader.discover(
            start_dir=str(root / "tests"),
            top_level_dir=str(root),
        )
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        if saved is not None:
            os.environ[PLANS_ENV] = saved
    if code != 0 or not result.wasSuccessful():
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass
    parser = _build_parser()
    args = parser.parse_args(argv)
    commands = {
        "validate": cmd_validate,
        "schedule": cmd_schedule,
        "gantt": cmd_gantt,
        "ready": cmd_ready,
        "status": cmd_status,
        "calibrate": cmd_calibrate,
        "estimate": cmd_estimate,
        "apply-calibration": cmd_apply_calibration,
        "portfolio": cmd_portfolio,
        "simulate": cmd_simulate,
        "instantiate": cmd_instantiate,
        "dashboard": cmd_dashboard,
        "digest": cmd_digest,
        "serve": cmd_serve,
        "brief": cmd_brief,
        "dispatch-dry-run": cmd_dispatch,
        "record-artifact": cmd_record,
        "attest-done": cmd_attest,
        "attest": cmd_attest,
        "complete": cmd_complete,
        "set-status": cmd_set_status,
        "replan": cmd_replan,
        "check": cmd_check,
    }
    return commands[args.cmd](args)
