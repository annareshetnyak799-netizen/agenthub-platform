import re
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
    ("reveal_system_prompt", r"(reveal|show|print).{0,40}(system prompt|hidden prompt)"),
    ("developer_message_access", r"(developer message|system message)"),
    ("safety_bypass", r"(bypass|disable|ignore).{0,40}(safety|guardrail|policy)"),
)

SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("openai_key", r"\bsk-[A-Za-z0-9]{16,}\b"),
    ("aws_access_key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("private_key", r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ("api_key_assignment", r"\bapi[_-]?key\s*[:=]\s*\S+"),
    ("password_assignment", r"\bpassword\s*[:=]\s*\S+"),
)

EXFILTRATION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("exfiltrate_secret", r"(dump|exfiltrate|export|leak).{0,40}(secret|credential|token|key)"),
    ("show_env_vars", r"(print|show|list).{0,40}(environment variables|env vars|secrets)"),
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


def evaluate_guardrails(payload: dict[str, Any]) -> GuardrailDecision:
    text = flatten_request_text(payload)
    if not text:
        return GuardrailDecision(blocked=False)

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
