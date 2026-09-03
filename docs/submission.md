# Parchi, submission notes

*Razorpay AI Buildathon · Track 02 · AI Risk Manager*

This page answers, in order, what a reviewer wants to know in five minutes.
Everything it claims is reproducible from the README's two commands.

## The problem

Razorpay has launched Agentic Payments backed by UPI Reserve Pay, and Agent
Studio automates merchant operations. When an AI agent spends a human's money,
two questions are still unanswered at transaction time:

1. **Enforcement**, did the actual cart stay inside what the human signed?
2. **Evidence**, when the customer says *"my agent did that, I didn't,"* can
   the merchant prove what was authorised?

Agent Studio's dispute agent answers disputes on human transactions. Parchi
prevents and evidences disputes on *agent* transactions.

### Track and compliance

This is **Track 02, AI Risk Manager**, chosen over Track 01 on purpose: my unit
of value is *loss prevented*, not revenue grown. Build threshold, in the track's
own words, is "honest metrics including false-positive cost" on "a working
detector, verifier or auto-responder for one class of loss, measured on a
held-out test set."

It is **strictly defense-only**: nothing in this repository can *initiate* a
payment. Every path is a verifier or a blocker. It can permit, refuse, or escalate,
so it satisfies the track's "anything offense-capable is disqualified" rule by
construction, not by assertion. The measured held-out set is
`eval/heldout.py`; the tuned synthetic batch (`data/transactions.jsonl`) is
reported alongside it, never instead of it.

## The solution

Every agent purchase must carry a signed, AP2-inspired intent record: the
human's cap, categories, methods, TTL, nonce, and the agent's own playback of
the request. Parchi verifies the purchase against that mandate *before*
authorisation:

- **12 deterministic checks** (signature, expiry, payee, method, line items,
  quantity, prices, category, discount, cap, agent identity, replay), plain
  code, auditable, no AI. Discount is verified *before* the cap, because the
  cap applies to the post-discount total, so an unverified coupon is a way
  under any ceiling.
- **1 model call** for the one question rules cannot answer: *does this cart
  match what the human asked for?* Strict typed JSON, provider timeout,
  untrusted text fenced as data, and the cap deliberately kept out of the
  prompt so the model never re-decides arithmetic.
- **Three verdicts**, not two: `ALLOW`, `BLOCK`, and `STEP_UP`, ask the human.
  A degraded intent check fails to `STEP_UP`, never to silent auto-approval.
- A **hash-chained ledger** and a **dispute evidence pack** on every decision,
  either way.

### The second layer: what one cart cannot show

Some attacks are only visible across many carts, and every individual verdict
in them is correct. `parchi/behavior.py` watches the sequence: purchase
velocity, one coupon code swept across many mandates, and the same code claimed
at different values in different carts. These raise alerts and can never change
a verdict, which is the same enforcement/detection split the rest of the system
keeps.

Two shapes are never accidents, and the sharpest is an **agent swarm**: several
genuinely registered agent credentials all presenting slips for one payer.
Every deterministic check passes for every one of them. That pattern goes to an
**AI adjudicator**, which reads the situation and answers what a counter
cannot: is this account being *used*, or *worked*? On a confident yes the
account is cooled for ten minutes, enforced deterministically before the engine
runs.

That adjudicator is deliberately outside the payment path in both senses. It
cannot change a verdict, and it runs on its own thread after the decision is
made, so a slow model costs nobody a wait. A wrong conviction costs a cooldown
a human lifts from the console. It can never cost a silently stolen purchase.

### Measuring the AI that can refuse a customer

`eval/adjudicator.py` scores the adjudicator against twelve hand-written
situations, half of them ordinary customers who happen to trip a counter. The
benign half is the point: a model that convicts everything scores perfect
recall and is useless.

The first version convicted **15 of 18** benign judgements. Writing the cost
asymmetry into the prompt took it to **18/18 attacks caught with 16/18
customers left alone**. Both runs are in `FAILURES.md` entry 16. This is the
single result I would most want a risk reviewer to look at, because the failure
was invisible to every test that existed at the time.

### The operations console

`/console` is the staff side: a real sign-in (scrypt, per-account lockout, and
the password never in source), an alert feed where every entry names the
account it was about, the adjudicator's verdict with its confidence and model,
a per-account release button, and an on/off switch for the AI gate so the
person paying the token bill can cap it. Turning it off stops model calls and
automatic cooldowns; the deterministic alerts keep flowing, because cheaper
must not mean blind.

## The Razorpay integration

| Surface | What Parchi does |
|:--- |:--- |
| Orders API | An `ALLOW`/approved decision creates a real Order (test mode) carrying `authorization_id` + `mandate_id` in notes |
| Checkout | `razorpay_signature` verified with HMAC-SHA256 before the state moves to authorised |
| Webhooks | `payment.captured` / `payment.failed` / `refund.processed` close the loop: `X-Razorpay-Signature` verified over the raw body, outcome written to the hash-chained ledger |
| UPI Reserve Pay | Every mandate field mapped onto the rail in [`docs/upi-mapping.md`](upi-mapping.md), including the two fields it has no equivalent for |

Live-mode keys are rejected by design; the demo runs on test credentials.

## Results

1,000 labelled agent purchases, false positives reported in rupees. Full
tables, provider stamps and reproduction commands in the README. The headline
row is stamped with the provider that produced it, and the model-run table is
published next to the heuristic one rather than blended into it.

The full model run is published with its own hash-chained ledger, written in
the same pass, and a test asserts that the ledger's records fall inside the
window of the run that reports them. That test exists because they once did
not: the previously published ledger was from a different run hours earlier,
which is `FAILURES.md` entry 17.

The held-out set (`python eval/heldout.py`) is the number that answers "is this
overfit to its own generator": hand-written cases, every one handled as
specified, 0 false blocks, in CI next to the 48-pattern attack suite.

## What is deliberately not claimed

Hardware-backed keys, multi-instance nonce stores, shared agent registry, UPI
Reserve Pay provisioning, real traffic. The behavioural detectors and the
cooldown store are per-process too, so they are a design and an interface
rather than a deployment. All named in the README's *Known limitations*: a
hackathon build pretending to be production-grade is the actual red flag.
`FAILURES.md` keeps the full post-mortem of everything that broke on the way,
including the two entries where the thing that broke was my own measurement.
