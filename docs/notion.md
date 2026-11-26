# Notion Integration Guide

SentinelPR can automatically log every completed PR analysis to a Notion database, creating a structured page with properties and full verification evidence.

## 1. Create a Notion Integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) and click **New integration**.
2. Name it **SentinelPR**, select your workspace, and copy the **Internal Integration Secret** (`secret_...`).

## 2. Create the Database

Create a full-page database in Notion with these exact property names and types:

| Property Name | Type | Notes |
|---|---|---|
| `Name` | Title | Default title column |
| `Decision` | Select | Full 9-category verdict (`PASS_LOW`, `BLOCK_HIGH`, etc.) |
| `Base Decision` | Select | `PASS`, `CANARY`, or `BLOCK` |
| `Risk Level` | Select | `LOW`, `MEDIUM`, `HIGH`, or `STANDARD` |
| `Risk Score` | Number | 0–100 percentage |
| `Repository` | Rich text | `owner/repo` |
| `PR Number` | Number | Pull request number |
| `Author` | Rich text | PR author |
| `Status` | Select | `analysing`, `completed`, `failed` |
| `Run ID` | Rich text | SentinelPR run ID |
| `Analysed At` | Date | Timestamp |
| `Tests Passed` | Number | Passing test count |
| `Tests Failed` | Number | New failing test count |
| `Coverage` | Number | Changed-line coverage % |
| `Mutation Score` | Number | Changed-line mutation score % |
| `Elapsed Seconds` | Number | Analysis runtime in seconds |

## 3. Share Database with Integration

Open your Notion database → click `•••` (top right) → **Connections** → add **SentinelPR**.

Copy the **Database ID** from the URL:
`https://www.notion.so/<workspace>/<DATABASE_ID>?v=...`

## 4. Environment Variables

Add to `.env`:

```env
NOTION_API_KEY=secret_...
NOTION_DATABASE_ID=...
SENTINEL_DASHBOARD_URL=http://localhost:5173
```

## 5. Troubleshooting

- **404 / object_not_found**: Make sure you shared the database with the **SentinelPR** integration under `••• → Connections`.
- **400 / validation_error**: Check that property names and types match the table above (case-sensitive).
- Notion errors are logged at `WARNING` level and never break an analysis run.
