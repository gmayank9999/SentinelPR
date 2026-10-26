# SentinelPR validation — 2026-10-26 04:44 UTC

Server: http://127.0.0.1:8765. **6 passed, 0 failed, 2 not tested.**

| Stage | Result | What was tested | Evidence |
|---|---|---|---|
| Environment | **PASSED** | server reachable; capabilities reported | version 0.1.0 |
| Local demo repository | **PASSED** | analyse the demo PR demo/hidden end to end, host execution | run run-20261026044141-35a660: BLOCK (risk 0.8895), 41 events, stream and report agree: True |
| Public GitHub PR | **PASSED** | analyse a real public pull request (more-itertools/more-itertools#1338) in the Docker sandbox | run run-20261026044154-7aef16: head af341af7fd, 619 tests passed / 0 failed, decision CANARY (risk 0.63), execution=docker |
| Live LLM provider | **NOT TESTED** | real provider call | no provider configured: set GROQ_API_KEY or GEMINI_API_KEY (or run Ollama) and restart the server |
| Staging canary: healthy rollout | **PASSED** | target local-processes | deployment dep-20261026044316-c3e51b: promoted, promotion verified=True, exposed 0.4668, no breach |
| Staging canary: SLO breach and rollback | **PASSED** | target local-processes, injected errors fault (validation only) | deployment dep-20261026044352-9bbc57: rolled_back, rollback verified=True, exposed 0.0967, detection 8.47s |
| Staging canary on Docker containers: SLO breach and rollback | **PASSED** | target docker-staging, injected slow fault (validation only) | deployment dep-20261026044405-f0837e: rolled_back, rollback verified=True, exposed 0.0962, detection 13.95s |
| GitHub Check Run / PR comment | **NOT TESTED** | publish to a pull request in a repository you own | disabled or no token: set SENTINEL_ALLOW_PUBLISH=1 and GITHUB_TOKEN, then analyse a PR in your own repository with 'Publish result to GitHub' ticked |

### Environment
- git: ok — C:\Program Files\Git\mingw64\bin\git.EXE
- github: NOT available — No GITHUB_TOKEN: public repositories only, 60 API requests per hour
- llm: NOT available — groq: not configured, gemini: not configured, ollama: not configured. Without a provider the agents use evidence-only claims; decisions never depend on LLM text.
- auth: NOT available — Open local single-user mode: no accounts, no token. Bind to localhost only.
- sandbox: ok — Docker engine 29.6.2. Repository tests and mutants run in containers with no network, a read-only root filesystem, dropped capabilities and CPU, memory, process and time limits.
- publish: NOT available — Disabled (default). Set SENTINEL_ALLOW_PUBLISH=1 and a token with pull-request write access to allow runs to publish.

### Staging canary: healthy rollout
- step_result: step 10%: candidate served 9.9% of 444 requests (44); healthy
- step_result: step 25%: candidate served 24.9% of 461 requests (115); healthy
- step_result: step 50%: candidate served 50.0% of 452 requests (226); healthy
- step_result: step 100%: candidate served 100.0% of 466 requests (466); healthy
- promotion_check: probe at 100%: {'candidate-9682c5f': 40}

### Staging canary: SLO breach and rollback
- step_result: step 10%: candidate served 9.7% of 269 requests (26); canary error rate 69.23% > 1.00%; canary error rate 69.2
- rollback_check: after rollback the router weight reads 0%; of 40 probe requests, 0 reached the candidate and 40 the baseline

### Staging canary on Docker containers: SLO breach and rollback
- step_result: step 10%: candidate served 9.6% of 208 requests (20); canary p95 504ms > 55ms (stable 3ms)
- rollback_check: after rollback the router weight reads 0%; of 40 probe requests, 0 reached the candidate and 40 the baseline
