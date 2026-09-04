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

### The slides

`docs/pitch-deck.html` is the deck for the beats that are not the live demo.
Open it in a browser, press <kbd>F</kbd> for fullscreen, and drive it with the
right arrow while you talk.

Two of its slides say **CUT TO THE BROWSER** in red. That is the instruction:
stop sharing the deck and switch to the running demo, because the demo has to
be the live thing rather than a picture of one. Switch back afterwards.

Press <kbd>P</kbd> for a presenter bar carrying the line for the current slide
and a running clock; <kbd>R</kbd> starts that clock when you start speaking. It
is off by default so it cannot land in a take by accident. <kbd>?</kbd> lists
the keys.

It needs no network and no fonts, because a deck that fetches something is a
deck that can fail halfway through a recording.

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

## The script, word for word

### How the five minutes are spent

The form asks for three minutes of explaining and two of demonstrating. That is
exactly how this is built, and it is worth knowing the shape before you learn
the lines:

| | What | On screen | Time |
|:--- |:--- |:--- |:--- |
| **Block A** | The problem | Deck, slides 1-2 | 0:00-1:10 · 70s |
| **Block B** | **The demo** | **Live browser** | **1:10-3:10 · 120s** |
| **Block C** | Numbers, what broke, architecture | Deck, slides 5-10 | 3:10-5:00 · 110s |

Blocks A and C are the three minutes of slides. Block B is the two minutes of
live demonstration, and it is the middle of the video on purpose: a panel told
to judge execution reliability should see the thing running before they are
asked to believe any number about it.

Say the lines roughly as written. They are built to be **spoken**, not read:
short sentences, one idea each, no clause you have to hold in your head. Where
it says *pause*, actually stop for a beat — it is the difference between
reciting and explaining. Slide numbers refer to `docs/pitch-deck.html`.

---

### BLOCK A · 0:00-1:10 · The problem (70 seconds, deck slides 1-2)

**What this block has to achieve:** by the end of it, someone who has never
thought about agent payments should understand the gap, and want to see it.
Do not explain your solution here. Just make the problem real.

**Slide 1.** On screen: the title.

> "When an AI agent spends your money, there is no parchi."
>
> "In India, a parchi is a permission slip. You show it, and you get through."

*Pause. Advance to slide 2.*

> "Razorpay has agentic payments now, on UPI Reserve Pay. The human approves a
> spending limit up front, then the agent shops on its own."
>
> "UPI can answer two questions. Is the money there, and is this the right
> merchant. It cannot answer the third one, and that is the one that matters.
> **Is this the thing I actually asked for?**"

*Pause. Let that question sit for a beat.*

> "That gap is where the fraud lives. The customer says, my agent did that, I
> didn't. The merchant cannot prove otherwise."
>
> "So I built the missing check. Every agent purchase carries a signed
> permission slip: the cap, the categories, the expiry, and the agent's own
> playback of what it thinks you asked for. Parchi checks the cart against it
> **before** the money moves."

---

### BLOCK B · 1:10-3:10 · The demo (120 seconds, live browser)

Slide 3 says CUT TO THE BROWSER. Stop sharing the deck and switch to the
running demo.

**What this block has to achieve:** three things that build on each other. One
cart the rules cannot catch. One attack no single cart can show. And the page
a human sits in front of. Roughly 40 seconds each — the beats are equal on
purpose, so losing time on the first does not eat the third.

**Beat 1, the injection (about 40 seconds).**

> "The checkpoint, running. Straight to the hardest case: a prompt injection on
> the product page."

*Click the `Prompt injection` scenario. Let the card render before you keep talking.*

> "Right category. Under the cap. Valid signature. Every price is the shop's
> own. All twelve deterministic rules pass this cart."
>
> "The product page told the agent to add a protection plan. The human never
> asked for one."

*Click Authorize. Wait for the verdict. Do not talk over the wait.*

> "Blocked, and the reason is in plain English: a protection plan the human did
> not ask for. One model call is the only thing that could have caught that."

**Beat 2, the swarm (about 40 seconds).**

> "Now an attack that no single cart can show you."

*Click the `swarm` scenario.*

> "Allowed, and that is correct. Three registered agents each presented a valid
> slip for the same account, and every rule passes. But one wallet with many
> faces is a credential farm."

*Switch to the console tab. Refresh.*

> "The adjudicator read the pattern and called it exactly that, ninety percent
> confident. The account is blocked for ten minutes."

**Beat 3, the employee side (about 40 seconds).** This is the half a company
actually staffs, and it is the beat most submissions do not have at all.

> "This page is the product for whoever answers for that decision."
>
> "A human releases that block, with their name on it. The AI can lock an
> account. It can never unlock one."

*Point at the refund row.*

> "When a purchase already went out wrong, the model reads it again and
> **proposes** a refund. It does not issue it. An employee approves it here."

*Point at the defence lamp on the band.*

> "That lamp is the protecting AI reporting on itself. Not is it switched on.
> **Is it answering.** It showed green through a dead key until I measured it."

---

### BLOCK C · 3:10-5:00 · Numbers, what broke, architecture (110 seconds)

Switch back to the deck, slide 5.

**What this block has to achieve:** prove you measured it, then prove you can
be trusted with your own measurements. The second half is the harder sell and
it is the one that separates you.

**Slide 5, the scoreboard (about 35 seconds).**

> "A thousand labelled agent purchases. I count false positives in rupees,
> because a blocked real customer is money the merchant lost."
>
> "Block everything and you refuse thirty-three lakh of good revenue. Allow
> everything and you pay out seventeen lakh. Rules alone still leak two lakh."
>
> "Parchi catches two hundred and seventy-eight of two hundred and eighty, and
> wrongly blocks twenty-two."

*Point at the last row. This next sentence is the one that buys you credibility:*

> "That row is a run against a **real model**, not an offline stand-in. I could
> show you a hundred percent, but it would be my data and my rules marking
> their own homework."
>
> "So I had a model that has never seen my code write the attacks instead.
> Against those, **seventy-six percent**. That is the number I would defend."

**Slide 6, what broke (about 45 seconds). This is the beat that wins it.
Slow down.**

> "Now the part I would want a risk team to read."
>
> "I built that adjudicator, the one that just locked an account. It caught
> every attack I threw at it. It looked finished."

*Pause.*

> "Then I noticed: every deterministic check here is scored against a thousand
> rows. The one component that can refuse a **real customer** was scored
> against nothing."
>
> "So I wrote the test that should have existed first. Twelve situations, half
> of them ordinary customers who trip a counter. An office manager buying for a
> team. Someone retrying on bad wifi."
>
> "It convicted fifteen out of eighteen innocent customers."

*Pause. Let that sit.*

> "Its perfect recall was never skill. **A rule that always says yes catches
> every attack too.**"
>
> "The fix was telling the model what a false conviction costs. Eighteen of
> eighteen attacks caught. Sixteen of eighteen customers left alone."

**Slide 7, the architecture (about 30 seconds).**

> "The architecture is deliberately boring. Twelve deterministic checks first:
> plain code, no AI in that file, stopping at the first failure. Only if every
> one passes does a single model call run."
>
> "Three verdicts, not two: allow, block, and ask the human. If that call dies,
> the answer is ask. Never allow."

**Slides 8 to 10, the close (about 20 seconds).**

> "This sits where Razorpay sits, between the agent and the merchant. Bad agent
> traffic that clears costs the merchant twice: the chargeback, and the dispute
> ratio behind it. Every number I showed is merchant money."
>
> "Agent Studio answers disputes on human transactions. This prevents them on
> agent transactions. Defence only. Nothing here can initiate a payment."

*Advance to the last slide.*

> "No parchi, no purchase. The repo is public, and two commands reproduce every
> number in it."

*Stop talking. Leave the URL up for two seconds, then end the recording.*

---

### Timing, measured rather than hoped

The spoken lines are **758 words**. Add about 20 seconds of clicking and
waiting in the demo, where you should not be talking anyway.

| Your pace | Total |
|:--- |:--- |
| 145 wpm, unhurried | 5:33 |
| 155 wpm, normal for a technical talk | **5:13** |
| 165 wpm, brisk | **4:55** |

The split is 3:00 of explaining (blocks A and C, 70s + 110s) against 2:00 of
live demonstration (block B), which is exactly what the form asks for.

Read that table honestly. At an unhurried pace this runs over five minutes, so
if the limit is hard, either rehearse at the brisk row or take the first cut
below. Knowing which row you are is worth more than anything else in this
document.

If you speak slowly, buy time in this order: the ledger tamper beat is already
out of the script and is listed below under what to add if you run short, so
start instead by cutting slide 8's Agent Studio sentence (about 15 seconds),
then beat 3's defence-lamp line (about 15). Do not cut slide 6.

So: **rehearse once with a stopwatch and read your own row off that table.**
Nothing else in this document matters as much as knowing which row you are.

`tests/test_published_results.py` re-counts these lines and fails if the script
grows past its budget, because a script that quietly drifts to six minutes is
found out on the day rather than in the repo.

If you still run long, cut in this order and never anything below it:

1. Slide 8, the Agent Studio comparison.
2. Beat 3's defence-lamp line, keeping the refund-approval line.
3. Slide 7 down to its last sentence, the three verdicts.

Never cut slide 6, and never cut the refund-approval line in beat 3. Those two
are the beats that separate this from a demo of a filter: one is a measurement
that reversed a decision, the other is an AI that proposes and a human who
disposes.

**If you run short**, add the ledger tamper back. It used to be beat 2 and it
costs about 15 seconds:

> "Every decision goes into a hash-chained ledger. I am not going to ask you to
> believe it is tamper-evident."

*Click Tamper.*

> "It reports itself broken."

A 5:20 video with slide 6 and the refund line intact is a better submission
than a tight 5:00 without them.

---

## Panel prep: the questions, answered

1. **"How do you know this isn't overfitting your own generator?"**
   → It was, and that is why the headline is no longer the perfect score. Three
   answers, weakest to strongest. The hand-written held-out set
   (`eval/heldout.py`, in CI) has cases chosen to beat the generator's blind
   spots. The 48-pattern attack suite is adversarial by construction. And
   `eval/redteam.py` gives a model the product with **no rule, no check name
   and no threshold**, and asks it for attacks: 40 distinct cases, **76%
   caught**, and the seven that got through are named in the README. That last
   one is the only number here I did not mark myself.

2. **"What's the false-positive cost model, and what's the model's own rate?"**
   → False positives are counted in rupees because a blocked real customer is
   lost money: ₹33 lakh under block-all, ₹1,59,521 under Parchi. And the split
   matters more than the total. The rules block 235 violations and **zero** good
   customers. The model call catches the 43 no rule can see and produces **all
   22** false blocks, so its own precision is 66%. `eval/attribute.py` derives
   that from the published ledger.

3. **"Your model run got worse than your earlier one. Why publish it?"**
   → Because it's the run whose ledger I can hand you. The earlier one's
   evidence file turned out to be from a *different* run, which is FAILURES
   entry 17, and there's now a test that checks a published ledger's timestamps
   fall inside the run that reports them.

4. **"Why one model call and not an agent loop?"** → It sits in front of a
   payment; a slow answer is a wrong answer. Deterministic fallback *is* the
   retry policy. The one place I do allow a retry is the adjudicator, which
   gates a cooldown rather than a payment.

5. **"What happens when the model dies, and how slow is it?"** → Nothing
   auto-approves: it fails to STEP_UP and asks the human, and all 22 degraded
   calls in the published run did exactly that. On speed, `eval/latency.py`:
   a refusal a rule settles is **0.2ms at p95**; a cart that reaches the model
   is **10.9s at p95**, which is slow and is the endpoint rather than the design.
   Only carts that pass all twelve checks pay it, 300 of 1,000 in that run.

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

9. **"Why is the step-up threshold a flat Rs 10,000?"**
   → Because it is policy, not a model. A flat line is predictable, auditable
   and cannot be gamed by shaping a cart, which a learned threshold can. It is
   a constructor argument, not a constant buried in a function. The honest end
   state is per-payer and risk-scored, and the input that needs is behavioural
   history, which a four-day build does not have. The ledger already records
   everything that scoring would read.

10. **"What would you do with real Razorpay infra?"** → Hardware-backed keys, a
   shared nonce store and agent registry, a signed external anchor for the
   ledger, and shared state behind the behavioural detectors, which are
   per-process today.

11. **"Who is the user of the console, and what can they actually do?"**
   → A risk or operations employee, and the list is short on purpose: release a
   cooled account, approve a refund the AI proposed, acknowledge an alert, flip
   the AI gate off when the token bill matters, and read the defence lamp. Every
   one of those is attributed in the ledger by name. The two that cost a
   customer money — releasing a block and approving a refund — are the two the
   AI is structurally not allowed to do. It can lock an account and it can
   propose a refund; a person does the rest.

12. **"Your AI proposes refunds. What stops it refunding everything?"**
   → It has no refund capability. The after-purchase review writes a
   `REFUND_PENDING` state and a critical alert; the money moves when an operator
   clicks approve, and the approval carries their name. A review that could not
   judge the cart proposes nothing rather than proposing a refund it cannot
   justify. The general rule is the one in the README: **an AI is never the last
   actor on anything that costs a customer money.**

13. **"You have an autonomous defence toggle. Why is it off by default?"**
   → Because unattended AI action on live accounts is a decision a company makes
   deliberately, not a default it discovers after an incident. On, it lets the
   adjudicator triage privilege-escalation incidents without waiting for a human.
   Off, the deterministic ten-minute block still lands and an operator still gets
   the alert — the safety property does not depend on the toggle, only the speed
   of the triage does.

14. **"How do you know the protecting AI is actually running?"**
   → For a while I did not, and that is the honest answer. The console's lamp
   read the call budget, which counts calls the process is *allowed* to make. A
   refused call still spends one, so a dead key read green at zero successful
   calls while every adjudication silently returned nothing and the detectors
   carried on alerting as normal. The lamp now tracks outcomes: four consecutive
   failures turn it red and it names the cause. Fail-open is only safe if
   somebody is told. FAILURES entry 21.

15. **"You changed model providers late. What broke?"**
   → Nothing in the HTTP shape, which is what the portability claim was about,
   and three things around it. Reasoning tokens are spent from `max_tokens`
   before any content, so a 300-token ceiling returned an *empty* message with a
   200 status. The models took 58-83s until asked not to reason, then 6-9s. And
   the adjudicator's retry chain spent three calls to cover one failure, with a
   fallback pinned to a model the new endpoint does not offer. The cost is in
   the README: intent p95 went 6.1s to 10.9s. FAILURES entry 22.

---

## The submission form, answered

The form asks for the track, the project name, the problem statement, the repo
URL, the video URL, and **what broke and how it was resolved**. That last field
is a scored parameter, not a formality, and `FAILURES.md` is twenty-two entries
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
> Full log, twenty-two entries with what I assumed, what it actually was, and
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

- [ ] `python demo/server.py` boots and **all 16 scenarios** behave before you record
- [ ] Console signed in on a **second tab** already, so no password is typed on camera
- [ ] `python eval/adjudicator.py` run recently enough that you can quote it
- [ ] Microphone test; screen at 1080p; no browser tabs that autoplay
- [ ] The injection → BLOCK clip and the swarm → cooldown clip are the two you
      must nail; re-record either if anything flickers
- [ ] Trim to **≤ 5:00**. Cut demo beats, never the opener or closer
- [ ] Put the video link at the **top of the README** (first line under the hero)
