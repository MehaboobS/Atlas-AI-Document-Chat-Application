"""
Atlas AI - Deterministic Input & Output Guardrails
Enforces prompt-injection defense, sensitive-request Human-in-the-Loop (HITL) detection
(including document deletion/removal requests), and secret leakage scrubbing.
"""

import re
from typing import Any
from app.observability import trace

MAX_QUESTION_LENGTH = 4000

# Deterministic prompt-injection & jailbreak patterns
INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(all\s+|previous\s+|prior\s+)?instructions\b", re.IGNORECASE),
    re.compile(r"\bdisregard\s+(all\s+|previous\s+|prior\s+)?(instructions|rules)\b", re.IGNORECASE),
    re.compile(r"\breveal\s+(your\s+|the\s+)?(system\s+prompt|hidden\s+prompt|instructions)\b", re.IGNORECASE),
    re.compile(r"\bbypass\s+(all\s+)?(guardrails|security|filters|restrictions)\b", re.IGNORECASE),
    re.compile(r"\bjailbreak\b|\bDAN\s+mode\b", re.IGNORECASE),
    re.compile(r"<\s*script\b", re.IGNORECASE),
]

# Destructive document management requests requiring Human-in-the-Loop approval
DELETE_ACTION_PATTERNS = [
    re.compile(
        r"\b(delete|remove|erase|purge|retire|destroy)\b.*\b(doc|document|documents|file|files|record|records|pdf|docx|txt|all)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*(delete|remove|erase|purge|retire)\s+\S+", re.IGNORECASE),
]

# High-risk / sensitive actions routed to Human-in-the-Loop (HITL) approval
SENSITIVE_PATTERNS = [
    *DELETE_ACTION_PATTERNS,
    re.compile(r"\b(salary|salaries|payroll|compensation|ssn|social\s+security)\b", re.IGNORECASE),
    re.compile(r"\b(purge\s+database|drop\s+table|bulk\s+export)\b", re.IGNORECASE),
    re.compile(r"\b(override\s+policy|policy\s+exception|waive\s+compliance)\b", re.IGNORECASE),
    re.compile(r"\b(api\s*key|secret\s*key|password|credentials|private\s*key)\b", re.IGNORECASE),
]

# Secret / credential patterns scrubbed from model output
SECRET_OUTPUT_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z\-_]{20,}"),
    re.compile(r"AQ\.[0-9A-Za-z\-_]{20,}"),
    re.compile(r"pcsk_[0-9A-Za-z\-_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ri_[0-9A-Za-z\-_\.]{20,}"),
    re.compile(r"postgresql://[^\s]+"),
    re.compile(r"hf_[0-9A-Za-z]{20,}"),
]


def is_delete_action(question: str) -> bool:
    """Return True if the prompt is requesting document deletion or removal."""
    text = (question or "").strip()
    return any(p.search(text) for p in DELETE_ACTION_PATTERNS)


@trace("guardrail_input")
def check_input(question: str) -> dict[str, Any]:
    """
    Validate incoming user question deterministically.
    Returns:
        allowed (bool): False if empty, oversized, or prompt-injection detected.
        requires_approval (bool): True if sensitive topic or delete action requires HITL review.
        reason (str): Human-readable explanation when blocked or flagged.
        sanitized (str): Normalized question string.
    """
    text = (question or "").strip()
    if not text:
        return {
            "allowed": False,
            "requires_approval": False,
            "reason": "Question cannot be empty.",
            "sanitized": "",
        }

    if len(text) > MAX_QUESTION_LENGTH:
        return {
            "allowed": False,
            "requires_approval": False,
            "reason": f"Question exceeds maximum length of {MAX_QUESTION_LENGTH} characters.",
            "sanitized": text[:MAX_QUESTION_LENGTH],
        }

    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return {
                "allowed": False,
                "requires_approval": False,
                "reason": "Request blocked by security guardrails: suspected prompt injection or policy bypass attempt.",
                "sanitized": text,
            }

    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            return {
                "allowed": True,
                "requires_approval": True,
                "reason": "Sensitive request detected; queued for supervisor HITL approval.",
                "sanitized": text,
            }

    return {
        "allowed": True,
        "requires_approval": False,
        "reason": "",
        "sanitized": text,
    }


@trace("guardrail_output")
def check_output(answer_text: str, evidence_count: int = 0) -> dict[str, Any]:
    """
    Inspect and sanitize outgoing LLM response.
    Redacts any accidental credential or secret patterns and enforces grounding.
    """
    cleaned = (answer_text or "").strip()
    redacted = False

    for pattern in SECRET_OUTPUT_PATTERNS:
        if pattern.search(cleaned):
            cleaned = pattern.sub("[REDACTED_SECRET]", cleaned)
            redacted = True

    if evidence_count == 0 and not cleaned:
        cleaned = "I could not find an authorized answer in your uploaded documents."

    return {
        "safe": not redacted,
        "text": cleaned,
        "redacted": redacted,
    }
