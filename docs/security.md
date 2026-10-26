# Security model

SentinelPR analyses code it did not write, so the design assumes **the repository, the pull request text,
the pull request's tests, its dependencies and any model output are hostile**.

## Execution sandbox

For a GitHub repository, every execution of repository code runs in Docker (`sentinel/verify/sandbox.py`):

| control | how |
|---|---|
| no network | `--network none` for test runs and mutants |
| no privileges | `--cap-drop ALL`, `--security-opt no-new-privileges`, non-root user |
| read-only system | `--read-only` root filesystem; writable `/tmp` is a size-limited tmpfs |
| limits | CPU (`--cpus`), memory (`--memory`, no swap), process count (`--pids-limit`), file size (`ulimit fsize`), wall-clock timeout that kills the container |
| minimal mounts | a disposable *copy* of the project, a scratch directory, the dependency directory (read-only), and only SentinelPR's pytest plugin (read-only). Never the host checkout, Docker socket, Git credentials, SSH keys, home directory or environment |
| no inherited secrets | only variables SentinelPR names are passed; `GITHUB_TOKEN`, model keys and the API token do not reach the container (tested) |
| cleanup | workspace copies are removed after each run; timed-out and cancelled jobs have their containers killed; orphans are swept; a run's containers carry a label so *Cancel* stops them |
| no silent fallback | if Docker is unavailable the run fails with `sandbox_unavailable`. The only way to run a repository on the host is for the operator to trust it explicitly, and the UI and the run record say so |

Dependencies are installed by a **separate** container that has network access but sees only the project's
dependency manifests (`requirements*.txt`, `pyproject.toml`), never its source or any secret. The result is
mounted read-only into test runs.

`tests/test_sandbox.py` attacks the sandbox from inside (network, host paths, secrets in the environment,
writes outside the workspace, fork bombs, memory hogs, infinite loops, leftover data between runs, the
no-fallback guarantee). They need a running Docker engine and are skipped, not passed, without one.

### Residual risks, honestly

* **Containers are not virtual machines.** A kernel or Docker escape would defeat this. For genuinely
  hostile code use a dedicated host, rootless Docker, gVisor or Kata (`--runtime`), none of which are
  configured here.
* **Dependency installation has network access** and can run a package's build scripts inside that container.
  A malicious dependency could exfiltrate only what the container holds (manifests and nothing else), but it
  can reach the internet and write to the cached dependency directory that later runs read.
* **Resource limits are per container**, not per user: many concurrent runs can still exhaust the host.
* **Docker Desktop on Windows** runs containers in a VM; its file-sharing layer is part of the trust boundary.
* **Host mode is unsandboxed.** The bundled demo runs this way (it is part of this repository), and any
  repository the operator explicitly trusts when Docker is down.
* A hostile test suite can make the *verdict* unreliable (for instance by hanging or failing on purpose). It
  cannot make the system claim safety it did not measure, but a BLOCK from a sabotaged suite is possible.

## Input handling

* Repository URLs must be `github.com/<owner>/<name>`; every other scheme, host, port, userinfo, query,
  fragment, IP literal, `file:`/`ssh:`/`git:` URL and look-alike host is rejected (`tests/test_github_security.py`).
* Branch names, run ids, deployment ids and repository ids are validated before they reach git, a path or a URL;
  paths are resolved and checked to lie under the service directory.
* `git` is invoked with an argument list (no shell), HTTPS only, hooks disabled, submodules off, symlinks off.
* The GitHub API client never follows redirects, retries only transient failures, and reports rate limits with their reset time.
* The analysed repository's own `sentinel.yaml` is ignored and the risk model always comes from SentinelPR, so a
  pull request cannot change how it is scored.

## Credentials

* `GITHUB_TOKEN`, `GROQ_API_KEY`, `GEMINI_API_KEY` and `SENTINEL_API_TOKEN` are read from the **server
  environment** only. They are never returned by the API, never stored in the browser (except the dashboard's
  own API token, if you set one), never put on a command line or in a clone URL (git receives the GitHub
  token through `GIT_CONFIG_*` environment variables), and secret patterns are redacted from events, stored reports and error messages.
* GitHub permissions, as a fine-grained token: **Metadata: read**, **Contents: read**, **Pull requests: read** to analyse. To post the PR
  comment add **Pull requests: Read and write** (and **Issues: Read and write**, which GitHub uses for PR comments). To post the commit status
  add **Commit statuses: Read and write**.
* **Check Runs cannot be created with a personal access token.** GitHub's documentation for *Create a check run* states that you must use a
  GitHub App ("OAuth apps and authenticated users are not able to create them"), and fine-grained tokens have no Checks permission. SentinelPR
  therefore creates Check Runs only with a **GitHub App installation token** and does not attempt one without an App. The token is requested per
  publish, limited to the one repository and `checks: write`, valid for an hour, held in memory only, and never logged or returned. The App's
  private key is referenced by file path (`SENTINEL_GITHUB_APP_PRIVATE_KEY_PATH`), never stored in `.env` or the repository. Setup: [github-app.md](github-app.md).
  (An earlier version of this document said a Checks permission could be added to a personal token; that was wrong.)

## Model output

Models see PR text only after secrets are redacted and injection phrases neutralised, and their output is
treated as untrusted: claims must cite evidence the system supplied, are then checked by execution, and text
is filtered to cited sentences. The decision is computed from execution evidence, the calibrated risk model and
the policy; a deterministic output guard recomputes it. `tests/test_pipeline.py` runs a model that lies, invents
evidence and tells the gate to approve a PR whose tests fail, and asserts the verdict is still BLOCK.
Fork pull requests never reach a cloud model.

## Access to the application

Single-user, local-first. No accounts and no GitHub sign-in. The API accepts browser requests only from the
dashboard's origin (`SENTINEL_CORS_ORIGINS`), checks a bearer token when `SENTINEL_API_TOKEN` is set, and
should be bound to localhost. Publishing to GitHub and deployments are off or explicit by default (see below).

## Side effects

Nothing writes outside this machine unless asked: comments and Check Runs only when the server sets
`SENTINEL_ALLOW_PUBLISH=1` *and* the run opted in; deployments only to configured staging targets with an
explicit confirmation, never production (`docs/deployment.md`).
