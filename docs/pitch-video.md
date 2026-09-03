# The 5-Minute Pitch, shot by shot

For the Razorpay AI Buildathon · Track 02 · AI Risk Manager.
The buildathon page asks for three things: a public repo, a **5-minute pitch
video**, and the architecture. The repo and architecture already exist. This is
the video, timed to the second.

**Timing budget:** 5:00 total, hard. Trim the middle before the end; never cut
the opening or the closer.

**Every number below is checked against the repo as it stands.** If you re-run
anything and it moves, fix it here before you record. Reading a stale number
off a script is the one mistake this project cannot afford to make on camera.

---

## What is actually being judged

Straight from Razorpay's own pages, so the script can aim at it rather than at
a general idea of a good demo.

**The Track 02 bar, quoted:**

> "Stop the merchant losing money to fraud, returns and chargebacks. Build a
> working detector, verifier or auto-responder for one class of loss, with
> measured precision and recall on a held-out test set."
>
> "Honest metrics including false-positive cost. Strictly defense-only:
> anything offense-capable is disqualified."

**The four parameters submissions are scored on**, and the beat in this script
that answers each:

| Judged on | Where this video earns it |
|:--- |:--- |
| **Problem taste** (a real financial or merchant problem) | 0:00-0:40, the gap UPI cannot close |
| **Build quality** (clean repo, reliable execution, code you can trust) | 0:40-1:35 running live, and the repo itself |
| **AI judgment** (AI where it belongs, deterministic where it does not) | 3:55-4:35, twelve rules and exactly one model call |
| **Failure recovery** (what broke, and how you fixed it) | 3:05-3:55, FAILURES entry 16 |

Two lines of their guidance are worth taping to the monitor:

> "Record your pitch video like you are explaining the build to an engineer,
> not a recruiter. Focus on architecture and trade-offs."

> "A complete, working project in a narrower scope will outperform an ambitious
> but incomplete one."

Both are already true of this build. The video's only job is to not get in the
way of them.

### How to record it

**Screen recording with your own voice over it.** Not an animation, not a
generated video, not slides.

The whole argument of this project is that showing beats claiming, and a panel
that has been told to judge execution reliability wants to watch the thing run.
A polished animated video says the opposite of what a risk submission should
say, and it costs you the one thing you cannot fake: a page responding in real
time. There is also a panel interview immediately after, where every claim gets
tested, so a video that oversells is a debt you pay in the interview.

Concretely: OBS or the Windows Game Bar (`Win+G`), 1080p, screen plus
microphone. Your face is optional. The terminal and the browser are not.

Unlisted YouTube link, which is what the form asks for.

---

## 0:00-0:40: The problem (40s)

**On screen:** the landing page / hero image, or you talking to camera.

**Say (verbatim-ish):**

> "When an AI agent spends your money, there's no parchi. In India a *parchi* is
> a permission slip, show it, you get through. Right now AI has no parchi."

> "Razorpay has Agentic Payments on UPI Reserve Pay. The human pre-approves a
> spending *limit*, but the rail records *nothing about what the money was for*.
> UPI can answer 'is there money, and is it the right merchant'. It cannot answer
> 'is this the thing I actually asked for'."

> "That gap is where fraud enters. The customer says *'my agent did that, I
> didn't'*, and today the merchant cannot prove otherwise."

**Why this order:** lead with the problem, exactly as every judging guide says.
Do not open with "I built a thing called Parchi."

---

## 0:40-1:35: The demo (55s)

**On screen:** `python demo/server.py` → the page loads → you run scenarios.

Three beats, in this order, nothing else:

1. **The injection that passes every rule** (0:40-1:05). Open the
   `injection` scenario. Say:

   > "This cart: right category, under the cap, valid slip, unspent nonce, and
   > every price is the shop's own. All twelve of my deterministic rules pass.
   > The product page talked the agent into a protection plan the human never
   > asked for. One model call is the only thing that catches it."

   Click **Authorize** → **BLOCK, reason: doesn't match intent.**

2. **Tamper the ledger** (1:05-1:20). Say:

   > "Every decision is written to a hash-chained ledger. I don't ask you to
   > believe it's tamper-evident, press Tamper and it verifies itself as broken."

   Click **Tamper** → the chain panel flips red.

3. **The three-way verdict** (1:20-1:35). Say:

   > "Three answers, not two. A high-value legitimate purchase isn't
   > auto-approved and isn't refused: it steps up to a human. Allow, block, or
   > ask. That's the difference between a filter and a risk product."

   Run `step_up` → **STEP_UP**.

---

## 1:35-2:20: The second layer, and the AI that can block you (45s)

**On screen:** run the `swarm` scenario, then switch to `/console`.

This is the beat that makes it an *AI Risk Manager* rather than a validator.
Have the console already signed in on a second tab so you are not typing a
password on camera.

**Say:**

> "Everything so far judges one cart against one slip. But some attacks are
> only visible across *many* carts. Watch this one."

Run **`swarm`** → it comes back **ALLOW**.

> "Allowed. Correctly. Three different agent credentials each presented a
> perfectly valid slip for the same account, and every rule passed for every
> one of them, because each agent really is registered. No single-cart check
> can see this."

Switch to the console tab, refresh:

> "One payer wearing many faces is a credential farm. So the checkpoint hands
> that pattern to a model and asks the one question a counter can't: *is this
> account genuinely under attack?* It said yes, ninety-five percent confident,
> and the account is now cooled down for ten minutes."

Point at the alert feed, then the release button:

> "This is the operations console. Only staff get in. Every alert names who it
> was about, the AI's verdict is attached with its confidence and which model
> said it, and there's a Release button, because an automatic block nobody can
> undo is a lockout waiting for 3am."

Run any scenario again on the shop tab → **BLOCK, account in cooldown.**

> "And it's not in the payment path. The model runs *after* the verdict, on its
> own thread. A wrong answer there costs a cooldown a human can lift. It can
> never cost a silently stolen purchase."

---

## 2:20-3:05: The scoreboard (45s)

**On screen:** the README results table.

**Say:**

> "I scored it like a risk product, not a demo. A thousand labelled agent
> purchases. The number I care about is not recall: it's the *cost of the
> mistakes, in rupees*, because a blocked genuine customer is money the merchant
> lost."

Walk the table:

> "Allow everything: seventeen lakh paid out on violations. Block all agent
> traffic: thirty-three lakh of good revenue gone. Rules alone: eighty-four
> percent caught, two lakh nineteen still through. Parchi, rules plus one model
> call: all two hundred eighty violations, zero good customers blocked."

Then scroll to the model-run table:

> "And posted right next to it, my full run against a *real* model. Two hundred
> seventy-eight of two hundred eighty caught, twenty-two false blocks, and
> twenty-two calls that missed the four-second budget. Every one of those
> twenty-two went to a human. Not one was auto-approved."

> "An earlier run of the *same code* scored ninety-seven percent with zero
> degraded. Same thousand rows, different day, slower endpoint. I publish both,
> because a risk product that only quotes its best run is doing the exact thing
> it exists to prevent."

**Why this lands:** the buildathon bar is literally "honest metrics including
false-positive cost." You are reading their grading rubric back at them.

---

## 3:05-3:55: The honesty beat (50s)

**On screen:** `FAILURES.md`, scrolled to entry 16.

This is your strongest 50 seconds. Do not rush it.

**Say:**

> "The file I'm proudest of is the failure log. Seventeen entries, each with
> what I first assumed, what it actually was, and what it cost. Entry sixteen is
> the one I'd want a risk team to read."

> "I built that AI adjudicator, the one that locks an account for ten minutes.
> It worked. It caught every attack I threw at it. Then I noticed something:
> every deterministic check in this repo is scored against a thousand rows, and
> the one component that can refuse a real customer was scored against nothing."

> "So I wrote the eval that should have existed first. Twelve situations, six
> real attacks and six ordinary customers who just happen to trip a counter, an
> office manager buying for a team, someone retrying on bad wifi, a shopper in a
> public sale. It convicted fifteen out of eighteen innocent customers."

> "Its perfect recall wasn't skill. A rule that always says yes catches every
> attack too. The prompt listed what attacks look like and never once said what
> *clearing* someone looks like."

> "The fix was writing the cost asymmetry into the prompt: convicting a real
> customer blocks them for ten minutes while they're mid-purchase, and clearing
> a real attacker costs nothing, because every deterministic refusal still
> stands. Eighteen out of eighteen attacks, sixteen out of eighteen customers
> left alone. Both runs are in the file."

> "I'm not pretending a build this size is production. Keys in memory, a
> single-process nonce store, all named in the README, not hidden."

---

## 3:55-4:35: The architecture (40s)

**On screen:** the mermaid flowchart from the README (full screen).

**Say, pointing:**

> "Three parts. One: the human signs an AP2-inspired intent mandate, cap,
> categories, expiry, nonce, and the agent's own playback of the request."

> "Two: twelve deterministic checks run first, signature, expiry, payee, method,
> line items, quantity, prices, category, discount, cap, agent identity, replay.
> Cheap, exact, auditable, no AI in that file. They short-circuit on the first
> failure. Discount is checked *before* the cap on purpose, because the cap is
> enforced on the post-discount total, so an unverified coupon is a way under
> any ceiling."

> "Three: only if every rule passes, *one* model call answers the question rules
> can't. Strict typed JSON, a timeout, untrusted merchant text fenced off as
> data, and the spending cap deliberately kept *out* of the prompt so the model
> never re-decides arithmetic."

> "Every decision, allow, block or step-up, is hash-chained either way, so a
> merchant can prove what was authorised. And behind all of it, the behavioural
> layer watching what one cart can't show: velocity, coupon farming, agent
> swarms. Those raise alerts. They never change a verdict."

---

## 4:35-4:52: Why this matters to Razorpay (17s)

**Say:**

> "Agent Studio already has an agent that *answers* disputes on human
> transactions. Parchi is the opposite half: it *prevents* and *evidences*
> disputes on *agent* transactions, the unsolved one. It's a pre-authorisation
> checkpoint sitting where the mandate meets the cart, mapping field-for-field
> onto UPI Reserve Pay."

---

## 4:52-5:00: The closer (8s)

**Say:**

> "No parchi, no purchase. The repo is public, two commands reproduce every
> number, and the failure log is as long as the feature list."

**Screen:** the repo URL.

---

## Panel prep: the questions, answered

1. **"How do you know your 100% isn't overfitting your generator?"**
   → The 1,000-row batch is generator-tuned. The *held-out* set
   (`eval/heldout.py`, hand-written, in CI) is not: it includes categories,
   playback phrasings and one-paise edges the generator never produces. Every
   case handled as specified, 0 false blocks. Plus a 48-pattern attack suite.

2. **"What's the false-positive cost model?"** → False positives are counted in
   rupees, not percentages, because a blocked real customer is lost money.
   ₹33 lakh under block-all, ₹0 under the reproducible run.

3. **"Your model run got worse than your earlier one. Why publish it?"**
   → Because it's the run whose ledger I can hand you. The earlier one's
   evidence file turned out to be from a *different* run, which is FAILURES
   entry 17, and there's now a test that checks a published ledger's timestamps
   fall inside the run that reports them.

4. **"Why one model call and not an agent loop?"** → It sits in front of a
   payment; a slow answer is a wrong answer. Deterministic fallback *is* the
   retry policy. The one place I do allow a retry is the adjudicator, which
   gates a cooldown rather than a payment.

5. **"What happens when the model dies?"** → Nothing auto-approves. It fails to
   STEP_UP, ask the human. Demo it with `--provider off`. In the published
   model run, all 22 degraded calls became STEP_UP.

6. **"Why is the cap kept out of the prompt?"** → Because the model re-decided
   it and got it wrong, blocking a ₹4,077 cart as "exceeds ₹5,000". FAILURES
   entry 10. Two later attempts to fix it by prompt both measured *worse* and
   were reverted: entry 15.

7. **"How do you stop the AI blocker from blocking real customers?"**
   → I measured it, found it convicting 15 of 18, and fixed it. `eval/adjudicator.py`
   re-runs the measurement. The gate is a named constant, the model must clear a
   confidence threshold, an unavailable model blocks nobody, and a human can
   release any cooldown from the console.

8. **"What's defense-only about it?"** → The whole system is a verifier/blocker.
   Nothing here can *initiate* a payment, only permit or refuse one. Track 02
   requires defense-only; this is compliance by construction, not a caveat.

9. **"What would you do with real Razorpay infra?"** → Hardware-backed keys, a
   shared nonce store and agent registry, a signed external anchor for the
   ledger, and shared state behind the behavioural detectors, which are
   per-process today.

---

## The submission form, answered

The form asks for the track, the project name, the problem statement, the repo
URL, the video URL, and **what broke and how it was resolved**. That last field
is a scored parameter, not a formality, and `FAILURES.md` is seventeen entries
long. Paste this instead, and link the file.

> Three worth naming, all measured rather than remembered.
>
> **The AI that blocks accounts was convicting innocent customers.** I built an
> adjudicator that reads an account's behaviour and can lock it for ten minutes.
> It caught every attack I threw at it, so it looked finished. Then I noticed
> every deterministic check in the repo is scored against a thousand rows while
> the one component that can refuse a real customer was scored against nothing.
> I wrote the eval that should have existed first: twelve situations, half of
> them ordinary customers who trip a counter. It convicted fifteen of eighteen
> benign cases. Its perfect recall was not skill; a rule that always says yes
> catches every attack too. The prompt listed what attacks look like and never
> said what clearing someone looks like. Writing the cost asymmetry into the
> prompt took it to 18/18 attacks caught with 16/18 customers left alone.
> `eval/adjudicator.py` re-runs the measurement. FAILURES entry 16.
>
> **The evidence published beside my headline numbers was from a different
> run.** The ledger linked as proof of the full model run ended 5,550 seconds
> before that run started, and the results file named a third file that a later
> run had overwritten. Every artefact verified on its own; the claim that one
> was evidence for the other did not. The cause was that a full batch always
> wrote the same ledger path, so publishing meant remembering to copy a file.
> `evaluate.py` now takes `--ledger`, the run was redone on a quiet tree, and a
> test asserts a published ledger's records fall inside the window of the run
> reporting them. The honest consequence: recall went 97.1% to 99.3% and false
> blocks went 12 to 22, and I published the worse-looking half. FAILURES 17.
>
> **Two prompt fixes that were obviously right measured worse.** The model was
> re-deciding the spending cap, so I told it not to; it invented a different
> price judgement instead. I forbade price reasoning entirely; that was worse
> again. Three full batches, about two hours, to arrive back where I started.
> Adding a rule to a prompt does not delete a behaviour, it moves it. FAILURES
> 15, with both measured tables.
>
> Full log, seventeen entries with what I assumed, what it actually was, and
> what it cost: [FAILURES.md](FAILURES.md)

### If they ask why the numbers are not all perfect

Because they are measured. The reproducible offline run catches 280/280 with
zero false blocks; the live model run catches 278/280 and wrongly blocks 22.
Both are published, next to each other, with the ledger for each. The gap
between them is the finding, not an embarrassment: it is what a shared
inference endpoint on a slow day does to a four-second budget, and the design
turns that into customers routed to a human rather than violations let through.

---

## Recording checklist

- [ ] `python demo/server.py` boots and **all 15 scenarios** behave before you record
- [ ] Console signed in on a **second tab** already, so no password is typed on camera
- [ ] `python eval/adjudicator.py` run recently enough that you can quote it
- [ ] Microphone test; screen at 1080p; no browser tabs that autoplay
- [ ] The injection → BLOCK clip and the swarm → cooldown clip are the two you
      must nail; re-record either if anything flickers
- [ ] Trim to **≤ 5:00**. Cut demo beats, never the opener or closer
- [ ] Put the video link at the **top of the README** (first line under the hero)
