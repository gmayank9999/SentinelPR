# What the LLM adds — uni-erp

75 PRs scored out of fold on Release 1.3.0, Release 1.4.0; model `openai/gpt-oss-120b`, temperature 0. Every gate arm uses the same forward-chained model per release.

## Release gate

| arm | ROC-AUC | 95% CI | PR-AUC | 95% CI | recall@5%FPR | Brier | ECE | false-block | block recall | defective passed | McNemar p vs no LLM |
|---|---|---|---|---|---|---|---|---|---|---|---|
| no LLM | 0.966 | [0.93, 0.99] | 0.982 | [0.96, 1.00] | 0.851 | 0.069 | 0.088 | 0.000 | 0.787 | 2 | — |
| + LLM rating | 0.964 | [0.92, 0.99] | 0.981 | [0.95, 1.00] | 0.830 | 0.074 | 0.072 | 0.000 | 0.766 | 7 | 1.000 |
| LLM pipeline | 0.964 | [0.92, 0.99] | 0.980 | [0.95, 1.00] | 0.830 | 0.073 | 0.088 | 0.000 | 0.787 | 5 | 1.000 |
| LLM rating alone | 0.629 | [0.55, 0.71] | 0.726 | [0.62, 0.81] | 0.149 | 0.324 | 0.331 | 0.000 | 0.149 | — | 0.000 |
| B-LLM | 0.924 | [0.86, 0.98] | 0.954 | [0.91, 0.98] | 0.723 | 0.193 | 0.246 | 0.000 | 0.617 | 13 | 0.115 |

Blocked / passed by category (B-LLM: passed = approved):

| category | n | defective | no LLM blocked / passed | LLM pipeline blocked / passed | B-LLM blocked / passed |
|---|---|---|---|---|---|
| C1 | 12 | 0 | 0 / 12 | 0 / 12 | 0 / 12 |
| C2 | 14 | 14 | 14 / 0 | 14 / 0 | 14 / 0 |
| C3 | 8 | 4 | 4 / 0 | 4 / 0 | 1 / 6 |
| C4 | 4 | 4 | 4 / 0 | 4 / 0 | 2 / 0 |
| C5 | 10 | 8 | 5 / 3 | 5 / 5 | 1 / 9 |
| C6 | 10 | 0 | 0 / 10 | 0 / 10 | 0 / 10 |
| C7 | 10 | 10 | 8 / 0 | 8 / 0 | 6 / 2 |
| HF | 7 | 7 | 2 / 1 | 2 / 2 | 5 / 1 |

Impact agent's risk rating:

| | low | medium | high | none |
|---|---|---|---|---|
| benign | 26 | 2 | 0 | 0 |
| defective | 32 | 8 | 7 | 0 |

## Adversarial PRs

Attack success (a defective, attacked PR gets through): B-LLM approves 0.167, SentinelPR with the LLM passes 0.000 (12 PRs). Mean change in B-LLM's defect probability caused by the attack text: -0.086 over 12 paired PRs.

| attack | n | B-LLM approved | SentinelPR PASS |
|---|---|---|---|
| fake-secret | 2 | 0 | 0 |
| injection-body | 4 | 0 | 0 |
| injection-comment | 2 | 0 | 0 |
| injection-title | 2 | 0 | 0 |
| oversized | 2 | 2 | 0 |

## Impact claims: LLM vs rules (same PRs)

| agent | PRs | macro P | macro R | macro F1 | claims | precision before verification | after | refuted |
|---|---|---|---|---|---|---|---|---|
| rules | 44 | 0.677 | 0.767 | 0.715 | 488 | 0.621 | 1.000 | 154 |
| llm | 44 | 0.808 | 0.820 | 0.809 | 381 | 0.672 | 0.992 | 111 |

Impact agent mode in the LLM run: {'llm': 47, 'rules': 28, 'rejected': 2} (rules = none of the model's claims cited evidence it was given, so the evidence-only claims are used; its risk rating is still recorded).

## Cost

SentinelPR with the LLM: 4442 tokens per PR on average (p95 7807), 4.33 calls, latency p50 6.93 s / p95 15.34 s; tasks that never got an answer (PRs): none. B-LLM: 554.700 tokens per PR. Latency includes waiting out free-tier rate limits.

Consistency: execution evidence identical in both runs for 77 of 77 PRs.
