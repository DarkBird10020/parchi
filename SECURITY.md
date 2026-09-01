# Security

Parchi decides whether money moves, so the security posture is part of the product
rather than an afterthought. This file says what the threat model actually is, and —
just as importantly — what it is not.

## What this repository is

A four-day hackathon build. It is a **demonstration of a checkpoint design**, not a
deployable payment component. The [known limitations](README.md#known-limitations)
in the README are not boilerplate: keys live in process memory, nonces live in an
in-process set, and there is no agent registry. Do not put this in front of real
money as it stands.

## Threat model

Parchi assumes the **agent and the merchant are both untrusted**. The human's
signature on the mandate is the only thing it trusts, and only for as long as the
canonical bytes verify.

| Attacker | Can do | Parchi's answer |
| :--- | :--- | :--- |
| A compromised or manipulated agent | Present any cart, any mandate, repeatedly | 8 deterministic checks, short-circuiting; one-time nonce |
| A merchant | Write arbitrary product text the agent reads, and present someone else's mandate | Untrusted text is fenced as data in the model prompt; `payee` check scopes a mandate to one merchant |
| Anyone holding a mandate | Edit a field, extend the window, widen the categories | Ed25519 over canonical JSON; any edit fails verification |
| Anyone holding the log | Rewrite a past verdict | Hash-chained records; `verify_chain()` reports the break |

Explicitly **out of scope**: proving *which* agent is presenting a slip (that is the
half the card networks are building), key custody, and anything requiring a PSP
sandbox.

## Adversarial test suite

Every bypass known to me is a named, executable pattern:

```bash
python tests/test_attacks.py
```

28 patterns covering forging, tampering, time manipulation, payee substitution,
amount arithmetic, Unicode confusables, replay, and prompt injection aimed at the one
model call — each with the verdict Parchi must return. **Six of them got through on
the first run.** See [FAILURES.md](FAILURES.md) for what each one was.

The suite is the contract: CI fails if any pattern regresses.

### The rule for a newly found bypass

1. Add it to `tests/test_attacks.py` **first**, with the verdict it should get.
2. Watch it fail.
3. Then fix it.

A fix without a pattern is a fix that can silently come undone.

## Known blind spots

Recorded rather than hidden:

- **`quantity-inflation`** — five identical, allowed, under-cap items still read as
  in-scope. No rule and no lexical check can distinguish one pair of shoes from five.
- **The offline intent matcher** is a lexical stand-in, not a model. Numbers produced
  with it are labelled `heuristic` everywhere they appear.

## Reporting

This is a hackathon submission, not a maintained service. If you find a bypass,
please open an issue with the cart, mandate and expected verdict — ideally as a
patch to `tests/test_attacks.py`.
