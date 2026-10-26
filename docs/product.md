# The SentinelPR application

A local web application on top of the pipeline: connect a repository, pick a pull request, start an
analysis, watch the pipeline execute stage by stage, then inspect the evidence behind the decision.

```bash
python -m sentinel.data.index --seed-demo      # once: builds the bundled UniERP demo
python demo/scenarios.py                       # optional: creates and analyses the demo pull requests
python -m sentinel.api                         # the server, http://127.0.0.1:8765
cd dashboard && npm install && npm run dev     # the UI,     http://localhost:5173
```

The research pages (Evaluation Lab, Guardrails, Cost & Latency, Canary) read static benchmark results;
generate them with `python -m sentinel.publish.dashboard_data`.

## What you can do

| | |
|---|---|
| **Repositories** | Paste a GitHub URL; SentinelPR checks access and says what it can verify (Python with pytest only). The bundled UniERP demo is always there. |
| **Pull Requests** | Open PRs across repositories with the latest decision for each and an *Analyse* action. |
| **PR workspace** | Decision, risk against the thresholds, findings, next actions; tabs for Evidence, Diff & Impact, Execution Trace, Risk Analysis, History. |
| **Product View / Live Execution** | The toggle in the top bar. Two presentations of the *same* run: a decision-oriented summary, or the pipeline graph with a stage inspector and the full event timeline. |
| **Analysis Runs / Compare** | Every analysis is kept. Re-analysing adds a run; two runs can be compared (decision, claims, tests, features, configuration). |
| **Risk Intelligence** | The model card (read from the model file and committed results) and where recent PRs fall. |

## Live Execution

A run produces an ordered log of events (`sentinelpr.event/1`, documented in
`sentinel/service/events.py`): `stage_started`, `stage_completed`, `stage_failed`, `stage_skipped`,
`substep` (test execution, coverage, each mutant), `llm_call`, `log`, and run lifecycle events. The
pipeline reports them as it executes, the server stores them per run
(`.sentinel/service/runs/<run_id>/events.jsonl`) and streams them over Server-Sent Events, resuming from
the last event after a dropped connection. The UI is driven only by these events: nothing advances on a
timer. The three agents run concurrently and are shown as independent branches.

The stage graph (`sentinel/orchestrator/observe.py`) is served by the API and a test checks it against
the compiled LangGraph, so the picture cannot drift from the pipeline.

Metrics that were not measured display "Not measured". Runs made with the command line before live
events existed are listed as imported; their timeline is rebuilt from the recorded trace and labelled
as reconstructed (sub-steps such as individual mutants were not recorded for them).

## API

| route | |
|---|---|
| `GET /api/service/status`, `/api/service/pipeline` | health checks, queue, stage graph |
| `GET/POST /api/repos`, `POST /api/repos/validate`, `GET/DELETE /api/repos/{id}`, `POST /api/repos/{id}/trust` | connect and manage repositories |
| `GET /api/repos/{id}/pulls[/{ref}]`, `GET /api/pulls`, `GET /api/overview`, `GET /api/activity` | pull requests and summaries |
| `POST /api/analyses`, `GET /api/analyses[/{id}]`, `POST /api/analyses/{id}/cancel`, `GET /api/analyses/compare?a=&b=` | runs |
| `GET /api/analyses/{id}/events?after=`, `GET /api/analyses/{id}/stream` | stored events / live SSE |
| `GET /api/model` | the risk model card |
| `GET /api/llm/status`, `POST /api/llm/check` | provider configuration (never keys) and live connectivity / model checks |
| `GET /api/deployments[/targets]`, `POST /api/deployments`, `GET /api/deployments/{id}`, `POST /api/deployments/{id}/{pause,resume,abort,rollback}` | staging canary deployments |

Publishing is an action on a finished run: `GET /api/analyses/{id}/publish` lists what blocks it (each with its fix) and names the exact repository, PR and commit; `POST /api/analyses/{id}/publish` (body: `force`, and `targets` to choose among `comment`, `status`, `check_run`) posts the PR comment and a commit status with your personal token and, only if a GitHub App is configured, a Check Run with an installation token. Each is reported separately: *posted*, *refused by GitHub* (with its message and the fix) or *not attempted* (a Check Run without an App; GitHub does not let a personal token create one, see [github-app.md](github-app.md)). Nothing is claimed unless GitHub returned its id. It needs `SENTINEL_ALLOW_PUBLISH=1` and a token with Pull requests and Commit statuses write access, refuses a second publish unless `force`, and ignores concurrent duplicate requests.

Analyses are queued; up to `service.max_concurrent_runs` (default 2) execute at once and two analyses of
the same repository never overlap. Cancelling stops a run before its next stage; a stage that is already
executing tests finishes first.

## Security, sandbox and what is verified

Repository code (tests, mutants, dependencies) runs in a Docker sandbox with no network, a read-only system, dropped
capabilities and CPU, memory, process and time limits; the host is never used unless Docker is down *and* you explicitly
trust the repository. Credentials stay on the server. Model output cannot change a verdict. Publishing to GitHub and
deployments are opt-in. Details, tests and residual risks: [security.md](security.md). Staging canary deployments:
[deployment.md](deployment.md). What has actually been observed end to end: [validation.md](validation.md).

## Limitations

* Python projects with pytest only. Declared test dependencies are installed in a sandbox (`requirements*.txt`, `pyproject.toml`);
  projects that need system libraries, services or unusual build steps will report a collection error.
* The risk model is hand-weighted with a fitted calibration, calibrated on UniERP; on other repositories read a decision as
  a prompt for review. See the model card in the UI and [evaluation.md](evaluation.md).
* Docker is a container boundary, not a VM boundary ([security.md](security.md)).
* No accounts, GitHub sign-in or GitHub App; GitHub is read with a server-side token.
* Canary deployments target staging only (never production) and the built-in targets deploy the bundled demo service.
