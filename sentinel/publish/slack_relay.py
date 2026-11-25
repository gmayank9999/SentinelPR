"""Publish one existing Actions report from a trusted workflow_run job."""

import argparse
import json
import os
from pathlib import Path

import httpx

from sentinel.publish.slack import public_https


def relay_payload(event: dict, report: dict) -> dict:
    run = event["workflow_run"]
    repository = event["repository"]["full_name"]
    pulls = run["pull_requests"]
    if run["event"] != "pull_request" or len(pulls) != 1:
        raise ValueError("The upstream run must identify exactly one pull request.")
    pull = pulls[0]
    if (pull["base"]["repo"]["id"] != event["repository"]["id"] or report["repository"].lower() != repository.lower()
            or pull["number"] != report["pr"]["number"] or pull["head"]["sha"] != report["pr"]["head_sha"]):
        raise ValueError("The report does not match the upstream workflow's repository, PR and head.")
    return {"report": report, "actions_run_id": run["id"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    url = public_https(os.environ.get("SENTINEL_SLACK_RELAY_URL"))
    secret = os.environ.get("SLACK_RELAY_TOKEN")
    if not url or not secret:
        parser.exit(0, "Slack relay is not configured.\n")
    if args.report.stat().st_size > 950_000:
        parser.exit(1, "Slack relay report exceeds the configured request limit.\n")
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        report = json.loads(args.report.read_text(encoding="utf-8"))
        payload = relay_payload(event, report)
        response = httpx.post(url, headers={"Authorization": f"Bearer {secret}"}, json=payload, timeout=20, follow_redirects=False)
        response.raise_for_status()
        state = response.json().get("delivery")
    except (KeyError, TypeError, ValueError, httpx.HTTPError) as exc:
        parser.exit(1, f"Slack relay failed ({type(exc).__name__}).\n")
    if state != "sent":
        parser.exit(1, f"Slack relay did not confirm delivery ({state if state in ('failed', 'uncertain', 'sending', 'skipped', 'disabled') else 'unknown'}).\n")
    print("Slack relay confirmed delivery.")


if __name__ == "__main__":
    main()
