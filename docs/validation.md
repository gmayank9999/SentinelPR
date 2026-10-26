# Validation: what has actually been observed

This page separates what was **observed running** from what is only **covered by tests** or **not yet tested**.
The automated checklist is `python -m sentinel.validate` (it drives a running server and records run and
deployment ids); its latest output is [validation-latest.md](validation-latest.md). A stage is marked passed only
if it was observed; a stage that needs a credential or service the environment lacks is marked NOT TESTED with the
exact setup needed, never passed.

```bash
python -m sentinel.api &                     # the server
python -m sentinel.validate --public-pr more-itertools/more-itertools#1338 --out docs/validation-latest.md
```

## Results (2026-10-26, Windows 11, Docker Desktop 29.6.2, no model or GitHub credentials)

| # | Stage | Result | Evidence |
|---|---|---|---|
| 1 | Bundled demo repository, end to end | **Observed, passed** | `run-20261026044141-35a660`: BLOCK, risk 0.89 (a one-line change every existing test passes; 3 of 4 mutants survive). The event stream and stored report agree. |
| 2 | Real public Python PR, cloned from GitHub, analysed in the Docker sandbox | **Observed, passed** | `run-20261026044154-7aef16` (also `run-20261026033016-6935d7`): more-itertools PR #1338, head `af341af7fd`, dependencies prepared in a container, 619 tests run in a no-network container, 0 failed, CANARY at risk 0.63. Fork PR, so no cloud model. |
| 3 | Controlled defect, run in the sandbox and compared with the host | **Observed, passed** | The bundled demo PRs `demo/hidden` (BLOCK 0.8895, mutation 0.25) and `demo/broken` (BLOCK 0.998, failing test caught) give the same decisions and mutation scores in Docker as on the host. The defects live in the bundled repository, which this project owns; no third-party repository was modified. |
| 4 | Live LLM provider | **Partly observed: Groq connectivity only** (2026-10-26, later) | Groq accepted the key and offers the configured model `openai/gpt-oss-120b` (model listing, no tokens spent). No analysis has yet been run with a model, so claim generation, fallback and token accounting against a real provider are still unobserved. Original status: | No `GROQ_API_KEY`/`GEMINI_API_KEY`/Ollama here. The health checks, fallback, usage display and "a lying model cannot change the verdict" are covered by tests with simulated providers, which prove the handling, not that a real provider works. |
| 5 | Staging canary, real baseline and candidate | **Observed, passed** | `dep-20261026044316-c3e51b` (local processes): 10→25→50→100%, each step's candidate share matched the weight (9.9%, 24.9%, 50.0%, 100%) from the applications' own counters; promotion verified. |
| 6 | Controlled SLO breach and rollback | **Observed, passed** | `dep-20261026044352-9bbc57` (local, injected 500s) and `dep-20261026044405-f0837e` (Docker containers built from the two commits, injected latency, p95 504 ms vs 3 ms). Both detected the breach on the first step (about 9% of traffic exposed) and rolled back; the router read 0% and 0 of 40 probes reached the candidate. An operator abort (`dep-20261026035842-83593f`) takes the same verified rollback path. All containers and images were removed. |
| 7 | GitHub PR comment, commit status and Check Run | **Comment observed; Check Run needs a GitHub App (not configured); commit status not yet observed** | In `gmayank9999/sentinelpr-integration-lab` PR #2 (commit `a390d80`) the PR comment was posted (comment id 6409486038, recorded in the run's `publish_history`). The Check Run was refused with `403 Resource not accessible by personal access token`. GitHub's documentation states a Check Run can only be created by a GitHub App, and the token page offers no Checks permission, so no permission change can fix this for a personal token. (An intermediate version of this page wrongly said it could be fixed by granting Checks; corrected.) SentinelPR now posts a commit status with the personal token (needs Commit statuses: Read and write) and creates Check Runs only through a GitHub App ([github-app.md](github-app.md)); the App JWT/installation-token flow is covered by tests with a locally generated key against a mocked GitHub, not against a real App. To verify: publish with the status ticked, then follow github-app.md and publish again with the Check Run ticked. |
| 8 | A real staging environment you operate | **Not tested** | The external-target protocol (router admin API, version headers, application counters) is exercised against a self-hosted stand-in in `tests/test_deploy.py`, not against real infrastructure. |
| 9 | Private repositories | **Not tested** | Needs a token with access; the token path (environment only, never argv or logs) is covered by tests. |

## Bugs the validation found and fixed

* An operator abort reported `rolled_back` while the router still read 25%: aborts now run the same verified rollback as a breach.
* A pre-traffic check read the candidate's version from a faulty endpoint and failed randomly: health uses `/health`, identity tolerates a faulty endpoint.
* On Windows, a thread reading `state.json` made the writer's `os.replace` raise and stranded a deployment as "rolling out": state files now use a lock plus retry (`sentinel/service/atomic.py`); the run manager had the same flaw.
* The GitHub token was passed to `git` on the command line; it now travels in the environment.
* A summary tile credited "100% of mutants killed" when no mutants had been generated; the UI now says the model treats a missing score as 100%.

## Not yet verified anywhere

Running the sandbox on Linux/macOS (file-ownership handling is coded but was only exercised on Windows), a hardened runtime
(gVisor/Kata/rootless), very large repositories, languages other than Python, and anything involving a real model.
