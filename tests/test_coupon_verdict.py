"""Coupon abuse that can be settled by counting, and is.

These cases used to be put to the adjudicator. That was a mistake of the same
family as FAILURES entry 10: the model was handed arithmetic, and adding a
numbered decision table to the prompt to help it dropped recall on the attack
half of `eval/adjudicator.py` from five of six to four of eight.

Counting distinct payers and looking a code up in the merchant's own book are
not judgement calls. They are decided here, deterministically, with no key and
no network, which also means they work in CI and on a fresh clone. Only a case
the numbers genuinely cannot read is worth a model call.
"""

from __future__ import annotations

import pytest

from parchi.behavior import coupon_verdict

PUBLIC, PRIVATE, UNKNOWN = True, False, None


def ev(values=(100.0,), payers=1, mandates=1):
    return {
        "code": "save10",
        "claimed_values_rupees": list(values),
        "distinct_payers_on_this_code": payers,
        "distinct_mandates_on_this_code": mandates,
    }


# --------------------------------------------------------------------- drift

@pytest.mark.parametrize("is_public", [PUBLIC, PRIVATE, UNKNOWN])
def test_one_code_at_two_values_is_convicted_whatever_the_code_is(is_public):
    """The shape the operator asked for: the value was raised, so block.

    A coupon is worth what it is worth. No sale, no retry and no honest mistake
    makes one code pay two different sums, so this needs no other evidence and
    does not depend on the code being public.
    """
    verdict = coupon_verdict(ev(values=(100.0, 900.0), payers=1, mandates=2), is_public)
    assert verdict is not None
    convict, why = verdict
    assert convict is True
    assert "900" in why and "different values" in why


def test_the_reason_names_every_value_that_was_claimed():
    """An operator reading the alert should not have to go and look."""
    _, why = coupon_verdict(
        ev(values=(100.0, 400.0, 900.0), payers=1, mandates=3), PUBLIC)
    for amount in ("100", "400", "900"):
        assert amount in why


def test_one_value_claimed_repeatedly_is_not_drift():
    """Refusing the same wrong claim over and over is one wrong claim."""
    settled = coupon_verdict(ev(values=(900.0,), payers=1, mandates=1), PUBLIC)
    assert settled is None or settled[0] is False


# ------------------------------------------------------------------- farming

def test_one_payer_across_many_mandates_is_farming_even_on_a_public_code():
    """A public code is meant to be used once by many people.

    Not many times by one. This is the case the model cleared when it was asked
    to weigh "the code is public" against "there is only one payer".
    """
    convict, why = coupon_verdict(ev(payers=1, mandates=28), PUBLIC)
    assert convict is True
    assert "28" in why and "farming" in why


def test_many_payers_on_a_single_issue_code_means_it_leaked():
    convict, why = coupon_verdict(ev(payers=26, mandates=26), PRIVATE)
    assert convict is True
    assert "leaked" in why


def test_many_payers_on_an_advertised_code_is_the_sale_working():
    """The false block this whole exercise exists to avoid."""
    convict, why = coupon_verdict(ev(payers=26, mandates=26), PUBLIC)
    assert convict is False
    assert "working as intended" in why


# ----------------------------------------------------------------- ambiguity

def test_a_code_the_merchant_does_not_know_is_left_to_the_adjudicator():
    """None means "ask someone", not "allow"."""
    assert coupon_verdict(ev(payers=26, mandates=26), UNKNOWN) is None


def test_too_little_traffic_to_read_is_not_a_conviction():
    assert coupon_verdict(ev(payers=2, mandates=2), PRIVATE) is None


def test_an_empty_evidence_block_convicts_nobody():
    assert coupon_verdict({}, PRIVATE) is None
    assert coupon_verdict({}, PUBLIC) is None


def test_two_mandates_from_one_payer_is_not_yet_farming():
    """Somebody buying two things is not a farm. The line is at three."""
    assert coupon_verdict(ev(payers=1, mandates=2), PUBLIC) is None
