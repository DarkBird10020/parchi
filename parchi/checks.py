"""The deterministic checks. No AI in this file.

Every check returns a name, pass/fail and a human-readable reason, because the
reason is what ends up in the evidence pack a merchant sends to an issuer.

Order matters: cheap and unambiguous first, so a tampered mandate never gets as
far as a category lookup.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .mandate import (
    CLOCK_SKEW_SECONDS,
    MAX_CART_LINES,
    Cart,
    IntentMandate,
    norm,
    rupees,
    verify,
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NonceStore:
    """Remembers spent nonces so a mandate cannot be replayed.

    In-memory on purpose: a demo should not pretend to have Redis. The
    interface is the part that would survive.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def seen(self, nonce: str) -> bool:
        with self._lock:
            return nonce in self._seen

    def claim(self, nonce: str) -> bool:
        """Atomically spend an unused nonce."""
        with self._lock:
            if nonce in self._seen:
                return False
            self._seen.add(nonce)
            return True

    def spend(self, nonce: str) -> None:
        with self._lock:
            self._seen.add(nonce)

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()


def check_signature(m: IntentMandate, sig_hex: str, pub: Ed25519PublicKey) -> CheckResult:
    ok = verify(m, sig_hex, pub)
    return CheckResult(
        "signature",
        ok,
        "mandate signature valid for the payer's key"
        if ok
        else "mandate signature does not verify - the slip was edited or forged",
    )


def check_expiry(m: IntentMandate, now: int | None = None) -> CheckResult:
    """Expiry, and the two ways a timestamp can be nonsense rather than late.

    A slip whose window closes before it opens, or one dated into the future, is
    not an expired slip - it is a fabricated one, and saying so in the reason is
    what makes the evidence pack useful.
    """
    now = int(now if now is not None else time.time())

    if m.expires_at <= m.issued_at:
        return CheckResult("expiry", False,
                           "mandate expires at or before it was issued - not a valid window")
    if m.issued_at > now + CLOCK_SKEW_SECONDS:
        drift_h = (m.issued_at - now) / 3600
        return CheckResult("expiry", False,
                           f"mandate is dated {drift_h:.1f}h in the future - "
                           "clock skew beyond tolerance, or a backdated slip")

    ok = now < m.expires_at
    return CheckResult(
        "expiry",
        ok,
        f"mandate valid for another {(m.expires_at - now) / 3600:.1f}h"
        if ok
        else f"mandate expired {(now - m.expires_at) / 3600:.1f}h ago",
    )


def check_payee(m: IntentMandate, cart: Cart) -> CheckResult:
    """A mandate is scoped to one merchant. Anyone else holding it is holding
    someone else's permission slip."""
    ok = norm(cart.payee_id) == norm(m.payee_id)
    return CheckResult(
        "payee",
        ok,
        f"payee '{cart.payee_id}' matches the mandate"
        if ok
        else f"mandate authorises '{m.payee_id}', not '{cart.payee_id}'",
    )


def check_method(m: IntentMandate, cart: Cart) -> CheckResult:
    allowed = {norm(x) for x in m.allowed_methods}
    ok = norm(cart.method) in allowed
    return CheckResult(
        "method",
        ok,
        f"method '{cart.method}' is authorised"
        if ok
        else f"method '{cart.method}' not in allowed methods {list(m.allowed_methods)}",
    )


def check_line_items(cart: Cart) -> CheckResult:
    """Every line must be a real, positive, integral amount, and there must be a
    sane number of them.

    Without this, a negative "promotional adjustment" line drags an over-cap cart
    back under the cap, a zero-priced line smuggles an unrequested item through,
    and an empty cart authorises a payment for nothing.
    """
    if not cart.lines:
        return CheckResult("line_items", False, "cart has no line items - nothing to authorise")
    if len(cart.lines) > MAX_CART_LINES:
        return CheckResult("line_items", False,
                           f"cart has {len(cart.lines)} lines, above the {MAX_CART_LINES}-line limit")
    for ln in cart.lines:
        if not isinstance(ln.amount_paise, int) or isinstance(ln.amount_paise, bool):
            return CheckResult("line_items", False,
                               f"'{ln.description}' has a non-integer amount - money is integers")
        if ln.amount_paise <= 0:
            return CheckResult("line_items", False,
                               f"'{ln.description}' has a non-positive amount "
                               f"({ln.amount_paise} paise) - a line cannot subtract from the total")
    return CheckResult("line_items", True,
                       f"{len(cart.lines)} line(s), every amount positive and integral")


def check_category(m: IntentMandate, cart: Cart) -> CheckResult:
    allowed = {norm(c) for c in m.allowed_categories}
    outside = [c for c in cart.categories if norm(c) not in allowed]
    ok = not outside
    return CheckResult(
        "category",
        ok,
        f"every line is within {list(m.allowed_categories)}"
        if ok
        else f"cart contains {outside}, outside allowed categories {list(m.allowed_categories)}",
    )


def check_amount(m: IntentMandate, cart: Cart) -> CheckResult:
    ok = cart.total_paise <= m.max_amount_paise
    return CheckResult(
        "amount_cap",
        ok,
        f"cart {rupees(cart.total_paise)} is within the cap {rupees(m.max_amount_paise)}"
        if ok
        else f"cart {rupees(cart.total_paise)} exceeds the cap {rupees(m.max_amount_paise)}",
    )


def check_nonce(m: IntentMandate, store: NonceStore) -> CheckResult:
    unused = store.claim(m.nonce)
    return CheckResult(
        "nonce_replay",
        unused,
        "nonce unused - this mandate has not been spent before"
        if unused
        else f"nonce {m.nonce[:12]}... already spent - replayed mandate",
    )


def run_all(
    m: IntentMandate,
    sig_hex: str,
    pub: Ed25519PublicKey,
    cart: Cart,
    store: NonceStore,
    now: int | None = None,
) -> list[CheckResult]:
    """Run every deterministic check in order, short-circuiting on first failure.

    Short-circuit is deliberate: the first failing check is the reason code the
    merchant would quote, and running the rest wastes work on a dead request.
    """
    results: list[CheckResult] = []
    for produce in (
        lambda: check_signature(m, sig_hex, pub),
        lambda: check_expiry(m, now),
        lambda: check_payee(m, cart),
        lambda: check_method(m, cart),
        lambda: check_line_items(cart),
        lambda: check_category(m, cart),
        lambda: check_amount(m, cart),
        lambda: check_nonce(m, store),
    ):
        r = produce()
        results.append(r)
        if not r.passed:
            break
    return results


def all_passed(results: Iterable[CheckResult]) -> bool:
    return all(r.passed for r in results)
