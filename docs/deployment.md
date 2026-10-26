# Canary deployments (staging)

A deployment rolls a **candidate commit** out against a **baseline commit**: 10% → 25% → 50% → 100% of traffic,
judging each step against SLOs and rolling back on the first breach. It runs on a *target*; nothing is
deployable until it is listed as one, and **production is never accepted** (a target whose `environment` is
`production` is refused).

## Targets

| target | what it is | verified here |
|---|---|---|
| `local-processes` | two uvicorn processes (baseline and candidate commits) behind the built-in weighted router | yes |
| `docker-staging` | an image is built from each commit and run as a locked-down container (read-only root, no capabilities, memory/CPU/pid limits, loopback ports) behind the built-in router; everything is removed afterwards | yes |
| your own | a staging environment you operate, declared in `sentinel.yaml` (below) | protocol exercised against a self-hosted stand-in; not against real infrastructure |

The built-ins deploy the bundled UniERP service (`.sentinel/uni-erp-repo`, from `python -m sentinel.data.index --seed-demo`).

### Connecting a staging environment you operate

```yaml
# sentinel.yaml
deploy:
  targets:
    my-staging:
      kind: external
      environment: staging              # local | staging; production is refused
      stable_url: https://stable.staging.example
      candidate_url: https://candidate.staging.example
      router_url: https://router.staging.example
      router_token_env: MY_ROUTER_TOKEN # name of an environment variable; the token itself is never stored in config
      stable_version: stable-abc1234    # what each application's X-UniERP-Version header must report
      candidate_version: candidate-def5678
```

SentinelPR does not build or start anything on an external target: you deploy the candidate, then it drives and
measures. The router must implement:

* `PUT /__canary/weight` with `{"weight": 0.25}` sets the share of traffic sent to the candidate;
* `GET /__canary/status` returns `{"weight": 0.25, ...}` (read back to prove the change took effect).

Both applications must expose `GET /health`, `GET /metrics.json` (cumulative `requests`, `errors`, `latency_ms.{p95,mean}`)
and put their version on every response in `X-UniERP-Version`. A target without a `router_url` cannot split traffic by
weight, and SentinelPR says so instead of offering a rollout.

## What "success" means

A deployment is never marked successful because an API call returned 200. It is judged from measurements:

1. **Weight read back.** After setting a weight, the router is asked what it holds; a mismatch fails the rollout and rolls back.
2. **Routing proven.** The share the candidate actually served is computed from the applications' *own request counters* and must match the
   weight within statistical tolerance. A candidate that gets no traffic fails the step.
3. **SLOs per step**, on that step's traffic only: candidate error rate (absolute and relative to baseline), p95 latency (ratio plus slack),
   and a minimum request count below which the step is inconclusive and extended. A breach is acted on immediately, not at the end of the window.
4. **Rollback proven.** After a breach, abort or routing failure, the weight is set to 0, read back, and 40 probe requests are sent; the version
   header each application puts on its response must show **zero** reached the candidate. If that cannot be shown the deployment is `failed` with
   "rollback could not be verified", not `rolled_back`.
5. **Promotion proven.** At 100% a probe must show every response came from the candidate.

"Promoted" means the candidate served 100% of *staging* traffic through every SLO window. It is not a production release.

## Controls, state and audit

Pause, resume, abort and roll back are available while a deployment is active. Abort and roll back perform the same verified rollback as an SLO
breach. State (`state.json`) and an append-only audit trail (`audit.jsonl`, every operator and controller action with timestamp and actor) persist
under `<workdir>/service/deployments/<id>/`. A deployment interrupted by a server restart is marked `failed`, never left looking alive. One rollout
runs at a time.

## Test faults

For validation you can inject a fault (`errors` or `slow`) into the candidate. The deployment is labelled as having one, in its record and the UI, so a
rollback it triggers is not mistaken for a real regression. Faults are refused outside `local` and `staging` targets.

## Recorded demo reports

`/canary/recorded` shows simulations recorded earlier for the evaluation. They are labelled historical demo data and are not connected to a deployment.
