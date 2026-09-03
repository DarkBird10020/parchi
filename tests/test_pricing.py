"""Claimed value: what a coupon is worth, and what the shop actually charges.

tests/test_attacks.py covers whether these attacks are refused. These cover the
arithmetic underneath, and one property that the attack suite cannot see: which
check does the refusing, and therefore what the merchant gets told.
"""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from parchi.checks import NonceStore, check_discount, check_prices, run_all
from parchi.mandate import Cart, CartLine, new_mandate, sign
from parchi.pricing import Coupon, CouponBook, PriceBook

NOW = 1_767_225_600
KEY = Ed25519PrivateKey.generate()
PUB = KEY.public_key()


def cart(amount=420_000, code="", claimed=0, category="footwear",
         description="running shoes", quantity=1):
    return Cart((CartLine(description, category, amount, quantity),),
                "upi", "mrc_bluleaf", discount_code=code, discount_paise=claimed)


def mandate(**over):
    kw = dict(payer_id="usr", payee_id="mrc_bluleaf", allowed_methods=("upi",),
              max_amount_paise=500_000, allowed_categories=("footwear",),
              prompt_playback="buy running shoes", issued_at=NOW - 60)
    kw.update(over)
    return new_mandate(**kw)


# --------------------------------------------------------------------------
# what a coupon is actually worth
# --------------------------------------------------------------------------

def test_a_percentage_coupon_is_computed_not_taken_on_trust():
    assert Coupon("X", percent_off=10).value_for(420_000) == 42_000


def test_a_percentage_coupon_respects_its_own_ceiling():
    """The ceiling is the whole reason a 10% code is safe to hand out."""
    c = Coupon("X", percent_off=10, max_discount_paise=50_000)
    assert c.value_for(420_000) == 42_000        # under the ceiling
    assert c.value_for(4_200_000) == 50_000      # capped, not 420,000


def test_a_discount_can_never_exceed_the_cart():
    """A reduction larger than the purchase is a refund in disguise."""
    assert Coupon("X", flat_paise=900_000).value_for(100_000) == 100_000


def test_percentage_and_flat_stack_within_one_code_but_still_cap():
    c = Coupon("X", percent_off=10, flat_paise=10_000, max_discount_paise=30_000)
    assert c.value_for(420_000) == 30_000


def test_coupon_values_stay_integers():
    """Money is paise. A percentage that does not divide evenly must not become
    a float on the way to a comparison."""
    v = Coupon("X", percent_off=33).value_for(100_001)
    assert isinstance(v, int) and v == 33_000


def test_codes_are_matched_case_and_whitespace_insensitively():
    """A customer typing ' save10 ' is not an attacker."""
    book = CouponBook([Coupon("SAVE10", percent_off=10)])
    assert book.get("save10") is not None
    assert book.get("  SaVe10 ") is not None
    assert book.get("SAVE11") is None


# --------------------------------------------------------------------------
# the check
# --------------------------------------------------------------------------

def test_no_code_and_no_discount_is_not_a_finding():
    assert check_discount(cart(), CouponBook(), now=NOW).passed


def test_a_claim_with_no_coupon_book_fails_closed():
    """An unverifiable reduction in what the payer pays is exactly the thing
    this check exists for, so 'I cannot check' has to mean no."""
    r = check_discount(cart(code="SAVE10", claimed=42_000), None, now=NOW)
    assert not r.passed
    assert "no coupon book" in r.reason


def test_the_true_value_is_recomputed_rather_than_believed():
    book = CouponBook([Coupon("SAVE10", percent_off=10)])
    assert check_discount(cart(code="SAVE10", claimed=42_000), book, now=NOW).passed
    off_by_one = check_discount(cart(code="SAVE10", claimed=42_001), book, now=NOW)
    assert not off_by_one.passed


def test_the_reason_names_both_numbers_so_a_human_can_settle_it():
    book = CouponBook([Coupon("SAVE10", percent_off=10)])
    r = check_discount(cart(code="SAVE10", claimed=300_000), book, now=NOW)
    assert "420.00" in r.reason and "3,000.00" in r.reason


@pytest.mark.parametrize("claimed,expected_pass", [(0, False), (-1, False)])
def test_a_code_with_nothing_or_less_than_nothing_taken_off(claimed, expected_pass):
    book = CouponBook([Coupon("SAVE10", percent_off=10)])
    r = check_discount(cart(code="SAVE10", claimed=claimed), book, now=NOW)
    assert r.passed is expected_pass


def test_an_expired_code_is_refused_at_the_boundary():
    book = CouponBook([Coupon("X", flat_paise=10_000, expires_at=NOW)])
    assert check_discount(cart(code="X", claimed=10_000), book, now=NOW).passed
    assert not check_discount(cart(code="X", claimed=10_000), book, now=NOW + 1).passed


# --------------------------------------------------------------------------
# prices
# --------------------------------------------------------------------------

def test_no_price_book_passes_but_says_it_verified_nothing():
    """A claim nobody checked is not a claim that checked out, and the evidence
    pack has to be able to tell them apart."""
    r = check_prices(cart(), None)
    assert r.passed
    assert "not verified" in r.reason


def test_a_configured_empty_price_book_fails_closed():
    r = check_prices(cart(), PriceBook({}))
    assert not r.passed
    assert "empty" in r.reason


def test_a_line_priced_differently_from_the_book_is_refused():
    book = PriceBook({"running shoes": 420_000})
    assert check_prices(cart(amount=420_000), book).passed
    assert not check_prices(cart(amount=1_000), book).passed
    assert not check_prices(cart(amount=480_000), book).passed


def test_price_lookup_survives_ordinary_merchant_variance():
    book = PriceBook({"Running Shoes": 420_000})
    assert check_prices(cart(description=" running shoes ", amount=420_000), book).passed


# --------------------------------------------------------------------------
# ordering, which is the part that is easy to get wrong and invisible when you do
# --------------------------------------------------------------------------

def test_the_discount_is_validated_before_the_cap_is_applied():
    """An inflated discount is a way under any ceiling.

    Rs 12,000 of shoes against a Rs 5,000 cap, with Rs 8,000 claimed off, nets
    Rs 4,000 and would pass `check_amount`. Both orderings end in BLOCK, so the
    attack suite cannot tell them apart. The difference shows up here, in which
    check reports it, and therefore in what the merchant is told: 'over the cap'
    is a budgeting problem, 'this coupon is not worth that' is fraud.
    """
    book = CouponBook([Coupon("SAVE10", percent_off=10, max_discount_paise=50_000)])
    m = mandate()
    c = cart(amount=1_200_000, code="SAVE10", claimed=800_000)
    results = run_all(m, sign(m, KEY), PUB, c, NonceStore(), now=NOW, coupons=book)

    failed = next(r for r in results if not r.passed)
    assert failed.name == "discount", (
        f"the cap reported this instead of the discount check: {failed.name}")
    assert "amount_cap" not in [r.name for r in results], (
        "the cap check ran, which means the discount check did not short-circuit")


def test_an_honest_discount_still_reaches_the_cap_check():
    book = CouponBook([Coupon("SAVE10", percent_off=10)])
    m = mandate()
    c = cart(amount=420_000, code="SAVE10", claimed=42_000)
    results = run_all(m, sign(m, KEY), PUB, c, NonceStore(), now=NOW, coupons=book)
    assert all(r.passed for r in results)
    assert "amount_cap" in [r.name for r in results]


def test_the_cap_applies_to_what_the_payer_actually_pays():
    book = CouponBook([Coupon("HALF", percent_off=50)])
    m = mandate(max_amount_paise=250_000)
    # Rs 4,200 gross, Rs 2,100 off, Rs 2,100 net against a Rs 2,500 cap.
    c = cart(amount=420_000, code="HALF", claimed=210_000)
    results = run_all(m, sign(m, KEY), PUB, c, NonceStore(), now=NOW, coupons=book)
    assert all(r.passed for r in results), [r.reason for r in results if not r.passed]
    assert c.gross_paise == 420_000
    assert c.total_paise == 210_000
