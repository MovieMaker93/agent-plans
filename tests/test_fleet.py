# SPDX-License-Identifier: Apache-2.0
"""Shared capacity, sub-plans, cloud agents, simulation, and the static surface."""

import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from planner.calibration import Factor, identity_calibration, preview_hours, resolve_factor
from planner.cli import main
from planner.dashboard import render_dashboard_md, render_digest
from planner.dispatch import render_dispatch
from planner.forecast import project
from planner.gantt import render_gantt, render_schedule_yaml
from planner.leveling import is_open, level_plans, render_portfolio_schedule
from planner.library import instantiate
from planner.model import CalibrationOverride, load_plan, load_roster
from planner.reports import schedule_plan
from planner.simulate import parse_capacity_adds, render_simulation
from planner.util import parse_datetime, plan_dirs, repo_root, template_library_dirs
from planner.validate import validate_plan
from planner.yamlio import load_yaml

ROOT = repo_root()
ROSTER = ROOT / "roster.yaml"
EXAMPLE = ROOT / "plans" / "2026-10-08-product-announcement"
ORIGIN = "2026-10-08T09:00:00-05:00"


def _run(argv):
    from contextlib import redirect_stdout
    from io import StringIO

    buffer = StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


def _task(task_id, assignee, hours, caps, outputs, deps, status="planned"):
    cap_yaml = "\n".join(f"      - {name}" for name in caps)
    dep_yaml = "[]" if not deps else "\n".join(f"      - {dep}" for dep in deps)
    if deps:
        dep_yaml = "\n" + dep_yaml
    out_yaml = "\n".join(
        f"      - artifact: {name}\n        type: markdown" for name in outputs
    )
    inputs = []
    for dep in deps:
        inputs.append(f"      - artifact: {dep}.md\n        from_task: {dep}")
    input_yaml = "[]" if not inputs else "\n" + "\n".join(inputs)
    return f"""
  - id: {task_id}
    title: {task_id}
    task_type: work
    objective: Do {task_id}.
    inputs: {input_yaml}
    outputs:
{out_yaml}
    depends_on: {dep_yaml}
    estimate_hours:
      optimistic: {hours}
      likely: {hours}
      pessimistic: {hours}
    required_capabilities:
{cap_yaml}
    assignee: {assignee}
    acceptance_criteria:
      - {outputs[0]} exists
    risk: low
    reversible: true
    needs_human: false
    budget:
      tokens: 10
      hours: 8
      usd: 1
    max_retries: 1
    status: {status}
    actuals:
      start: null
      end: null
      hours: null
      attempts: 0
"""


def _plan(plan_id, tasks, priority=0, deadline="2026-10-20T17:00:00-05:00", extra=""):
    deliverables = []
    # Last task's first output is the deliverable. Callers use one chain.
    return f"""
schema_version: 1
plan_id: {plan_id}
version: 1
title: {plan_id}
priority: {priority}
goal: Ship {plan_id}.
owner: orchestrator
definition_of_done: The deliverable exists.
budget:
  tokens: 100
  hours: 20
  usd: 10
human_approval_policy: Hold if unsure.
constraints:
  deadline: "{deadline}"
  max_parallel: 3
  schedule_origin: "{ORIGIN}"
{extra}deliverables:
  - PLACEHOLDER
tasks:
{tasks}
"""


class RepoMixin:
    def make_repo(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        shutil.copy(ROSTER, root / "roster.yaml")
        (root / "plans").mkdir()
        return root

    def write_plan(self, root, folder, text):
        # The last output in the file is the deliverable.
        outputs = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- artifact:") or stripped.startswith("artifact:"):
                outputs.append(stripped.split(":", 1)[1].strip())
        deliverable = outputs[-1] if outputs else "out.md"
        text = text.replace("PLACEHOLDER", deliverable)
        # Inputs named <dep>.md must match a predecessor output. The helper
        # names outputs explicitly, so rewrite from_task inputs to that file
        # only when the caller used the helper's <dep>.md convention.
        directory = root / "plans" / folder
        directory.mkdir(parents=True)
        (directory / "plan.yaml").write_text(text, encoding="utf-8")
        (directory / "log.md").write_text("# Decision log\n\n## 2026-10-08 v1\n\n- Reason: Fixture.\n", encoding="utf-8")
        return directory


class LevelingTests(RepoMixin, unittest.TestCase):
    def test_shared_assignee_does_not_overlap_and_priority_wins(self):
        root = self.make_repo()
        image = _task("T1", "creative-director", 2, ["image_generation"], ["shot.png"], [])
        self.write_plan(
            root,
            "alpha",
            _plan("alpha", image, priority=2, deadline="2026-10-20T17:00:00-05:00"),
        )
        self.write_plan(
            root,
            "beta",
            _plan("beta", image, priority=0, deadline="2026-10-09T17:00:00-05:00"),
        )
        roster = load_roster(root / "roster.yaml")
        plans = [load_plan(path / "plan.yaml") for path in sorted((root / "plans").iterdir())]
        for plan in plans:
            self.assertEqual(validate_plan(plan, roster).errors, [], plan.plan_id)
        document = level_plans(plans, roster)
        self.assertEqual(document["overallocated"], [])
        by_plan = {row["plan_id"]: row for row in document["tasks"]}
        self.assertEqual(by_plan["alpha"]["start"], Decimal(0))
        self.assertEqual(by_plan["alpha"]["finish"], Decimal(2))
        self.assertEqual(by_plan["beta"]["start"], Decimal(2))
        self.assertEqual(by_plan["beta"]["finish"], Decimal(4))

    def test_earlier_deadline_wins_when_priority_ties(self):
        root = self.make_repo()
        image = _task("T1", "creative-director", 2, ["image_generation"], ["shot.png"], [])
        self.write_plan(root, "late", _plan("late", image, priority=0, deadline="2026-10-20T17:00:00-05:00"))
        self.write_plan(root, "soon", _plan("soon", image, priority=0, deadline="2026-10-09T17:00:00-05:00"))
        roster = load_roster(root / "roster.yaml")
        plans = [load_plan(path / "plan.yaml") for path in (root / "plans").iterdir()]
        document = level_plans(plans, roster)
        by_plan = {row["plan_id"]: row for row in document["tasks"]}
        self.assertEqual(by_plan["soon"]["start"], Decimal(0))
        self.assertEqual(by_plan["late"]["start"], Decimal(2))
        self.assertEqual(document["overallocated"], [])

    def test_example_leveling_matches_its_own_schedule(self):
        roster = load_roster(ROSTER)
        plan = load_plan(EXAMPLE / "plan.yaml")
        document = level_plans([plan], roster)
        self.assertEqual(document["overallocated"], [])
        solo = {item["id"]: item for item in load_yaml(EXAMPLE / "schedule.yaml")["tasks"]}
        for row in document["tasks"]:
            self.assertEqual(row["start"], Decimal(str(solo[row["task_id"]]["start"])))
            self.assertEqual(row["finish"], Decimal(str(solo[row["task_id"]]["finish"])))
        live = [
            load_plan(directory / "plan.yaml")
            for directory in plan_dirs(ROOT, include_templates=False)
        ]
        shared = level_plans([item for item in live if is_open(item)], roster)
        committed = render_portfolio_schedule(shared)
        self.assertEqual((ROOT / "portfolio-schedule.yaml").read_text(encoding="utf-8"), committed)

    def test_closed_plan_is_not_open(self):
        roster = load_roster(ROSTER)
        plan = load_plan(EXAMPLE / "plan.yaml")
        self.assertTrue(is_open(plan))
        for task in plan.tasks:
            task.status = "done"
        self.assertFalse(is_open(plan))
        self.assertEqual(level_plans([], roster)["tasks"], [])


class SubplanTests(RepoMixin, unittest.TestCase):
    def _child(self):
        first = _task("C1", "research", 2, ["research"], ["notes.md"], [])
        second = _task("C2", "research", 2, ["research"], ["report.md"], ["C1"])
        # Helper names the C2 input C1.md, but C1 produces notes.md.
        second = second.replace("artifact: C1.md", "artifact: notes.md")
        return _plan("child-work", first + second, extra="")

    def test_duration_and_status_roll_up_without_rewriting_the_parent(self):
        root = self.make_repo()
        child_dir = self.write_plan(root, "child", self._child())
        child = load_plan(child_dir / "plan.yaml")
        child_text = (child_dir / "plan.yaml").read_text(encoding="utf-8")
        child_text = child_text.replace("id: C1\n    title: C1", "id: C1\n    title: C1")
        # Mark C1 in progress by replacing its status only. Both tasks say planned.
        child_text = child_text.replace("status: planned", "status: in_progress", 1)
        (child_dir / "plan.yaml").write_text(child_text, encoding="utf-8")
        parent_task = _task("T1", "research", 1, ["research"], ["parent.md"], [])
        parent_task = parent_task.replace(
            "status: planned",
            "subplan: plans/child\n    status: planned",
        )
        parent_dir = self.write_plan(root, "parent", _plan("parent-work", parent_task))
        before = (parent_dir / "plan.yaml").read_text(encoding="utf-8")
        roster = load_roster(root / "roster.yaml")
        parent = load_plan(parent_dir / "plan.yaml")
        findings = validate_plan(parent, roster)
        self.assertEqual(findings.errors, [])
        self.assertTrue(any("rolls up to in_progress" in warning for warning in findings.warnings))
        result = schedule_plan(parent, roster)
        self.assertEqual(result.by_id["T1"].expected, Decimal(4))
        self.assertEqual(result.by_id["T1"].finish, Decimal(4))
        self.assertEqual(result.rolled_status["T1"], "in_progress")
        schedule = render_schedule_yaml(parent, result)
        self.assertIn('subplan: "plans/child"', schedule)
        self.assertIn("rolled_status: in_progress", schedule)
        gantt = render_gantt(parent, result)
        self.assertIn("Subplan rollup: T1 is in_progress", gantt)
        self.assertEqual((parent_dir / "plan.yaml").read_text(encoding="utf-8"), before)
        self.assertEqual(parent.tasks[0].status, "planned")

    def test_done_parent_over_an_open_child_fails_validation(self):
        root = self.make_repo()
        self.write_plan(root, "child", self._child())
        parent_task = _task("T1", "research", 1, ["research"], ["parent.md"], [])
        parent_task = parent_task.replace(
            "status: planned",
            "subplan: plans/child\n    status: done",
        )
        parent_dir = self.write_plan(root, "parent", _plan("parent-work", parent_task))
        roster = load_roster(root / "roster.yaml")
        findings = validate_plan(load_plan(parent_dir / "plan.yaml"), roster)
        self.assertTrue(any("rolls up to planned" in error for error in findings.errors))

    def test_missing_subplan_is_an_error(self):
        root = self.make_repo()
        parent_task = _task("T1", "research", 1, ["research"], ["parent.md"], [])
        parent_task = parent_task.replace(
            "status: planned",
            "subplan: plans/missing\n    status: planned",
        )
        parent_dir = self.write_plan(root, "parent", _plan("parent-work", parent_task))
        roster = load_roster(root / "roster.yaml")
        findings = validate_plan(load_plan(parent_dir / "plan.yaml"), roster)
        self.assertTrue(any("was not found" in error for error in findings.errors))


class CloudAndCalibrationTests(RepoMixin, unittest.TestCase):
    def test_cloud_agent_is_dispatched_like_a_bot(self):
        root = self.make_repo()
        task = _task("T1", "cloud-worker", 1, ["research"], ["note.md"], [])
        directory = self.write_plan(root, "cloud", _plan("cloud-job", task))
        roster = load_roster(root / "roster.yaml")
        plan = load_plan(directory / "plan.yaml")
        self.assertEqual(validate_plan(plan, roster).errors, [])
        bot = roster.get("cloud-worker")
        self.assertEqual(bot.kind, "cloud_agent")
        self.assertEqual(bot.capacity, 2)
        self.assertEqual(bot.cost.usd_per_hour, Decimal("1.5"))
        text = render_dispatch(plan, schedule_plan(plan, roster), roster)
        self.assertIn("T1 | cloud-worker | briefs/T1.md", text)
        self.assertIn("kind cloud_agent", text)

    def test_plan_override_beats_the_shared_file_and_kind_is_last(self):
        root = self.make_repo()
        task = _task("T1", "research", 2, ["research"], ["note.md"], [])
        directory = self.write_plan(
            root,
            "sample",
            _plan(
                "sample",
                task,
                extra=(
                    "template_kind: announcement\n"
                    "calibration_override:\n"
                    "  by_pair:\n"
                    "    - assignee: research\n"
                    "      task_type: work\n"
                    "      factor: 1.25\n"
                ),
            ),
        )
        plan = load_plan(directory / "plan.yaml")
        cal = identity_calibration()
        cal.by_pair[("research", "work")] = Factor(4, Decimal("2"), Decimal("2"))
        cal.by_kind["announcement"] = Factor(4, Decimal("1.5"), Decimal("1.5"))
        factor, _count, basis = resolve_factor(cal, plan.tasks[0], plan)
        self.assertEqual(basis, "plan_pair")
        self.assertEqual(factor, Decimal("1.25"))
        _raw, adjusted, _n, basis = preview_hours(plan.tasks[0], cal, plan)
        self.assertEqual(adjusted, Decimal("2.5"))
        plan.calibration_override = CalibrationOverride(factor=Decimal("1.1"))
        _factor, _count, basis = resolve_factor(cal, plan.tasks[0], plan)
        self.assertEqual(basis, "plan_factor")
        plan.calibration_override = None
        _factor, _count, basis = resolve_factor(cal, plan.tasks[0], plan)
        self.assertEqual(basis, "pair")
        cal.by_pair.clear()
        factor, _count, basis = resolve_factor(cal, plan.tasks[0], plan)
        self.assertEqual(basis, "template_kind")
        self.assertEqual(factor, Decimal("1.5"))


class SurfaceTests(RepoMixin, unittest.TestCase):
    def test_simulate_is_stable_and_capacity_changes_the_p50(self):
        root = self.make_repo()
        tasks = _task("T1", "creative-director", 2, ["image_generation"], ["a.png"], [])
        tasks += _task("T2", "creative-director", 2, ["image_generation"], ["b.png"], [])
        directory = self.write_plan(root, "pics", _plan("pics", tasks))
        roster = load_roster(root / "roster.yaml")
        plan = load_plan(directory / "plan.yaml")
        before = (directory / "plan.yaml").read_text(encoding="utf-8")
        first = render_simulation(plan, roster, seed=1, runs=30)
        second = render_simulation(plan, roster, seed=1, runs=30)
        self.assertEqual(first, second)
        self.assertIn("p10:", first)
        self.assertIn("p50:", first)
        self.assertIn("p90:", first)
        self.assertIn("what_if: (none)", first)
        self.assertIn("(4h)", first)
        added = render_simulation(
            plan,
            roster,
            seed=1,
            runs=30,
            additions=parse_capacity_adds(["creative-director=1"]),
        )
        self.assertIn("delta_p50_hours: -2", added)
        self.assertEqual((directory / "plan.yaml").read_text(encoding="utf-8"), before)
        code, out = _run(
            ["--root", str(ROOT), "simulate", str(EXAMPLE), "--runs", "40", "--seed", "1"]
        )
        self.assertEqual(code, 0, out)
        self.assertIn("plan: launch-announce-2026-10", out)
        self.assertIn("p50:", out)
        self.assertIn("writes: nothing", out)

    def test_omit_and_deadline_are_report_only(self):
        root = self.make_repo()
        tasks = _task("T1", "research", 2, ["research"], ["notes.md"], [])
        tasks += _task("T2", "research", 2, ["research"], ["report.md"], ["T1"])
        tasks = tasks.replace("artifact: T1.md", "artifact: notes.md")
        directory = self.write_plan(root, "chain", _plan("chain", tasks))
        roster = load_roster(root / "roster.yaml")
        plan = load_plan(directory / "plan.yaml")
        text = render_simulation(
            plan,
            roster,
            seed=3,
            runs=10,
            omit={"T2"},
            deadline="2026-10-08T13:00:00-05:00",
        )
        self.assertIn("delta_p50_hours: -2", text)
        self.assertIn("omit: T2", text)
        self.assertIn("deadline: 2026-10-08T13:00:00-05:00", text)
        self.assertTrue((directory / "plan.yaml").is_file())

    def test_templates_instantiate(self):
        self.assertEqual(
            [path.name for path in template_library_dirs(ROOT)],
            ["announcement", "research-report"],
        )
        roster = load_roster(ROSTER)
        for directory in template_library_dirs(ROOT):
            plan = load_plan(directory / "plan.yaml")
            self.assertEqual(validate_plan(plan, roster).errors, [], directory.name)
            self.assertEqual(plan.template_kind, directory.name)
        root = self.make_repo()
        dest = root / "plans" / "2026-10-10-announce"
        path = instantiate(ROOT, "announcement", dest, "announce-test", title="Announce test")
        plan = load_plan(path)
        self.assertEqual(plan.plan_id, "announce-test")
        self.assertEqual(plan.title, "Announce test")
        self.assertEqual(plan.template_kind, "announcement")
        self.assertTrue((dest / "log.md").is_file())
        self.assertEqual(validate_plan(plan, roster).errors, [])

    def test_forecast_warns_before_breach_and_hold_is_separate_from_the_kill_switch(self):
        roster = load_roster(ROSTER)
        plan = load_plan(EXAMPLE / "plan.yaml")
        result = schedule_plan(plan, roster)
        text = render_dispatch(plan, result, roster)
        self.assertIn("halt_new_dispatch: no", text)
        self.assertIn("kill_switch:\n  (none)", text)
        self.assertIn("hours: 7.5/8 (0.9375) warn", text)
        self.assertIn("forecast_hold: no", text)
        self.assertIn("tokens: n/a", text)
        forecast = project(plan, result, roster)
        self.assertEqual(forecast.warnings, ["hours"])
        self.assertFalse(forecast.hold)
        root = self.make_repo()
        task = _task("T1", "cloud-worker", 4, ["research"], ["note.md"], [])
        # Budget of 1 hour is under the 4h forecast. Actuals are still empty.
        body = _plan("tight", task).replace("hours: 20", "hours: 1", 1)
        directory = self.write_plan(root, "tight", body)
        tight = load_plan(directory / "plan.yaml")
        tight_text = render_dispatch(tight, schedule_plan(tight, roster), roster)
        self.assertIn("forecast_hold: yes", tight_text)
        self.assertIn("halt_new_dispatch: no", tight_text)
        self.assertIn("usd:", tight_text)
        self.assertIn("breach", tight_text)

    def test_dashboard_and_digest_match_the_committed_snapshot(self):
        roster = load_roster(ROSTER)
        plans = []
        results = {}
        from planner.util import plan_dirs

        for directory in plan_dirs(ROOT, include_templates=False):
            plan = load_plan(directory / "plan.yaml")
            plans.append(plan)
            results[plan.plan_id] = schedule_plan(plan, roster)
        at = parse_datetime(ORIGIN, "origin")
        markdown = render_dashboard_md(plans, roster, results, at)
        digest = render_digest(plans, roster, results, at)
        self.assertIn("Ready queue", markdown)
        self.assertIn("T1 | research | briefs/T1.md", markdown)
        self.assertIn("Burn versus forecast", markdown)
        self.assertIn("hours: 7.5/8 (0.9375) warn", markdown)
        self.assertIn("Over-allocated assignees:", markdown)
        self.assertIn("What changed", digest)
        self.assertIn("Decisions needed", digest)
        self.assertIn("Next 24h", digest)
        self.assertIn("forecast warning on hours", digest)
        self.assertIn("launch-announce-2026-10 T1", digest)
        self.assertIn("SLACK_DIGEST_WEBHOOK", digest)
        self.assertIn("digest --post", digest)
        self.assertEqual((ROOT / "dashboard" / "index.md").read_text(encoding="utf-8"), markdown)
        self.assertEqual((ROOT / "dashboard" / "digest.md").read_text(encoding="utf-8"), digest)
        from planner.dashboard import render_dashboard_html

        html = render_dashboard_html(markdown, at)
        self.assertEqual((ROOT / "dashboard" / "index.html").read_text(encoding="utf-8"), html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)


if __name__ == "__main__":
    unittest.main()
