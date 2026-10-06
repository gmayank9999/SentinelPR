# Setup

## Requirements

* Python 3.11, Git, Node 20+ (dashboard only), Docker (optional: compose demo, Zoekt)
* Optional: [Ollama](https://ollama.com) with `qwen2.5-coder:1.5b` (and `nomic-embed-text` for
  Ollama embeddings); free API keys for [Groq](https://console.groq.com) and
  [Google AI Studio](https://aistudio.google.com)

SentinelPR runs end-to-end **without any LLM**: agents fall back to evidence-only claims, the
Q&A bot answers extractively and the risk model does not need a model rating.

## Install

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r sentinel/requirements.txt
pip install -r sentinel/requirements-extra.txt   # optional: Chroma, sentence-transformers, LightGBM, SHAP, RAGAS, Locust
```

LLM providers are picked up from the environment:

```bash
export GROQ_API_KEY=...        # hard tasks
export GEMINI_API_KEY=...      # medium tasks
ollama pull qwen2.5-coder:1.5b # easy tasks, and fork PRs (no secrets)
```

Model ids are set under `llm.providers` in `sentinel/config.yaml` (Groq: `openai/gpt-oss-120b`;
free-tier catalogues change, so check `https://api.groq.com/openai/v1/models` if calls fail).
On a rate limit the client tries the next provider in the tier; if every provider is limited it
waits up to `llm.max_wait_s` (90 s) before the agents fall back to their evidence-only path.
Network errors are retried the same way. `GROQ_API_KEY` may hold several comma-separated keys
from separate accounts; each is used in turn as the previous one reaches its daily quota.

## First index

```bash
python -m sentinel.data.index --seed-demo
```

This rebuilds the UniERP demo's development history (63 commits, 20 issues, 32 PRs), mines
it, runs SZZ, builds the per-test coverage map and the retrieval index under `.sentinel/`.

## Analyse a change

```bash
git checkout -b demo/change
# edit demo/uni-erp/...
python -m sentinel.run --base main                      # working tree vs main
python -m sentinel.run --base main --head demo/change   # two revisions
```

The report is printed as a summary line and written to `.sentinel/runs/<run_id>.json`.

## Q&A, API and dashboard

```bash
python -m sentinel.api                              # http://127.0.0.1:8765
python -m sentinel.publish.dashboard_data           # collect runs/results into dashboard/public/data
cd dashboard && npm install && npm run dev          # http://localhost:5173
```

## Canary

```bash
python -m sentinel.canary.run                                                   # healthy: promoted
python -m sentinel.canary.run --canary-env UNIERP_FAULT=slow --canary-env UNIERP_FAULT_RATE=0.3   # rolled back
docker compose up unierp-stable unierp-canary proxy                             # nginx split_clients variant
```

## Benchmark

```bash
python -m bench.run_matrix --repo uni-erp --workers 4 --resume
python -m bench.analysis.report                     # results.json, report.md, trained model
python -m bench.qa.evaluate                         # Q&A conditions (add --llm with providers)
```

LLM conditions reuse the labelled PRs of the evidence-only run, so both are compared on exactly
the same PRs (one worker keeps a free-tier key under its tokens-per-minute limit):

```bash
python -m bench.run_matrix --repo uni-erp --llm --workers 1 --labels bench/out/uni-erp/prs.jsonl     --bases "Release 1.3.0" "Release 1.4.0" --out bench/out/uni-erp-llm
python -m bench.analysis.llm --base bench/out/uni-erp --llm bench/out/uni-erp-llm   # llm.md, llm.json
python -m bench.qa.evaluate --llm                   # adds the no-RAG condition
python -m bench.prompt_ab --limit 20                # impact prompt v1 / v2 / v3
```

The two open-source repositories are cloned on first use (into `.sentinel/repos/`). Their
tests import the cloned sources, but the environment needs their test dependencies, and
humanize's releases read their version from installed metadata:

```bash
pip install humanize freezegun                                       # humanize
pip install odfpy openpyxl pandas pyyaml tabulate xlrd xlwt markuppy # tablib
python -m bench.run_matrix --repo humanize --workers 4
python -m bench.analysis.report --results bench/out/humanize/results.jsonl --no-model
```

`--no-model` keeps the shipped risk model (trained on UniERP) untouched; the report then
also shows how that model does on a repository it never saw.

## Code search backends

Local scanning is the default. To use Zoekt, index the repository and point SentinelPR at
the web server:

```bash
docker compose --profile search up zoekt
export SENTINEL__RETRIEVAL__ZOEKT_URL=http://localhost:6070
```

For public repositories, `SENTINEL__RETRIEVAL__SOURCEGRAPH_URL=https://sourcegraph.com` uses
Sourcegraph's GraphQL search (set `SRC_ACCESS_TOKEN` for a private instance).

## Using SentinelPR in another repository

```yaml
# .github/workflows/sentinelpr.yml
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
          groq_api_key: ${{ secrets.GROQ_API_KEY }}
          gemini_api_key: ${{ secrets.GEMINI_API_KEY }}
          project-requirements: requirements.txt
```

with a `sentinel.yaml` at the repository root:

```yaml
project:
  root: .
  package: mypackage
  tests: tests
```

Make **SentinelPR Gate** a required status check in branch protection so a BLOCK prevents
merging.

## Configuration

`sentinel/config.yaml` documents every setting. Override per repository in `sentinel.yaml`, or
per run with environment variables such as `SENTINEL__RETRIEVAL__TOP_K=5`.
