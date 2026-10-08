# SPDX-License-Identifier: Apache-2.0
"""The history guard accepts a clean temporary repo and rejects a bad one."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from scripts.check_commit_hygiene import ALLOWED_EMAIL, main, problems

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_commit_hygiene.py"


class CommitHygieneTests(unittest.TestCase):
    def test_good_history_passes_and_bad_commits_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._git(repo, "init", "-b", "main")
            readme = repo / "README.md"
            readme.write_text("clean\n", encoding="utf-8")
            self._git(repo, "add", "README.md")
            self._commit(repo, "good history", author=ALLOWED_EMAIL, committer=ALLOWED_EMAIL)
            self.assertEqual(problems(repo), [])
            self.assertEqual(self._main(repo), 0)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), str(repo)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("commit hygiene ok", completed.stdout)

            self._commit(
                repo,
                "hidden co-author\n\nCo-authored-by: Pat <pat@example.net>",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
                allow_empty=True,
            )
            coauthor = problems(repo)
            self.assertTrue(any("Co-authored-by:" in item for item in coauthor), coauthor)
            self.assertEqual(self._main(repo), 1)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._git(repo, "init", "-b", "main")
            (repo / "README.md").write_text("clean\n", encoding="utf-8")
            self._git(repo, "add", "README.md")
            self._commit(
                repo,
                "sign-off\n\nSigned-off-by: Pat <pat@example.net>",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
            )
            signed = problems(repo)
            self.assertTrue(any("Signed-off-by:" in item for item in signed), signed)

            self._commit(
                repo,
                "lower case trailer\n\nco-authored-by: Pat <pat@example.net>",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
                allow_empty=True,
            )
            lowered = problems(repo)
            self.assertTrue(any("co-authored-by:" in item for item in lowered), lowered)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._git(repo, "init", "-b", "main")
            (repo / "README.md").write_text("clean\n", encoding="utf-8")
            self._git(repo, "add", "README.md")
            self._commit(
                repo,
                "foreign author",
                author="other@example.net",
                committer=ALLOWED_EMAIL,
            )
            authored = problems(repo)
            self.assertTrue(any("author email" in item for item in authored), authored)

            self._commit(
                repo,
                "foreign committer",
                author=ALLOWED_EMAIL,
                committer="other@example.net",
                allow_empty=True,
            )
            committed = problems(repo)
            self.assertTrue(any("committer email" in item for item in committed), committed)

            self._commit(repo, "child stays checked", author=ALLOWED_EMAIL, committer=ALLOWED_EMAIL, allow_empty=True)
            still = problems(repo)
            self.assertTrue(any("author email" in item or "committer email" in item for item in still), still)

    def test_script_entrypoint_rejects_extra_args(self) -> None:
        self.assertEqual(self._main_args(["one", "two"]), 2)
        self.assertTrue(SCRIPT.is_file())

    def _main(self, repo: Path) -> int:
        return self._main_args([str(repo)])

    def _main_args(self, args: list[str]) -> int:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return main(args)

    def _git(self, repo: Path, *args: str) -> None:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def _commit(
        self,
        repo: Path,
        message: str,
        *,
        author: str,
        committer: str,
        allow_empty: bool = False,
    ) -> None:
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "mr-r0b0t",
                "GIT_AUTHOR_EMAIL": author,
                "GIT_COMMITTER_NAME": "mr-r0b0t",
                "GIT_COMMITTER_EMAIL": committer,
            }
        )
        command = [
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--no-verify",
            "-m",
            message,
        ]
        if allow_empty:
            command.append("--allow-empty")
        result = subprocess.run(
            command,
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
