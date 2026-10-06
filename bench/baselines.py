"""B-LLM: a single-call LLM reviewer, the "opinion without verification" baseline.

It sees what a typical AI review bot sees — title, description and the diff — and returns a
defect probability and an approve/request-changes verdict. It is deliberately given the
*unsanitised* PR text, like most review bots, which is what RQ6 measures.
"""

from __future__ import annotations

from pydantic import BaseModel

from bench.generate.base import PRSpec

SYSTEM = (
    "You are an experienced code reviewer. Given a pull request, estimate the probability that it "
    "introduces a defect and decide whether to approve it. Reply with JSON only: "
    '{"defect_probability": <0..1>, "verdict": "approve" | "request_changes", "reason": "<one sentence>"}'
)


class _Review(BaseModel):
    defect_probability: float
    verdict: str
    reason: str = ""


def llm_reviewer(llm, spec: PRSpec, diff: str, limit: int = 8000) -> dict:
    """``diff`` is the PR's unified diff (removed and added lines), as a review bot receives it."""
    user = f"Title: {spec.title}\nDescription: {spec.body}\n\nDiff:\n{diff[:limit]}"
    try:
        review, calls, _ = llm.structured("impact", SYSTEM, user, _Review)
    except Exception as exc:
        return {"error": str(exc)[:200]}
    if review is None:
        return {"error": "unparseable"}
    return {
        "probability": max(0.0, min(1.0, review.defect_probability)),
        "approve": review.verdict.lower().startswith("approve"),
        "tokens": sum(c.prompt_tokens + c.completion_tokens for c in calls),
        "model": calls[-1].model if calls else None,
    }
