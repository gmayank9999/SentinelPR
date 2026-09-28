"""PR generators, one module per benchmark category.

| category | module        | what it produces                                                        |
|----------|---------------|-------------------------------------------------------------------------|
| C1       | refactor.py   | behaviour-preserving edits: renamed locals, docstrings, sorted imports  |
| C2 / HF  | faults.py     | mutation faults; the oracle splits them into caught (C2) and hidden (HF)|
| C3       | api.py        | signature changes with only some callers updated                        |
| C4       | regression.py | reverts of real bug fixes found by SZZ                                  |
| C5       | config.py     | constant / dependency changes                                           |
| C6       | noise.py      | test-only and docs-only changes                                         |
| C7       | adversarial.py| defective PRs dressed up with prompt injection, secrets, huge diffs     |

Generators never decide labels. Every PR is labelled afterwards by running tests (see
``bench.oracle``), so a "refactor" that accidentally changes behaviour is labelled defective.
"""
