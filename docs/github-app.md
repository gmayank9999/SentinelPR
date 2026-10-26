# Check Runs: setting up a GitHub App

## Why an App

GitHub's REST documentation for *Create a check run* says: **"To create a check run, you must use a GitHub App. OAuth apps and authenticated
users are not able to create them."** A personal access token, fine-grained or classic, is an authenticated *user*: it cannot create a Check Run,
and fine-grained tokens have no "Checks" permission to grant (that is why the option is missing from the token page). This is a GitHub rule,
not a SentinelPR limitation. (Inside a GitHub Actions workflow, the workflow's own `GITHUB_TOKEN` is an App token, which is why the Action
can create Check Runs without any setup.)

What each credential can do in SentinelPR:

| thing posted | credential | GitHub permission |
|---|---|---|
| PR comment | your personal token | Pull requests: Read and write (and Issues: Read and write) |
| **Commit status** `SentinelPR / gate` | your personal token | Commit statuses: Read and write |
| **Check Run** `SentinelPR Gate` | a GitHub App installation token | the App's Checks: Read and write |

**You do not need an App to gate merges.** A commit status can be marked required in branch protection exactly like a check
(Settings → Branches → rule → *Require status checks to pass* → pick `SentinelPR / gate`; it appears in the list after it has been posted once).
`success` is posted for PASS and CANARY (CANARY says so in the description) and `failure` for BLOCK. A Check Run adds richer output (inline
annotations, a summary page) and is the right choice if you want those.

Without an App, SentinelPR does **not** attempt a Check Run (a request that GitHub is documented to refuse is never sent); the Publish panel
and the stored record say "not attempted: needs a GitHub App" and keep that distinct from "GitHub refused".

## Setting one up (about 5 minutes)

1. GitHub → **Settings → Developer settings → GitHub Apps → New GitHub App**.
2. *GitHub App name*: for example `SentinelPR Gate` (must be unique). *Homepage URL*: anything (your repository's URL is fine).
3. **Webhook**: untick **Active**. SentinelPR polls; it does not need events.
4. **Repository permissions**: set **Checks → Read and write**. Leave everything else as it is (Metadata: Read-only is added automatically).
5. *Where can this GitHub App be installed?* **Only on this account**.
6. **Create GitHub App.** On the next page note the **Client ID** (starts with `Iv`).
7. Scroll to **Private keys → Generate a private key**. A `.pem` file downloads. Move it somewhere **outside this repository** (for example
   `C:\Users\you\secrets\sentinelpr-gate.pem`). Anyone with this file can act as the App.
8. In the left menu choose **Install App**, install it on your account, and select the repository (or repositories) to publish to.
9. Add to `.env` (a *path* to the key, never the key itself):

   ```
   SENTINEL_GITHUB_APP_CLIENT_ID=Iv1.your-client-id
   SENTINEL_GITHUB_APP_PRIVATE_KEY_PATH=C:\Users\you\secrets\sentinelpr-gate.pem
   ```

   Optional: `SENTINEL_GITHUB_APP_INSTALLATION_ID=<number>` skips looking the installation up (the number is in the installation's URL).
10. `pip install cryptography` if it is not installed (it signs the App's JWT), then restart the server.
11. Open **Integration Settings**: the GitHub App panel should show the App's name and *Checks permission: Read and write*. Open a run and
    **Publish to GitHub**: the Check Run row is now selectable.

## What SentinelPR does with the App

It follows GitHub's documented flow: sign a JWT (RS256, `iat` 60 s in the past, `exp` under 10 minutes, `iss` = your Client ID) with the private key;
find the installation on the repository; exchange the JWT for an installation access token **limited to that one repository and to
`checks: write`**; use it only for the Check Run call. The token lasts one hour and is cached in memory until five minutes before it expires.
The key, the JWT and the installation token are never logged, stored or returned by the API. Your personal token is never used for the Check Run
and the App token is never used for the comment or status.

## Troubleshooting

| the panel says | cause | fix |
|---|---|---|
| "GitHub rejected the App's credentials (401)" | wrong Client ID, a key that belongs to another App, or this computer's clock off by more than a minute | check the ID and key; sync the clock |
| "The GitHub App is not installed on this repository" | the App is not installed there | App settings → Install App → select the repository |
| "GitHub would not issue a Checks token (422)" / "App does not have Checks: Read and write" | the App lacks the permission, or the installation has not accepted it | App settings → Permissions & events → Checks → Read and write → Save; then accept the updated permissions on the installation (GitHub emails you / shows a banner) |
| "The file is not an unencrypted PEM RSA private key" | wrong file, or a passphrase-protected key | use the `.pem` GitHub generated |
| "'cryptography' package is not installed" | optional dependency missing | `pip install cryptography` |
