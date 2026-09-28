# SentinelPR benchmark — uni-erp

_Generated 2026-09-28T15:57:10+00:00._

## Dataset

154 pull requests over 4 base revisions (Release 1.1.0, Release 1.2.0, Release 1.3.0, Release 1.4.0); 95 defective by execution.

| category | PRs | defective | visible tests pass |
|---|---|---|---|
| C1 | 24 | 0 | 24 |
| C2 | 28 | 28 | 0 |
| C3 | 16 | 6 | 10 |
| C4 | 8 | 8 | 2 |
| C5 | 20 | 15 | 11 |
| C6 | 20 | 0 | 20 |
| C7 | 24 | 24 | 9 |
| HF | 14 | 14 | 14 |

## RQ3 — release-risk gate (forward-chained, out-of-fold)

75 PRs scored by models that never saw a later revision.

| method | ROC-AUC | 95% CI | PR-AUC | 95% CI | recall@5%FPR | Brier | ECE | false-block | block recall | McNemar p |
|---|---|---|---|---|---|---|---|---|---|---|
| SentinelPR | 0.966 | [0.93, 0.99] | 0.982 | [0.96, 1.00] | 0.851 | 0.069 | 0.088 | 0.000 | 0.787 | — |
| SentinelPR (learned weights, sign-constrained) | 0.933 | [0.87, 0.98] | 0.954 | [0.90, 0.99] | 0.745 | 0.101 | 0.060 | 0.036 | 0.808 | 1.000 |
| SentinelPR (unconstrained LR) | 0.924 | [0.85, 0.98] | 0.943 | [0.87, 0.99] | 0.638 | 0.096 | 0.106 | 0.179 | 0.851 | 0.727 |
| B-JIT | 0.799 | [0.68, 0.89] | 0.871 | [0.78, 0.94] | 0.383 | 0.166 | 0.074 | 0.000 | 0.149 | 0.000 |
| ablation: no verification | 0.771 | [0.65, 0.88] | 0.857 | [0.76, 0.94] | 0.298 | 0.179 | 0.263 | 0.000 | 0.766 | 1.000 |
| ablation: no mutation | 0.961 | [0.92, 0.99] | 0.981 | [0.95, 1.00] | 0.872 | 0.074 | 0.107 | 0.000 | 0.872 | 0.125 |
| ablation: no history | 0.970 | [0.93, 1.00] | 0.984 | [0.96, 1.00] | 0.851 | 0.070 | 0.102 | 0.000 | 0.830 | 0.500 |
| ablation: no LLM feature | 0.966 | [0.93, 0.99] | 0.982 | [0.96, 1.00] | 0.851 | 0.069 | 0.088 | 0.000 | 0.787 | 1.000 |
| B-tests | 0.862 | [0.80, 0.92] | 0.897 | [0.84, 0.95] | 0.723 | 0.173 | 0.173 | 0.000 | 0.723 | 0.250 |
| SentinelPR (prior, untrained) | 0.970 | [0.93, 0.99] | 0.984 | [0.96, 1.00] | 0.851 | 0.077 | 0.057 | 0.000 | 0.808 | 1.000 |

Decisions by category (SentinelPR):

| category | n | defective | PASS | CANARY | BLOCK |
|---|---|---|---|---|---|
| C1 | 12 | 0 | 12 | 0 | 0 |
| C2 | 14 | 14 | 0 | 0 | 14 |
| C3 | 8 | 4 | 0 | 4 | 4 |
| C4 | 4 | 4 | 0 | 0 | 4 |
| C5 | 10 | 8 | 3 | 2 | 5 |
| C6 | 10 | 0 | 10 | 0 | 0 |
| C7 | 10 | 10 | 0 | 2 | 8 |
| HF | 7 | 7 | 1 | 4 | 2 |

## RQ1 — change impact (tests)

| method | PRs | macro P | macro R | macro F1 | micro F1 |
|---|---|---|---|---|---|
| lexical | 83 | 0.563 | 0.414 | 0.439 | 0.383 |
| static | 83 | 0.558 | 0.740 | 0.586 | 0.527 |
| sentinel | 83 | 0.660 | 0.798 | 0.714 | 0.795 |
| sentinel_verified | 83 | 0.747 | 0.726 | 0.734 | 0.908 |

Test selection: 11.45 tests per PR on average, 0.856 of suite time saved, safe-selection rate 0.953 over 64 PRs with failing tests. Wilcoxon (test F1): vs static p=0.000, vs lexical p=0.000.

## RQ2 — claim verification

789 execution-checkable impact claims. Precision before verification 0.535, after 1.000 (false-claim rate 0.465 → 0.000; true claims lost: 0.081). Refuted: 308, unverifiable: 103. History claims: {'VERIFIED': 37, 'REFUTED': 0, 'UNVERIFIABLE': 0}.

## RQ6 — adversarial robustness

Attack success rate (defective + attack PR receives PASS): 0.000 over 24 PRs. Guardrail false-positive rate on non-attack PRs: 0.000.

| attack | n | received PASS | detected by guards |
|---|---|---|---|
| injection-body | 8 | 0 | 8 |
| injection-title | 4 | 0 | 4 |
| injection-comment | 4 | 0 | 4 |
| fake-secret | 4 | 0 | 4 |
| oversized | 4 | 0 | 4 |

## RQ5 — cost and latency

Latency per PR: p50 4.14s, p95 13.37s. LLM tokens per PR: mean 0.0. Slowest stages: verify 4.483s, signals 0.11s, impact 0.085s, historian 0.07s.

## Deployed model

Family `expert`, selected by out-of-fold PR-AUC (learned 0.9543, expert 0.9815). Expert weights, Platt-calibrated on all releases; thresholds from Release 1.4.0: canary ≥ 0.4982, block ≥ 0.8672.

Largest coefficients: `verified_test_failures` +3.00, `docs_only` -2.50, `test_only` -1.80, `changed_line_coverage` -1.40, `mutation_score` -1.20, `verified_api_breaks` +1.20, `touches_dependencies` +0.90, `llm_risk` +0.80, `touches_ci` +0.60, `injection_attempts` +0.60.
