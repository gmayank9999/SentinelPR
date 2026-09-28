# Prompt design

Prompts live in `sentinel/agents/prompts/<name>.v<N>.md` with `## system` and `## user` sections
and `$placeholders`. The newest version is used unless one is pinned (`prompts.impact: v2` in
`sentinel.yaml`), and the name@version of every prompt used is stored in the run report.

## Impact prompt: three iterations

| version | style | what changed and why |
|---|---|---|
| `impact.v1` | zero-shot | "Which tests and modules could this change affect? Answer in JSON." Replies were free-form: invented test names, file paths that do not exist, inconsistent JSON keys. Nothing could be checked. |
| `impact.v2` | few-shot | One worked example fixes the output shape and adds the callers of the changed code as context. Structure became reliable, but the model still named plausible-sounding tests that do not exist, because nothing tied a claim to evidence. |
| `impact.v3` | schema + evidence constrained | The model receives an **EVIDENCE** list (call-path ids, coverage ids, signature changes) and **CANDIDATE TESTS**, and every claim must cite ids from those lists. PR text is wrapped in `<untrusted>` tags with an explicit instruction not to follow it. Claims citing anything else are dropped in code, so a hallucinated test can never reach the report. A risk rating (low/medium/high) is requested as one feature among ~30. |

The shift from v2 to v3 is the core idea of SentinelPR in miniature: instead of asking the model to
be right, ask it to choose among checkable facts and cite them, then check.

`python -m bench.prompt_ab` measures the three versions on benchmark PRs (parse rate, share of
claims grounded in given evidence, precision/recall against execution ground truth, tokens);
it needs an LLM provider.

## Other prompts

| prompt | used by | notes |
|---|---|---|
| `historian.v1` | Historian agent | claims must cite history document ids (`issue:87`, `commit:…`, `pr:88`); speculation is disallowed |
| `intent.v1` | Signals agent | one-word change intent; cheapest routing tier |
| `explain.v1` | risk explanation | the model restates the model's top contributions; every sentence must end with evidence ids, enforced by the citation guard |
| `rationale_judge.v1` | Verifier | entailment check of a rationale claim against the cited text |
| `qa.v1` | Q&A bot | cite ids after every sentence, or reply with the fixed refusal |
| `patch.v1` | Patch Advisor | exact find/replace edits plus a regression test; no dependencies, no CI edits |

## Routing

`sentinel/orchestrator/router.py` scores each call's complexity from the task type, diff size,
number of change units and context size, and picks a tier: easy → local Ollama model, medium →
Gemini Flash, hard → the largest Groq model, each with fallbacks. When the run's token budget runs
low everything drops to the easy tier. All responses are cached by (model, prompt hash).
