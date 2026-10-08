# SPDX-License-Identifier: Apache-2.0
"""Fail if any commit reachable from HEAD has a foreign identity or a trailer.

The public history of this repository uses one address, ``noreply@example.com``,
for both author and committer. A ``Co-authored-by:`` or ``Signed-off-by:``
line is rejected in any case, because a local commit hook can append one.

Requires the ``git`` executable. The script itself uses only the standard library.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ALLOWED_EMAIL = "noreply@example.com"
_TRAILER_PREFIXES = ("co-authored-by:", "signed-off-by:")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )


def problems(repo: Path) -> list[str]:
    """Return one string per hygiene failure in commits reachable from HEAD."""
    result = _git(repo, "log", "-z", "--format=%H%x1e%ae%x1e%ce%x1e%B", "HEAD")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "git log failed"
        return [detail]

    found: list[str] = []
    for record in result.stdout.split("\0"):
        if not record:
            continue
        sha, author, committer, body = (record.split("\x1e", 3) + ["", "", "", ""])[:4]
        label = sha or "(unknown)"
        if author != ALLOWED_EMAIL:
            found.append(f"{label}: author email {author!r} is not {ALLOWED_EMAIL}")
        if committer != ALLOWED_EMAIL:
            found.append(f"{label}: committer email {committer!r} is not {ALLOWED_EMAIL}")
        for line in body.splitlines():
            stripped = line.strip()
            lowered = stripped.lower()
            if lowered.startswith(_TRAILER_PREFIXES):
                found.append(f"{label}: forbidden trailer {stripped}")
    return found


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        print("usage: check_commit_hygiene.py [repo]", file=sys.stderr)
        return 2
    repo = Path(args[0]).resolve() if args else Path.cwd()
    found = problems(repo)
    if found:
        for item in found:
            print(item, file=sys.stderr)
        return 1
    print(f"commit hygiene ok: {repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
