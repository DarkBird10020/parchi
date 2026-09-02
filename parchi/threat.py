"""What kind of attack was that?

A verdict says whether money moves. It does not say whether someone is *trying
something*, and those are different questions with different audiences. A cart
over the cap is an agent with a stale budget. A cart signed by an unregistered
key is someone testing whether the signature check is real. Both come back BLOCK.

This module reads a decision that already happened and names the attempt, so a
fraud team can be told about the second one and not woken for the first.

It classifies, it never decides. Nothing here can change a verdict, and a wrong
label costs an alert rather than a payment. That separation is deliberate: the
detection logic is allowed to be heuristic precisely because the enforcement
logic is not.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

CRITICAL = "critical"
HIGH = "high"
INFO = "info"


@dataclass(frozen=True)
class Threat:
    kind: str
    severity: str
    summary: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "severity": self.severity,
                "summary": self.summary, "detail": self.detail}


# Which failed check means what. The severity is about intent, not about money:
# an expired slip is usually a slow agent, while a forged signature is never an
# accident.
BY_CHECK: dict[str, tuple[str, str, str]] = {
    "signature": (
        "mandate_forgery", CRITICAL,
        "Someone presented a permission slip that was not signed by the payer"),
    "payee": (
        "payee_substitution", CRITICAL,
        "A slip issued for one merchant was presented at another"),
    "agent_identity": (
        "agent_impersonation", CRITICAL,
        "A cart arrived signed by an agent the payer never authorised"),
    "nonce_replay": (
        "replay_attack", HIGH,
        "A slip that had already been spent was presented again"),
    "expiry": (
        "expired_mandate", INFO,
        "A slip was presented outside its validity window"),
    "method": (
        "instrument_abuse", HIGH,
        "The agent tried to pay with an instrument the payer did not authorise"),
    "amount_cap": (
        "cap_breach", HIGH,
        "The agent tried to spend more than the payer allowed"),
    "category": (
        "scope_breach", HIGH,
        "The agent tried to buy outside the approved categories"),
    "line_quantity": (
        "quantity_abuse", HIGH,
        "The agent tried to exhaust the budget through one line's quantity"),
    "line_items": (
        "malformed_cart", HIGH,
        "The cart contained line items that are not a real purchase"),
}

# Text a merchant should never have reason to write, and an attacker has every
# reason to. Matched only to LABEL an attempt the intent check already refused,
# never to refuse one, so a false positive here is a mislabelled alert and not a
# blocked customer.
INJECTION_MARKERS = (
    r"\bai\s+(assistant|agent|shopping)",
    r"\bassistants?\s+(must|should|completing)",
    r"\bsystem\s*[:>]",
    r"ignore\s+(the\s+)?(previous|above|prior)",
    r"pre[-\s]?approved",
    r"already\s+(agreed|authorised|authorized|consented)",
    r"do\s+not\s+(tell|inform|mention)",
    r"\bmust\s+also\s+add\b",
)


def looks_like_injection(text: str) -> list[str]:
    """Which markers a piece of merchant text trips. Empty list means none."""
    lowered = str(text or "").lower()
    return [m for m in INJECTION_MARKERS if re.search(m, lowered)]


def classify(verdict: str, checks: list[dict[str, Any]],
             intent: dict[str, Any] | None,
             merchant_note: str = "") -> Threat | None:
    """Name the attempt behind a decision, or return None if there was none."""
    if verdict != "BLOCK":
        return None

    failed = next((c for c in checks if not c.get("passed")), None)
    if failed is not None:
        entry = BY_CHECK.get(failed.get("name", ""))
        if entry is None:
            return None
        kind, severity, summary = entry
        return Threat(kind, severity, summary, str(failed.get("reason", "")))

    # Every rule passed and the cart was still refused, so the intent check is
    # what caught it. If the product text was also carrying instructions, that is
    # a merchant attacking the agent rather than an agent going astray.
    if intent is not None and not intent.get("match"):
        markers = looks_like_injection(merchant_note)
        if markers:
            return Threat(
                "prompt_injection", CRITICAL,
                "A product page carried instructions aimed at the shopping agent",
                f"{intent.get('reason', '')} | markers: {len(markers)}")
        return Threat(
            "intent_mismatch", HIGH,
            "The agent bought something the payer did not ask for",
            str(intent.get("reason", "")))
    return None


class ProbeDetector:
    """Repetition is its own signal.

    One refused cart is a mistake. Five from the same agent inside a minute is
    someone learning where the wall is, and that is worth telling a fraud team
    even though every individual verdict was correct and no money moved.

    Deliberately in-memory and per-process. A real deployment needs shared state
    across instances, which is a Redis and an operational story rather than a
    hackathon file, and README says so.
    """

    def __init__(self, threshold: int = 5, window_seconds: int = 60) -> None:
        self.threshold = threshold
        self.window = window_seconds
        self._seen: dict[str, deque[float]] = {}

    def record(self, actor: str, now: float | None = None) -> int:
        """Register a refused attempt. Returns how many are inside the window."""
        now = time.time() if now is None else now
        hits = self._seen.setdefault(actor, deque())
        hits.append(now)
        while hits and now - hits[0] > self.window:
            hits.popleft()
        return len(hits)

    def is_probing(self, count: int) -> bool:
        return count >= self.threshold

    def reset(self) -> None:
        self._seen.clear()
