"""How long the checkpoint takes, at the percentiles a payment cares about.

An average is the wrong statistic here. A checkpoint that answers in 200ms on
average and 9 seconds at p99 is a checkpoint that times out on one purchase in
a hundred, and in this design a timeout is a customer sent to a human. So this
reports the distribution, and reports the two paths separately, because they
are not the same product decision.

A cart refused by a rule never reaches the model. That is most refusals and it
is the whole reason the rules run first: signature, expiry, payee, method, line
items, quantity, prices, category, discount, cap, agent identity, replay, all
short-circuiting on the first failure. Only a cart that passes every one of
them costs a model call.

    python eval/latency.py              # both paths
    python eval/latency.py --calls 60   # more samples for the model path

The model path needs a key. Without one it reports the rules path alone and
says so, rather than printing a number it did not measure.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from parchi import openai_provider
from parchi.agents import AgentRegistry
from parchi.checks import NonceStore
from parchi.engine import Engine
from parchi.mandate import Cart, CartLine, new_mandate, sign, sign_cart


def percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    def at(p: float) -> float:
        if not ordered:
            return 0.0
        i = min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))
        return ordered[i]
    return {"min": ordered[0], "p50": at(50), "p95": at(95),
            "p99": at(99), "max": ordered[-1],
            "mean": statistics.fmean(ordered)}


def show(name: str, samples: list[float], note: str = "") -> None:
    if not samples:
        print(f"{name}: not measured")
        return
    p = percentiles(samples)
    unit = "ms"
    print(f"\n{name}  ({len(samples)} calls){('  ' + note) if note else ''}")
    print(f"  min {p['min']:8.1f}{unit}    p50 {p['p50']:8.1f}{unit}"
          f"    p95 {p['p95']:8.1f}{unit}")
    print(f"  p99 {p['p99']:8.1f}{unit}    max {p['max']:8.1f}{unit}"
          f"    mean {p['mean']:8.1f}{unit}")


def build(kind: str, agent_key, agent_id: str):
    """A cart that a rule refuses, or one that reaches the model."""
    mandate = new_mandate("usr_lat", "mrc_lat", ("upi",), 500_000, ("footwear",),
                          "buy running shoes under Rs 5,000",
                          allowed_agent_id=agent_id)
    if kind == "rule":
        # Over the cap: refused by check_amount, well before any model call.
        lines = (CartLine("premium running shoes", "footwear", 1_200_000),)
    else:
        lines = (CartLine("running shoes", "footwear", 420_000),)
    unsigned = Cart(lines, "upi", "mrc_lat", agent_id=agent_id)
    cart = Cart(unsigned.lines, unsigned.method, unsigned.payee_id,
                unsigned.merchant_note, unsigned.agent_id,
                sign_cart(unsigned, agent_key))
    return mandate, cart


def measure(provider: str, kind: str, calls: int, timeout: float,
            model: str | None) -> list[float]:
    payer_key = Ed25519PrivateKey.generate()
    payer_pub = payer_key.public_key()
    agent_key = Ed25519PrivateKey.generate()
    agents = AgentRegistry()
    agents.register("agt_lat", agent_key.public_key())

    engine = Engine(ledger=None, nonces=NonceStore(), agents=agents,
                    provider=provider, timeout=timeout, model=model)

    samples: list[float] = []
    for i in range(calls):
        mandate, cart = build(kind, agent_key, "agt_lat")
        signature = sign(mandate, payer_key)
        started = time.perf_counter()
        engine.authorize(mandate, signature, payer_pub, cart,
                         txn_id=f"txn_lat_{kind}_{i:04d}")
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=40,
                    help="samples for the model path; the rules path uses 20x")
    ap.add_argument("--timeout", type=float, default=4.0,
                    help="the production intent budget")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    openai_provider.load_dotenv()

    print("Every cart pays the deterministic checks. Only a cart that passes")
    print("all of them pays a model call, which is why the two are separate.")

    rules = measure("off", "rule", args.calls * 20, args.timeout, args.model)
    show("refused by a rule, no model call", rules,
         "signature verify + 12 checks")

    if not os.environ.get("PARCHI_OPENAI_API_KEY", "").strip():
        print("\nNo key configured, so the model path is not measured. The "
              "number above is the whole cost of every refusal a rule settles, "
              "which in the published 1,000-row run was 235 of 280.")
        return 0

    live = measure("openai", "model", args.calls, args.timeout, args.model)
    show("passed every rule, so it costs one model call", live,
         f"{args.timeout:.0f}s budget, degrades to STEP_UP")

    p = percentiles(live)
    over = sum(1 for s in live if s > args.timeout * 1000)
    print(f"\nover the {args.timeout:.0f}s budget: {over}/{len(live)} calls"
          f"  ({over / len(live):.0%})")
    print("A call over budget is not an error. It degrades to STEP_UP and the")
    print("cart goes to a human, which is the whole point of the third verdict.")
    print(f"\nThe honest headline: a refusal a rule settles costs "
          f"{percentiles(rules)['p95']:.1f}ms at p95. A cart that reaches the "
          f"model costs {p['p95'] / 1000:.1f}s at p95.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
