# SPDX-License-Identifier: Apache-2.0
"""Read-only dashboard routes and opt-in Slack posting. No live webhook calls."""

import json
import os
import unittest
from datetime import datetime
from unittest import mock

from planner.cli import main
from planner.serve import inject_html, read_served, route
from planner.slack import WEBHOOK_ENV, post_webhook, render_payload, slack_payload
from planner.util import parse_datetime, repo_root

ROOT = repo_root()
ORIGIN = "2026-10-08T09:00:00-05:00"


def _run(argv, env=None):
    from contextlib import redirect_stdout
    from io import StringIO

    buffer = StringIO()
    with mock.patch.dict(os.environ, env or {}, clear=False):
        with redirect_stdout(buffer):
            code = main(argv)
    return code, buffer.getvalue()


class RouteTests(unittest.TestCase):
    def test_known_pages_and_rejects(self):
        self.assertEqual(route("GET", "/").relpath, "dashboard/index.html")
        self.assertEqual(route("GET", "/dashboard/digest.md").relpath, "dashboard/digest.md")
        self.assertEqual(route("GET", "/portfolio.yaml").relpath, "portfolio.yaml")
        self.assertEqual(route("GET", "/portfolio-schedule.yaml").relpath, "portfolio-schedule.yaml")
        self.assertEqual(route("GET", "/regenerate").kind, "regenerate")
        self.assertEqual(route("GET", "/roster.yaml").kind, "missing")
        self.assertEqual(route("GET", "/dashboard/../../roster.yaml").kind, "missing")
        self.assertEqual(route("POST", "/").kind, "method")
        self.assertEqual(route("PUT", "/portfolio.md").kind, "method")
        self.assertEqual(route("DELETE", "/").kind, "method")

    def test_served_html_gains_nav_without_rewriting_the_file(self):
        disk = (ROOT / "dashboard" / "index.html").read_bytes()
        self.assertNotIn(b"bot-ui", disk)
        served = read_served(ROOT, "dashboard/index.html", 60)
        self.assertIsNotNone(served)
        body, kind = served
        self.assertEqual(kind, "text/html; charset=utf-8")
        self.assertIn(b"bot-ui", body)
        self.assertIn(b'href="/dashboard/digest.md"', body)
        self.assertIn(b'href="/portfolio.yaml"', body)
        self.assertIn(b'http-equiv="refresh"', body)
        self.assertEqual((ROOT / "dashboard" / "index.html").read_bytes(), disk)
        plain = inject_html(b"<head></head><body></body>", None)
        self.assertIn(b"bot-ui", plain)
        self.assertNotIn(b"refresh", plain)


class SlackTests(unittest.TestCase):
    def test_payload_keeps_the_meeting_sections(self):
        digest = (ROOT / "dashboard" / "digest.md").read_text(encoding="utf-8")
        payload = slack_payload(digest)
        text = payload["text"]
        self.assertIn("*What changed*", text)
        self.assertIn("*Decisions needed*", text)
        self.assertIn("*Risks*", text)
        self.assertIn("*Next 24h*", text)
        rendered = render_payload(digest)
        self.assertEqual(
            (ROOT / "dashboard" / "slack-payload.json").read_text(encoding="utf-8"),
            rendered,
        )
        self.assertNotIn("hooks.", rendered)
        loaded = json.loads(rendered)
        self.assertEqual(loaded["text"], text)

    def test_post_uses_the_opener_and_not_a_live_socket(self):
        seen = []

        class Response:
            status = 204

            def close(self):
                seen.append("closed")

        def opener(request, timeout=15):
            seen.append((request.full_url, request.data, timeout))
            return Response()

        status = post_webhook(
            "https://hooks.example.test/services/SECRET",
            {"text": "hello"},
            opener=opener,
        )
        self.assertEqual(status, 204)
        self.assertEqual(seen[0][0], "https://hooks.example.test/services/SECRET")
        self.assertIn(b"hello", seen[0][1])
        self.assertEqual(seen[-1], "closed")
        with self.assertRaises(ValueError):
            post_webhook("file:///tmp/nope", {"text": "x"}, opener=opener)
        self.assertEqual(len([item for item in seen if item != "closed"]), 1)

    def test_digest_dry_run_writes_and_does_not_post(self):
        calls = []

        def fake(url, payload, **kwargs):
            calls.append(url)
            return 200

        with mock.patch("planner.slack.post_webhook", fake):
            code, out = _run(
                ["--root", str(ROOT), "digest", "--at", ORIGIN],
                env={WEBHOOK_ENV: "https://hooks.example.test/services/SECRET"},
            )
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, [])
        self.assertIn("slack: dry run", out)
        self.assertNotIn("SECRET", out)
        self.assertIn("What changed", out)

    def test_post_requires_the_env_var(self):
        calls = []

        def fake(url, payload, **kwargs):
            calls.append(url)
            return 200

        env = os.environ.copy()
        env.pop(WEBHOOK_ENV, None)
        with mock.patch("planner.slack.post_webhook", fake):
            with mock.patch.dict(os.environ, env, clear=True):
                from io import StringIO
                from contextlib import redirect_stdout

                buffer = StringIO()
                with redirect_stdout(buffer):
                    code = main(
                        ["--root", str(ROOT), "digest", "--post", "--at", ORIGIN]
                    )
                out = buffer.getvalue()
        self.assertEqual(code, 1, out)
        self.assertEqual(calls, [])
        self.assertIn("SLACK_DIGEST_WEBHOOK", out)

    def test_post_flag_sends_the_payload_through_the_fake(self):
        calls = []

        def fake(url, payload, **kwargs):
            calls.append((url, payload["text"]))
            return 200

        with mock.patch("planner.slack.post_webhook", fake):
            code, out = _run(
                ["--root", str(ROOT), "digest", "--post", "--at", ORIGIN],
                env={WEBHOOK_ENV: "https://hooks.example.test/services/SECRET"},
            )
        self.assertEqual(code, 0, out)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].endswith("/SECRET"))
        self.assertIn("*What changed*", calls[0][1])
        self.assertIn("slack: posted (200)", out)
        self.assertNotIn("SECRET", out)
        self.assertNotIn("hooks.example.test", out)


class ClockPinTests(unittest.TestCase):
    def test_pinned_timestamp_parses(self):
        moment = parse_datetime(ORIGIN, "origin")
        self.assertIsInstance(moment, datetime)
        self.assertEqual(moment.isoformat(), ORIGIN)


if __name__ == "__main__":
    unittest.main()
