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

Entry 19 is the companion to it. Fixing the way the spending cap was reaching
the model improved recall to 100%, precision to 95.2% and false blocks from 22
to 14, and made the **total cost of the mistakes 22% worse**, because the false
blocks that remain land on expensive carts. Nine metrics said ship it. The one
that counts what a merchant loses said no, so it ships off, behind a flag, with
both runs published.

### The operations console: the employee side of the product

Everything above is the checkpoint, which runs in milliseconds and nobody
watches. `/console` is the other half, and it is the half a company staffs. A
risk product is a verdict **plus the person who answers for it**.

Real sign-in (scrypt, per-account lockout, the password never in source), an
alert feed where every entry names the account it was about, and the
adjudicator's verdict with its confidence and model. What an employee can do:

| Action | Note |
|:--- |:--- |
| Release a cooled account | Overruling the adjudicator on a live customer; logged with the operator's name |
| Approve a refund | Executes a refund the AI *proposed* after a purchase went out wrong |
| Acknowledge an alert | Attribution, not deletion: the alert stays in the feed |
| Read the defence lamp | Reports whether the protecting AI is *answering*, not merely configured |
| AI gate on/off | Stops model calls and cooldowns; detectors keep alerting, because cheaper must not mean blind |
| Autonomous defence on/off | Unattended AI triage. **Default off** — that is a decision a company makes, not a default it discovers |
| Clear all / watch history | An attributed shift handover; the ledger is untouched |

Two properties hold across all of it. Every consequential action is attributed
in the ledger by name. And **the AI is never the last actor on anything that
costs a customer money**: it can cool an account for ten minutes and propose a
refund, and a person releases the one and approves the other.

## The Razorpay integration

| Surface | What Parchi does |
|:--- |:--- |
| Orders API | An `ALLOW`/approved decision creates a real Order (test mode) carrying `authorization_id` + `mandate_id` in notes |
| Checkout | `razorpay_signature` verified with HMAC-SHA256 before the state moves to authorised |
| Webhooks | `payment.captured` / `payment.failed` / `refund.processed` close the loop: `X-Razorpay-Signature` verified over the raw body, outcome written to the hash-chained ledger |
| UPI Reserve Pay | Every mandate field mapped onto the rail in [`docs/upi-mapping.md`](upi-mapping.md), including the two fields it has no equivalent for |

Live-mode keys are rejected by design; the demo runs on test credentials.

## Results

1,000 labelled agent purchases, false positives reported in rupees because a
blocked genuine customer is money the merchant lost. **The headline row is the
run against a real model**, not the offline stand-in: 278/280 caught, 22 good
customers wrongly blocked, ₹1,59,521. The no-key reproduction scores 280/280
with zero false blocks, and it is published further down rather than at the top,
because a perfect score on data I generated against rules I wrote is a closed
loop and reads like one.

**Which half produced which number** matters more than the total, and
`eval/attribute.py` derives it from the published ledger:

| Settled by | Violations caught | Good customers blocked | Precision |
|:--- |:--- |:--- |:--- |
| A deterministic rule | 235 | 0 | 100% |
| The one model call | 43 | 22 | 66.2% |

The model earns its place and is also the entire source of the error. Eighteen
of those 22 false blocks reason about price, which led to entry 19 below.

**Three answers to "is this overfit to your own generator?"**, weakest to
strongest. The hand-written held-out set (`eval/heldout.py`, in CI) uses cases
chosen to beat the generator's blind spots. The 48-pattern attack suite is
adversarial by construction. And `eval/redteam.py` gives a model the product
with no rule, no check name and no threshold, and asks it for attacks:
**40 distinct cases, 76% caught**, with the seven that got through named rather
than summarised. That last number is the only one here I did not mark myself.

**Latency**, since this sits in front of a payment (`eval/latency.py`): a
refusal a rule settles is 0.2ms at p95; a cart that reaches the model is 10.9s at
p95, which is slow and is the endpoint rather than the design. Only carts that
pass all twelve checks pay it, 300 of 1,000 in the published run, and an
over-budget call degrades to `STEP_UP` rather than to `ALLOW`.

Every published run carries its own hash-chained ledger written in the same
pass, and a test asserts the ledger's records fall inside the window of the run
reporting them. That test exists because they once did not, which is entry 17.

## What is deliberately not claimed

Hardware-backed keys, multi-instance nonce stores, shared agent registry, UPI
Reserve Pay provisioning, real traffic. The behavioural detectors and the
cooldown store are per-process too, so they are a design and an interface
rather than a deployment. All named in the README's *Known limitations*: a
hackathon build pretending to be production-grade is the actual red flag.
`FAILURES.md` keeps the full post-mortem of everything that broke on the way,
including the two entries where the thing that broke was my own measurement.
