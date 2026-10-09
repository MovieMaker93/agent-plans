# SPDX-License-Identifier: Apache-2.0
"""The history guard accepts a clean temporary repo and rejects a bad one."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from scripts.check_commit_hygiene import ALLOWED_EMAIL, DEFAULT_NAME, main, problems

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_commit_hygiene.py"
FOREIGN = "someone@example.org"


@unittest.skipUnless(shutil.which("git"), "git not installed")
class CommitHygieneTests(unittest.TestCase):
    def test_good_history_passes_and_bad_commits_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
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
            repo = self._repo(Path(tmp))
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
            repo = self._repo(Path(tmp))
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

            self._commit(
                repo,
                "child stays checked",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
                allow_empty=True,
            )
            still = problems(repo)
            self.assertTrue(any("author email" in item or "committer email" in item for item in still), still)

    def test_spaced_coauthor_trailer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            self._commit(
                repo,
                "space before the colon\n\nCo-authored-by : x",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
            )
            found = problems(repo)
            self.assertTrue(any("forbidden trailer" in item for item in found), found)
            self.assertEqual(self._main(repo), 1)

    def test_foreign_address_in_the_body_or_another_trailer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            self._commit(
                repo,
                f"the note mentions {FOREIGN} in prose",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
            )
            body = problems(repo)
            self.assertTrue(any(FOREIGN in item for item in body), body)

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            self._commit(
                repo,
                f"reviewed\n\nReviewed-by: Pat <{FOREIGN}>",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
            )
            trailer = problems(repo)
            self.assertTrue(any(FOREIGN in item for item in trailer), trailer)

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            self._commit(
                repo,
                f"the maintainer address {ALLOWED_EMAIL} is allowed in prose",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
            )
            self.assertEqual(problems(repo), [])

    def test_address_followed_by_dot_or_hyphen_is_rejected(self) -> None:
        cases = (
            (f"Thanks to {FOREIGN}.", FOREIGN),
            (f"{FOREIGN}-based", FOREIGN),
            ("see noreply@example.com.evil.io", "noreply@example.com.evil.io"),
        )
        for message, needle in cases:
            with tempfile.TemporaryDirectory() as tmp:
                repo = self._repo(Path(tmp))
                self._commit(repo, message, author=ALLOWED_EMAIL, committer=ALLOWED_EMAIL)
                found = problems(repo)
                self.assertTrue(any(needle in item for item in found), (message, found))

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            self._commit(
                repo,
                f"Thanks to {ALLOWED_EMAIL}.",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
            )
            self.assertEqual(problems(repo), [])

    def test_wrong_name_on_main_fails_and_the_flag_overrides_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            self._commit(
                repo,
                "other name",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
                author_name="Pat",
                committer_name="Pat",
            )
            found = problems(repo)
            self.assertTrue(any("author name 'Pat'" in item for item in found), found)
            self.assertTrue(any("committer name 'Pat'" in item for item in found), found)
            self.assertEqual(problems(repo, required_name="Pat"), [])
            self.assertEqual(self._main_args(["--name", "Pat", str(repo)]), 0)
            self.assertEqual(DEFAULT_NAME, "mr-r0b0t")

    def test_shallow_clone_is_rejected_even_when_the_tip_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin = self._repo(base / "origin")
            self._commit(origin, "hidden bad root", author=FOREIGN, committer=ALLOWED_EMAIL)
            self._commit(
                origin,
                "clean tip",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
                allow_empty=True,
            )
            clone = base / "clone"
            self._git(
                base,
                "clone",
                "--no-local",
                "--depth",
                "1",
                str(origin),
                str(clone),
            )
            count = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=clone,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(count.stdout.strip(), "1", count.stderr)
            found = problems(clone)
            self.assertTrue(any("shallow" in item for item in found), found)
            self.assertFalse(any(FOREIGN in item for item in found), found)

    def test_unreachable_branch_is_checked_only_with_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            self._commit(repo, "main stays clean", author=ALLOWED_EMAIL, committer=ALLOWED_EMAIL)
            self._git(repo, "checkout", "-b", "side")
            self._commit(
                repo,
                "only on the side branch",
                author=FOREIGN,
                committer=ALLOWED_EMAIL,
                allow_empty=True,
            )
            self._git(repo, "checkout", "main")
            self.assertEqual(problems(repo), [])
            found = problems(repo, all_refs=True)
            self.assertTrue(any(FOREIGN in item for item in found), found)
            self.assertEqual(self._main_args(["--all", str(repo)]), 1)

            self._git(repo, "checkout", "-b", "nicknamed")
            self._commit(
                repo,
                "a side branch may use another name",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
                author_name="Pat",
                committer_name="Pat",
                allow_empty=True,
            )
            self._git(repo, "checkout", "main")
            # The foreign commit is still on ``side``. Drop that ref and keep the
            # nickname branch, which is not on main.
            self._git(repo, "branch", "-D", "side")
            self.assertEqual(problems(repo, all_refs=True), [])

    def test_nested_directory_does_not_scan_the_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = self._repo(Path(tmp) / "parent")
            self._commit(parent, "parent is clean", author=ALLOWED_EMAIL, committer=ALLOWED_EMAIL)
            export = parent / "export"
            export.mkdir()
            (export / "README.md").write_text("not a repository\n", encoding="utf-8")
            clean = problems(export)
            self.assertTrue(any("toplevel" in item for item in clean), clean)
            self.assertNotEqual(self._main(export), 0)

        with tempfile.TemporaryDirectory() as tmp:
            parent = self._repo(Path(tmp) / "parent")
            self._commit(parent, "parent is tainted", author=FOREIGN, committer=ALLOWED_EMAIL)
            export = parent / "export"
            export.mkdir()
            (export / "README.md").write_text("not a repository\n", encoding="utf-8")
            tainted = problems(export)
            self.assertTrue(any("toplevel" in item for item in tainted), tainted)
            self.assertFalse(any(FOREIGN in item for item in tainted), tainted)

    def test_name_rule_uses_origin_main_then_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            self._commit(
                repo,
                "detached name",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
                author_name="Pat",
                committer_name="Pat",
            )
            self._git(repo, "checkout", "--detach")
            self._git(repo, "branch", "-D", "main")
            found = problems(repo)
            self.assertTrue(any("author name 'Pat'" in item for item in found), found)

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            self._commit(
                repo,
                "remote main name",
                author=ALLOWED_EMAIL,
                committer=ALLOWED_EMAIL,
                author_name="Pat",
                committer_name="Pat",
            )
            self._git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            self._git(repo, "checkout", "--detach")
            self._git(repo, "branch", "-D", "main")
            found = problems(repo, all_refs=True)
            self.assertTrue(any("author name 'Pat'" in item for item in found), found)

    def test_missing_main_ref_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._git(repo, "init", "-b", "main")
            found = problems(repo)
            self.assertTrue(
                any("refs/heads/main, origin/main, and HEAD did not resolve" in item for item in found),
                found,
            )

    def test_stash_is_excluded_from_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            self._commit(repo, "clean", author=ALLOWED_EMAIL, committer=ALLOWED_EMAIL)
            (repo / "README.md").write_text("dirty\n", encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "GIT_AUTHOR_NAME": "Pat",
                    "GIT_AUTHOR_EMAIL": FOREIGN,
                    "GIT_COMMITTER_NAME": "Pat",
                    "GIT_COMMITTER_EMAIL": FOREIGN,
                }
            )
            stashed = subprocess.run(
                [
                    "git",
                    "-c",
                    "commit.gpgsign=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "stash",
                    "push",
                    "-m",
                    "wip",
                ],
                cwd=repo,
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(stashed.returncode, 0, stashed.stderr)
            stash = subprocess.run(
                ["git", "log", "-1", "--format=%ae", "refs/stash"],
                cwd=repo,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(stash.stdout.strip(), FOREIGN, stash.stderr)
            self.assertEqual(problems(repo, all_refs=True), [])

    def test_script_entrypoint_rejects_extra_args(self) -> None:
        self.assertEqual(self._main_args(["one", "two"]), 2)
        self.assertTrue(SCRIPT.is_file())

    def test_class_skips_when_git_is_missing(self) -> None:
        env = os.environ.copy()
        env["PATH"] = "/var/empty"
        env["PYTHONPATH"] = str(ROOT)
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import shutil, unittest\n"
                "from tests.test_commit_hygiene import CommitHygieneTests\n"
                "suite = unittest.defaultTestLoader.loadTestsFromTestCase(CommitHygieneTests)\n"
                "result = unittest.TextTestRunner(verbosity=0).run(suite)\n"
                "raise SystemExit(0 if result.wasSuccessful() and result.skipped else 1)\n",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)

    def _repo(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        self._git(path, "init", "-b", "main")
        return path

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
        author_name: str = DEFAULT_NAME,
        committer_name: str = DEFAULT_NAME,
        allow_empty: bool = False,
    ) -> None:
        marker = repo / "README.md"
        if not marker.exists():
            marker.write_text("clean\n", encoding="utf-8")
            self._git(repo, "add", "README.md")
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": author_name,
                "GIT_AUTHOR_EMAIL": author,
                "GIT_COMMITTER_NAME": committer_name,
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
        if allow_empty or not self._pending(repo):
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

    def _pending(self, repo: Path) -> bool:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
        return bool(result.stdout.strip())
