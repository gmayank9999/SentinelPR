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
* **LLM conditions** reuse the labelled PRs of the evidence-only run (`run_matrix --llm --labels`),
  so the two runs are paired PR by PR, and every gate arm is scored by the same forward-chained
  model (`bench/analysis/llm.py`). All calls use temperature 0 on Groq's free tier, which allows
  200k tokens per model per day, so each experiment uses one model and is sized to that quota:

  | experiment | model | scope | result |
  |---|---|---|---|
  | LLM agents + B-LLM reviewer | `openai/gpt-oss-120b` | UniERP releases 1.3.0 and 1.4.0, 75 PRs out of fold | [`llm.md`](../bench/results/uni-erp/llm.md) |
  | Impact prompt v1 / v2 / v3 | `openai/gpt-oss-20b` | 20 UniERP PRs | [`prompt_ab.json`](../bench/results/uni-erp/prompt_ab.json) |
  | Q&A: no RAG / code / code + history | `qwen/qwen3.8-27b` | 41 questions | [`results-llm.json`](../bench/results/qa/results-llm.json) |

  Reproduce with `python -m bench.run_matrix --repo uni-erp --llm --workers 1 --labels
  bench/out/uni-erp/prs.jsonl --bases "Release 1.3.0" "Release 1.4.0" --out bench/out/uni-erp-llm`,
  then `python -m bench.analysis.llm`; every task got an answer for all 77 PRs, and the execution
  evidence is identical between the two runs.
  The evidence-only results (everything else) use no LLM.

## Results (UniERP)

The full report with confidence intervals is in
[`bench/results/uni-erp/report.md`](../bench/results/uni-erp/report.md); headline numbers are in the
[README](../README.md#results).

## Threats to validity

* **Construct.** Generated defects (mutations, reverted fixes, signature changes) approximate real
  defects but are not the same population. C4 reverts real historical fixes to mitigate this.
* **Shared mutation operators.** C2 and hidden-fault PRs are generated with the same operators
  (`sentinel/verify/mutation.py`) the verifier uses to measure test adequacy, so the defects
  have the shape the detector looks for. C3, C4 and C5 are generated differently and are where
  this bias does not apply.
* **Circularity in RQ1.** The ground truth for impacted tests is "tests that execute a changed
  line", and SentinelPR predicts it largely from the base revision's line coverage, so part of
  its advantage over static reachability and name search comes from sharing that definition.
* **Circularity in RQ2.** A test-impact claim is verified by executing the test on the head, and the
  ground truth is also execution-based, so post-verification precision is close to 1 by
  construction. The meaningful quantity is how many *true* claims verification loses (8%).
* **Model choices made after inspection.** In order: (1) an unconstrained logistic model showed
  small-sample artefacts (e.g. recently changed code judged safer); (2) sign constraints from
  the JIT-prediction literature were added, and the refuted-claim ratio was bounded after its
  negative weight turned out to re-encode failing tests; (3) the PR-title intent features were
  removed because authors control them; (4) because the expert-weighted prior kept outperforming
  fitted weights, an "expert weights + learned calibration" family was added and the family is
  now selected by forward-chained PR-AUC. Every variant is still reported. The expert weights
  were written before the benchmark existed, but the choice to deploy them was made after seeing
  these results, so the headline numbers carry some selection optimism.
* **The "merge if tests pass" baseline is binary.** Its ROC curve has a single operating point,
  which flatters the AUC gap. At the decision level the two disagree on 3 of 75 PRs (all in
  SentinelPR's favour, exact McNemar p = 0.25): the gain over passing tests is in ranking and
  calibration (Brier 0.069 vs 0.173), not a significant difference in what gets blocked.
* **What the ablations say.** Verification evidence carries the signal (PR-AUC 0.98 → 0.86
  without it). Mutation adds little over changed-line coverage on UniERP, whose visible suite
  already covers 94% of lines; it matters where coverage is high but assertions are weak, which
  the hidden-fault category exercises only with 14 PRs. Out of fold, removing mutation even
  gives 4 more correct block decisions (0 vs 4, p = 0.125): most hidden faults sit on lines no
  test executes, which changed-line coverage already flags. The untrained expert prior scores as
  well as the deployed model (PR-AUC 0.984 vs 0.982), so training contributes calibration and
  thresholds, not ranking.
* **Small sample.** 154 PRs in total and 75 scored out-of-fold; confidence intervals are wide.
* **External validity.** UniERP plus two real open-source libraries, humanize
  ([report](../bench/results/humanize/report.md)) and tablib
  ([report](../bench/results/tablib/report.md)). Verification evidence carries the signal on all
  three (PR-AUC without it: 0.86 / 0.72 / 0.80), and on tablib the block decisions improve
  significantly over passing tests (McNemar p = 0.021). Hidden faults are the weak spot outside
  UniERP: 3 of 10 passed on humanize and 5 of 10 on tablib. On humanize the ranking holds (PR-AUC 0.891 vs 0.757 for
  passing tests) but the false-block rate is 8.1%, above the 5% target, and the model trained on
  UniERP needs re-chosen thresholds there (21.6% false blocks with UniERP's). On humanize the
  family selection kept expert weights (PR-AUC 0.891 vs 0.884 for fitted sign-constrained weights)
  although the unconstrained model did better (0.958): the selection only compares the two
  families, chosen before humanize was run. The generators produced no C5 (config/dependency)
  PRs for humanize, so that category is UniERP-only.
* **Attack success vs. missed faults.** The one attacked PR that passed (tablib) is a hidden fault
  whose unattacked twin passed too; attack success should be read against the clean twin.
* **Oracle completeness.** A PR is defective only if a base or hidden test catches it. On humanize,
  source-only reverts of real bug fixes that no test covers, and new optional parameters no test
  exercises, are labelled benign; SentinelPR blocks several of them, which counts as a false block.
* **What the LLM measurements can and cannot say.** 75 paired PRs from two releases and one model
  per experiment: they show the LLM adds no signal to the gate *here* and that its low-risk
  ratings let subtle defects through, not that no model could. B-LLM sees the unified diff truncated to 8,000 characters, so a defect late in
  an oversized PR is invisible to it by construction. Free-tier models change; ids are recorded in
  every result file.
* **Q&A set.** The 41 questions were written with knowledge of the repository; `accuracy` is
  scored by gold citations, which rewards finding the right sources rather than judging the prose,
  and a code-only retriever cannot cite a commit or issue, so its 0% on the three history
  categories is structural. `content_accuracy` (the answer states every gold keyword) does not
  need citations and is the fair comparison across conditions, including no-RAG.
* **Order-dependent tests.** Running a relevance-ranked subset of a suite can surface test-order
  dependencies: humanize's i18n tests set `i18n.__file__ = None` and rely on later-defined tests
  running first. The first humanize run blocked three comment/docstring PRs because of this.
  Selected tests now run in definition order (`sentinel/verify/pytest_plugin.py`); order-dependent
  suites remain a risk for any test-selection tool.
* **Model drift.** When LLMs are used, provider model ids and prompt versions are recorded in every
  run report.
