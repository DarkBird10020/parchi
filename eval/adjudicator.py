"""Does the AI adjudicator actually judge, or only agree?

The deterministic checks are scored against 1,000 rows and a held-out set. The
adjudicator was not scored against anything, which is the wrong asymmetry: it
is the one component whose verdict can lock a real customer out of their own
account for ten minutes.

So this file scores it the same way: hand-written situations, half of them
attacks and half ordinary customers who happen to trip a counter, each labelled
independently of what any detector would say. The benign half is the half that
matters. A model that convicts everything scores perfect recall and is useless,
because every false conviction here is a paying customer told their account is
blocked.

Coupon abuse is deliberately absent. Those cases are settled by counting in
`parchi/behavior.py` and never reach a model, so scoring them here would be
scoring a decision the adjudicator is never asked to make. They are covered by
`tests/test_coupon_verdict.py` instead.

The one shape the checkpoint routes here today is the agent swarm, plus any
coupon case the numbers cannot read. The rest of these are held as a general
check on the adjudicator's judgement: a counter is easy to add and the next one
routed here will lean on exactly this reasoning, so it is worth knowing whether
the reasoning holds before that happens.

    python eval/adjudicator.py

Needs a key. Without one every call fails open, and the run says so instead of
printing a score it did not measure.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parchi.ai_guard import CONFIDENCE_GATE, assess_attack
from parchi.cooldown import COOLDOWN_SECONDS

# (is_attack, name, signals). ATTACK means a human reviewer, shown only these
# facts, should conclude the account is being worked rather than used.
CASES: list[tuple[bool, str, dict]] = [
    (True, "agent swarm on one wallet", {
        "detectors_fired": [{"kind": "agent_swarm", "severity": "critical"}],
        "swarm_agents_on_this_payer": ["agt_a1", "agt_a2", "agt_a3", "agt_a4"],
        "verdict_this_attempt": "ALLOW",
        "cart_lines": ["running shoes"],
        "human_asked_for": "buy running shoes under Rs 5,000",
    }),
    (True, "card testing: many cheap items, many instruments", {
        "detectors_fired": [{"kind": "purchase_burst", "severity": "high"}],
        "attempts_in_60s": 34,
        "distinct_payment_instruments": 29,
        "verdict_this_attempt": "ALLOW",
        "cart_lines": ["phone charger Rs 99"],
        "human_asked_for": "buy a phone charger",
    }),
    (True, "catalogue sweep on one mandate", {
        "detectors_fired": [{"kind": "purchase_burst", "severity": "high"}],
        "attempts_in_60s": 41,
        "distinct_items_this_mandate": 40,
        "verdict_this_attempt": "ALLOW",
        "cart_lines": ["sku-0001", "sku-0002", "sku-0003", "sku-0004"],
        "human_asked_for": "buy a pair of running shoes",
    }),


    (False, "office manager buying for a team", {
        "detectors_fired": [{"kind": "purchase_burst", "severity": "high"}],
        "attempts_in_60s": 9,
        "swarm_agents_on_this_payer": ["agt_office"],
        "verdict_this_attempt": "ALLOW",
        "cart_lines": ["notebook", "pens", "desk lamp", "monitor stand"],
        "human_asked_for": "buy office supplies for the team under Rs 20,000",
    }),
    (False, "checkout retry on a flaky connection", {
        "detectors_fired": [{"kind": "purchase_burst", "severity": "high"}],
        "attempts_in_60s": 8,
        "identical_carts_resubmitted": 8,
        "verdict_this_attempt": "ALLOW",
        "cart_lines": ["wireless earbuds"],
        "human_asked_for": "buy wireless earbuds under Rs 4,000",
    }),
    (False, "gift shopping: many distinct items, one agent", {
        "detectors_fired": [{"kind": "purchase_burst", "severity": "high"}],
        "attempts_in_60s": 11,
        "swarm_agents_on_this_payer": ["agt_home"],
        "verdict_this_attempt": "ALLOW",
        "cart_lines": ["scarf", "board game", "chocolate box", "book"],
        "human_asked_for": "buy Diwali gifts for my family under Rs 15,000",
    }),
    (False, "small reseller with a real standing mandate", {
        "detectors_fired": [{"kind": "purchase_burst", "severity": "high"}],
        "attempts_in_60s": 10,
        "swarm_agents_on_this_payer": ["agt_shopfront"],
        "mandate_age_days": 240,
        "verdict_this_attempt": "ALLOW",
        "cart_lines": ["running shoes", "running shoes", "running shoes"],
        "human_asked_for": "restock running shoes, up to Rs 60,000 a day",
    }),
    (False, "one expired mandate, nothing else", {
        "detectors_fired": [{"kind": "expired_mandate", "severity": "info"}],
        "attempts_in_60s": 1,
        "verdict_this_attempt": "BLOCK",
        "cart_lines": ["running shoes"],
        "human_asked_for": "buy running shoes under Rs 5,000",
    }),
]


def main() -> int:
    if not os.environ.get("PARCHI_OPENAI_API_KEY", "").strip():
        from parchi.openai_provider import load_dotenv
        load_dotenv()
    if not os.environ.get("PARCHI_OPENAI_API_KEY", "").strip():
        print("No key configured. Every call would fail open, which is correct "
              "behaviour and a meaningless score. Set PARCHI_OPENAI_API_KEY "
              "and run again.")
        return 2

    tp = fp = tn = fn = abstain = 0
    models: dict[str, int] = {}
    print(f"{'':6s} {'case':46s} {'label':7s} {'verdict':8s} conf  model")
    print("-" * 104)
    started = time.time()
    for is_attack, name, signals in CASES:
        assessment = assess_attack("actor_under_review", signals, timeout=45.0)
        label = "ATTACK" if is_attack else "benign"
        if assessment is None:
            abstain += 1
            print(f"{'--':6s} {name:46s} {label:7s} {'none':8s}  -    failed open")
            continue
        models[assessment.model] = models.get(assessment.model, 0) + 1
        convicted = assessment.attack and assessment.confidence >= CONFIDENCE_GATE
        if is_attack and convicted:
            tp += 1
            mark = "ok"
        elif is_attack and not convicted:
            fn += 1
            mark = "MISS"
        elif not is_attack and convicted:
            fp += 1
            mark = "FALSE"
        else:
            tn += 1
            mark = "ok"
        print(f"{mark:6s} {name:46s} {label:7s} "
              f"{'convict' if convicted else 'clear':8s} "
              f"{assessment.confidence:.2f}  {assessment.model}")

    took = time.time() - started
    print("-" * 104)
    judged = tp + fp + tn + fn
    print(f"judged {judged}/{len(CASES)}, abstained {abstain} "
          f"(failed open, nobody blocked)")
    if judged:
        print(f"caught       {tp}/{tp + fn} attacks")
        print(f"left alone   {tn}/{tn + fp} ordinary customers")
        if fp:
            print(f"FALSE BLOCKS {fp}, each a real customer locked out for "
                  f"{COOLDOWN_SECONDS // 60} minutes")
        print(f"accuracy     {(tp + tn) / judged:.0%}")
    print(f"gate         convict only at confidence >= {CONFIDENCE_GATE}")
    print(f"models       {models}")
    print(f"took         {took:.0f}s for {len(CASES)} calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
