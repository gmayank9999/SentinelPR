# Slack PR review and detailed verdicts

SentinelPR publishes a completed GitHub PR report from the CLI, the FastAPI service (including dashboard-initiated analyses), or the GitHub Actions relay. Slack delivery, human review, and GitHub synchronization are independent of the automated gate. Approval never changes the automated verdict, starts a rollout, or merges a PR.

## Detailed verdicts

`decision` remains `PASS`, `CANARY`, or `BLOCK`. `risk` is preserved and `risk_score` is its alias. `severity` is `LOW`, `MEDIUM`, or `HIGH`, and `detailed_decision` combines it with the primary decision. Each numerical interval uses cutoffs `lower + (upper − lower)/3` and `lower + 2 × (upper − lower)/3`. The intervals are lower-inclusive and upper-exclusive, with 1.0 included in `BLOCK_HIGH`. Cutoffs are calculated from the report's actual thresholds before rounding to the score format. These are descriptive bands.

| Primary verdict | Numerical interval for the shipped model | LOW → MEDIUM | MEDIUM → HIGH |
| --- | --- | --- | --- |
| PASS | [0, 0.4982) | ≈0.166067 | ≈0.332133 |
| CANARY | [0.4982, 0.8672) | 0.6212 | 0.7442 |
| BLOCK | [0.8672, 1] | ≈0.911467 | ≈0.955733 |

Hard blocks from the input guard or verified new test failures use `BLOCK_HIGH` regardless of score. `policy_override` contains the rule and the original reason. If the input guard prevents scoring, both risk fields remain null. Older reports without enough data to classify keep their primary decision and show unavailable severity. The numerical policy and existing GitHub Check Run conclusions are unchanged.

## Create the Slack App

1. Set up the HTTPS callback endpoint below. Replace the example host in [the manifest](../deploy/slack-app-manifest.yml) with your actual HTTPS callback URL. The path is `/api/slack/interactions`.
2. At [Your Apps](https://api.slack.com/apps), select **Create New App → From an app manifest**, choose your workspace, paste the YAML, and create the app. Under **Interactivity & Shortcuts**, check that the Request URL exactly matches the callback endpoint.
3. Under **OAuth & Permissions**, install the app to the workspace and copy its **Bot User OAuth Token** to `SLACK_BOT_TOKEN`. Under **Basic Information → App Credentials**, copy **Signing Secret** to `SLACK_SIGNING_SECRET`.
4. Invite the bot to the channel and copy the channel ID from channel details. Use a private channel with appropriate membership when sharing private repo evidence. The single bot scope is `chat:write`. Interactivity uses a bot token with `views.open` and signed HTTP requests. No Slack incoming webhook, admin scope, channel-reading scope or user token is required. [Slack scopes](https://docs.slack.dev/reference/scopes/chat.write/) · [Slack interactivity](https://docs.slack.dev/interactivity/handling-user-interaction/)
5. Copy the Slack workspace ID (`T…`), for example from the workspace's Slack web URL, into `SLACK_TEAM_ID`. On each selected reviewer's Slack profile, use **More → Copy member ID** and add the ID to the repository allowlist.

## Authorize the GitHub integration

Follow the [GitHub App setup](github-app.md), giving the App **Pull requests: Read and write** and installing it on each repository that supports Slack review. The App uses an installation token scoped to the repo and this permission. **Checks: Read and write** is only needed if you separately use existing Check Run publishing. Configure the App client ID and private key file on the server. The signing library can be installed via `python -m pip install cryptography`.

GitHub attributes a review submitted this way to the App. The body identifies the authorized Slack workspace and reviewer, run ID, commit and automated verdict. Keep branch protection enabled, including stale approval dismissal if required by your repository's policies. A review is sent with `commit_id` set to the analyzed PR head. [GitHub review API](https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request)

The GitHub Actions card also shows the tested checkout SHA when it differs from the PR head, such as the existing workflow's merge checkout.

## Server configuration

Put these variables in the repository's gitignored `.env` for the API. Use the corresponding PowerShell process environment variables for CLI runs. Keep actual credentials in a local secret store or deployment secret manager. Keep the GitHub App PEM and Slack SQLite directory private.

| Setting | Use |
| --- | --- |
| `SLACK_ENABLED` | Explicit opt-in. Default `false`. |
| `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_TEAM_ID` | Installed bot, request verification, and interactive workspace allowlist. |
| `SLACK_CHANNEL_ID`, `SLACK_REPOSITORIES` | Default destination, and explicit comma-separated `owner/repo` allowlist. A channel without an approved repo receives only explicitly requested test notifications. |
| `SLACK_ALLOWED_REVIEWERS` | Comma-separated Slack IDs explicitly authorized for each default route. Give this list review authority only for repositories where all listed members should have it. |
| `SENTINEL_GITHUB_APP_CLIENT_ID`, `SENTINEL_GITHUB_APP_PRIVATE_KEY_PATH` | GitHub review identity. The private key path points outside the repo. |
| `SENTINEL_API_TOKEN` | Enables the test and resend controls under the existing dashboard authentication. Enter this token in local dashboard Settings. |
| `SENTINEL_PUBLIC_BASE_URL` | Optional HTTPS URL of a dashboard that can load the actual locally stored run and is reachable by the intended reviewers. Leave blank for a localhost-only UI. CI cards link to the corresponding GitHub Actions artifacts instead. |
| `SLACK_STATE_DIR` | Optional shared absolute directory for a CLI and FastAPI instance in different checkout directories. Default `.sentinel-slack` beside the project. Keep the volume persistent when deploying the API. |
| `SLACK_RELAY_TOKEN` | Random token for the Actions relay in a single-repository setup. For a multi-repository server, assign a distinct `relay_token_env` for each repository allowed to relay. The variable name must start with `SLACK_RELAY_TOKEN_`. |

Set a repository-specific channel and reviewer list in the **operator-controlled** `sentinel.yaml` when needed. An explicitly empty list provides notifications without interactive review for that repo.

```yaml
slack:
  repositories:
    acme/payments:
      channel: C0123456789
      reviewers: [U0123456789, U0987654321]
      relay_token_env: SLACK_RELAY_TOKEN_PAYMENTS
```

The CLI ignores Slack routes from an implicit repository config. Supply an operator-controlled file with `--config` to enable YAML routes, or define allowlisted routes in the environment. The service always uses the server's operator configuration.

## Windows startup and HTTPS callback

Run from the SentinelPR checkout in PowerShell.

```powershell
py -3.13 -m venv .venv
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $python -m pip install -r sentinel/requirements.txt -r demo/uni-erp/requirements.txt cryptography
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
code .env
# Fill in the intended Slack settings and SENTINEL_API_TOKEN.
& $python -m sentinel.api --host 127.0.0.1 --port 8765
```

If `.env` already exists, edit it in place and skip the copy. Use Python 3.11+ and Node 24.15+ in the 24.x line. In a second PowerShell terminal:

```powershell
Set-Location dashboard
npm.cmd ci
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

For development, use a named HTTPS tunnel on a domain you control. This example uses Cloudflare Tunnel, with `cloudflared` installed and the domain configured in Cloudflare.

```powershell
cloudflared tunnel login
cloudflared tunnel create sentinel-slack
cloudflared tunnel route dns sentinel-slack slack-callback.example.com
```

Save a tunnel config outside the repository and replace the UUID, Windows credentials path and hostname with your real values.

```yaml
tunnel: YOUR-TUNNEL-UUID
credentials-file: C:/Users/YOU/.cloudflared/YOUR-TUNNEL-UUID.json
ingress:
  - hostname: slack-callback.example.com
    path: ^/api/slack/interactions$
    service: http://127.0.0.1:8765
  - service: http_status:404
```

Run `cloudflared tunnel --config C:\path\to\sentinel-slack.yml run sentinel-slack`. Register `https://slack-callback.example.com/api/slack/interactions` with Slack, using your real host. If using the CI relay, change the path rule to `^/api/slack/(interactions|relay)$`. Keep the other API routes private. The tunnel exposes the signed callback only, or the signed callback and the separately authenticated relay. This fits the existing FastAPI service and requires no Socket Mode process. [Cloudflare tunnel ingress rules](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/) · [Slack request signature verification](https://docs.slack.dev/authentication/verifying-requests-from-slack/)

## GitHub Actions delivery

The bundled [.github/workflows/sentinel-slack.yml](../.github/workflows/sentinel-slack.yml) runs from the default branch after the existing SentinelPR workflow. Set repository variable `SENTINEL_SLACK_RELAY_URL` to `https://<callback-host>/api/slack/relay`. Create the GitHub environment `sentinel-slack`, restrict it to the protected default branch, and add `SLACK_RELAY_TOKEN` as an **environment secret**. Match its value to the API's repo-scoped relay secret, or to the global relay token on a single-repository server. The relay reads only the specific report artifact from the original run and matches repo, PR number and head SHA to GitHub's workflow-run event. It checks out trusted workflow code, downloads the artifact outside that checkout, and passes the file only as data. Slack and GitHub App credentials stay on the server.

For a consumer using the composite Action, upload its `report` output as `sentinel-run-<PR number>` with file name `run.json`. Use the equivalent trusted workflow in that repository and set its trigger to the consumer's workflow name. GitHub sometimes omits PR association in workflow-run events, including some fork runs. The relay skips an event without exactly one identified PR. Such a PR can be analyzed and notified through the authenticated service. Re-running the delivery for the same run ID does not create another message. [GitHub workflow-run event](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)

## Test the workflow

1. Use a repository and private Slack channel you control. Invite the app, grant the GitHub App access, confirm the reviewers' Slack IDs, and restart the API.
2. Open [local Settings](http://127.0.0.1:5173/#/settings), save the dashboard's API token, then use **Check connection**. Select **Send test notification** to send a message containing no PR evidence.
3. Connect the approved Python repo in **Repositories** and analyze an open PR. External-repository analysis needs Docker. The Slack message appears automatically after the report completes. With the CLI, export the Slack settings in that shell and run `python -m sentinel.run --base <base-ref> --head <committed-pr-ref> --repository <owner/repo> --pr <number>`. Use the same Slack state directory for the callback service.
4. Click **Approve PR** and submit the optional comment. On a separate completed run, choose **Reject / Request Changes** and submit a reason. Check the original Slack message, the run's **Slack review** section, and the integration-attributed GitHub review.
5. Push a new commit to your test PR and try reviewing the earlier run. A mismatched live head prevents a GitHub review. Analyze the new commit to get a new Slack message. The earlier message becomes outdated when the changed head is confirmed by the callback or a later notification.

The review store claims each run and message before side effects. A confirmed Slack failure can be retried with **Resend to Slack**. A sent message is refreshed in place. An ambiguous network delivery is recorded as unconfirmed and is not reposted automatically. Reconcile unconfirmed or interrupted requests in Slack and GitHub before any manual recovery. Exactly one reviewer decision is recorded per run. If GitHub rejects synchronization, the reviewer decision is retained and the GitHub failure is shown separately.

### Example Block Kit payload excerpt

The production payload includes additional sections for author and timestamps, rationale, override, evidence, test counts, coverage, mutation, recommended action and synchronization. The following is an illustrative redacted excerpt for `chat.postMessage`.

```json
{
  "channel": "C0123456789",
  "text": "SentinelPR BLOCK_LOW · acme/payments PR #17 · Pending",
  "unfurl_links": false,
  "unfurl_media": false,
  "attachments": [{
    "color": "#DA3633",
    "fallback": "SentinelPR BLOCK_LOW · acme/payments PR #17 · Pending",
    "blocks": [
      {"type": "header", "text": {"type": "plain_text", "text": "🔴 SentinelPR · BLOCK_LOW"}},
      {"type": "section", "fields": [
        {"type": "plain_text", "text": "Risk score\n0.8895"},
        {"type": "plain_text", "text": "Changed-line coverage\n100.0%"},
        {"type": "plain_text", "text": "Mutation score\n25.0%"}
      ]},
      {"type": "section", "text": {"type": "plain_text", "text": "Decision rationale\nRisk crosses the block threshold."}},
      {"type": "section", "text": {"type": "plain_text", "text": "Review status\nPending"}},
      {"type": "actions", "elements": [
        {"type": "button", "action_id": "sentinel_approve", "value": "run-20261124000000-abcdef", "style": "primary", "text": {"type": "plain_text", "text": "Approve PR"}},
        {"type": "button", "action_id": "sentinel_request_changes", "value": "run-20261124000000-abcdef", "style": "danger", "text": {"type": "plain_text", "text": "Reject / Request Changes"}},
        {"type": "button", "action_id": "sentinel_view_pr", "url": "https://github.com/acme/payments/pull/17", "text": {"type": "plain_text", "text": "View GitHub PR"}}
      ]}
    ]
  }]
}
```

### Automated verification

`python -m pytest tests/test_severity.py tests/test_slack.py` uses mock HTTP transports for Slack and GitHub. It tests cutoff math, legacy reports, notification formatting and delivery, signed callbacks, replay, authorization, stale commits, App review synchronization and the Slack message update. An actual Slack workspace and a GitHub App installation are needed for the manual steps above.

## Implementation files

| Area | New files | Modified files |
| --- | --- | --- |
| Severity and report surfaces | `sentinel/risk/severity.py` | `sentinel/run.py`, `sentinel/api.py`, `sentinel/publish/render.py`, `sentinel/service/api.py`, `sentinel/service/runs.py`, `action.yml` |
| Slack and CI | `sentinel/publish/slack.py`, `sentinel/publish/slack_relay.py`, `sentinel/service/slack.py`, `.github/workflows/sentinel-slack.yml`, `deploy/slack-app-manifest.yml`, `docs/slack.md` | `.env.example`, `.gitignore` |
| Dashboard | `dashboard/src/features/run/SlackReview.jsx` | `dashboard/src/ui.jsx`, `dashboard/src/components/PullTable.jsx`, `dashboard/src/features/run/tabs.jsx`, and `Compare.jsx`, `Overview.jsx`, `PRWorkspace.jsx`, `ReleaseHistory.jsx`, `RepoDetail.jsx`, `RiskIntelligence.jsx`, `Runs.jsx`, `Settings.jsx` under `dashboard/src/pages/product/` |
| Tests | `tests/test_severity.py`, `tests/test_slack.py` | |
