# SentinelPR benchmark — tablib

_Generated 2026-10-06T10:30:21+00:00._

## Dataset

143 pull requests over 4 base revisions (v3.6.0, v3.7.0, v3.8.0, v3.9.0); 84 defective by execution.

| category | PRs | defective | visible tests pass |
|---|---|---|---|
| C1 | 24 | 0 | 24 |
| C2 | 28 | 28 | 0 |
| C3 | 13 | 0 | 13 |
| C4 | 16 | 14 | 5 |
| C6 | 20 | 0 | 20 |
| C7 | 24 | 24 | 9 |
| HF | 18 | 18 | 18 |

## RQ3 — release-risk gate (forward-chained, out-of-fold)

70 PRs scored by models that never saw a later revision.

| method | ROC-AUC | 95% CI | PR-AUC | 95% CI | recall@5%FPR | Brier | ECE | false-block | block recall | McNemar p |
|---|---|---|---|---|---|---|---|---|---|---|
| SentinelPR | 0.913 | [0.84, 0.97] | 0.944 | [0.89, 0.98] | 0.775 | 0.123 | 0.136 | 0.067 | 0.800 | — |
| SentinelPR (learned weights, sign-constrained) | 0.860 | [0.76, 0.94] | 0.891 | [0.80, 0.96] | 0.375 | 0.164 | 0.160 | 0.167 | 0.775 | 0.289 |
| SentinelPR (unconstrained LR) | 0.886 | [0.79, 0.96] | 0.864 | [0.74, 0.97] | 0.000 | 0.121 | 0.142 | 0.167 | 0.775 | 0.289 |
| B-JIT | 0.769 | [0.65, 0.88] | 0.792 | [0.65, 0.91] | 0.225 | 0.203 | 0.222 | 0.267 | 0.575 | 0.000 |
| ablation: no verification | 0.745 | [0.62, 0.86] | 0.799 | [0.67, 0.91] | 0.275 | 0.214 | 0.242 | 0.167 | 0.725 | 0.031 |
| ablation: no mutation | 0.891 | [0.81, 0.95] | 0.927 | [0.87, 0.97] | 0.650 | 0.155 | 0.170 | 0.133 | 0.750 | 0.219 |
| ablation: no history | 0.921 | [0.85, 0.97] | 0.947 | [0.90, 0.98] | 0.775 | 0.122 | 0.141 | 0.033 | 0.750 | 1.000 |
| ablation: no LLM feature | 0.913 | [0.84, 0.97] | 0.944 | [0.89, 0.98] | 0.775 | 0.123 | 0.136 | 0.067 | 0.800 | 1.000 |
| B-tests | 0.771 | [0.69, 0.85] | 0.794 | [0.68, 0.88] | 0.575 | 0.257 | 0.257 | 0.033 | 0.575 | 0.021 |
| SentinelPR (prior, untrained) | 0.917 | [0.85, 0.97] | 0.945 | [0.89, 0.98] | 0.775 | 0.123 | 0.118 | 0.033 | 0.675 | 0.219 |
| SentinelPR (shipped uni-erp model, no retraining) | 0.917 | [0.84, 0.97] | 0.945 | [0.89, 0.98] | 0.775 | 0.126 | 0.169 | 0.033 | 0.675 | 0.219 |

Decisions by category (SentinelPR):

| category | n | defective | PASS | CANARY | BLOCK |
|---|---|---|---|---|---|
| C1 | 12 | 0 | 10 | 1 | 1 |
| C2 | 14 | 14 | 0 | 0 | 14 |
| C3 | 6 | 0 | 3 | 3 | 0 |
| C4 | 8 | 6 | 0 | 1 | 7 |
| C6 | 10 | 0 | 10 | 0 | 0 |
| C7 | 10 | 10 | 1 | 0 | 9 |
| HF | 10 | 10 | 5 | 2 | 3 |

## RQ1 — change impact (tests)

| method | PRs | macro P | macro R | macro F1 | micro F1 |
|---|---|---|---|---|---|
| lexical | 85 | 0.208 | 0.162 | 0.161 | 0.065 |
| static | 85 | 0.315 | 0.242 | 0.247 | 0.146 |
| sentinel | 85 | 0.834 | 0.855 | 0.837 | 0.955 |
| sentinel_verified | 85 | 0.859 | 0.855 | 0.857 | 0.959 |

Test selection: 26.15 tests per PR on average, 0.756 of suite time saved, safe-selection rate 0.944 over 54 PRs with failing tests. Wilcoxon (test F1): vs static p=0.000, vs lexical p=0.000.

## RQ2 — claim verification

1387 execution-checkable impact claims. Precision before verification 0.753, after 1.000 (false-claim rate 0.247 → 0.000; true claims lost: 0.000). Refuted: 304, unverifiable: 52. History claims: {'VERIFIED': 50, 'REFUTED': 0, 'UNVERIFIABLE': 0}.

## RQ6 — adversarial robustness

Attack success rate (defective + attack PR receives PASS): 0.042 over 24 PRs. Guardrail false-positive rate on non-attack PRs: 0.000.

| attack | n | received PASS | detected by guards |
|---|---|---|---|
| injection-body | 8 | 1 | 8 |
| injection-title | 4 | 0 | 4 |
| injection-comment | 4 | 0 | 4 |
| fake-secret | 4 | 0 | 4 |
| oversized | 4 | 0 | 4 |

## RQ5 — cost and latency

Latency per PR: p50 8.44s, p95 23.74s. LLM tokens per PR: mean 0.0. Slowest stages: verify 9.341s, signals 0.235s, historian 0.139s, impact 0.127s.
