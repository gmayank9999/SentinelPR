# SentinelPR benchmark — humanize

_Generated 2026-10-06T09:40:05+00:00._

## Dataset

150 pull requests over 4 base revisions (4.7.0, 4.8.0, 4.9.0, 4.10.0); 78 defective by execution.

| category | PRs | defective | visible tests pass |
|---|---|---|---|
| C1 | 24 | 0 | 24 |
| C2 | 28 | 28 | 0 |
| C3 | 16 | 0 | 16 |
| C4 | 16 | 4 | 13 |
| C6 | 20 | 0 | 18 |
| C7 | 24 | 24 | 13 |
| HF | 22 | 22 | 22 |

## RQ3 — release-risk gate (forward-chained, out-of-fold)

72 PRs scored by models that never saw a later revision.

| method | ROC-AUC | 95% CI | PR-AUC | 95% CI | recall@5%FPR | Brier | ECE | false-block | block recall | McNemar p |
|---|---|---|---|---|---|---|---|---|---|---|
| SentinelPR | 0.917 | [0.84, 0.97] | 0.891 | [0.77, 0.98] | 0.571 | 0.113 | 0.098 | 0.081 | 0.629 | — |
| SentinelPR (learned weights, sign-constrained) | 0.911 | [0.83, 0.97] | 0.884 | [0.77, 0.98] | 0.429 | 0.122 | 0.133 | 0.081 | 0.686 | 0.625 |
| SentinelPR (unconstrained LR) | 0.955 | [0.90, 0.99] | 0.958 | [0.91, 0.99] | 0.714 | 0.092 | 0.070 | 0.054 | 0.857 | 0.004 |
| B-JIT | 0.785 | [0.67, 0.89] | 0.715 | [0.56, 0.88] | 0.000 | 0.194 | 0.152 | 0.000 | 0.000 | 0.000 |
| ablation: no verification | 0.805 | [0.69, 0.91] | 0.723 | [0.57, 0.90] | 0.000 | 0.186 | 0.140 | 0.054 | 0.629 | 1.000 |
| ablation: no mutation | 0.886 | [0.79, 0.96] | 0.863 | [0.74, 0.96] | 0.286 | 0.132 | 0.120 | 0.081 | 0.629 | 1.000 |
| ablation: no history | 0.945 | [0.89, 0.98] | 0.951 | [0.90, 0.99] | 0.771 | 0.102 | 0.117 | 0.054 | 0.686 | 0.250 |
| ablation: no LLM feature | 0.917 | [0.84, 0.98] | 0.891 | [0.77, 0.98] | 0.571 | 0.113 | 0.098 | 0.081 | 0.629 | 1.000 |
| B-tests | 0.787 | [0.70, 0.87] | 0.757 | [0.63, 0.86] | 0.000 | 0.208 | 0.208 | 0.054 | 0.629 | 1.000 |
| SentinelPR (prior, untrained) | 0.921 | [0.85, 0.97] | 0.904 | [0.80, 0.98] | 0.571 | 0.120 | 0.184 | 0.216 | 0.914 | 0.302 |
| SentinelPR (shipped uni-erp model, no retraining) | 0.913 | [0.84, 0.97] | 0.879 | [0.75, 0.97] | 0.000 | 0.157 | 0.220 | 0.216 | 0.943 | 0.210 |

Decisions by category (SentinelPR):

| category | n | defective | PASS | CANARY | BLOCK |
|---|---|---|---|---|---|
| C1 | 12 | 0 | 11 | 1 | 0 |
| C2 | 14 | 14 | 0 | 0 | 14 |
| C3 | 8 | 0 | 3 | 5 | 0 |
| C4 | 8 | 1 | 4 | 2 | 2 |
| C6 | 10 | 0 | 8 | 0 | 2 |
| C7 | 10 | 10 | 0 | 3 | 7 |
| HF | 10 | 10 | 3 | 7 | 0 |

## RQ1 — change impact (tests)

| method | PRs | macro P | macro R | macro F1 | micro F1 |
|---|---|---|---|---|---|
| lexical | 92 | 0.656 | 0.664 | 0.638 | 0.516 |
| static | 92 | 0.717 | 0.870 | 0.766 | 0.749 |
| sentinel | 92 | 0.856 | 0.935 | 0.879 | 0.899 |
| sentinel_verified | 92 | 0.913 | 0.913 | 0.913 | 0.973 |

Test selection: 154.47 tests per PR on average, 0.665 of suite time saved, safe-selection rate 0.909 over 44 PRs with failing tests. Wilcoxon (test F1): vs static p=0.000, vs lexical p=0.000.

## RQ2 — claim verification

805 execution-checkable impact claims. Precision before verification 0.427, after 1.000 (false-claim rate 0.573 → 0.000; true claims lost: 0.012). Refuted: 397, unverifiable: 84. History claims: {'VERIFIED': 104, 'REFUTED': 0, 'UNVERIFIABLE': 0}.

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

Latency per PR: p50 11.56s, p95 43.42s. LLM tokens per PR: mean 0.0. Slowest stages: verify 21.243s, signals 0.18s, historian 0.107s, impact 0.078s.
