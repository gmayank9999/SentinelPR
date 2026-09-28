# Responsible use and threat model

## Who decides

The LLM proposes claims and may phrase an explanation. It never decides. The decision is a
function of the calibrated risk model and a fixed policy, recomputed by the
decision-integrity guard before anything is published. A human still approves every merge;
SentinelPR only gates (BLOCK) or recommends a cautious rollout (CANARY).

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| Prompt injection in PR title, body, commit messages, code comments or strings | Scanned by `sentinel/guards/input.py`; matching spans are replaced before any model sees them; all PR text is wrapped in `<untrusted>` tags; injection attempts are a risk feature; the decision does not depend on generated text |
| Hallucinated impact claims | Claims must cite evidence ids the agent was given; every claim is verified by execution or repository facts; refuted claims never count as evidence |
| Secrets in a PR | Detected before analysis (provider-specific patterns and high-entropy assignments); the PR is blocked and nothing is sent to an LLM; secrets are redacted from any text that is logged |
| Oversized PRs flooding the context | Hard size limit rejects the PR; above a soft limit the context is summarised per change unit |
| Fork PRs exfiltrating secrets | Analysis runs on `pull_request`, never `pull_request_target`; forks get no secrets and fall back to the local model or evidence-only mode |
| Slash-command abuse | Commands are honoured only for owners, members and collaborators |
| Unsafe generated patches | Static patch review blocks new dependencies, CI/build edits, `eval`/`exec`, `shell=True`, unsafe deserialisation; patches must pass the affected tests and are opened as drafts for human review |
| Malicious model artifacts | The risk model is stored as JSON, never pickled |

## Known limitations

* Python projects only (the parser, mutation engine and coverage tooling are Python-specific).
* Mutation testing approximates test adequacy; equivalent mutants count as survivors.
* The benchmark's defects are generated (mutations, reverted fixes, signature changes). They
  resemble real defects but are not identical, and the demo project is small.
* The injection scanner is pattern-based; novel phrasings can slip through. This is why the
  decision never depends on model output.
* Free-tier model versions change over time; model ids and prompt versions are recorded in
  every run report.

## Over-trust

A PASS means "the evidence SentinelPR could gather did not suggest risk", not "this change is
correct". The PR comment always shows what was verified, what was refuted, which lines no test
executes and which mutants survived, so reviewers can see the limits of the evidence.
