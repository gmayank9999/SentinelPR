# SentinelPR benchmark — uni-erp

_Generated 2026-09-28T15:10:33+00:00._

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
| SentinelPR | 0.981 | [0.95, 1.00] | 0.989 | [0.97, 1.00] | 0.894 | 0.055 | 0.053 | 0.036 | 0.894 | — |
| SentinelPR (unconstrained LR) | 0.935 | [0.88, 0.98] | 0.961 | [0.92, 0.99] | 0.787 | 0.099 | 0.085 | 0.000 | 0.872 | 1.000 |
| B-JIT | 0.815 | [0.71, 0.90] | 0.884 | [0.79, 0.95] | 0.319 | 0.160 | 0.066 | 0.036 | 0.319 | 0.000 |
| ablation: no verification | 0.832 | [0.73, 0.91] | 0.904 | [0.83, 0.96] | 0.447 | 0.154 | 0.098 | 0.036 | 0.808 | 0.219 |
| ablation: no mutation | 0.973 | [0.94, 0.99] | 0.983 | [0.96, 1.00] | 0.872 | 0.066 | 0.049 | 0.036 | 0.872 | 1.000 |
| ablation: no history | 0.985 | [0.96, 1.00] | 0.991 | [0.98, 1.00] | 0.894 | 0.051 | 0.061 | 0.036 | 0.894 | 1.000 |
| ablation: no LLM feature | 0.981 | [0.95, 1.00] | 0.989 | [0.97, 1.00] | 0.894 | 0.055 | 0.053 | 0.036 | 0.894 | 1.000 |
| B-tests | 0.862 | [0.79, 0.92] | 0.897 | [0.83, 0.95] | 0.723 | 0.173 | 0.173 | 0.000 | 0.723 | 0.039 |
| SentinelPR (prior, untrained) | 0.966 | [0.93, 0.99] | 0.981 | [0.96, 1.00] | 0.830 | 0.081 | 0.058 | 0.000 | 0.787 | 0.219 |

Decisions by category (SentinelPR):

| category | n | defective | PASS | CANARY | BLOCK |
|---|---|---|---|---|---|
| C1 | 12 | 0 | 10 | 1 | 1 |
| C2 | 14 | 14 | 0 | 0 | 14 |
| C3 | 8 | 4 | 3 | 1 | 4 |
| C4 | 4 | 4 | 0 | 0 | 4 |
| C5 | 10 | 8 | 3 | 2 | 5 |
| C6 | 10 | 0 | 10 | 0 | 0 |
| C7 | 10 | 10 | 0 | 0 | 10 |
| HF | 7 | 7 | 0 | 2 | 5 |

## RQ1 — change impact (tests)

| method | PRs | macro P | macro R | macro F1 | micro F1 |
|---|---|---|---|---|---|
| lexical | 83 | 0.563 | 0.414 | 0.439 | 0.383 |
| static | 83 | 0.558 | 0.740 | 0.586 | 0.527 |
| sentinel | 83 | 0.660 | 0.798 | 0.714 | 0.795 |
| sentinel_verified | 83 | 0.747 | 0.726 | 0.734 | 0.908 |

Test selection: 11.45 tests per PR on average, 0.864 of suite time saved, safe-selection rate 0.953 over 64 PRs with failing tests. Wilcoxon (test F1): vs static p=0.000, vs lexical p=0.000.

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

Latency per PR: p50 4.54s, p95 14.56s. LLM tokens per PR: mean 0.0. Slowest stages: verify 5.159s, signals 0.098s, impact 0.066s, historian 0.059s.

## Deployed model

Trained on Release 1.1.0, Release 1.2.0, Release 1.3.0; calibrated and thresholded on Release 1.4.0: canary ≥ 0.7419, block ≥ 0.96.

Largest standardised coefficients: `verified_test_failures` +1.62, `refuted_claim_ratio` -0.96, `surviving_mutants` +0.64, `changed_line_coverage` -0.63, `uncovered_changed_lines` +0.61, `injection_attempts` +0.55, `test_only` -0.49, `docs_only` -0.46, `mutation_score` -0.36, `tests_changed` -0.36.
