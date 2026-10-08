# SPDX-License-Identifier: Apache-2.0
"""Calibration write-back, done-lock, briefs, replan, and portfolio."""

import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path

from planner.brief import render_brief
from planner.calibration import (
    CLAMP_MAX,
    CLAMP_MIN,
    PRIOR_STRENGTH,
    build_calibration,
    render_calibration_yaml,
    shrink,
)
from planner.cli import main
from planner.dispatch import render_dispatch
from planner.model import load_plan, load_roster
from planner.planedit import set_status
from planner.portfolio import plan_entry, render_portfolio_md, render_portfolio_yaml
from planner.reports import schedule_plan
from planner.schedule import schedule_tasks
from planner.util import plan_dirs, repo_root
from planner.validate import validate_plan
from planner.yamlio import load_yaml
from tests.factories import task

ROOT = repo_root()
EXAMPLE = ROOT / "plans" / "2026-10-08-product-announcement"
ROSTER = ROOT / "roster.yaml"

MINIMAL = """
schema_version: 1
plan_id: sample
version: 1
goal: Write a note.
owner: orchestrator
definition_of_done: note.md exists.
budget:
  tokens: 10
  hours: 20
  usd: 1
human_approval_policy: Hold if unsure.
constraints:
  max_parallel: 2
  schedule_origin: "2026-10-08T09:00:00-05:00"
deliverables:
  - note.md
tasks:
  - id: T1
    title: Write the note
    task_type: research
    objective: Write note.md.
    inputs: []
    outputs:
      - artifact: note.md
        type: markdown
    depends_on: []
    estimate_hours:
      optimistic: 1
      likely: 1
      pessimistic: 1
    required_capabilities:
      - research
    assignee: research
    acceptance_criteria:
      - note.md exists
    risk: low
    reversible: true
    needs_human: false
    budget:
      tokens: 10
      hours: 4
      usd: 1
    max_retries: 2
    status: planned
    actuals:
      start: null
      end: null
      hours: null
      attempts: 0
"""

REPLAN = """
schema_version: 1
plan_id: replan-sample
version: 1
goal: Ship a note.
owner: orchestrator
definition_of_done: review.md exists.
budget:
  tokens: 10
  hours: 20
  usd: 1
human_approval_policy: Hold if unsure.
constraints:
  max_parallel: 2
  schedule_origin: "2026-10-08T09:00:00-05:00"
replan_policy:
  min_interval_minutes: 60
  hysteresis_hours: 0.5
  freeze_window_hours: 1
deliverables:
  - review.md
tasks:
  - id: T1
    title: Write the note
    task_type: research
    objective: Write note.md.
    inputs: []
    outputs:
      - artifact: note.md
        type: markdown
    depends_on: []
    estimate_hours:
      optimistic: 4
      likely: 4
      pessimistic: 4
    required_capabilities:
      - research
    assignee: research
    acceptance_criteria:
      - note.md exists
    risk: low
    reversible: true
    needs_human: false
    budget:
      tokens: 10
      hours: 8
      usd: 1
    max_retries: 2
    status: in_progress
    actuals:
      start: "2026-10-08T09:00:00-05:00"
      end: null
      hours: 1
      attempts: 1
  - id: T2
    title: Check the note
    task_type: accuracy_review
    objective: Check note.md.
    inputs:
      - artifact: note.md
        from_task: T1
    outputs:
      - artifact: review.md
        type: markdown
    depends_on:
      - T1
    estimate_hours:
      optimistic: 2
      likely: 2
      pessimistic: 2
    required_capabilities:
      - verification
    assignee: accuracy
    acceptance_criteria:
      - review.md says pass or fail
    risk: low
    reversible: true
    needs_human: false
    budget:
      tokens: 5
      hours: 4
      usd: 1
    max_retries: 2
    status: planned
    actuals:
      start: null
      end: null
      hours: null
      attempts: 0
"""


def _run(argv: list[str]) -> tuple[int, str]:
    buffer = StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class ShrinkTests(unittest.TestCase):
    def test_one_sample_moves_a_quarter_of_the_way(self):
        # n=1, prior=3, raw=2 -> 1 + 0.25 * 1 = 1.25
        self.assertEqual(
            shrink(Decimal(2), 1, PRIOR_STRENGTH, CLAMP_MIN, CLAMP_MAX),
            Decimal("1.25"),
        )

    def test_clamp_stops_a_single_bad_run_from_tripling(self):
        # n=1, raw=10 -> 3.25 before the clamp, stored as 2.
        self.assertEqual(
            shrink(Decimal(10), 1, PRIOR_STRENGTH, CLAMP_MIN, CLAMP_MAX),
            Decimal("2"),
        )

    def test_clamp_floor(self):
        self.assertEqual(
            shrink(Decimal("0.1"), 100, PRIOR_STRENGTH, CLAMP_MIN, CLAMP_MAX),
            Decimal("0.5"),
        )


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.roster = load_roster(ROSTER)
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        shutil.copy(ROSTER, self.root / "roster.yaml")
        self.plan_dir = self.root / "plans" / "sample"
        self.plan_dir.mkdir(parents=True)
        (self.plan_dir / "plan.yaml").write_text(MINIMAL, encoding="utf-8")
        (self.plan_dir / "log.md").write_text("# Decision log\n", encoding="utf-8")

    def test_apply_is_deterministic_and_does_not_compound(self):
        observed = load_plan(self.plan_dir / "plan.yaml")
        observed.tasks[0].status = "done"
        observed.tasks[0].actuals.hours = Decimal(2)
        first = render_calibration_yaml(build_calibration([observed]))
        second = render_calibration_yaml(build_calibration([observed]))
        self.assertEqual(first, second)
        cal = build_calibration([observed])
        self.assertEqual(cal.by_pair[("research", "research")].factor, Decimal("1.25"))
        self.assertEqual(cal.by_pair[("research", "research")].n, 1)
        (self.root / "calibration.yaml").write_text(first, encoding="utf-8")
        # Poison the stored calibrated value. Apply must replace it from raw PERT, not scale it.
        text = (self.plan_dir / "plan.yaml").read_text(encoding="utf-8")
        text = text.replace(
            "      pessimistic: 1\n",
            "      pessimistic: 1\n      calibrated: 99\n",
            1,
        )
        (self.plan_dir / "plan.yaml").write_text(text, encoding="utf-8")
        code, out = _run(
            ["--root", str(self.root), "apply-calibration", str(self.plan_dir), "--at", "2026-10-08T12:00:00-05:00"]
        )
        self.assertEqual(code, 0, out)
        plan = load_plan(self.plan_dir / "plan.yaml")
        estimate = plan.tasks[0].estimate_hours
        self.assertEqual(estimate.optimistic, Decimal(1))
        self.assertEqual(estimate.likely, Decimal(1))
        self.assertEqual(estimate.pessimistic, Decimal(1))
        self.assertEqual(estimate.calibrated, Decimal("1.25"))
        self.assertNotEqual(estimate.calibrated, Decimal("123.75"))
        self.assertEqual(plan.version, 2)
        code, out = _run(
            ["--root", str(self.root), "apply-calibration", str(self.plan_dir), "--at", "2026-10-08T13:00:00-05:00"]
        )
        self.assertEqual(code, 0, out)
        self.assertIn("unchanged", out)
        self.assertEqual(load_plan(self.plan_dir / "plan.yaml").version, 2)
        code, out = _run(["--root", str(self.root), "calibrate", "--apply"])
        self.assertEqual(code, 0, out)
        again = _run(["--root", str(self.root), "calibrate", "--apply"])
        self.assertEqual(again[0], 0, again[1])
        written = (self.root / "calibration.yaml").read_text(encoding="utf-8")
        code, out = _run(["--root", str(self.root), "calibrate", "--apply"])
        self.assertEqual(code, 0, out)
        self.assertEqual((self.root / "calibration.yaml").read_text(encoding="utf-8"), written)

    def test_cli_apply_bytes_are_stable(self):
        self._close_t1()
        code, out = _run(["--root", str(self.root), "calibrate", "--apply"])
        self.assertEqual(code, 0, out)
        first = (self.root / "calibration.yaml").read_text(encoding="utf-8")
        code, out = _run(["--root", str(self.root), "calibrate", "--apply"])
        self.assertEqual(code, 0, out)
        second = (self.root / "calibration.yaml").read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertIn("factor: 1.25", first)

    def test_schedule_uses_calibrated_hours(self):
        graph = [task("A", 2)]
        graph[0].estimate_hours.calibrated = Decimal(5)
        result = schedule_tasks(graph, {"worker": 1}, 1)
        self.assertEqual(result.by_id["A"].finish, Decimal(5))
        self.assertEqual(result.by_id["A"].expected, Decimal(5))

    def _close_t1(self):
        note = self.plan_dir / "artifacts" / "T1" / "note.md"
        note.parent.mkdir(parents=True)
        note.write_text("hello\n", encoding="utf-8")
        code, out = _run(
            [
                "--root",
                str(self.root),
                "record-artifact",
                str(self.plan_dir),
                "T1",
                "--file",
                str(note),
            ]
        )
        self.assertEqual(code, 0, out)
        code, out = _run(
            [
                "--root",
                str(self.root),
                "attest-done",
                str(self.plan_dir),
                "T1",
                "--verdict",
                "pass",
                "--at",
                "2026-10-08T12:00:00-05:00",
            ]
        )
        self.assertEqual(code, 0, out)
        code, out = _run(
            [
                "--root",
                str(self.root),
                "complete",
                str(self.plan_dir),
                "T1",
                "--hours",
                "2",
                "--attempts",
                "1",
                "--at",
                "2026-10-08T12:00:00-05:00",
            ]
        )
        self.assertEqual(code, 0, out)


class DoneLockTests(unittest.TestCase):
    def test_done_without_attestation_fails(self):
        roster = load_roster(ROSTER)
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(directory, ignore_errors=True))
        text = MINIMAL.replace("status: planned", "status: done", 1)
        (directory / "plan.yaml").write_text(text, encoding="utf-8")
        findings = validate_plan(load_plan(directory / "plan.yaml"), roster)
        self.assertTrue(any("attestation" in error for error in findings.errors))

    def test_set_status_refuses_done(self):
        code, out = _run(["set-status", "unused", "T1", "done"])
        self.assertEqual(code, 1)
        self.assertIn("refusing to set done", out)
        self.assertIn("attest-done", out)

    def test_pass_attestation_allows_done_and_a_bad_hash_does_not(self):
        roster = load_roster(ROSTER)
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        shutil.copy(ROSTER, root / "roster.yaml")
        plan_dir = root / "plans" / "sample"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.yaml").write_text(MINIMAL, encoding="utf-8")
        (plan_dir / "log.md").write_text("# Decision log\n", encoding="utf-8")
        note = plan_dir / "artifacts" / "T1" / "note.md"
        note.parent.mkdir(parents=True)
        note.write_text("hello\n", encoding="utf-8")
        self.assertEqual(
            _run(["--root", str(root), "record-artifact", str(plan_dir), "T1", "--file", str(note)])[0],
            0,
        )
        self.assertEqual(
            _run(
                [
                    "--root",
                    str(root),
                    "attest-done",
                    str(plan_dir),
                    "T1",
                    "--verdict",
                    "pass",
                    "--at",
                    "2026-10-08T12:00:00-05:00",
                ]
            )[0],
            0,
        )
        code, out = _run(
            [
                "--root",
                str(root),
                "complete",
                str(plan_dir),
                "T1",
                "--hours",
                "1",
                "--attempts",
                "1",
                "--at",
                "2026-10-08T12:00:00-05:00",
            ]
        )
        self.assertEqual(code, 0, out)
        findings = validate_plan(load_plan(plan_dir / "plan.yaml"), roster)
        self.assertEqual(findings.errors, [])
        manifest = plan_dir / "artifacts" / "T1" / "manifest.yaml"
        manifest.write_text(_tamper(manifest), encoding="utf-8")
        findings = validate_plan(load_plan(plan_dir / "plan.yaml"), roster)
        self.assertTrue(any("sha256" in error for error in findings.errors))


def _tamper(path: Path) -> str:
    import re

    text = path.read_text(encoding="utf-8")
    match = re.search(r"[0-9a-f]{64}", text)
    if match is None:
        raise AssertionError("manifest has no sha256")
    digest = match.group(0)
    flipped = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    return text.replace(digest, flipped, 1)


class BriefTests(unittest.TestCase):
    def test_example_brief_includes_acceptance_criteria(self):
        plan = load_plan(EXAMPLE / "plan.yaml")
        task = next(item for item in plan.tasks if item.id == "T1")
        text = render_brief(plan, task)
        self.assertIn("## Acceptance criteria", text)
        self.assertIn("Every factual claim names a source.", text)
        self.assertIn("Do not expand scope", text)
        self.assertIn("launch-announce-2026-10", text)
        self.assertIn("research", text)
        self.assertIn("do not set status to done".lower(), text.lower())
        brief_path = EXAMPLE / "briefs" / "T1.md"
        self.assertEqual(brief_path.read_text(encoding="utf-8"), text)


class ReplanTests(unittest.TestCase):
    def setUp(self):
        self.roster_path = ROSTER
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        shutil.copy(self.roster_path, self.root / "roster.yaml")
        self.plan_dir = self.root / "plans" / "replan-sample"
        self.plan_dir.mkdir(parents=True)
        (self.plan_dir / "plan.yaml").write_text(REPLAN, encoding="utf-8")
        (self.plan_dir / "log.md").write_text(
            "# Decision log\n\nAppend-only. Add new entries at the bottom. Do not edit earlier entries.\n",
            encoding="utf-8",
        )

    def _replan(self, at: str, **flags) -> tuple[int, str]:
        argv = [
            "--root",
            str(self.root),
            "replan",
            str(self.plan_dir),
            "--now",
            "--reason",
            "T1 is still running",
            "--at",
            at,
        ]
        if flags.get("force"):
            argv.append("--force")
        if flags.get("human"):
            argv.append("--human")
        return _run(argv)

    def test_replan_freezes_in_progress_and_rate_limits(self):
        code, out = self._replan("2026-10-08T11:00:00-05:00")
        self.assertEqual(code, 0, out)
        self.assertIn("Frozen: T1", out)
        plan = load_plan(self.plan_dir / "plan.yaml")
        self.assertEqual(plan.version, 2)
        self.assertEqual(plan.tasks[0].status, "in_progress")
        self.assertEqual(plan.tasks[0].assignee, "research")
        self.assertEqual(plan.tasks[0].estimate_hours.optimistic, Decimal(4))
        schedule = load_yaml(self.plan_dir / "schedule.yaml")
        by_id = {item["id"]: item for item in schedule["tasks"]}
        self.assertEqual(Decimal(str(by_id["T1"]["start"])), Decimal(0))
        self.assertEqual(Decimal(str(by_id["T1"]["finish"])), Decimal(5))
        self.assertEqual(Decimal(str(by_id["T2"]["start"])), Decimal(5))
        log = (self.plan_dir / "log.md").read_text(encoding="utf-8")
        self.assertIn("- Kind: replan", log)
        self.assertIn("- Frozen: T1", log)
        self.assertIn("- At: 2026-10-08T11:00:00-05:00", log)

        # --human bypasses the rate limit and still honors hysteresis.
        code, out = self._replan("2026-10-08T11:00:00-05:00", human=True)
        self.assertEqual(code, 0, out)
        self.assertIn("suppressed by hysteresis", out)
        self.assertEqual(load_plan(self.plan_dir / "plan.yaml").version, 2)

        code, out = self._replan("2026-10-08T11:45:00-05:00")
        self.assertEqual(code, 1, out)
        self.assertIn("refused", out)
        self.assertIn("60 minutes", out)
        self.assertEqual(load_plan(self.plan_dir / "plan.yaml").version, 2)

        code, out = self._replan("2026-10-08T11:45:00-05:00", human=True)
        self.assertEqual(code, 0, out)
        self.assertIn("version 3", out)
        plan = load_plan(self.plan_dir / "plan.yaml")
        self.assertEqual(plan.tasks[0].status, "in_progress")
        self.assertEqual(plan.tasks[0].assignee, "research")
        schedule = load_yaml(self.plan_dir / "schedule.yaml")
        by_id = {item["id"]: item for item in schedule["tasks"]}
        self.assertEqual(Decimal(str(by_id["T1"]["start"])), Decimal(0))


class PortfolioTests(unittest.TestCase):
    def test_portfolio_includes_the_example_plan(self):
        roster = load_roster(ROSTER)
        entries = []
        for directory in plan_dirs(ROOT, include_templates=False):
            plan = load_plan(directory / "plan.yaml")
            result = schedule_plan(plan, roster)
            entries.append(plan_entry(plan, result, roster))
        markdown = render_portfolio_md(entries)
        document = render_portfolio_yaml(entries)
        self.assertIn("launch-announce-2026-10", markdown)
        self.assertIn("0/7 done (0.0%)", markdown)
        self.assertIn("Blocked: 0", markdown)
        self.assertIn("Awaiting human: (none)", markdown)
        self.assertIn("Critical path residual: T1 -> T2 -> T4 -> T5 -> M1 -> T7", markdown)
        loaded = load_yaml(ROOT / "portfolio.yaml")
        ids = [item["plan_id"] for item in loaded["plans"]]
        self.assertIn("launch-announce-2026-10", ids)
        self.assertEqual((ROOT / "portfolio.md").read_text(encoding="utf-8"), markdown)
        self.assertEqual((ROOT / "portfolio.yaml").read_text(encoding="utf-8"), document)

    def test_dispatch_dry_run_lists_the_ready_task(self):
        roster = load_roster(ROSTER)
        plan = load_plan(EXAMPLE / "plan.yaml")
        text = render_dispatch(plan, schedule_plan(plan, roster))
        self.assertIn("T1 | research | briefs/T1.md", text)
        self.assertIn("halt_new_dispatch: no", text)
        self.assertIn("kill_switch:\n  (none)", text)
        plan.tasks[0].actuals.hours = plan.tasks[0].budget.hours
        stopped = render_dispatch(plan, schedule_plan(plan, roster))
        self.assertIn("kill_switch:", stopped)
        self.assertIn("T1 | budget", stopped)


class ScenarioTests(unittest.TestCase):
    def test_announcement_loop_attests_overruns_and_rejects_a_forged_done(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        shutil.copy(ROSTER, root / "roster.yaml")
        dest = root / "plans" / EXAMPLE.name
        shutil.copytree(EXAMPLE, dest)
        note = dest / "artifacts" / "T1" / "research_brief.md"
        note.parent.mkdir(parents=True)
        note.write_text("Facts from brand_notes.md.\n", encoding="utf-8")
        code, out = _run(
            ["--root", str(root), "record-artifact", str(dest), "T1", "--file", str(note)]
        )
        self.assertEqual(code, 0, out)
        code, out = _run(
            [
                "--root",
                str(root),
                "attest-done",
                str(dest),
                "T1",
                "--verdict",
                "pass",
                "--at",
                "2026-10-08T11:00:00-05:00",
            ]
        )
        self.assertEqual(code, 0, out)
        code, out = _run(
            [
                "--root",
                str(root),
                "complete",
                str(dest),
                "T1",
                "--hours",
                "2",
                "--attempts",
                "1",
                "--start",
                "2026-10-08T09:00:00-05:00",
                "--end",
                "2026-10-08T11:00:00-05:00",
                "--at",
                "2026-10-08T11:00:00-05:00",
            ]
        )
        self.assertEqual(code, 0, out)
        code, out = _run(
            [
                "--root",
                str(root),
                "set-status",
                str(dest),
                "T3",
                "in_progress",
                "--hours",
                "0.5",
                "--attempts",
                "1",
                "--start",
                "2026-10-08T11:00:00-05:00",
                "--at",
                "2026-10-08T17:00:00-05:00",
            ]
        )
        self.assertEqual(code, 0, out)
        code, out = _run(
            [
                "--root",
                str(root),
                "replan",
                str(dest),
                "--now",
                "--reason",
                "T3 ran long",
                "--at",
                "2026-10-08T17:00:00-05:00",
            ]
        )
        self.assertEqual(code, 0, out)
        self.assertIn("Frozen: T3", out)
        log = (dest / "log.md").read_text(encoding="utf-8")
        self.assertEqual(log.count("- Kind: replan"), 1)
        schedule = load_yaml(dest / "schedule.yaml")
        by_id = {item["id"]: item for item in schedule["tasks"]}
        self.assertEqual(Decimal(str(by_id["T3"]["start"])), Decimal(2))
        plan = load_plan(dest / "plan.yaml")
        t3 = next(item for item in plan.tasks if item.id == "T3")
        self.assertEqual(t3.status, "in_progress")
        self.assertEqual(t3.assignee, "creative-director")
        code, out = _run(
            [
                "--root",
                str(root),
                "replan",
                str(dest),
                "--now",
                "--reason",
                "again",
                "--at",
                "2026-10-08T17:20:00-05:00",
            ]
        )
        self.assertEqual(code, 1, out)
        self.assertIn("refused", out)
        self.assertEqual((dest / "log.md").read_text(encoding="utf-8").count("- Kind: replan"), 1)
        forged = set_status((dest / "plan.yaml").read_text(encoding="utf-8"), "T2", "done")
        (dest / "plan.yaml").write_text(forged, encoding="utf-8")
        findings = validate_plan(load_plan(dest / "plan.yaml"), load_roster(ROSTER))
        self.assertTrue(any("attestation" in error for error in findings.errors))


class CommittedCalibrationTests(unittest.TestCase):
    def test_committed_calibration_matches_a_fresh_apply(self):
        plans = [
            load_plan(directory / "plan.yaml")
            for directory in plan_dirs(ROOT, include_templates=False)
        ]
        text = render_calibration_yaml(build_calibration(plans))
        self.assertEqual((ROOT / "calibration.yaml").read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    unittest.main()
