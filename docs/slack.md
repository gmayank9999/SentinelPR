# Slack Integration Guide

SentinelPR can post rich Block Kit summaries of every PR analysis directly to a Slack channel and respond to interactive button clicks (`✅ Approve`, `⛔ Request Changes`, `🔄 Rerun`, `❓ Ask`).

## 1. Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App → From scratch**.
2. Name it **SentinelPR** and pick your workspace.

## 2. Configure Bot Token Scopes

Under **OAuth & Permissions → Bot Token Scopes**, add:
- `chat:write` — post analysis cards to channels
- `chat:write.public` — post to public channels without inviting the bot first

Click **Install to Workspace** and copy the **Bot User OAuth Token** (`xoxb-...`).

## 3. Configure Interactive Components (Optional)

To handle button clicks from Slack:
1. Under **Interactivity & Shortcuts**, toggle **Interactivity** ON.
2. Set **Request URL** to `https://<your-public-host>/api/slack/interactive`.
   - For local development, expose port `8765` with `ngrok http 8765` and use `https://<ngrok-id>.ngrok.io/api/slack/interactive`.
3. Copy the **Signing Secret** from **Basic Information → App Credentials**.

## 4. Environment Variables

Add to `.env`:

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_CHANNEL=#sentinel-pr
SENTINEL_DASHBOARD_URL=http://localhost:5173
```

## 5. Test the Integration

```powershell
.venv\Scripts\python -c "from sentinel.publish.slack import post_notification; print(post_notification('Hello from SentinelPR!', level='info'))"
```

Once configured, every completed PR analysis automatically posts a Block Kit card to `SLACK_CHANNEL` with:
- 9-category verdict (`PASS_LOW` .. `BLOCK_HIGH`), risk score, and reason
- Test pass/fail counts, changed-line coverage, and mutation score
- Top 3 risk contributors and surviving mutants
- Interactive buttons: **Approve**, **Request Changes**, **Rerun**, **Ask**, and **View Full Report**
