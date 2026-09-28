"""Input guardrails: size limits, secret detection and prompt-injection sanitising.

All PR-authored text (title, description, commit messages, comments and string literals
in the diff) is untrusted. It is scanned, injection attempts are neutralised before any of
it reaches a language model, and secrets are never sent anywhere.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from sentinel.models import GuardEvent

# --- secrets -------------------------------------------------------------------

SECRET_RULES: dict[str, re.Pattern] = {
    "aws-access-key": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "github-fine-grained": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "groq-api-key": re.compile(r"\bgsk_[A-Za-z0-9]{40,}\b"),
    "openai-api-key": re.compile(r"\bsk-(proj-)?[A-Za-z0-9_\-]{32,}\b"),
    "stripe-secret": re.compile(r"\b(sk|rk)_live_[0-9a-zA-Z]{20,}\b"),
    "private-key": re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY( BLOCK)?-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
}
ASSIGNED_SECRET = re.compile(
    r"""(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\b\s*[:=]\s*["']([^"'\s]{12,})["']"""
)
PLACEHOLDER = re.compile(r"(?i)(example|placeholder|dummy|changeme|xxxx|your[_-]|<.*>|\$\{|os\.environ|getenv)")


def shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    return -sum((c / len(text)) * math.log2(c / len(text)) for c in counts.values())


@dataclass
class SecretFinding:
    rule: str
    location: str
    preview: str


def find_secrets(text: str, location: str) -> list[SecretFinding]:
    findings = []
    for rule, pattern in SECRET_RULES.items():
        for match in pattern.finditer(text):
            findings.append(SecretFinding(rule, location, match.group(0)[:4] + "…"))
    for match in ASSIGNED_SECRET.finditer(text):
        value = match.group(2)
        if not PLACEHOLDER.search(value) and shannon_entropy(value) >= 3.5:
            findings.append(SecretFinding(f"assigned-{match.group(1).lower()}", location, value[:3] + "…"))
    return findings


def redact_secrets(text: str) -> str:
    for pattern in SECRET_RULES.values():
        text = pattern.sub("[REDACTED-SECRET]", text)
    return ASSIGNED_SECRET.sub(lambda m: m.group(0).replace(m.group(2), "[REDACTED-SECRET]"), text)


# --- prompt injection ------------------------------------------------------------

INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("override", re.compile(r"(?i)\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|system|your)\b[^.\n]{0,20}\b(instructions?|prompts?|rules?|guidelines?)")),
    ("role-hijack", re.compile(r"(?i)\byou are (now|no longer)\b|\bact as (an?|the)\b|\bnew instructions?\s*:|\bsystem prompt\b")),
    ("verdict-steering", re.compile(r"(?i)\b(ai|llm|bot|automated)[ -]?(reviewer|review|assistant|gate|checker)s?\b[^.\n]{0,40}\b(approve|pass|lgtm|merge|accept|ignore|skip)")),
    ("verdict-steering", re.compile(r"(?i)\b(approve|mark|rate|score|classify)\b[^.\n]{0,30}\b(this|the)\b[^.\n]{0,20}\b(as )?(safe|low[- ]risk|pass(ed)?|approved)\b")),
    ("verdict-steering", re.compile(r"(?i)\b(return|output|respond with|set)\b[^.\n]{0,20}\b(risk|decision|verdict|risk_rating)\b[^.\n]{0,20}\b(0(\.0+)?|low|pass|none)\b")),
    ("chat-markup", re.compile(r"<\|?(im_start|im_end|system|endoftext)\|?>|\[/?INST\]|<</?SYS>>|^\s*###\s*(system|instruction)", re.IGNORECASE | re.MULTILINE)),
    ("exfiltration", re.compile(r"(?i)\b(print|reveal|show|repeat|leak)\b[^.\n]{0,30}\b(system prompt|your instructions|api key|secrets?|environment variables)\b")),
]

COMMENT_OR_STRING = re.compile(r"#.*$|\"\"\".*?\"\"\"|'''.*?'''|\"[^\"\n]*\"|'[^'\n]*'", re.MULTILINE | re.DOTALL)


@dataclass
class InjectionFinding:
    kind: str
    location: str
    excerpt: str


def find_injections(text: str, location: str) -> list[InjectionFinding]:
    findings = []
    for kind, pattern in INJECTION_PATTERNS:
        for match in pattern.finditer(text or ""):
            findings.append(InjectionFinding(kind, location, match.group(0)[:80]))
    return findings


def neutralise(text: str) -> str:
    """Replace injection spans so a model sees that something was removed, not the instruction."""
    for _, pattern in INJECTION_PATTERNS:
        text = pattern.sub("[removed: suspected prompt injection]", text or "")
    return text


# --- orchestration ---------------------------------------------------------------


@dataclass
class InputGuardResult:
    events: list[GuardEvent] = field(default_factory=list)
    rejected: bool = False
    reject_reason: str = ""
    truncate_context: bool = False
    title: str = ""
    body: str = ""
    injection_attempts: int = 0
    secrets_found: int = 0


def scan_inputs(title: str, body: str, commit_messages: list[str], added_text: dict[str, str], total_diff_lines: int, files_changed: int, cfg) -> InputGuardResult:
    """``added_text`` maps file path -> the lines the PR adds to it (joined)."""
    result = InputGuardResult()
    max_lines = int(cfg.get("guards.max_diff_lines", 2500))
    hard_limit = int(cfg.get("guards.hard_limit_lines", 15000))
    max_files = int(cfg.get("guards.max_files", 150))

    if total_diff_lines > hard_limit or files_changed > max_files:
        result.rejected = True
        result.reject_reason = f"diff too large to analyse ({total_diff_lines} lines, {files_changed} files; limits {hard_limit} lines / {max_files} files)"
        result.events.append(GuardEvent(guard="size", stage="in", severity="block", message=result.reject_reason))
    elif total_diff_lines > max_lines:
        result.truncate_context = True
        result.events.append(GuardEvent(guard="size", stage="in", severity="warn",
                                        message=f"large diff ({total_diff_lines} lines): LLM context is summarised per change unit"))

    secrets: list[SecretFinding] = []
    for location, text in [("pr.title", title), ("pr.body", body)] + [(f"commit[{i}]", m) for i, m in enumerate(commit_messages)]:
        secrets += find_secrets(text or "", location)
    for path, text in added_text.items():
        secrets += find_secrets(text, path)
    for s in secrets:
        result.events.append(GuardEvent(guard="secret", stage="in", severity="block", message=f"possible {s.rule} ({s.preview})", location=s.location))
    result.secrets_found = len(secrets)
    if secrets:
        result.rejected = True
        result.reject_reason = result.reject_reason or f"{len(secrets)} possible secret(s) in the change; nothing was sent to an LLM"

    injections: list[InjectionFinding] = []
    for location, text in [("pr.title", title), ("pr.body", body)] + [(f"commit[{i}]", m) for i, m in enumerate(commit_messages)]:
        injections += find_injections(text, location)
    for path, text in added_text.items():
        for match in COMMENT_OR_STRING.finditer(text):
            injections += find_injections(match.group(0), path)
    for inj in injections:
        result.events.append(GuardEvent(guard="injection", stage="in", severity="warn",
                                        message=f"{inj.kind}: \"{inj.excerpt}\"", location=inj.location))
    result.injection_attempts = len(injections)
    result.title = redact_secrets(neutralise(title))[:300]
    result.body = redact_secrets(neutralise(body))[:4000]
    return result
