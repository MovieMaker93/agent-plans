# Security

## Reporting a vulnerability

Email `security@example.com` with a description, the version or commit you tested, and the impact. That address is a placeholder. Replace it with a real contact before you rely on it.

Please do not open a public issue that includes exploit details, a live webhook URL, or credentials.

We will acknowledge a report when we see it. This project does not run a paid bounty program.

## What this tool stores

Plans, rosters, and generated views are files in the checkout. The digest posts to Slack only if you pass `--post` and `SLACK_DIGEST_WEBHOOK` is set. The URL is not written into the repository. `serve` is a read-only HTTP server. It refuses POST, PUT, and DELETE.

`dispatch-dry-run` does not send work.
