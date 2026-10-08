# SPDX-License-Identifier: Apache-2.0
"""Ready queue, status, calibration, Gantt, and committed generated files."""

import unittest
from decimal import Decimal

from planner.gantt import render_gantt, render_schedule_yaml
from planner.model import load_plan, load_roster
from planner.reports import render_calibration, render_ready, render_status, schedule_plan
from planner.util import fmt_hours, plan_dirs, repo_root
from planner.yamlio import load_yaml

ROOT = repo_root()
EXAMPLE = ROOT / "plans" / "2026-10-08-product-announcement"
TEMPLATE = ROOT / "plans" / "_template"


class ExampleViewTests(unittest.TestCase):
    def setUp(self):
        self.roster = load_roster(ROOT / "roster.yaml")
        self.plan = load_plan(EXAMPLE / "plan.yaml")
        self.result = schedule_plan(self.plan, self.roster)

    def test_critical_path_matches_the_design_doc(self):
        self.assertEqual(self.result.critical_path, ["T1", "T2", "T4", "T5", "M1", "T7"])
        self.assertEqual(self.result.by_id["T3"].slack, Decimal("0.5"))
        self.assertEqual(fmt_hours(self.result.by_id["T1"].std_dev), "0.3333")
        self.assertEqual(fmt_hours(self.result.by_id["T2"].std_dev), "0.1667")

    def test_ready_queue_is_only_research(self):
        text = render_ready(self.plan, self.result)
        self.assertEqual(
            text,
            "\n".join(
                [
                    "plan: launch-announce-2026-10",
                    "dispatch:",
                    "  T1 | research | 2h | Research brief",
                    "awaiting_human:",
                    "  (none)",
                ]
            ),
        )

    def test_status_summary(self):
        text = render_status(self.plan, self.result)
        self.assertIn("progress: 0/7 done (0.0%)", text)
        self.assertIn("critical_path: T1 -> T2 -> T4 -> T5 -> M1 -> T7", text)
        self.assertIn("cpm_duration_hours: 5.5", text)
        self.assertIn("makespan_hours: 5.5", text)
        self.assertIn(
            f"cpm_std_dev_hours: {fmt_hours(self.result.cpm_std_dev)}",
            text,
        )
        self.assertIn("schedule_variance: n/a", text)
        self.assertIn("directory: 2026-10-08-product-announcement", text)
        self.assertIn("awaiting_human:\n  (none)", text)

    def test_gantt_marks_the_critical_path(self):
        chart = render_gantt(self.plan, self.result)
        t1 = _mermaid_line(chart, "T1 ")
        t3 = _mermaid_line(chart, "T3 ")
        m1 = _mermaid_line(chart, "M1 ")
        self.assertIn(":crit, t1, 09:00, 2h", t1)
        self.assertNotIn(":crit", t3)
        self.assertIn("11:00, 2h", t3)
        self.assertIn(":milestone, crit, m1, 14:00, 0m", m1)
        self.assertIn("T2 Draft blog and social copy - social :crit, t2, 11:00, 90m", chart)
        self.assertIn("T5 Assemble launch package - chief-of-staff :crit, t5, 13:30, 30m", chart)
        self.assertIn("T7 Publish - social :crit, t7, 14:00, 30m", chart)

    def test_generated_files_match_a_fresh_run(self):
        for directory in plan_dirs(ROOT, include_templates=True):
            plan = load_plan(directory / "plan.yaml")
            result = schedule_plan(plan, self.roster)
            self.assertEqual(
                (directory / "schedule.yaml").read_text(encoding="utf-8"),
                render_schedule_yaml(plan, result),
                directory.name,
            )
            self.assertEqual(
                (directory / "gantt.md").read_text(encoding="utf-8"),
                render_gantt(plan, result),
                directory.name,
            )

    def test_schedule_yaml_round_trip(self):
        rendered = render_schedule_yaml(self.plan, self.result)
        again = render_schedule_yaml(self.plan, self.result)
        self.assertEqual(rendered, again)
        loaded = load_yaml(EXAMPLE / "schedule.yaml")
        self.assertEqual(loaded["project"]["critical_path"], self.result.critical_path)
        self.assertEqual(loaded["tasks"][0]["id"], "T1")
        self.assertTrue(loaded["tasks"][0]["critical"])


class ReportRuleTests(unittest.TestCase):
    def test_human_gate_is_not_in_the_dispatch_list(self):
        roster = load_roster(ROOT / "roster.yaml")
        plan = load_plan(TEMPLATE / "plan.yaml")
        for item in plan.tasks:
            if item.id in {"T1", "T2"}:
                item.status = "done"
        result = schedule_plan(plan, roster)
        text = render_ready(plan, result)
        dispatch, waiting = text.split("awaiting_human:")
        self.assertIn("(none)", dispatch)
        self.assertIn("M1 | human | 0h | Approve the brief", waiting)

    def test_calibration_ratios(self):
        roster = load_roster(ROOT / "roster.yaml")
        plan = load_plan(TEMPLATE / "plan.yaml")
        # T1 expected is 1h. Actual 2h is outside 1..1. T2 expected is 0.5h.
        plan.tasks[0].actuals.hours = Decimal(2)
        plan.tasks[1].actuals.hours = Decimal("0.5")
        text = render_calibration([plan])
        self.assertIn("samples: 2", text)
        self.assertIn("skipped_zero_estimate: 0", text)
        self.assertIn("pooled_ratio=1.6667", text)
        self.assertIn("inside_range=1/2", text)
        self.assertIn("research", text)
        self.assertIn("accuracy", text)

    def test_live_plans_have_no_actuals_yet(self):
        # The public tree keeps the synthetic announcement only. It has no
        # recorded actual hours, so calibrate has nothing to sample.
        plans = [
            load_plan(directory / "plan.yaml")
            for directory in plan_dirs(ROOT, include_templates=False)
        ]
        text = render_calibration(plans)
        self.assertIn("samples: 0", text)
        self.assertIn("(none)", text)


def _mermaid_line(chart: str, prefix: str) -> str:
    for line in chart.splitlines():
        if line.strip().startswith(prefix):
            return line.strip()
    raise AssertionError(f"no mermaid line starting with {prefix!r}")


if __name__ == "__main__":
    unittest.main()
