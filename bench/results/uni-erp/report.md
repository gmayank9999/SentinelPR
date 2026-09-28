# SentinelPR benchmark — uni-erp

_Generated 2026-09-28T15:04:37+00:00._

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
| SentinelPR | 0.935 | [0.87, 0.98] | 0.961 | [0.92, 0.99] | 0.787 | 0.099 | 0.085 | 0.000 | 0.872 | — |
| B-JIT | 0.760 | [0.64, 0.87] | 0.845 | [0.74, 0.93] | 0.234 | 0.199 | 0.133 | 0.107 | 0.383 | 0.000 |
| ablation: no verification | 0.783 | [0.68, 0.88] | 0.869 | [0.78, 0.94] | 0.340 | 0.191 | 0.133 | 0.036 | 0.830 | 0.375 |
| ablation: no mutation | 0.864 | [0.77, 0.95] | 0.890 | [0.78, 0.97] | 0.383 | 0.134 | 0.124 | 0.143 | 0.830 | 0.031 |
| ablation: no history | 0.931 | [0.86, 0.98] | 0.949 | [0.88, 0.99] | 0.787 | 0.096 | 0.094 | 0.036 | 0.830 | 0.250 |
| ablation: no LLM feature | 0.935 | [0.88, 0.98] | 0.961 | [0.92, 0.99] | 0.787 | 0.099 | 0.085 | 0.000 | 0.872 | 1.000 |
| B-tests | 0.862 | [0.80, 0.92] | 0.897 | [0.82, 0.95] | 0.723 | 0.173 | 0.173 | 0.000 | 0.723 | 0.016 |
| SentinelPR (prior, untrained) | 0.966 | [0.93, 0.99] | 0.981 | [0.96, 1.00] | 0.830 | 0.081 | 0.058 | 0.000 | 0.787 | 0.125 |

Decisions by category (SentinelPR):

| category | n | defective | PASS | CANARY | BLOCK |
|---|---|---|---|---|---|
| C1 | 12 | 0 | 9 | 3 | 0 |
| C2 | 14 | 14 | 0 | 0 | 14 |
| C3 | 8 | 4 | 4 | 0 | 4 |
| C4 | 4 | 4 | 0 | 0 | 4 |
| C5 | 10 | 8 | 4 | 1 | 5 |
| C6 | 10 | 0 | 8 | 2 | 0 |
| C7 | 10 | 10 | 0 | 1 | 9 |
| HF | 7 | 7 | 1 | 1 | 5 |

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

Trained on Release 1.1.0, Release 1.2.0, Release 1.3.0; calibrated and thresholded on Release 1.4.0: canary ≥ 0.6334, block ≥ 0.8914.

Largest standardised coefficients: `verified_test_failures` +1.46, `refuted_claim_ratio` -0.95, `log_code_age_days` +0.81, `surviving_mutants` +0.64, `signature_changes` -0.62, `changed_line_coverage` -0.55, `uncovered_changed_lines` +0.53, `mutation_score` -0.53, `log_churn` -0.46, `injection_attempts` +0.45.
