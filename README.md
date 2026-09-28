# SentinelPR

**Self-verifying, evidence-gated release intelligence for pull requests.**

SentinelPR reads every pull request, lets LLM agents make *claims* about what the change
affects and why the code looks the way it does, **verifies those claims by executing tests and
targeted mutations**, turns the verified evidence into a calibrated release-risk score, and gates
the merge (`PASS` / `CANARY` / `BLOCK`) — followed by a canary rollout with automatic rollback.

> LLMs propose. Execution verifies. A calibrated model decides. The pipeline acts. A human approves.

Work in progress — full documentation lands as the components come together.
