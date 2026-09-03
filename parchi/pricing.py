"""Claimed value: discounts, coupons, loyalty redemptions, and line prices.

Every check up to here asked "is this the thing the human asked for". This file
asks a different question: "is this cart telling the truth about what it costs".

The two are not the same, and the second one has its own attacks. A cart is
assembled by an agent out of numbers a merchant supplied, and both of those are
untrusted. An agent that claims a Rs 2,000 discount on a Rs 500 coupon, or writes
a line price the shop never charged, produces a cart where every other check
passes and the arithmetic is still a lie.

Why this runs BEFORE the cap check
----------------------------------
The cap is enforced on what the payer actually pays, which is the total after
discounts. So an unvalidated discount is a way through the cap: claim a large
enough reduction and any cart fits under any ceiling. Validating the claim first
is not a stylistic ordering choice, it is the reason the cap check means anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from .mandate import norm


@dataclass(frozen=True)
class Coupon:
    """One entry in the merchant's own book of what it has agreed to give away.

    `kind` separates a marketing coupon from a loyalty redemption because they
    fail differently: a forged coupon costs the merchant margin, a forged loyalty
    redemption is theft from another customer's balance.
    """

    code: str
    kind: str = "coupon"              # "coupon" | "loyalty"
    percent_off: int = 0              # 0-100
    flat_paise: int = 0
    max_discount_paise: int = 0       # 0 means no ceiling
    min_spend_paise: int = 0
    categories: tuple = ()            # empty means any category
    expires_at: int = 0               # unix seconds, 0 means never
    # Is this code advertised to everyone, or issued to one customer?
    #
    # It changes nothing about what the coupon is worth, and everything about
    # what heavy use of it means. Many payers on a public code is a sale doing
    # its job. Many payers on a single-issue code is a code that leaked. The
    # behavioural layer cannot tell those apart by counting, so the merchant's
    # own book has to say which one it is, rather than leaving a model to guess
    # from the name.
    public: bool = False

    def value_for(self, gross_paise: int) -> int:
        """What this coupon is actually worth on a cart of this size.

        Integer arithmetic throughout, and the result can never exceed the cart:
        a discount larger than the purchase is a refund wearing a discount's
        clothes.
        """
        value = self.flat_paise
        if self.percent_off:
            value += gross_paise * self.percent_off // 100
        if self.max_discount_paise:
            value = min(value, self.max_discount_paise)
        return max(0, min(value, gross_paise))


class CouponBook:
    """The codes a merchant will honour. Anything not in here is not a discount."""

    def __init__(self, coupons: list[Coupon] | None = None) -> None:
        self._by_code: dict[str, Coupon] = {}
        for coupon in coupons or []:
            self.add(coupon)

    def add(self, coupon: Coupon) -> None:
        self._by_code[norm(coupon.code)] = coupon

    def get(self, code: str) -> Coupon | None:
        return self._by_code.get(norm(code))

    def __len__(self) -> int:
        return len(self._by_code)

    def is_public(self, code: str) -> bool | None:
        """Whether this code is advertised publicly. None if it is not ours.

        `None` rather than False for an unknown code, because "we have never
        heard of this code" and "this code is not public" are different facts
        and a caller that conflates them is guessing.
        """
        coupon = self.get(code)
        return None if coupon is None else bool(coupon.public)


class PriceBook:
    """What the shop charges, so a cart cannot invent its own prices.

    Line prices reach Parchi through the agent, and an agent that understates
    them slides an expensive cart under the cap while the merchant still settles
    the real amount. Nothing else in the checkpoint can see that, because every
    other check trusts the number in the cart.
    """

    def __init__(self, prices: dict[str, int] | None = None) -> None:
        # Keyed on the normalised description, which is what a cart line carries.
        self._prices = {norm(k): int(v) for k, v in (prices or {}).items()}

    def get(self, description: str) -> int | None:
        return self._prices.get(norm(description))

    def __len__(self) -> int:
        return len(self._prices)
