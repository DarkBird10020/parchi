# Parchi — submission notes

*Razorpay AI Buildathon · Track 02 · AI Risk Manager*

This page answers, in order, what a reviewer wants to know in five minutes.
Everything it claims is reproducible from the README's two commands.

## The problem

Razorpay has launched Agentic Payments backed by UPI Reserve Pay, and Agent
Studio automates merchant operations. When an AI agent spends a human's money,
two questions are still unanswered at transaction time:

1. **Enforcement** — did the actual cart stay inside what the human signed?
2. **Evidence** — when the customer says *"my agent did that, I didn't,"* can
   the merchant prove what was authorised?

Agent Studio's dispute agent answers disputes on human transactions. Parchi
prevents and evidences disputes on *agent* transactions.

### Track and compliance

This is **Track 02 — AI Risk Manager**, chosen over Track 01 on purpose: my unit
of value is *loss prevented*, not revenue grown. Build threshold, in the track's
own words, is "honest metrics including false-positive cost" on "a working
detector, verifier or auto-responder for one class of loss, measured on a
held-out test set."

It is **strictly defense-only**: nothing in this repository can *initiate* a
payment. Every path is a verifier or a blocker — permit, refuse, or escalate —
so it satisfies the track's "anything offense-capable is disqualified" rule by
construction, not by assertion. The measured held-out set is
`eval/heldout.py`; the tuned synthetic batch (`data/transactions.jsonl`) is
reported alongside it, never instead of it.

## The solution

Every agent purchase must carry a signed, AP2-inspired intent record: the
human's cap, categories, methods, TTL, nonce, and the agent's own playback of
the request. Parchi verifies the purchase against that mandate *before*
authorisation:

- **10 deterministic checks** (signature, expiry, payee, method, line items,
  quantity, category, cap, agent identity, replay) — plain code, auditable, no AI.
- **1 model call** for the one question rules cannot answer: *does this cart
  match what the human asked for?* Strict typed JSON, provider timeout,
  untrusted text fenced as data, and the cap deliberately kept out of the
  prompt so the model never re-decides arithmetic.
- **Three verdicts**, not two: `ALLOW`, `BLOCK`, and `STEP_UP` — ask the human.
  A degraded intent check fails to `STEP_UP`, never to silent auto-approval.
- A **hash-chained ledger** and a **dispute evidence pack** on every decision,
  either way.

## The Razorpay integration

| Surface | What Parchi does |
| :--- | :--- |
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

The held-out set (`python eval/heldout.py`) is the number that answers "is this
overfit to its own generator": **13/13 hand-written cases, 100% precision, 0
false blocks**, in CI next to the 31-pattern attack suite.

## What is deliberately not claimed

Hardware-backed keys, multi-instance nonce stores, shared agent registry, UPI
Reserve Pay provisioning, real traffic. All named in the README's *Known
limitations* — a hackathon build pretending to be production-grade is the
actual red flag. `FAILURES.md` keeps the full post-mortem of everything that
broke on the way.
