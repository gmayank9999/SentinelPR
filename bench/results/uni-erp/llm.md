# What the LLM adds — uni-erp

37 PRs scored out of fold on Release 1.3.0; model `openai/gpt-oss-120b`, temperature 0. Every gate arm uses the same forward-chained model per release.

## Release gate

| arm | ROC-AUC | 95% CI | PR-AUC | 95% CI | recall@5%FPR | Brier | ECE | false-block | block recall | McNemar p vs no LLM |
|---|---|---|---|---|---|---|---|---|---|---|
| no LLM | 0.963 | [0.90, 1.00] | 0.980 | [0.94, 1.00] | 0.826 | 0.086 | 0.106 | 0.000 | 0.826 | — |
| + LLM rating | 0.960 | [0.89, 1.00] | 0.978 | [0.94, 1.00] | 0.826 | 0.092 | 0.110 | 0.000 | 0.826 | 1.000 |
| LLM pipeline | 0.953 | [0.87, 1.00] | 0.976 | [0.93, 1.00] | 0.826 | 0.087 | 0.087 | 0.000 | 0.826 | 1.000 |
| LLM rating alone | 0.568 | [0.43, 0.69] | 0.688 | [0.53, 0.82] | 0.130 | 0.344 | 0.332 | 0.000 | 0.130 | 0.000 |
| B-LLM | 0.958 | [0.90, 1.00] | 0.970 | [0.92, 1.00] | 0.783 | 0.186 | 0.240 | 0.000 | 0.652 | 0.344 |

Blocked / passed by category (B-LLM: passed = approved):

| category | n | defective | no LLM blocked / passed | LLM pipeline blocked / passed | B-LLM blocked / passed |
|---|---|---|---|---|---|
| C1 | 6 | 0 | 0 / 6 | 0 / 6 | 0 / 6 |
| C2 | 7 | 7 | 7 / 0 | 7 / 0 | 7 / 0 |
| C3 | 4 | 2 | 2 / 0 | 2 / 0 | 0 / 4 |
| C4 | 2 | 2 | 2 / 0 | 2 / 0 | 1 / 0 |
| C5 | 5 | 4 | 3 / 2 | 3 / 2 | 1 / 4 |
| C6 | 5 | 0 | 0 / 5 | 0 / 5 | 0 / 5 |
| C7 | 5 | 5 | 4 / 0 | 4 / 0 | 4 / 1 |
| HF | 3 | 3 | 1 / 1 | 1 / 1 | 2 / 1 |

Impact agent's risk rating:

| | low | medium | high | none |
|---|---|---|---|---|
| benign | 12 | 2 | 0 | 0 |
| defective | 17 | 3 | 3 | 0 |

## Adversarial PRs

Attack success (a defective, attacked PR gets through): B-LLM approves 0.167, SentinelPR with the LLM passes 0.000 (6 PRs). Mean change in B-LLM's defect probability caused by the attack text: -0.053 over 6 paired PRs.

| attack | n | B-LLM approved | SentinelPR PASS |
|---|---|---|---|
| fake-secret | 1 | 0 | 0 |
| injection-body | 2 | 0 | 0 |
| injection-comment | 1 | 0 | 0 |
| injection-title | 1 | 0 | 0 |
| oversized | 1 | 1 | 0 |

## Impact claims: LLM vs rules (same PRs)

| agent | PRs | macro P | macro R | macro F1 | claims | precision before verification | after | refuted |
|---|---|---|---|---|---|---|---|---|
| rules | 22 | 0.716 | 0.785 | 0.746 | 226 | 0.739 | 1.000 | 48 |
| llm | 22 | 0.768 | 0.793 | 0.772 | 190 | 0.762 | 0.985 | 39 |

Impact agent mode in the LLM run: {'llm': 22, 'rules': 15, 'rejected': 1} (rules = none of the model's claims cited evidence it was given, so the evidence-only claims are used; its risk rating is still recorded).

## Cost

SentinelPR with the LLM: 3773 tokens per PR on average (p95 6803), 4.24 calls, latency p50 11.79 s / p95 23.43 s; tasks that never got an answer (PRs): {'explain': 20}. B-LLM: 567.200 tokens per PR. Latency includes waiting out free-tier rate limits.

Consistency: execution evidence identical in both runs for 38 of 38 PRs.
