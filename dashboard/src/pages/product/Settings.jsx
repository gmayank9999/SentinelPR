import { useState } from "react";
import { Plug } from "lucide-react";
import { api, apiBase, apiToken, saveConnection, useApi } from "../../lib/api.js";
import { Async, Banner, Button, Card, CopyButton, KV, PageHeader, useToast } from "../../ui.jsx";

const WORKFLOW = `# .github/workflows/sentinelpr.yml
on: pull_request
permissions: { contents: read, pull-requests: write, checks: write }
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: gmayank9999/SentinelPR@v1
        with:
          groq_api_key: \${{ secrets.GROQ_API_KEY }}      # optional
          gemini_api_key: \${{ secrets.GEMINI_API_KEY }}  # optional`;

function AppPanel() {
  const app = useApi("/github/app");
  return (
    <Card title="GitHub App (needed for Check Runs)">
      <Async state={app}>
        {(d) => (
          <div className="space-y-3 text-sm">
            <p className="text-muted">GitHub only lets a <b className="text-ink">GitHub App</b> create Check Runs; a personal access token cannot, and fine-grained tokens have no Checks permission. Without an App, SentinelPR posts the PR comment and a
              commit status with your personal token (the status can be a required check in branch protection) and does not attempt a Check Run.</p>
            <KV rows={[["Configured", d.configured ? "yes" : "no"], d.app && ["App", `${d.app.name} (${d.app.slug})`], d.app && ["Checks permission", d.checks_write ? "Read and write" : "missing"]]} />
            {d.problems.length > 0 && <Banner tone="warn" title={d.configured ? "The App is not ready" : "No GitHub App configured"}>
              <ul className="list-disc space-y-1 pl-4">{d.problems.map((p) => <li key={p}>{p}</li>)}</ul></Banner>}
            <details className="rounded-md border border-line p-3">
              <summary className="cursor-pointer text-muted hover:text-ink">How to set one up (about 5 minutes)</summary>
              <ol className="mt-2 list-decimal space-y-1 pl-5 text-muted">
                <li>GitHub → Settings → Developer settings → GitHub Apps → <b className="text-ink">New GitHub App</b>.</li>
                <li>Name it (for example “SentinelPR Gate”), any Homepage URL, and <b className="text-ink">untick Webhook → Active</b>.</li>
                <li>Repository permissions: <b className="text-ink">Checks: Read and write</b> (Metadata: Read-only is added for you). Where can it be installed: Only on this account.</li>
                <li>Create the App, note its <b className="text-ink">Client ID</b>, then <b className="text-ink">Generate a private key</b> (a .pem file downloads). Keep it outside this repository.</li>
                <li><b className="text-ink">Install App</b> → your account → select the repository.</li>
                <li>Add to <span className="mono">.env</span>: <span className="mono">SENTINEL_GITHUB_APP_CLIENT_ID=…</span> and <span className="mono">SENTINEL_GITHUB_APP_PRIVATE_KEY_PATH=C:\path\to\key.pem</span> (a path, never the key itself), then restart the server.</li>
              </ol>
              <p className="mt-2 text-xs text-faint">Full guide: docs/github-app.md.</p>
            </details>
          </div>
        )}
      </Async>
    </Card>
  );
}

const STATE_TEXT = { ok: "Connected", not_configured: "Not configured", invalid_credentials: "Invalid credentials", rate_limited: "Rate limited", model_not_found: "Model not available",
  timeout: "Timed out", unreachable: "Unreachable", provider_error: "Provider error", probe_failed: "Completion failed" };

function LlmPanel() {
  const llm = useApi("/llm/status");
  const [results, setResults] = useState({});
  const [busy, setBusy] = useState(null);
  const test = async (provider, probe) => {
    setBusy(provider);
    try {
      const result = await api("/llm/check", { method: "POST", body: { provider, probe } });
      setResults((r) => ({ ...r, [provider]: result }));
    } catch (e) {
      setResults((r) => ({ ...r, [provider]: { state: "error", detail: e.message, ok: false } }));
    } finally {
      setBusy(null);
    }
  };
  return (
    <Card title="Language models">
      <Async state={llm}>
        {(d) => (
          <div className="space-y-4">
            <Banner title={d.mode === "llm-assisted" ? "LLM-assisted mode" : "Evidence-only mode"}>
              {d.mode === "llm-assisted" ? "Agents may ask the configured models for impact hypotheses and explanations." : "No provider is configured, so agents use evidence-only claims. Everything else works unchanged."} {d.decision_note}
            </Banner>
            <ul className="divide-y divide-line">
              {Object.entries(d.providers).map(([name, p]) => {
                const r = results[name];
                return (
                  <li key={name} className="py-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <span className="font-medium">{name}</span> <span className="mono text-faint">{p.model}</span>
                        <div className="text-xs text-muted">{p.configured ? "credentials found on the server" : p.instructions}</div>
                      </div>
                      <div className="flex gap-2">
                        <Button size="sm" loading={busy === name} disabled={!p.configured} onClick={() => test(name, false)}>Test connection</Button>
                        <Button size="sm" variant="ghost" disabled={!p.configured || busy === name} onClick={() => test(name, true)} title="Also sends a one-token completion">Probe</Button>
                      </div>
                    </div>
                    {r && (
                      <div role="status" className={`mt-2 rounded-md border px-3 py-2 text-sm ${r.ok ? "border-ok/30 text-ok" : "border-bad/30 text-bad"}`}>
                        <b>{STATE_TEXT[r.state] || r.state}</b>: <span className="text-muted">{r.detail}</span>
                        {r.latency_ms != null && <span className="mono ml-2 text-faint">{r.latency_ms} ms</span>}
                        {r.probe && <span className="mono ml-2 text-faint">probe {r.probe.latency_ms} ms, {r.probe.prompt_tokens + r.probe.completion_tokens} tokens</span>}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
            <p className="text-xs text-faint">Keys are read from the server's environment only (GROQ_API_KEY, GEMINI_API_KEY) and are never sent to this page or stored in the browser. Restart the server after changing them.</p>
          </div>
        )}
      </Async>
    </Card>
  );
}

export default function Settings() {
  const toast = useToast();
  const status = useApi("/service/status");
  const [base, setBase] = useState(apiBase());
  const [token, setToken] = useState(apiToken());
  const save = (e) => {
    e.preventDefault();
    saveConnection({ base: base.trim().replace(/\/$/, ""), token: token.trim() });
    toast.push("Saved. Reloading…", "success");
    setTimeout(() => window.location.reload(), 500);
  };
  const s = status.data;
  return (
    <div className="max-w-4xl">
      <PageHeader title="Integration Settings" subtitle="How this dashboard reaches the server, and how SentinelPR connects to GitHub and your CI." />
      <div className="space-y-6">
        <Card title="Server connection">
          <form onSubmit={save} className="grid gap-4 sm:grid-cols-2">
            <label className="text-sm">API address<input value={base} onChange={(e) => setBase(e.target.value)} className="mt-1 h-9 w-full rounded-md border border-line2 bg-bg px-3 outline-none focus:border-brand" /></label>
            <label className="text-sm">API token <span className="text-faint">(only if the server sets SENTINEL_API_TOKEN)</span>
              <input type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" className="mt-1 h-9 w-full rounded-md border border-line2 bg-bg px-3 outline-none focus:border-brand" /></label>
            <div className="sm:col-span-2"><Button variant="primary" type="submit" icon={Plug}>Save and reconnect</Button></div>
          </form>
          <p className="mt-3 text-xs text-faint">The token is kept in this browser only. The server only accepts requests from the dashboard's origin (SENTINEL_CORS_ORIGINS).</p>
        </Card>

        <Card title="GitHub access">
          <KV rows={[["Server token", s ? (s.github.authenticated ? "configured (GITHUB_TOKEN)" : "not configured") : "…"], ["Public repositories", "readable without a token (60 API requests/hour)"], ["Private repositories", s?.github.authenticated ? "readable if the token has access" : "need GITHUB_TOKEN on the server"],
            ["Sign in with GitHub", "not available in this version"]]} />
          <div className="mt-4"><Banner title="Set the token on the server, not in the browser">Start the server with <span className="mono">GITHUB_TOKEN=…</span> (a fine-grained token with read access to contents and pull requests). It is never sent to the browser, never put in clone URLs, and is redacted from logs and events.</Banner></div>
        </Card>

        <AppPanel />

        <LlmPanel />

        <Card title="Run SentinelPR on every pull request (GitHub Actions)" action={<CopyButton text={WORKFLOW} label="Copy workflow" />}>
          <p className="mb-3 text-sm text-muted">Posts the SentinelPR Gate check and a PR comment. Make the check required in branch protection so a BLOCK prevents merging. Results are not streamed to this dashboard; use this server to analyse pull requests interactively.</p>
          <pre className="codeblock">{WORKFLOW}</pre>
        </Card>

        <Card title="Command line">
          <pre className="codeblock">{`python -m sentinel.api                      # this server
python -m sentinel.run --base main --head my-branch
python demo/scenarios.py                    # create and analyse the demo PRs
python -m sentinel.canary.run               # canary rollout simulation`}</pre>
        </Card>
      </div>
    </div>
  );
}
