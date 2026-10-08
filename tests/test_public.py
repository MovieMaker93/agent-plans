# SPDX-License-Identifier: Apache-2.0
"""Public examples, schema coverage, CLI flags, and the plans-directory override."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from planner.cli import _build_parser
from planner.gantt import render_gantt, render_schedule_yaml
from planner.model import load_plan, load_roster
from planner.reports import schedule_plan
from planner.util import (
    plan_dirs,
    plans_dir,
    repo_root,
    resolve_plan_reference,
    template_library_dirs,
)
from planner.validate import validate_plan

ROOT = repo_root()
GUIDE = ROOT / "docs" / "user-guide.md"
SCENARIOS = ROOT / "docs" / "scenarios.md"
README = ROOT / "README.md"
DOCS = (README, GUIDE, SCENARIOS)
SKIP_COMMANDS = {"check", "serve"}
COPY_COMMANDS = {"replan", "apply-calibration"}


def _schema_properties(path: Path) -> set[str]:
    document = json.loads(path.read_text(encoding="utf-8"))
    found: set[str] = set()

    def walk(node) -> None:
        if not isinstance(node, dict):
            return
        props = node.get("properties")
        if isinstance(props, dict):
            found.update(props)
            for child in props.values():
                walk(child)
        for key in ("$defs", "items"):
            child = node.get(key)
            if isinstance(child, dict):
                if key == "$defs":
                    for item in child.values():
                        walk(item)
                else:
                    walk(child)
        for item in node.get("oneOf", []):
            walk(item)

    walk(document)
    return found


def _example_dirs() -> list[Path]:
    return sorted(path.parent for path in (ROOT / "examples").rglob("plan.yaml"))


def _fenced_commands() -> list[str]:
    commands = []
    for path in DOCS:
        text = path.read_text(encoding="utf-8")
        blocks = text.split("```")
        for index, block in enumerate(blocks):
            if index % 2 == 0:
                continue
            lines = block.splitlines()
            if not lines:
                continue
            if lines[0].strip() not in {"bash", "sh"}:
                continue
            for line in lines[1:]:
                stripped = line.strip()
                if stripped.startswith("python3 -m planner "):
                    commands.append(stripped)
    return commands


class ExamplePlanTests(unittest.TestCase):
    def test_each_example_validates_and_schedules(self):
        roster = load_roster(ROOT / "roster.yaml")
        directories = _example_dirs()
        self.assertGreaterEqual(len(directories), 6)
        for directory in directories:
            plan = load_plan(directory / "plan.yaml")
            findings = validate_plan(plan, roster)
            self.assertEqual(findings.errors, [], directory)
            result = schedule_plan(plan, roster)
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
            self.assertIn("```mermaid", (directory / "gantt.md").read_text(encoding="utf-8"))


class DocContractTests(unittest.TestCase):
    def test_user_guide_names_every_schema_property(self):
        guide = GUIDE.read_text(encoding="utf-8")
        for schema in (ROOT / "schemas" / "plan.schema.json", ROOT / "schemas" / "roster.schema.json"):
            for name in _schema_properties(schema):
                self.assertIn(f"`{name}`", guide, schema.name)

    def test_user_guide_names_every_flag(self):
        guide = GUIDE.read_text(encoding="utf-8")
        parser = _build_parser()
        subparsers = [
            action
            for action in parser._actions
            if getattr(action, "choices", None) and "validate" in getattr(action, "choices", {})
        ]
        self.assertEqual(len(subparsers), 1)
        choices = subparsers[0].choices
        for action in list(parser._actions) + [
            item for choice in choices.values() for item in choice._actions
        ]:
            for option in action.option_strings:
                if option in {"-h", "--help"}:
                    continue
                self.assertIn(option, guide, option)
        for name in choices:
            self.assertIn(name, guide)

    def test_documented_commands_run(self):
        commands = _fenced_commands()
        self.assertGreaterEqual(len(commands), 10)
        created = []
        try:
            for line in commands:
                if "<" in line or ">" in line:
                    continue
                parts = shlex.split(line)
                command = parts[3]
                if command in SKIP_COMMANDS:
                    continue
                if command in COPY_COMMANDS:
                    self._run_on_copy(parts)
                    continue
                before = self._extra_files()
                result = subprocess.run(
                    [sys.executable, *parts[1:]],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f"{line}\n{result.stdout}\n{result.stderr}",
                )
                created.extend(path for path in self._extra_files() if path not in before)
        finally:
            for path in created:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.is_file():
                    path.unlink()

    def _extra_files(self) -> set[Path]:
        found = set()
        for directory in _example_dirs():
            briefs = directory / "briefs"
            if briefs.exists():
                found.add(briefs)
        return found

    def _run_on_copy(self, parts: list[str]) -> None:
        source = ROOT / "examples" / "05-slipping-plan"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "05-slipping-plan"
            shutil.copytree(source, dest)
            rewritten = [
                str(dest) if part == "examples/05-slipping-plan" else part for part in parts
            ]
            result = subprocess.run(
                [sys.executable, *rewritten[1:]],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"{parts}\n{result.stdout}\n{result.stderr}",
            )
            scenarios = SCENARIOS.read_text(encoding="utf-8")
            first = result.stdout.strip().splitlines()[0]
            self.assertIn(first, scenarios)


class PlansDirTests(unittest.TestCase):
    def test_default_directory_is_plans(self):
        self.assertEqual(plans_dir(ROOT), ROOT / "plans")
        names = {path.name for path in plan_dirs(ROOT, include_templates=False)}
        self.assertIn("2026-10-08-product-announcement", names)
        self.assertNotIn("01-solo-feature", names)

    def test_env_overrides_the_live_scan_and_keeps_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "roster.yaml").write_text("schema_version: 1\n", encoding="utf-8")
            bundled = root / "plans"
            (bundled / "_template").mkdir(parents=True)
            (bundled / "_template" / "plan.yaml").write_text("plan_id: template\n", encoding="utf-8")
            (bundled / "in-repo").mkdir()
            (bundled / "in-repo" / "plan.yaml").write_text("plan_id: in-repo\n", encoding="utf-8")
            external = root / "external-plans"
            (external / "moved").mkdir(parents=True)
            (external / "moved" / "plan.yaml").write_text("plan_id: moved\n", encoding="utf-8")
            (external / "child").mkdir()
            (external / "child" / "plan.yaml").write_text("plan_id: child\n", encoding="utf-8")
            self.assertEqual(
                [path.name for path in plan_dirs(root, include_templates=False)],
                ["in-repo"],
            )
            with mock.patch.dict(os.environ, {"AGENT_PLANS_DIR": "external-plans"}):
                names = [path.name for path in plan_dirs(root, include_templates=True)]
                self.assertIn("moved", names)
                self.assertNotIn("in-repo", names)
                self.assertIn("_template", names)
                self.assertEqual(
                    resolve_plan_reference(root, "plans/child"),
                    (external / "child").resolve(),
                )
            self.assertEqual(
                resolve_plan_reference(root, "plans/child"),
                (bundled / "child").resolve(),
            )
        kinds = {path.name for path in template_library_dirs(ROOT)}
        self.assertEqual(kinds, {"announcement", "research-report"})
        with mock.patch.dict(os.environ, {"AGENT_PLANS_DIR": "/tmp/does-not-matter-plans"}):
            self.assertEqual(
                {path.name for path in template_library_dirs(ROOT)},
                {"announcement", "research-report"},
            )


if __name__ == "__main__":
    unittest.main()
