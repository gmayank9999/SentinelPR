# Evaluation

## Research questions

| | Question | Measured by |
|---|---|---|
| RQ1 | Does hybrid retrieval + coverage predict impacted tests and modules better than static reachability or name search? | Precision/recall/F1 against execution ground truth; test-selection safety and time saved |
| RQ2 | Does verification remove false claims? | Claim precision before and after verification; share of true claims lost |
| RQ3 | Does an evidence-gated, calibrated model detect defective PRs better than JIT prediction, an LLM reviewer and "merge if tests pass"? | ROC-AUC, PR-AUC, recall at 5% false-positive rate, Brier score, ECE, false-block rate |
| RQ4 | Does retrieving history (commits, issues, PRs) improve answers to "why is the code like this?" | Accuracy, citation precision, hallucination rate; RAGAS with an LLM |
| RQ5 | What does it cost? | Latency per PR and per stage, tokens, calls, router tiers |
| RQ6 | Can PR text manipulate the gate? | Attack success rate; guardrail false positives |

## Benchmark construction

`bench/run_matrix.py` branches pull requests off four releases of UniERP's history:

| category | generator | typical label |
|---|---|---|
| C1 benign refactor | renamed locals, docstrings, reordered imports, extracted constants | safe |
| C2 logic fault | a mutation the visible suite catches | defective |
| HF hidden fault | a mutation every visible test misses but the hidden oracle catches | defective |
| C3 API ripple | a new (required or optional) parameter, only some callers updated | by execution |
| C4 historical regression | revert of a real bug fix identified by SZZ (source only, or including its test) | defective |
| C5 config / dependency | changed module constants, bumped requirement pins | by execution |
| C6 tests / docs only | a duplicated test, a comment, README text | safe |
| C7 adversarial | a defective PR plus prompt injection, a planted secret or a huge file | defective |

**Labels come from execution only.** For every PR the oracle runs the *base* revision's tests
and the hidden oracle suite (`bench/hidden/uni-erp`) against the PR; the PR is defective if any
test that passes on the base fails on the PR. Using the base's tests means a PR cannot hide a
regression by editing the test that would catch it. The oracle also runs the PR's visible suite
with per-test coverage, which provides the ground truth for impact (tests that execute a changed
line, plus tests that fail).

The seeded history is checked to be green at every commit (`seed_history.py --check`), so older
releases are valid bases.

## Protocol

* **Forward chaining.** For each release *k ≥ 3*, the risk model is trained on releases *< k−1*,
  calibrated and thresholded on *k−1*, and applied to *k*. Out-of-fold predictions are pooled.
  Splits are never random.
* **Thresholds** are chosen on the calibration release so that at most 5% of benign PRs are blocked.
* **Uncertainty:** 1000-resample bootstrap CIs for ROC-AUC and PR-AUC; exact McNemar tests on paired
  gate decisions; Wilcoxon signed-rank on per-PR impact F1.
* **Ablations** retrain the model on feature subsets: no verification evidence, no mutation, no
  history, no LLM rating; the JIT baseline uses change metrics only.
* LLM conditions (B-LLM reviewer, LLM-mode agents, Q&A with generation) run when provider keys are
  set (`python -m bench.run_matrix --llm`, `python -m bench.qa.evaluate --llm`). The committed
  results were produced without an LLM.

## Results (UniERP)

The full report with confidence intervals is in
[`bench/results/uni-erp/report.md`](../bench/results/uni-erp/report.md); headline numbers are in the
[README](../README.md#results).

## Threats to validity

* **Construct.** Generated defects (mutations, reverted fixes, signature changes) approximate real
  defects but are not the same population. C4 reverts real historical fixes to mitigate this.
* **Circularity in RQ2.** A test-impact claim is verified by executing the test on the head, and the
  ground truth is also execution-based, so post-verification precision is close to 1 by
  construction. The meaningful quantity is how many *true* claims verification loses (8%).
* **Model choices made after inspection.** The first trained model was unconstrained; its
  coefficients showed small-sample artefacts (e.g. younger code judged safer), after which sign
  constraints from the JIT-prediction literature were added. Both models are reported; the
  constrained one is deployed. The untrained hand-weighted prior is also reported and is
  competitive, which says the evidence features themselves carry most of the signal.
* **Small sample.** 154 PRs in total and 75 scored out-of-fold; confidence intervals are wide.
* **External validity.** One small Python project so far. `bench/repos.yaml` is ready for two real
  open-source repositories (they need hidden oracle tests before they can be enabled).
* **Q&A set.** The 41 questions were written with knowledge of the repository; answers are
  scored automatically by gold citations, which rewards finding the right sources rather than
  judging the prose.
* **Model drift.** When LLMs are used, provider model ids and prompt versions are recorded in every
  run report.
