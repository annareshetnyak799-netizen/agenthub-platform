"""Request guardrail evaluation.

All text is NFKC-normalised before matching so that unicode homoglyphs,
fullwidth characters, and composed forms do not bypass regex patterns.
For example "ｉｇｎｏｒｅ" (fullwidth) normalises to "ignore" before matching.

Evaluation order: prompt_injection → secret_leakage → policy_violation.
The first matching pattern wins and the request is blocked (HTTP 400).
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GuardrailDecision:
    blocked: bool
    category: str = ""
    reason: str = ""
    matched_pattern: str = ""


INJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("ignore_previous_instructions", r"ignore\s+(all\s+)?previous\s+instructions"),
    ("reveal_system_prompt", r"(reveal|show|print|output).{0,40}(system\s*prompt|hidden\s*prompt)"),
    # Require an action verb to reduce false positives on explanatory text.
    (
        "developer_message_access",
        r"(access|read|get|extract|steal|dump|retrieve).{0,30}(developer\s*message|system\s*message)",
    ),
    ("safety_bypass", r"(bypass|disable|ignore|circumvent|override).{0,40}(safety|guardrail|policy|filter)"),
    # Role-play / persona hijack patterns.
    ("jailbreak_roleplay", r"\b(do\s*anything\s*now|pretend\s+you\s+are|act\s+as\s+if\s+you\s+have\s+no)"),
    # Prompt continuation attacks.
    ("prompt_continuation", r"(end\s+of\s+(system\s+)?prompt|start\s+of\s+(new\s+)?instructions)"),
)

SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("openai_key", r"\bsk-[A-Za-z0-9]{16,}\b"),
    ("aws_access_key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("private_key", r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ("api_key_assignment", r"\bapi[_-]?key\s*[:=]\s*\S+"),
    # Require a value of ≥8 non-whitespace, non-quote chars to avoid blocking
    # JSON schemas, documentation, or any text that merely mentions "password".
    ("password_assignment", r"\bpassword\s*[:=]\s*[^\s\"',]{8,}"),
    # GitHub / GitLab / Hugging Face token patterns.
    ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    ("generic_bearer", r"\bBearer\s+[A-Za-z0-9\-._~+/]{32,}\b"),
)

EXFILTRATION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("exfiltrate_secret", r"(dump|exfiltrate|export|leak|send).{0,40}(secret|credential|token|key)"),
    ("show_env_vars", r"(print|show|list|output|display).{0,40}(environment\s*variables|env\s*vars|secrets)"),
)


def _iter_text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(_iter_text_values(item))
        return items
    if isinstance(value, dict):
        items = []
        for nested_value in value.values():
            items.extend(_iter_text_values(nested_value))
        return items
    return []


def flatten_request_text(payload: dict[str, Any]) -> str:
    parts = _iter_text_values(payload)
    return "\n".join(part for part in parts if part).strip()


def _normalise(text: str) -> str:
    """NFKC-normalise text to collapse unicode homoglyphs and fullwidth chars."""
    return unicodedata.normalize("NFKC", text)


def evaluate_guardrails(payload: dict[str, Any]) -> GuardrailDecision:
    raw_text = flatten_request_text(payload)
    if not raw_text:
        return GuardrailDecision(blocked=False)

    # Normalise once; all patterns are matched against the normalised form.
    text = _normalise(raw_text)

    for category, patterns in (
        ("prompt_injection", INJECTION_PATTERNS),
        ("secret_leakage", SECRET_PATTERNS),
        ("policy_violation", EXFILTRATION_PATTERNS),
    ):
        for reason, pattern in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
                return GuardrailDecision(
                    blocked=True,
                    category=category,
                    reason=reason,
                    matched_pattern=pattern,
                )

    return GuardrailDecision(blocked=False)
