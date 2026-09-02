"""Orchestrates: checks -> llm -> verdict -> ledger.

Three answers, not two. A system with only allow and block is a filter; the
third answer - ask the human - is what makes it a risk product.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .checks import CheckResult, NonceStore, all_passed, run_all
from .intent_match import IntentVerdict, intent_matches
from .ledger import Ledger
from .mandate import STEP_UP_PAISE, Cart, IntentMandate, rupees

ALLOW = "ALLOW"
BLOCK = "BLOCK"
STEP_UP = "STEP_UP"


@dataclass
class Decision:
    verdict: str
    reason: str
    checks: list[CheckResult]
    intent: IntentVerdict | None
    degraded: bool
    ledger_hash: str | None = None
    txn_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "txn_id": self.txn_id,
            "verdict": self.verdict,
            "reason": self.reason,
            "checks": [c.to_dict() for c in self.checks],
            "intent": self.intent.to_dict() if self.intent else None,
            "degraded": self.degraded,
            "ledger_hash": self.ledger_hash,
        }


class Engine:
    def __init__(
        self,
        ledger: Ledger | None = None,
        nonces: NonceStore | None = None,
        provider: str = "auto",
        timeout: float = 4.0,
        step_up_paise: int = STEP_UP_PAISE,
        use_intent: bool = True,
        model: str | None = None,
    ) -> None:
        self.ledger = ledger
        self.nonces = nonces or NonceStore()
        self.provider = provider
        self.timeout = timeout
        self.step_up_paise = step_up_paise
        self.use_intent = use_intent
        self.model = model

    def authorize(
        self,
        mandate: IntentMandate,
        signature: str,
        pub: Ed25519PublicKey,
        cart: Cart,
        now: int | None = None,
        txn_id: str | None = None,
    ) -> Decision:
        checks = run_all(mandate, signature, pub, cart, self.nonces, now=now)
        intent: IntentVerdict | None = None

        if not all_passed(checks):
            failed = next(c for c in checks if not c.passed)
            decision = Decision(BLOCK, failed.reason, checks, None, False, txn_id=txn_id)
        else:
            # Rules are satisfied. Now the one question rules cannot answer.
            if self.use_intent:
                intent = intent_matches(
                    mandate, cart, timeout=self.timeout, provider=self.provider,
                    model=self.model,
                )
            if intent is not None and not intent.match and intent.degraded:
                # Failing closed does not have to mean losing the customer. The
                # intent check could not run, so nothing is auto-approved - but
                # the answer is "ask the human", not "refuse a purchase we never
                # actually found anything wrong with".
                decision = Decision(
                    STEP_UP, f"{intent.reason} - routing to the human rather than auto-approving",
                    checks, intent, True, txn_id=txn_id,
                )
            elif intent is not None and not intent.match:
                decision = Decision(
                    BLOCK, f"cart does not match the authorised intent: {intent.reason}",
                    checks, intent, intent.degraded, txn_id=txn_id,
                )
            elif cart.total_paise >= self.step_up_paise:
                # Everything checks out, but this is real money. Ask the human.
                decision = Decision(
                    STEP_UP,
                    f"authorised, but {rupees(cart.total_paise)} is at or above the "
                    f"step-up threshold {rupees(self.step_up_paise)} - confirm with the human",
                    checks, intent, intent.degraded if intent else False, txn_id=txn_id,
                )
            else:
                decision = Decision(
                    ALLOW,
                    "mandate valid and cart within the authorised intent",
                    checks, intent, intent.degraded if intent else False, txn_id=txn_id,
                )

            # A nonce is spent the moment the slip clears the rules, whatever
            # the final verdict. Otherwise a blocked cart leaves a live mandate
            # behind for a second attempt.
            self.nonces.spend(mandate.nonce)

        # Write to the ledger regardless of verdict. A log that only records
        # refusals proves nothing about the approvals.
        if self.ledger is not None:
            rec = self.ledger.append(
                mandate_id=mandate.mandate_id,
                txn={
                    "txn_id": txn_id,
                    "payee_id": cart.payee_id,
                    "method": cart.method,
                    "total_paise": cart.total_paise,
                    "lines": [ln.to_dict() for ln in cart.lines],
                },
                checks=[c.to_dict() for c in decision.checks],
                verdict=decision.verdict,
                degraded=decision.degraded,
                intent=intent.to_dict() if intent else None,
            )
            decision.ledger_hash = rec["hash"]
        return decision
