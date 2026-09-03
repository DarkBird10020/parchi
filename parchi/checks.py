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

from .agents import AgentRegistry
from .mandate import (
    CLOCK_SKEW_SECONDS,
    MAX_CART_LINES,
    MAX_LINE_QUANTITY,
    Cart,
    IntentMandate,
    norm,
    rupees,
    verify,
    verify_cart,
)
from .pricing import CouponBook, PriceBook


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


def check_agent(m: IntentMandate, cart: Cart, agents: AgentRegistry | None) -> CheckResult:
    """If the mandate names an allowed agent, the cart must be signed by that
    agent's key.

    This check is what turns "my agent did that, I didn't" into a verifiable
    answer: either the agent's key signed this exact cart, or the claim is
    repudiable.
    """
    if not m.allowed_agent_id:
        return CheckResult("agent_identity", True, "no agent identity required on this mandate")
    if agents is None:
        # Refuse, but say the true reason. Reporting this as "wrong agent" sends
        # a fraud team hunting an impersonator when the actual fault is that
        # nobody configured a registry, and the evidence pack would have carried
        # that wrong story into a dispute.
        return CheckResult(
            "agent_identity",
            False,
            f"mandate requires agent '{m.allowed_agent_id}' but no agent registry "
            f"is configured to verify one",
        )
    if cart.agent_id != m.allowed_agent_id:
        return CheckResult(
            "agent_identity",
            False,
            f"cart presented by agent '{cart.agent_id}', but mandate allows '{m.allowed_agent_id}'",
        )
    pub = agents.get(cart.agent_id)
    if pub is None:
        return CheckResult(
            "agent_identity",
            False,
            f"agent '{cart.agent_id}' is not registered",
        )
    if not verify_cart(cart, cart.agent_signature, pub):
        return CheckResult(
            "agent_identity",
            False,
            f"agent '{cart.agent_id}' signature does not verify for this cart",
        )
    return CheckResult(
        "agent_identity",
        True,
        f"agent '{cart.agent_id}' signature verified",
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


def check_line_quantity(cart: Cart) -> CheckResult:
    """Every line must have a positive, sane quantity.

    Quantity inflation is mostly an intent question, but a runaway quantity is
    blocked here so the checkpoint does not depend on the model to catch a cart
    with 1,000 identical allowed items.
    """
    for ln in cart.lines:
        if not isinstance(ln.quantity, int) or isinstance(ln.quantity, bool):
            return CheckResult("line_quantity", False,
                               f"'{ln.description}' has a non-integer quantity")
        if ln.quantity <= 0:
            return CheckResult("line_quantity", False,
                               f"'{ln.description}' has a non-positive quantity")
        if ln.quantity > MAX_LINE_QUANTITY:
            return CheckResult("line_quantity", False,
                               f"'{ln.description}' has quantity {ln.quantity}, "
                               f"above the {MAX_LINE_QUANTITY} limit")
    return CheckResult("line_quantity", True,
                       f"every line quantity is positive and within {MAX_LINE_QUANTITY}")



def check_prices(cart: Cart, prices: PriceBook | None) -> CheckResult:
    """Every line must cost what the shop says it costs.

    Line prices arrive through the agent, and an agent that understates them
    slides an expensive cart under the cap while the merchant settles the real
    amount. No other check can see it: they all trust the number in the cart.

    With no price book this passes, and says so rather than pretending. A claim
    nobody can check is not the same as a claim that checked out, and the
    difference belongs in the evidence pack.
    """
    if prices is None:
        return CheckResult("prices", True, "no price book configured, line prices not verified")
    if not len(prices):
        return CheckResult("prices", False, "configured price book is empty")
    for line in cart.lines:
        listed = prices.get(line.description)
        if listed is None:
            return CheckResult("prices", False,
                               f"'{line.description}' is not in this merchant's price book")
        if int(line.amount_paise) != listed:
            return CheckResult(
                "prices", False,
                f"'{line.description}' is listed at {rupees(listed)} but the cart "
                f"says {rupees(line.amount_paise)}")
    return CheckResult("prices", True,
                       f"all {len(cart.lines)} line price(s) match the merchant's book")


def check_discount(cart: Cart, coupons: CouponBook | None,
                   now: int | None = None) -> CheckResult:
    """A claimed reduction has to be one the merchant actually agreed to.

    Runs before the amount check on purpose. The cap applies to what the payer
    pays, so an unvalidated discount is a way under any ceiling: claim a large
    enough reduction and any cart fits.
    """
    now = int(time.time() if now is None else now)
    claimed = int(cart.discount_paise or 0)
    code = (cart.discount_code or "").strip()

    if not code and not claimed:
        return CheckResult("discount", True, "no discount claimed")
    if claimed < 0:
        return CheckResult("discount", False,
                           "negative discount, which is a surcharge in disguise")
    if claimed and not code:
        return CheckResult("discount", False,
                           f"{rupees(claimed)} taken off with no code to justify it")
    if coupons is None or not len(coupons):
        # Fail closed. An unverifiable reduction in what the payer pays is
        # exactly the thing this check exists for.
        return CheckResult("discount", False,
                           f"code '{code}' claimed but this merchant has no coupon book")

    coupon = coupons.get(code)
    if coupon is None:
        return CheckResult("discount", False, f"code '{code}' is not a code this merchant issued")
    if coupon.expires_at and now > coupon.expires_at:
        return CheckResult("discount", False, f"code '{code}' expired")

    gross = cart.gross_paise
    if coupon.min_spend_paise and gross < coupon.min_spend_paise:
        return CheckResult("discount", False,
                           f"code '{code}' needs a spend of {rupees(coupon.min_spend_paise)}, "
                           f"cart is {rupees(gross)}")
    if coupon.categories:
        allowed = {norm(c) for c in coupon.categories}
        outside = [c for c in cart.categories if norm(c) not in allowed]
        if outside:
            return CheckResult("discount", False,
                               f"code '{code}' does not apply to {outside}")

    true_value = coupon.value_for(gross)
    if claimed != true_value:
        return CheckResult(
            "discount", False,
            f"code '{code}' is worth {rupees(true_value)} on this cart, "
            f"but {rupees(claimed)} was taken off")
    return CheckResult("discount", True,
                       f"{coupon.kind} '{code}' verified at {rupees(true_value)}")


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
    agents: AgentRegistry | None = None,
    coupons: CouponBook | None = None,
    prices: PriceBook | None = None,
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
        lambda: check_line_quantity(cart),
        lambda: check_prices(cart, prices),
        lambda: check_category(m, cart),
        # Before the cap, always. The cap is enforced on the post-discount total,
        # so an unvalidated reduction is a way under any ceiling.
        lambda: check_discount(cart, coupons, now),
        lambda: check_amount(m, cart),
        lambda: check_agent(m, cart, agents),
        lambda: check_nonce(m, store),
    ):
        r = produce()
        results.append(r)
        if not r.passed:
            break
    return results


def all_passed(results: Iterable[CheckResult]) -> bool:
    return all(r.passed for r in results)
