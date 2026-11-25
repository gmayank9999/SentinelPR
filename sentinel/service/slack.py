"""Opt-in Slack delivery and commit-bound human reviews."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from sentinel.publish.app_auth import AppAuthError, GitHubAppAuth
from sentinel.publish.slack import (REVIEW_ACTIONS, STYLE, SlackError, SlackPublisher, dashboard_report_url,
                                    evidence_card, public_https, render_message)
from sentinel.service.events import redact

log = logging.getLogger(__name__)
REPO = re.compile(r"[a-zA-Z0-9-]+/[a-zA-Z0-9_.-]+")
RUN = re.compile(r"run-[0-9]{14}-[a-f0-9]{6}")
SHA = re.compile(r"[a-f0-9]{40}")
WINDOW = 300
MAX_BODY = 1_000_000


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _list(value) -> set[str]:
    items = value.split(",") if isinstance(value, str) else (value or [])
    return {item.strip() for item in items if isinstance(item, str) and item.strip()}


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class SlackStore:
    def __init__(self, cfg):
        # Outside .sentinel so the Action's index cache cannot capture review records.
        configured = _env("SLACK_STATE_DIR")
        self.path = (Path(configured) if configured else cfg.repo_root / ".sentinel-slack") / "review.db"

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=0.5)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, repo TEXT, pr INTEGER, payload TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS slack_pr ON notifications (repo, pr)")
            db.execute("CREATE TABLE IF NOT EXISTS replays (digest TEXT PRIMARY KEY, expires INTEGER)")
            db.execute("BEGIN IMMEDIATE")
            yield db

    @staticmethod
    def read(db, run_id):
        result = db.execute("SELECT payload FROM notifications WHERE id = ?", (run_id,)).fetchone()
        return json.loads(result[0]) if result else None

    @staticmethod
    def save(db, record):
        card = record["card"]
        db.execute("INSERT OR REPLACE INTO notifications VALUES (?, ?, ?, ?)",
                   (card["run_id"], card["repository"], card["number"], json.dumps(record)))

    def get(self, run_id):
        if not self.path.exists():
            return None
        with closing(sqlite3.connect(self.path, timeout=0.5)) as db:
            return self.read(db, run_id)

    def change(self, run_id, **updates):
        with self.transaction() as db:
            record = self.read(db, run_id)
            record.update(updates)
            self.save(db, record)
        return record

    def fresh_request(self, signature, now):
        with self.transaction() as db:
            db.execute("DELETE FROM replays WHERE expires < ?", (now,))
            return bool(db.execute("INSERT OR IGNORE INTO replays VALUES (?, ?)",
                                   (hashlib.sha256(signature.encode()).hexdigest(), now + 2 * WINDOW)).rowcount)


class SlackIntegration:
    def __init__(self, cfg, app_auth=None, slack=None, github=None):
        self.cfg = cfg
        self.enabled = _env("SLACK_ENABLED").lower() in {"1", "true", "yes"}
        self.signing_secret = _env("SLACK_SIGNING_SECRET")
        self.team = _env("SLACK_TEAM_ID")
        self.channel = _env("SLACK_CHANNEL_ID")
        self.default_reviewers = _list(_env("SLACK_ALLOWED_REVIEWERS"))
        self.routes = {name.lower(): {} for name in _list(_env("SLACK_REPOSITORIES")) if REPO.fullmatch(name)}
        self.routes.update({name.lower(): route for name, route in (cfg.get("slack.repositories") or {}).items()
                            if REPO.fullmatch(name) and isinstance(route, dict)})
        self.client = slack or (SlackPublisher(_env("SLACK_BOT_TOKEN")) if _env("SLACK_BOT_TOKEN") else None)
        self.auth = app_auth or GitHubAppAuth()
        self.github = github or httpx.Client(base_url="https://api.github.com/", timeout=12, follow_redirects=False)
        self.store = SlackStore(cfg)

    def configured_channel(self, repo):
        route = self.routes.get(repo.lower())
        channel = (route.get("channel") or self.channel) if route is not None else ""
        return channel if isinstance(channel, str) and re.fullmatch(r"[CG][A-Z0-9]+", channel) else None

    def reviewers(self, repo):
        route = self.routes.get(repo.lower(), {})
        return _list(route.get("reviewers", self.default_reviewers))

    def relay_secret(self, repo):
        name = self.routes.get(repo.lower(), {}).get("relay_token_env")
        if isinstance(name, str) and re.fullmatch(r"SLACK_RELAY_TOKEN_[A-Z0-9_]+", name):
            return _env(name)
        return _env("SLACK_RELAY_TOKEN") if len(self.routes) == 1 else ""

    def snapshot(self):
        return {"enabled": self.enabled, "bot_configured": self.client is not None,
                "interactivity_configured": bool(self.signing_secret and self.team and self.auth.configured),
                "default_channel_configured": bool(self.channel), "repositories": len(self.routes),
                "report_links_configured": bool(public_https(_env("SENTINEL_PUBLIC_BASE_URL")))}

    def check(self, test=False):
        if not self.enabled or not self.client:
            return {"ok": False, "detail": "Slack is disabled or the bot token is missing."}
        try:
            identity = self.client.call("auth.test")
            if self.team and identity.get("team_id") != self.team:
                return {"ok": False, "detail": "The bot token belongs to a different Slack workspace."}
            if test:
                if not self.channel or not re.fullmatch(r"[CG][A-Z0-9]+", self.channel):
                    return {"ok": False, "detail": "Set SLACK_CHANNEL_ID to the channel for test messages."}
                message = self.client.call("chat.postMessage", channel=self.channel,
                                           text="SentinelPR is connected. This test contains no PR evidence.")
                if not message.get("ts"):
                    raise SlackError("slack_message_unconfirmed")
            return {"ok": True, "detail": "Test notification sent." if test else "Connected to Slack."}
        except SlackError as exc:
            return {"ok": False, "detail": str(exc)}

    def status(self, run_id):
        record = self.store.get(run_id)
        if not record:
            return {"delivery": "not_sent" if self.enabled else "disabled", "review": None}
        return {key: record.get(key) for key in ("delivery", "delivery_error", "message_update_error", "review")}

    def notify(self, report, *, resend=False, report_url=None, allow_refresh=False):
        repo, pr, run_id = report.get("repository") or "", report.get("pr") or {}, report.get("run_id") or ""
        if not self.enabled:
            return {"delivery": "disabled"}
        if not (REPO.fullmatch(repo) and RUN.fullmatch(run_id) and type(pr.get("number")) is int
                and pr["number"] > 0 and SHA.fullmatch(pr.get("head_sha") or "") and report.get("decision") in STYLE):
            return {"delivery": "skipped", "delivery_error": "No GitHub PR and analyzed commit were recorded."}
        channel = self.configured_channel(repo)
        if not self.client or not channel:
            return {"delivery": "skipped", "delivery_error": "No authorized Slack destination or bot token for this repository."}
        if report_url is None and not os.getenv("GITHUB_ACTIONS"):
            report_url = dashboard_report_url(_env("SENTINEL_PUBLIC_BASE_URL"), run_id)
        card = evidence_card(report, repo.lower(), report_url)
        with self.store.transaction() as db:
            previous = self.store.read(db, run_id)
            if previous:
                if (previous["card"]["repository"], previous["card"]["number"], previous["card"]["head"]) != (card["repository"], card["number"], card["head"]):
                    return {"delivery": "skipped", "delivery_error": "The run ID already belongs to another PR revision."}
                if not resend or previous["channel"] != channel:
                    return {"delivery": previous["delivery"]}
                if previous["delivery"] == "sent" and not allow_refresh:
                    return {"delivery": "sent"}
                if previous["delivery"] not in {"failed", "sent"}:
                    return {"delivery": previous["delivery"], "delivery_error": "Delivery needs manual reconciliation before retrying."}
            record = previous or {"card": card, "channel": channel, "review": {"status": "pending"}}
            update = bool(record.get("ts"))
            if not update:
                record.update(delivery="sending", delivery_error=None)
                self.store.save(db, record)
        if update:
            self.refresh(record)
            return self.status(run_id)
        try:
            result = self.client.call("chat.postMessage", channel=channel, **self.message(record))
            if not result.get("ts") or result.get("channel") != channel:
                raise SlackError("slack_message_unconfirmed", uncertain=True)
        except SlackError as exc:
            self.store.change(run_id, delivery="uncertain" if exc.uncertain else "failed", delivery_error=str(exc))
            return self.status(run_id)
        record = self.store.change(run_id, delivery="sent", ts=result["ts"], delivery_error=None)
        self.supersede(record)
        return self.status(run_id)

    def message(self, record):
        repo = record["card"]["repository"]
        interactive = bool(self.signing_secret and self.team and self.auth.configured and self.reviewers(repo))
        return render_message(record["card"], record["review"], interactive=interactive)

    def refresh(self, record):
        if not self.client or not record.get("ts") or self.configured_channel(record["card"]["repository"]) != record["channel"]:
            return
        run_id = record["card"]["run_id"]
        record = self.store.get(run_id)
        try:
            message = self.message(record)
            self.client.call("chat.update", channel=record["channel"], ts=record["ts"],
                             text=message["text"], attachments=message["attachments"])
            self.store.change(run_id, message_update_error=None)
        except SlackError as exc:
            self.store.change(run_id, message_update_error=str(exc))

    def supersede(self, record):
        card = record["card"]
        if not self.auth.configured:
            return
        with self.store.transaction() as db:
            rows = db.execute("SELECT payload FROM notifications WHERE repo = ? AND pr = ?", (card["repository"], card["number"])).fetchall()
        if not any(json.loads(row[0])["card"]["head"] != card["head"] for row in rows):
            return
        try:
            _, _, pull = self.current_pull(card)
            head = pull["head"]["sha"]
        except (AppAuthError, httpx.HTTPError, KeyError, ValueError):
            return  # The confirmation in synchronize still applies to every review.
        outdated = []
        with self.store.transaction() as db:
            for (payload,) in rows:
                candidate = json.loads(payload)["card"]
                current = self.store.read(db, candidate["run_id"])
                if (candidate["head"] != head or pull.get("state") != "open") and current["review"]["status"] != "outdated":
                    current["review"].update(status="outdated", sync_detail="The GitHub PR no longer has this open head.")
                    self.store.save(db, current)
                    outdated.append(current)
        for previous in outdated:
            self.refresh(previous)

    def identity(self, payload, record):
        user = (payload.get("user") or {}).get("id")
        workspace = (payload.get("team") or {}).get("id")
        repo = record["card"]["repository"]
        if (not isinstance(user, str) or not self.team or workspace != self.team or user not in self.reviewers(repo)
                or self.configured_channel(repo) != record["channel"]):
            raise HTTPException(403, "Reviewer is not authorized for this repository.")
        return user

    def modal(self, record, action, user, trigger):
        required = action == "changes_requested"
        run_id = record["card"]["run_id"]
        view = {"type": "modal", "callback_id": "sentinel_review",
                "private_metadata": json.dumps([run_id, action, user]),
                "title": {"type": "plain_text", "text": "Request Changes" if required else "Approve PR"},
                "submit": {"type": "plain_text", "text": "Submit review"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "blocks": [{"type": "input", "block_id": "reason", "optional": not required,
                            "label": {"type": "plain_text", "text": "Rejection reason" if required else "Approval comment"},
                            "element": {"type": "plain_text_input", "action_id": "comment", "max_length": 1000, "multiline": True}}]}
        try:
            self.client.call("views.open", trigger_id=trigger, view=view)
        except SlackError as exc:
            self.store.change(run_id, message_update_error=f"Could not open review dialog: {exc}")

    def claim(self, run_id, user, name, action, comment):
        with self.store.transaction() as db:
            record = self.store.read(db, run_id)
            if record["review"]["status"] != "pending":
                return False
            record["review"] = {"status": "processing", "reviewer": user, "reviewer_name": redact(name), "team": self.team, "decision": action,
                                "comment": redact(comment), "reviewed_at": _stamp(), "sync": "pending"}
            self.store.save(db, record)
        return True

    def synchronize(self, run_id):
        record = self.store.get(run_id)
        card, review = record["card"], record["review"]
        repo = card["repository"]
        if review["status"] != "processing":
            return
        try:
            if (review["team"] != self.team or review["reviewer"] not in self.reviewers(repo)
                    or self.configured_channel(repo) != record["channel"]):
                raise ValueError("Reviewer authorization changed.")
            path, headers, pull = self.current_pull(card)
            head = pull["head"]["sha"]
        except (AppAuthError, httpx.HTTPError, KeyError, ValueError) as exc:
            review = {"status": "pending", "sync": "failed", "sync_detail": f"Could not validate GitHub PR ({self.failure_code(exc)})."}
        else:
            if head != card["head"] or pull.get("state") != "open":
                review.update(status="outdated", sync="not submitted", sync_detail="PR head changed or the PR closed. Run a fresh analysis.")
            else:
                if self.store.get(run_id)["review"]["status"] == "outdated":
                    return
                review.update(status=review["decision"])
                event = "APPROVE" if review["decision"] == "approved" else "REQUEST_CHANGES"
                body = (f"Submitted by the SentinelPR GitHub App on behalf of authorized Slack reviewer "
                        f"`{review['team']}/{review['reviewer']}`. Analysis `{run_id}`, commit `{head}`. "
                        f"Automated verdict remains **{card['detail'] or card['decision']}**.")
                if review["comment"]:
                    body += "\n\n" + "\n".join(f"> {line.replace('@', '@' + chr(8203))}" for line in review["comment"].splitlines())
                try:
                    submitted = self.github.post(f"{path}/reviews", headers=headers,
                                                 json={"event": event, "body": body, "commit_id": head})
                    submitted.raise_for_status()
                    review_id = submitted.json().get("id")
                    if not isinstance(review_id, int):
                        raise ValueError("GitHub returned no review ID.")
                    review.update(sync="synced", github_review_id=review_id, sync_detail=f"GitHub review #{review_id}")
                except (httpx.HTTPError, ValueError) as exc:
                    review.update(sync="failed" if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500 else "unconfirmed",
                                  sync_detail=f"GitHub review submission ({self.failure_code(exc)}).")
        with self.store.transaction() as db:
            current = self.store.read(db, run_id)
            if current["review"]["status"] == "outdated":
                review = review | current["review"] if review["status"] == "pending" else current["review"] | review | {"status": "outdated"}
            current["review"] = review
            self.store.save(db, current)
        self.refresh(current)

    def current_pull(self, card):
        repo = card["repository"]
        path = f"repos/{repo}/pulls/{card['number']}"
        token = self.auth.token(repo, {"pull_requests": "write"})
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        response = self.github.get(path, headers=headers)
        response.raise_for_status()
        return path, headers, response.json()

    @staticmethod
    def failure_code(exc):
        if isinstance(exc, httpx.HTTPStatusError):
            return f"HTTP {exc.response.status_code}"
        if isinstance(exc, AppAuthError):
            return exc.code
        return type(exc).__name__


def notify_completed(cfg, report, integration=None, **options):
    """Delivery and relay errors are independent of the analysis lifecycle."""
    if not (integration and integration.enabled) and _env("SLACK_ENABLED").lower() not in {"1", "true", "yes"}:
        return {"delivery": "disabled"}
    try:
        result = (integration or SlackIntegration(cfg)).notify(report, **options)
        if result.get("delivery_error"):
            log.warning("Slack delivery for %s: %s", report.get("run_id"), result["delivery_error"])
        return result
    except Exception:  # A messaging failure cannot change the gate or fail the run.
        log.exception("Slack notification failed for run %s", report.get("run_id"))
        return {"delivery": "failed", "delivery_error": "Slack integration failed. See server logs."}


def verify_request(secret, timestamp, signature, body, now):
    try:
        age = abs(int(timestamp) - now)
    except (TypeError, ValueError):
        return False
    expected = "v0=" + hmac.new(secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256).hexdigest()
    return bool(secret and age <= WINDOW and hmac.compare_digest(expected.encode(), (signature or "").encode()))


def interaction_router(slack, cfg):
    router = APIRouter(prefix="/api/slack")

    @router.post("/interactions")
    async def interaction(request: Request, background: BackgroundTasks):
        if not (slack.enabled and slack.client and slack.signing_secret and slack.team):
            raise HTTPException(404)
        body = await bounded_body(request)
        now = int(time.time())
        signature = request.headers.get("x-slack-signature")
        if not verify_request(slack.signing_secret, request.headers.get("x-slack-request-timestamp"), signature, body, now):
            raise HTTPException(401, "Invalid Slack signature or expired request.")
        try:
            payload = json.loads(parse_qs(body.decode("utf-8"), max_num_fields=4)["payload"][0])
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
        except (KeyError, ValueError):
            raise HTTPException(400, "Invalid Slack payload.") from None
        if not await run_in_threadpool(slack.store.fresh_request, signature, now):
            # Slack retries valid deliveries. Acknowledge without repeating any work.
            return Response()
        return await run_in_threadpool(handle_interaction, slack, payload, background)

    @router.post("/relay")
    async def relay(request: Request):
        supplied = request.headers.get("authorization", "")
        if not slack.enabled or not supplied.startswith("Bearer ") or len(supplied) > 512:
            raise HTTPException(401)
        try:
            data = json.loads(await bounded_body(request))
            report, action_id = data["report"], str(data["actions_run_id"])
            if report.get("schema") != "sentinelpr.run/1" or not REPO.fullmatch(report["repository"]) or not re.fullmatch(r"[0-9]{1,24}", action_id):
                raise ValueError("Invalid run")
        except (AttributeError, KeyError, TypeError, ValueError):
            raise HTTPException(400, "Invalid completed report.") from None
        repo = report["repository"]
        secret = slack.relay_secret(repo)
        if not slack.configured_channel(repo) or not secret or not hmac.compare_digest(supplied.encode(), f"Bearer {secret}".encode()):
            raise HTTPException(401)
        link = f"https://github.com/{report['repository']}/actions/runs/{action_id}#artifacts"
        return await run_in_threadpool(lambda: notify_completed(cfg, report, slack, resend=True, report_url=link))

    return router


async def bounded_body(request):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_BODY:
            raise HTTPException(413)
    return bytes(content)


def handle_interaction(slack, payload, background):
    if payload.get("type") == "block_actions":
        actions = payload.get("actions") or []
        action = actions[0] if actions else {}
        action_id, run_id = action.get("action_id"), action.get("value")
        if action_id in {"sentinel_view_pr", "sentinel_view_report"}:
            return Response()
        if action_id not in REVIEW_ACTIONS or not isinstance(run_id, str) or not RUN.fullmatch(run_id):
            raise HTTPException(400, "Unknown review action.")
        record = slack.store.get(run_id)
        container = payload.get("container") or {}
        trigger = payload.get("trigger_id")
        if (not record or not isinstance(trigger, str) or not trigger or record.get("delivery") != "sent"
                or container.get("channel_id") != record["channel"] or container.get("message_ts") != record["ts"]):
            raise HTTPException(400, "Invalid message target.")
        user = slack.identity(payload, record)
        if record["review"]["status"] == "pending":
            background.add_task(slack.modal, record, REVIEW_ACTIONS[action_id], user, trigger)
        return Response()

    view = payload.get("view") or {}
    if payload.get("type") != "view_submission" or view.get("callback_id") != "sentinel_review":
        raise HTTPException(400, "Unknown Slack interaction.")
    try:
        run_id, action, actor = json.loads(view["private_metadata"])
        comment = (((view.get("state") or {}).get("values") or {}).get("reason") or {}).get("comment") or {}
        comment = (comment.get("value") or "").strip()
        if not RUN.fullmatch(run_id) or action not in REVIEW_ACTIONS.values() or len(comment) > 1000:
            raise ValueError("Invalid review")
    except (AttributeError, KeyError, TypeError, ValueError):
        raise HTTPException(400, "Invalid review submission.") from None
    record = slack.store.get(run_id)
    if not record or record.get("delivery") != "sent":
        raise HTTPException(404, "Unknown Slack message.")
    user = slack.identity(payload, record)
    if user != actor:
        raise HTTPException(403, "This review dialog belongs to another reviewer.")
    if action == "changes_requested" and not comment:
        return JSONResponse({"response_action": "errors", "errors": {"reason": "Enter a reason for requesting changes."}})
    name = (payload.get("user") or {}).get("name") or (payload.get("user") or {}).get("username")
    if slack.claim(run_id, user, name.strip()[:100] if isinstance(name, str) else "", action, comment):
        background.add_task(slack.synchronize, run_id)
    return Response()
