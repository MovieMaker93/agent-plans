# SPDX-License-Identifier: Apache-2.0
"""Fail if selected history breaks the maintainer identity rules.

Commits on ``refs/heads/main`` must use the author and committer name
``mr-r0b0t`` (change it with ``--name``) and the address
``noreply@example.com``. Every selected commit is rejected when its author
or committer address differs, when a ``Co-authored-by`` or ``Signed-off-by``
trailer is present, or when any other email appears in the message.
``--all`` selects every ref instead of ``HEAD``.

The git toplevel must be the directory you named. A shallow repository is
rejected because older commits would be invisible. Requires the ``git``
executable. The script itself uses only the standard library.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ALLOWED_EMAIL = "noreply@example.com"
DEFAULT_NAME = "mr-r0b0t"
_FORBIDDEN_TRAILER_RE = re.compile(
    r"^\s*(co-authored-by|signed-off-by)\s*:",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
)


def _git(
    repo: Path,
    *args: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
        errors="replace",
    )


def _foreign_emails(text: str) -> list[str]:
    found: list[str] = []
    for match in _EMAIL_RE.finditer(text):
        email = match.group(0)
        if email != ALLOWED_EMAIL and email not in found:
            found.append(email)
    return found


def _unique(items: list[str]) -> list[str]:
    found: list[str] = []
    for item in items:
        if item not in found:
            found.append(item)
    return found


def _prepare(repo: Path) -> tuple[Path | None, list[str]]:
    """Return the toplevel, or problems if this directory is not a full repo."""
    if shutil.which("git") is None:
        return None, ["git is not installed"]
    if not repo.is_dir():
        return None, [f"{repo}: not a directory"]
    top = _git(repo, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        detail = (top.stderr or top.stdout).strip() or "git rev-parse --show-toplevel failed"
        return None, [detail]
    toplevel = Path(top.stdout.strip()).resolve()
    target = repo.resolve()
    if toplevel != target:
        return None, [f"{target}: git toplevel is {toplevel}, not the target repository"]
    shallow = _git(repo, "rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0:
        detail = (shallow.stderr or shallow.stdout).strip() or "git rev-parse --is-shallow-repository failed"
        return None, [detail]
    if shallow.stdout.strip() == "true":
        return None, [f"{target}: shallow repository; history is incomplete"]
    return toplevel, []


def _main_commits(repo: Path) -> set[str]:
    present = _git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/main")
    if present.returncode != 0:
        return set()
    listed = _git(repo, "rev-list", "refs/heads/main")
    if listed.returncode != 0:
        return set()
    return {line for line in listed.stdout.splitlines() if line}


def _message_problems(repo: Path, label: str, body: str) -> list[str]:
    found: list[str] = []
    for line in body.splitlines():
        if _FORBIDDEN_TRAILER_RE.match(line):
            found.append(f"{label}: forbidden trailer {line.strip()}")
    parsed = _git(repo, "interpret-trailers", "--parse", input_text=body if body.endswith("\n") else body + "\n")
    if parsed.returncode != 0:
        found.append(f"{label}: git interpret-trailers failed")
    else:
        for line in parsed.stdout.splitlines():
            if _FORBIDDEN_TRAILER_RE.match(line):
                found.append(f"{label}: forbidden trailer {line.strip()}")
            for email in _foreign_emails(line):
                found.append(f"{label}: email {email} is not {ALLOWED_EMAIL}")
    for email in _foreign_emails(body):
        found.append(f"{label}: email {email} is not {ALLOWED_EMAIL}")
    return _unique(found)


def problems(
    repo: Path,
    *,
    all_refs: bool = False,
    required_name: str = DEFAULT_NAME,
) -> list[str]:
    """Return one string per hygiene failure in the selected history."""
    repo = repo.resolve()
    _toplevel, blocked = _prepare(repo)
    if blocked:
        return blocked
    command = ["log", "-z", "--format=%H%x1e%an%x1e%ae%x1e%cn%x1e%ce%x1e%B"]
    command.append("--all" if all_refs else "HEAD")
    result = _git(repo, *command)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "git log failed"
        return [detail]

    on_main = _main_commits(repo)
    found: list[str] = []
    for record in result.stdout.split("\0"):
        if not record:
            continue
        parts = record.split("\x1e", 5)
        if len(parts) != 6:
            found.append(f"unparsed commit record: {record[:80]!r}")
            continue
        sha, author_name, author, committer_name, committer, body = parts
        label = sha or "(unknown)"
        if author != ALLOWED_EMAIL:
            found.append(f"{label}: author email {author!r} is not {ALLOWED_EMAIL}")
        if committer != ALLOWED_EMAIL:
            found.append(f"{label}: committer email {committer!r} is not {ALLOWED_EMAIL}")
        if sha in on_main:
            if author_name != required_name:
                found.append(f"{label}: author name {author_name!r} is not {required_name}")
            if committer_name != required_name:
                found.append(f"{label}: committer name {committer_name!r} is not {required_name}")
        found.extend(_message_problems(repo, label, body))
    return found


def _parse(argv: list[str]) -> tuple[argparse.Namespace | None, int | None]:
    parser = argparse.ArgumentParser(prog="check_commit_hygiene.py")
    parser.add_argument("--all", action="store_true", help="check every ref, not only HEAD")
    parser.add_argument(
        "--name",
        default=DEFAULT_NAME,
        help="author and committer name required on main (default: %(default)s)",
    )
    parser.add_argument("repo", nargs="?", default=".")
    try:
        return parser.parse_args(argv), None
    except SystemExit as exc:
        code = exc.code
        return None, 0 if code is None else int(code)


def main(argv: list[str] | None = None) -> int:
    args, code = _parse(list(sys.argv[1:] if argv is None else argv))
    if code is not None:
        return code
    assert args is not None
    repo = Path(args.repo).resolve()
    found = problems(repo, all_refs=args.all, required_name=args.name)
    if found:
        for item in found:
            print(item, file=sys.stderr)
        return 1
    print(f"commit hygiene ok: {repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
