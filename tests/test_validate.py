# SPDX-License-Identifier: Apache-2.0
"""Plan and roster checks, including the committed template and example."""

import json
import tempfile
import unittest
from pathlib import Path

from planner.model import LoadError, load_plan, load_roster
from planner.util import plan_dirs, repo_root
from planner.validate import validate_plan

ROOT = repo_root()

MINIMAL = """
schema_version: 1
plan_id: sample
version: 1
goal: Write a note.
owner: orchestrator
definition_of_done: note.md exists.
budget:
  tokens: 10
  hours: 4
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
      hours: 2
      usd: 1
    max_retries: 1
    status: planned
    actuals:
      start: null
      end: null
      hours: null
      attempts: 0
"""


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.roster = load_roster(ROOT / "roster.yaml")

    def _plan(self, text: str):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "plan.yaml"
        path.write_text(text, encoding="utf-8")
        return load_plan(path)

    def test_template_and_example_are_clean(self):
        directories = plan_dirs(ROOT, include_templates=True)
        self.assertGreaterEqual(len(directories), 2)
        for directory in directories:
            plan = load_plan(directory / "plan.yaml")
            findings = validate_plan(plan, self.roster)
            self.assertEqual(findings.errors, [], directory.name)
            self.assertEqual(findings.warnings, [], directory.name)

    def test_minimal_plan_is_valid(self):
        findings = validate_plan(self._plan(MINIMAL), self.roster)
        self.assertEqual(findings.errors, [])

    def test_unknown_field(self):
        text = MINIMAL.replace("plan_id: sample", "plan_id: sample\nnickname: nope")
        with self.assertRaises(LoadError) as caught:
            self._plan(text)
        self.assertIn("unknown field 'nickname'", str(caught.exception))

    def test_unknown_assignee(self):
        plan = self._plan(MINIMAL.replace("assignee: research", "assignee: nobody"))
        findings = validate_plan(plan, self.roster)
        self.assertTrue(any("not in the roster" in error for error in findings.errors))

    def test_capability_mismatch(self):
        plan = self._plan(MINIMAL.replace("- research", "- social_copy"))
        findings = validate_plan(plan, self.roster)
        self.assertTrue(any("lacks social_copy" in error for error in findings.errors))

    def test_missing_dependency(self):
        plan = self._plan(MINIMAL.replace("depends_on: []", "depends_on:\n      - T9"))
        findings = validate_plan(plan, self.roster)
        self.assertTrue(any("unknown task 'T9'" in error for error in findings.errors))

    def test_cycle(self):
        text = """
schema_version: 1
plan_id: cycle
version: 1
goal: Loop.
owner: orchestrator
definition_of_done: Done.
budget: {tokens: 1, hours: 2, usd: 0}
human_approval_policy: Hold.
constraints:
  max_parallel: 2
  schedule_origin: "2026-10-08T09:00:00-05:00"
deliverables: [a.md]
tasks:
  - id: T1
    title: A
    task_type: research
    objective: Make a.
    inputs: [{artifact: b.md, from_task: T2}]
    outputs: [{artifact: a.md, type: markdown}]
    depends_on: [T2]
    estimate_hours: {optimistic: 1, likely: 1, pessimistic: 1}
    required_capabilities: [research]
    assignee: research
    acceptance_criteria: [a exists]
    risk: low
    reversible: true
    needs_human: false
    budget: {tokens: 1, hours: 1, usd: 0}
    max_retries: 1
    status: planned
    actuals: {start: null, end: null, hours: null, attempts: 0}
  - id: T2
    title: B
    task_type: research
    objective: Make b.
    inputs: [{artifact: a.md, from_task: T1}]
    outputs: [{artifact: b.md, type: markdown}]
    depends_on: [T1]
    estimate_hours: {optimistic: 1, likely: 1, pessimistic: 1}
    required_capabilities: [research]
    assignee: research
    acceptance_criteria: [b exists]
    risk: low
    reversible: true
    needs_human: false
    budget: {tokens: 1, hours: 1, usd: 0}
    max_retries: 1
    status: planned
    actuals: {start: null, end: null, hours: null, attempts: 0}
"""
        findings = validate_plan(self._plan(text), self.roster)
        self.assertTrue(any(error.startswith("cycle:") for error in findings.errors))

    def test_irreversible_requires_a_human(self):
        plan = self._plan(MINIMAL.replace("reversible: true", "reversible: false"))
        findings = validate_plan(plan, self.roster)
        self.assertTrue(any("needs_human must be true" in error for error in findings.errors))

    def test_estimate_order(self):
        text = MINIMAL.replace("pessimistic: 1", "pessimistic: 0.5")
        with self.assertRaises(LoadError) as caught:
            self._plan(text)
        self.assertIn("optimistic <= likely <= pessimistic", str(caught.exception))

    def test_dependency_shorthand(self):
        text = MINIMAL + """
  - id: T2
    title: Check the note
    task_type: accuracy_review
    objective: Check note.md.
    inputs: [{artifact: note.md, from_task: T1}]
    outputs: [{artifact: review.md, type: markdown}]
    depends_on: [T1]
    estimate_hours: {optimistic: 1, likely: 1, pessimistic: 1}
    required_capabilities: [verification]
    assignee: accuracy
    acceptance_criteria: [review exists]
    risk: low
    reversible: true
    needs_human: false
    budget: {tokens: 1, hours: 1, usd: 0}
    max_retries: 1
    status: planned
    actuals: {start: null, end: null, hours: null, attempts: 0}
"""
        text = text.replace("deliverables:\n  - note.md", "deliverables:\n  - review.md")
        plan = self._plan(text)
        dep = plan.tasks[1].depends_on[0]
        self.assertEqual(dep.task, "T1")
        self.assertEqual(dep.type, "FS")
        self.assertEqual(dep.lag_hours, 0)
        self.assertEqual(validate_plan(plan, self.roster).errors, [])

    def test_schema_status_enum(self):
        schema = json.loads((ROOT / "schemas" / "plan.schema.json").read_text(encoding="utf-8"))
        status = schema["$defs"]["task"]["properties"]["status"]["enum"]
        self.assertEqual(
            status,
            [
                "planned",
                "ready",
                "in_progress",
                "in_review",
                "done",
                "blocked",
                "failed",
                "cancelled",
            ],
        )
        dep_type = schema["$defs"]["dependency"]["oneOf"][1]["properties"]["type"]["enum"]
        self.assertEqual(dep_type, ["FS", "SS", "FF", "SF"])


if __name__ == "__main__":
    unittest.main()
