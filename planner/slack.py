# SPDX-License-Identifier: Apache-2.0
"""Slack-ready digest payload.

`digest` always writes the payload next to the markdown. It posts only when
the caller passes `--post` and `SLACK_DIGEST_WEBHOOK` is set. The webhook
URL stays in the environment. It is never written into the payload file.
"""

from __future__ import annotations

import json
from pathlib import Path

WEBHOOK_ENV = "SLACK_DIGEST_WEBHOOK"
SECTION_LIMIT = 2900


def to_mrkdwn(markdown: str) -> str:
    """Turn the digest headings into Slack mrkdwn. Lists stay as they are."""
    lines = []
    for line in markdown.splitlines():
        if line.startswith("### "):
            lines.append(f"*{line[4:].strip()}*")
        elif line.startswith("## "):
            lines.append(f"*{line[3:].strip()}*")
        elif line.startswith("# "):
            lines.append(f"*{line[2:].strip()}*")
        else:
            lines.append(line)
    return "\n".join(lines).strip() + "\n"


def _chunks(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in text.split("\n\n"):
        piece = paragraph + "\n\n"
        if size + len(piece) > limit and current:
            parts.append("".join(current).strip())
            current = []
            size = 0
        if len(piece) > limit:
            if current:
                parts.append("".join(current).strip())
                current = []
                size = 0
            start = 0
            body = piece.strip()
            while start < len(body):
                parts.append(body[start : start + limit])
                start += limit
            continue
        current.append(piece)
        size += len(piece)
    if current:
        parts.append("".join(current).strip())
    return [part for part in parts if part]


def slack_payload(digest: str) -> dict:
    """Incoming-webhook body: fallback text plus one block per chunk.

    Sections follow the digest: what changed, decisions needed, risks, next 24h.
    """
    text = to_mrkdwn(digest)
    blocks = [
        {"text": {"text": chunk, "type": "mrkdwn"}, "type": "section"}
        for chunk in _chunks(text, SECTION_LIMIT)
    ]
    return {"blocks": blocks, "text": text.strip()}


def render_payload(digest: str) -> str:
    return json.dumps(slack_payload(digest), indent=2, sort_keys=True) + "\n"


def write_payload(root: Path, digest: str) -> Path:
    folder = root / "dashboard"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "slack-payload.json"
    path.write_text(render_payload(digest), encoding="utf-8")
    return path


def post_webhook(url: str, payload: dict, *, opener=None, timeout: float = 15):
    """POST JSON to an incoming webhook. Pass `opener` in tests so nothing is sent."""
    import urllib.request

    cleaned = url.strip()
    if not cleaned.startswith("https://") and not cleaned.startswith("http://"):
        raise ValueError(f"{WEBHOOK_ENV} must be an http or https URL")
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        cleaned,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    open_fn = opener or urllib.request.urlopen
    response = open_fn(request, timeout=timeout)
    try:
        status = getattr(response, "status", None)
        if status is None:
            status = response.getcode()
        return int(status)
    finally:
        close = getattr(response, "close", None)
        if close:
            close()
