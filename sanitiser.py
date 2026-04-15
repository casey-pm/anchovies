"""
Input sanitiser for Anchovies.

Detects suspicious patterns in user input that suggest prompt-injection
attempts. The goal is detection and logging, NOT blocking — legitimate
messages can contain words like "ignore" or "system" in normal contexts,
so blocking would cause too many false positives.

Use:
    result = scan_message(user_message)
    if result.suspicious:
        logger.warning(f"Suspicious input from {user}: {result.matches}")
        storage.log_event("suspicious_input", details={...})
    # Always pass the message through to the LLM regardless
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# Patterns that strongly suggest prompt injection. Each entry is a tuple of
# (name, regex). Names go into the audit log and are useful for telemetry.
INJECTION_PATTERNS: list[tuple[str, str]] = [
    # Direct instruction overrides
    ("override_instructions", r"\bignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|rules?|messages?)\b"),
    ("forget_rules", r"\bforget\s+(?:all\s+)?(?:your\s+)?(?:rules?|instructions?|guidelines?|persona)\b"),
    ("disregard", r"\bdisregard\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier|safety|system)\b"),

    # Persona / system replacement
    ("you_are_now", r"\byou\s+are\s+now\s+(?:a\s+|an\s+)?(?!one\s)(?!an\s+anchovies)\w+"),
    ("new_persona", r"\bact\s+as\s+(?:a\s+|an\s+)?(?:different|new|another)\b"),
    ("system_prompt_leak", r"\b(?:show|reveal|tell|print|display|output)\s+(?:me\s+)?(?:the\s+|your\s+)?(?:system\s+prompt|original\s+prompt|instructions?\s+(?:you|that)\s+(?:were|was)\s+given)\b"),
    ("system_role", r"\bSystem\s*:\s*you\s+(?:are|must|will|should)\b"),

    # Role/tag injection
    ("tag_injection", r"</(?:system|user|assistant|instruction)s?>"),
    ("role_injection", r"<\|(?:system|user|assistant|im_start|im_end)\|>"),

    # Override authority claims
    ("admin_override", r"\b(?:as|i\s+am)\s+(?:the\s+)?(?:admin|administrator|developer|owner|root)\b"),

    # Goal hijacking
    ("new_goal", r"\bnew\s+(?:goal|task|objective|mission)\s*:"),
    ("from_now_on", r"\bfrom\s+now\s+on,?\s+you\s+(?:will|must|are|should)\b"),
]

COMPILED_PATTERNS: list[tuple[str, re.Pattern]] = [
    (name, re.compile(pattern, re.IGNORECASE | re.MULTILINE))
    for name, pattern in INJECTION_PATTERNS
]


@dataclass
class ScanResult:
    """Result of scanning a message for injection patterns."""
    message: str
    suspicious: bool
    matches: list[str] = field(default_factory=list)  # names of matched patterns
    snippets: list[str] = field(default_factory=list)  # actual matched text

    def to_audit_details(self) -> dict:
        """Format for storage.log_event details."""
        return {
            "matched_patterns": self.matches,
            "snippets": self.snippets[:5],  # cap to avoid bloat
            "message_preview": self.message[:200],
        }


def scan_message(message: str) -> ScanResult:
    """
    Scan a message for prompt injection patterns.

    Args:
        message: The user message to scan

    Returns:
        ScanResult with detected patterns. Suspicious if any pattern matched.
    """
    matches: list[str] = []
    snippets: list[str] = []

    for name, pattern in COMPILED_PATTERNS:
        m = pattern.search(message)
        if m:
            matches.append(name)
            snippets.append(m.group(0))

    return ScanResult(
        message=message,
        suspicious=len(matches) > 0,
        matches=matches,
        snippets=snippets,
    )


def log_if_suspicious(
    message: str,
    source: str = "unknown",
    member: Optional[str] = None,
) -> ScanResult:
    """
    Convenience wrapper: scan a message, log to audit trail if suspicious.

    Args:
        message: The text to scan
        source: Description of where the message came from (e.g., "slack:dm:U123")
        member: Persona name if applicable

    Returns:
        ScanResult — the caller decides whether to block or pass through.
        (Default behaviour is to pass through and just log.)
    """
    result = scan_message(message)
    if result.suspicious:
        # Lazy import to avoid circular dependency
        try:
            from .storage import get_storage
            storage = get_storage()
            storage.log_event(
                "suspicious_input",
                member=member,
                details={**result.to_audit_details(), "source": source},
            )
        except Exception as e:
            logger.error(f"Failed to log suspicious input: {e}")
        logger.warning(
            f"Suspicious input detected from {source}: "
            f"patterns={result.matches} snippets={result.snippets[:3]}"
        )
    return result
