# The 5-Minute Pitch — shot by shot

For the Razorpay AI Buildathon · Track 02 · AI Risk Manager.
The buildathon page asks for three things: a public repo, a **5-minute pitch
video**, and the architecture. The repo and architecture already exist. This is
the video, timed to the second.

**Timing budget:** 5:00 total, hard. Trim the middle before the end; never cut
the opening or the closer.

---

## 0:00–0:40 — The problem (40s)

**On screen:** the landing page / hero image, or you talking to camera.

**Say (verbatim-ish):**

> "When an AI agent spends your money, there's no parchi. In India a *parchi* is
> a permission slip — show it, you get through. Right now AI has no parchi."

> "Razorpay has Agentic Payments on UPI Reserve Pay. The human pre-approves a
> spending *limit*, but the rail records *nothing about what the money was for*.
> UPI can answer 'is there money, and is it the right merchant'. It cannot answer
> 'is this the thing I actually asked for'."

> "That gap is where fraud enters. The customer says *'my agent did that, I
> didn't'* — and today the merchant cannot prove otherwise."

**Why this order:** lead with the problem, exactly as every judging guide says.
Do not open with "I built a thing called Parchi."

---

## 0:40–1:50 — The demo (70s)

**On screen:** `python demo/server.py` → the page loads → you run scenarios.

The three beats that matter, in this order, nothing else:

1. **The injection that passes every rule** (0:40–1:10). Open the
   `injection` scenario. Say:

   > "This cart — right category, under the cap, valid slip, unspent nonce. Every
   > one of my ten deterministic rules passes. The product page talked the agent
   > into a protection plan the human never asked for. One model call is the only
   > thing that catches it."

   Click **Authorize** → shows **BLOCK, reason: doesn't match intent.**

2. **Tamper the ledger** (1:10–1:30). Say:

   > "Every decision is written to a hash-chained ledger. I don't ask you to
   > believe it's tamper-evident — press the Tamper button and it verifies itself
   > as broken."

   Click **Tamper** → the chain panel flips red.

3. **The three-way verdict** (1:30–1:50). Say:

   > "There are three answers, not two. A high-value legitimate purchase isn't
   > auto-approved and isn't refused — it steps up to a human. Allow, block, or
   > ask. That's the difference between a filter and a risk product."

   Run `step_up` → shows **STEP_UP**.

**Do NOT show** the Razorpay Order/webhook on this pass — it costs time. If asked
in the panel, you have it ready (see the closer).

---

## 1:50–2:50 — The scoreboard (60s)

**On screen:** the README results table, or `python eval/evaluate.py` running live.

**Say:**

> "I scored it like a risk product, not a demo. A thousand labelled agent
> purchases. The number I care about is not recall — it's the *cost of the
> mistakes, in rupees*, because a blocked genuine customer is money the merchant
> lost."

Walk the table top to bottom:

> "Allow everything: seventeen lakh rupees paid out on violations. Block all
> agent traffic: thirty-three lakh of good revenue gone. Rules alone: catches
> eighty-four percent, still lets two lakh nineteen of violations through.
> Parchi — rules plus one model call — catches all two hundred eighty violations,
> blocks zero good customers, zero rupees of mistakes."

> "And posted next to it, honestly, is my full run against a *real* model —
> ninety-eight percent, two false blocks, a hundred and fourteen degraded calls —
> because a risk product that only quotes its best run is doing the thing it
> exists to prevent."

**Why this lands:** the buildathon bar is literally "honest metrics including
false-positive cost." You are reading their grading rubric back at them.

---

## 2:50–3:30 — The honesty beat (40s)

**On screen:** `FAILURES.md` scrolled slowly.

**Say:**

> "The file I'm proudest of is the failure log. Thirteen bugs, each with what I
> first assumed, what it actually was, and what it cost. Two bugs where the model
> was a *worse copy of a rule* — the second one actively destroyed revenue by
> re-deciding arithmetic it was never supposed to touch. That's why ten of my
> eleven checks are plain code, and the model answers exactly one question: *is
> this the thing the human asked for.*"

> "I'm not pretending a four-day build is production. The keys-live-in-memory and
> nonce-store limitations are named in the README, not hidden."

---

## 3:30–4:30 — The architecture (60s)

**On screen:** the mermaid flowchart from the README (full screen).

**Say, pointing:**

> "Three parts. One: the human signs an AP2-inspired intent mandate — cap,
> categories, expiry, nonce, and the agent's own playback of the request. Two:
> ten deterministic checks run first — signature, expiry, payee, method, line
> items, quantity, category, amount, agent identity, replay. Cheap, exact,
> auditable, and no AI in that file. They short-circuit on the first failure."

> "Three: only if every rule passes, *one* model call answers the single question
> rules can't — does this cart match what the human actually said. Strict typed
> JSON, a timeout, and untrusted text fenced off as data. The spending cap is
> deliberately kept *out* of the prompt, so the model never re-decides math."

> "Every decision — allow, block, or step-up — is written to a hash-chained
> ledger either way, so a merchant can prove what was authorised."

---

## 4:30–4:50 — Why this matters to Razorpay (20s)

**Say:**

> "Agent Studio already has an agent that *answers* disputes on human
> transactions. Parchi is the opposite half — it *prevents* and *evidences*
> disputes on *agent* transactions, the unsolved one. It's a pre-authorisation
> checkpoint sitting where the mandate meets the cart, mapping field-for-field
> onto UPI Reserve Pay. Every mandate field is in the repo's upi-mapping doc."

---

## 4:50–5:00 — The closer (10s)

**Say:**

> "No parchi, no purchase. The repo is public — two commands reproduce every
> number. I'll take questions on the real-model run, the degraded-path design,
> or the Razorpay test-mode Order and webhook flow."

**Screen:** the repo URL.

---

## Panel prep — the 8 questions, answered

1. **"How do you know your 100% isn't overfitting your generator?"**
   → The 1,000-row batch is generator-tuned. The *held-out* set
   (`eval/heldout.py`, 13 hand-written cases, in CI) is not: it includes
   categories, playback phrasings, and one-paise edges the generator never
   produces. Held-out: 13/13, 100% precision, 0 false blocks.

2. **"What's the false-positive cost model?"** → False positives are counted in
   rupees, not percentages, because a blocked real customer is lost money —
   ₹33 lakh under block-all, ₹0 under Parchi.

3. **"Why one model call and not an agent loop?"** → It sits in front of a
   payment; a slow answer is a wrong answer. Deterministic fallback *is* the
   retry policy.

4. **"What happens when the model dies?"** → Nothing auto-approves. It fails to
   STEP_UP — ask the human — never silently allows. Demo it with
   `--provider off`.

5. **"Why is the cap kept out of the prompt?"** → Because the model re-decided it
   and got it wrong, blocking a ₹4,077 cart as "exceeds ₹5,000". It's
   FAILURES.md entry 10.

6. **"How does this differ from Agent Studio's dispute agent?"** → That one
   answers disputes on human transactions. Parchi prevents and evidences them on
   *agent* transactions — a different, unsolved problem.

7. **"What's defense-only about it?"** → The whole system is a verifier/blocker.
   Nothing here can *initiate* a payment, only permit or refuse one. Track 02
   requires defense-only; this is compliance, not a caveat.

8. **"What would you do with real Razorpay infra?"** → Hardware-backed keys,
   shared nonce store and agent registry, a signed external anchor for the
   ledger, and moving the intent check to a model that handles quantity
   inflation (the one recorded blind spot).

---

## Recording checklist

- [ ] `python demo/server.py` boots, all ten scenarios green before you record
- [ ] Microphone test; screen at 1080p; no browser tabs that autoplay
- [ ] The injection → BLOCK clip is the only clip you must nail; re-record it if
      anything flickers
- [ ] Trim to **≤ 5:00**. Cut demo beats, never the opener or closer
- [ ] Put the video link at the **top of the README** (first line under the hero)
- [ ] Submit before **September 5**