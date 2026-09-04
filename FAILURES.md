# What broke, and what it actually was

Kept as it happened, one entry per thing that broke. This is the file the
"build challenges" answer gets written from, so it is not tidied up afterwards.

Shape: what broke → what I first assumed → what it actually was → what I changed
→ what it cost.

---

### 1. The intent check silently agreed with the rules, and I almost shipped that as a result

**Broke.** The first full scoreboard run had `rules_only` and `parchi` producing
identical numbers, 90.0% recall, same rupee costs, to the decimal. The one model
call was adding literally nothing.

**Assumed.** The engine was never reaching the intent check: the deterministic
checks short-circuit, so I thought the in-category injection rows were being
blocked earlier by the category check and the model was simply never asked.

**Actually.** The engine was calling it. The matcher was wrong. It built the set of
things the human "wanted" as *playback words ∪ allowed categories*, and the set of
things a cart line "is" as *description words ∪ its own category*. Every in-category
line therefore matched on the category token alone, and that is the one case the
check exists to catch, and the one case `check_category` already covers
deterministically.

**Changed.** The intent check now compares descriptions against the playback only.
Category is a rule's job; folding it in here made the model a second, worse copy
of a rule that already ran. (`parchi/intent_match.py::_heuristic`)

**Cost.** ~25 minutes, and it would have cost the whole argument of the project: a
scoreboard where the model row equals the rules row says "delete the model".
Recall went 90.4% → 100% on the same batch once the check actually tested what it
claimed to test.

---

### 2. One row of the ground truth was labelled wrong, and the rules were right

**Broke.** `blocked_by` reported 84 blocks from the category check where the
generator had produced 85 category violations. One row was getting through.

**Assumed.** An ordering bug in `run_all`, some other check consuming the row
first.

**Actually.** The generator picked out-of-scope items from a fixed list without
excluding the mandate's own category. When a mandate allowed `electronics` and the
generator rolled "wireless earbuds / electronics", it produced a row labelled
`BLOCK` that was, in fact, a perfectly in-scope purchase. The engine was correct
and the label was wrong.

**Changed.** The generator now excludes the mandate's own category when choosing
an out-of-scope item, with a comment saying why.
(`data/generate.py`, `wrong_category` branch)

**Cost.** ~15 minutes. The lesson worth keeping: when the system disagrees with the
labels, check the labels first when *you* generated them.

---

### 3. Two policies disagreed about what a Rs 12,000 in-scope cart is

**Broke.** Exact-verdict accuracy stuck at 93.5% with 65 unexplained misses, all on
rows labelled `ALLOW`, none of them blocked.

**Assumed.** A step-up threshold bug.

**Actually.** The threshold was fine; the dataset overlapped it. Ordinary
`in_scope` rows drew caps up to Rs 15,000 and carts up to 85% of the cap, so 65 of
them landed above the Rs 10,000 step-up line. The engine answered `STEP_UP`; the
label said `ALLOW`. Both were defensible, which is the actual problem: the case
types were not disjoint.

**Changed.** Everyday caps are now capped below the step-up threshold, so a row is
either "small and fine" (`ALLOW`) or "large and fine" (`STEP_UP`) and never
ambiguously both. (`data/generate.py`)

**Cost.** ~20 minutes, and a real lesson about generated evaluation sets: the
generator has to encode the same policy the engine does, or you spend your time
scoring the disagreement between two of your own opinions.

---

### 4. "Fail closed" was implemented as "burn the customer"

**Broke.** With the model killed (`--provider off`), Parchi's precision fell to
85.5% and it destroyed **Rs 6,22,472** of legitimate revenue across 40 carts. On
that metric it was worse than the rules-only baseline it was meant to improve on.

**Assumed.** That this was the honest cost of failing closed and belonged in the
video as-is.

**Actually.** I had implemented "fail closed" as `BLOCK`. But the degraded path has
no finding: the intent check did not conclude the cart was wrong, it concluded
nothing at all. Refusing a purchase on the strength of "I could not check" throws
away a customer to avoid a risk that was never established. "Never auto-approve" is
the property that matters; "refuse" is a different and more expensive property.

**Changed.** A degraded intent check now returns `STEP_UP` at every amount: the
human decides. Semantic uncertainty can no longer reopen a low-value injection.
(`parchi/engine.py`, with tests pinning the fail-safe path.)

**Cost.** ~20 minutes, and it turned the worst number in the project into the best
demo beat: kill the model and every rules-valid cart asks the human, with nothing
silently auto-approved.

---

### 5. The tamper-evident log could be made unverifiable by deleting it

**Broke.** Found while filming-testing the demo page: I wiped `demo/ledger.jsonl`
from a shell while the server was running, ran one scenario, and the new first
record linked to `prev = f0defdb1…`: the hash of a record that no longer existed
anywhere. `verify_chain` would call that a broken chain forever after.

**Assumed.** Nothing; I noticed the hash in the UI did not start at zeros and went
looking.

**Actually.** `Ledger` reads the last hash once, at construction, and then keeps it
in memory. That is correct for an append-only file and wrong for a file that can
disappear underneath it, rotation, a wipe, a demo reset that recreates the object
in one place but not another.

**Changed.** `append()` re-anchors to `GENESIS` when the file is missing, plus a
test that removes the file mid-run and asserts the next record starts a fresh,
verifiable chain. (`parchi/ledger.py`,
`test_ledger_re_anchors_if_the_file_disappears_under_it`)

**Cost.** ~10 minutes. Worth the entry because the whole claim of the module is
"you can verify this log", and the failure mode was the log accusing *itself* of
tampering after an ordinary file operation.

---

### 6. Six ways past the checkpoint, found by writing the attacks down first

**Broke.** I wrote `tests/test_attacks.py` - 28 named attacks, each with the verdict
Parchi must return - and ran it against a version I had already convinced myself was
correct. Six patterns came back wrong:

| Pattern | Expected | Got | Why it mattered |
| --- | --- | --- | --- |
| `payee-substitution` | BLOCK | **ALLOW** | Nothing compared the cart's payee to the mandate's. A valid slip for one shop authorised a purchase at any other. |
| `zero-value-cart` | BLOCK | **ALLOW** | An empty cart passed every check and authorised a payment for nothing. |
| `issued-in-the-future` | BLOCK | **ALLOW** | A slip dated forward bought an agent an arbitrarily long window; only `expires_at` was ever read. |
| `method-case-variance` | ALLOW | **BLOCK** | `"UPI"` from a merchant integration was refused as an unauthorised instrument. |
| `category-case-variance` | ALLOW | **BLOCK** | So was `"Footwear"`. |
| `category-whitespace-padding` | ALLOW | **BLOCK** | And `" footwear "`. |

**Assumed.** That the six checks were complete, because each one I had written
worked. Every test I had written until then confirmed a check I had already thought
of - which is the failure mode, not the test suite.

**Actually.** Two different bugs wearing the same shape. The first three were
missing checks: I had verified the *slip* thoroughly and the *cart* barely, so
whoever was being paid, whether the cart had anything in it, and whether the clock
made sense were never asked. The last three were the opposite mistake - comparing
merchant-supplied strings byte-for-byte, which turns ordinary integration variance
into a refused customer. And two more patterns (`negative-line-offset`,
`line-flood`) were *passing for the wrong reason*: the intent check happened to
flag them, so a model outage would have re-opened both.

**Changed.** Two new deterministic checks (`payee`, `line_items` - non-empty,
positive, integral, bounded), expiry now rejects a window that closes before it
opens and a slip dated beyond clock-skew tolerance, and every merchant-supplied
string is compared through `norm()` (NFKC + strip + casefold). `norm` deliberately
does not fold confusables, so the Cyrillic-homoglyph pattern still blocks - the safe
direction. Six checks became eight; the pattern file went green.

**Cost.** ~45 minutes, and it changed how I test: the suite is now the first place
a newly found bypass goes, before the fix, and CI fails the build if any pattern
regresses. `quantity-inflation` is in there as a recorded blind spot rather than a
passing test I fudged.

---

### 7. The "deterministic" dataset was not deterministic, and CI would have failed forever

**Broke.** Testing the repo end to end, I generated the batch twice with the same
seed and diffed the bytes. All 1,000 rows differed. The README promises "same seed,
same 1,000 rows", and the CI job runs `git diff --exit-code -- data/` to enforce
it. That job would have gone red on its very first run, on a fresh clone, with no code
change at all.

**Assumed.** Briefly, that I had changed the generator. I had not: three consecutive
runs of untouched code produced three different files.

**Actually.** `new_mandate()` mints `mandate_id` and `nonce` from `uuid.uuid4()`,
which knows nothing about the generator's seed. Every other field came from the
seeded `random.Random`, so the *distribution* was identical run to run, same case
mix, same recall, same precision, while every row's identity, and therefore every
signature, was different. That is the nastiest shape a reproducibility bug can take:
all the summary numbers agree, and only a byte comparison disagrees.

**Changed.** `new_mandate` now accepts optional `mandate_id` and `nonce`, and the
generator passes seeded values. The uuid4 default stays, with a comment saying why:
a predictable nonce is a replay vulnerability, so injectability exists for the
fixed-seed generator and nothing else. Added
`test_the_same_seed_produces_byte_identical_rows`, which also asserts a *different*
seed still differs, otherwise the "fix" could be no randomness at all.
(`parchi/mandate.py`, `data/generate.py`)

**Cost.** ~25 minutes, plus a knock-on: seeding those two fields consumes from the
same random stream, so every downstream draw shifted and the rupee figures moved
(block-all Rs 33,34,734 → Rs 34,43,816). Recall and precision were unchanged. The
README and the demo page numbers had to be re-derived from the new run.

---

### 8. The demo served a cached page, and I nearly diagnosed the wrong thing

**Broke.** Mid-test, I fixed a CSS bug, reloaded the page, and the fix was not there.
`curl` proved the server was sending the corrected HTML; the browser was showing the
previous version.

**Assumed.** That my edit had not been written, or that the server had not picked it
up. Both wrong.

**Actually.** FastAPI's `FileResponse` sends `ETag` and `Last-Modified` but **no
`Cache-Control`**. With no explicit freshness directive, a browser is free to apply
heuristic caching and serve from cache *without revalidating*. The JSON endpoints
were worse: a cached `/api/ledger` would show a chain state that is no longer true.

**Changed.** A middleware that sets `Cache-Control: no-store, must-revalidate` on
every response. Verified on both the HTML and the API. (`demo/server.py`)

**Cost.** ~15 minutes, and it is the entry I would most want a reviewer to see: this
would have hit during filming, on stage, as "I fixed that, why isn't it showing".

---

### 9. The one that did not break, because the plan warned about it

Canonical bytes. `asdict()` on the frozen dataclass turns `allowed_methods` into a
tuple, `json.dumps` writes it as an array, and anything rebuilt from JSON comes
back as a **list**, different Python object, identical canonical bytes only if you
normalise on the way in. `IntentMandate.from_dict` does that normalisation, and
`test_canonical_bytes_are_stable_across_a_json_round_trip` is the test that would
have caught it. Written before it could bite, because it is the single most
predictable way this project dies.

---

### 10. The first real model run, and three things the offline stand-in had been hiding

**Broke.** Wired up an OpenAI-compatible provider (nano-gpt, GLM) so the scoreboard
could finally run against a model instead of the lexical stand-in. First 25-row
sample: **0.2 seconds**, and 21 of 25 rows marked degraded.

**Assumed.** A network or auth problem at the endpoint.

**Actually.** Three separate bugs, stacked, each of which produced a *complete,
plausible table* rather than an error. That is the thread running through all of
them: this system's failure mode is not a crash, it is a confident answer.

1. **`.env` was never loaded on an explicit `--provider`.** `resolve_provider`
   returned early for anything but `auto`, so the key was missing, every call
   raised, and every row took the degraded path. The batch finished, the table
   printed, and the numbers were the fallback's. Fixed by loading before the
   branch, and by `_guard_degraded_run`, which now makes one real call before
   scoring and refuses to continue if it degrades.

2. **The 4-second timeout was shorter than the endpoint.** Raising it to 30s cut
   degradation from 21 rows to 7, but the remaining 7 were not timeouts at all.
   Six were `getaddrinfo failed`. urllib opens a fresh socket, and therefore a
   fresh DNS lookup, per request; a few hundred lookups in a couple of minutes
   and the resolver simply stops answering. Fixed with one keep-alive connection
   and a reconnect-once-on-transport-error rule. HTTP statuses are still never
   retried: a 429 is the endpoint's answer, not a glitch. Degraded rows: 7 → 0.

3. **The model was enforcing the price cap, and getting it wrong.** With the
   transport fixed, precision fell to **57.1%**, three legitimate carts blocked,
   Rs 11,667 of false-positive cost. The reasons were fluent and wrong:

   > *"The cart contains a USB-C hub, which matches the authorised intent, but the
   > total amount of Rs 4,077.26 exceeds the authorised maximum of Rs 5,000."*

   Rs 4,077 does not exceed Rs 5,000. The prompt had been handing the model
   `Maximum: Rs {cap}`, so it dutifully re-decided a question `check_amount` had
   already answered exactly, and did it badly, because comparing two numbers is
   the one thing a model should never be asked for here.

**Changed.** The cap no longer appears in the prompt at all. The model is told, in
so many words, that the limit, the method, the merchant and the category list were
already checked by arithmetic and are not its job, and that it must never answer
false because of a price. Line prices stay, because recognising an unrequested
add-on needs them. Same 25 rows afterwards: **recall 100%, precision 100%, 0
degraded.**

**Cost.** ~90 minutes. It is the same lesson as entry 1, arriving from the
opposite direction: there, the model was a worse copy of a rule and added nothing;
here, the model was a worse copy of a rule and actively destroyed revenue. The
boundary is the whole design, rules decide anything arithmetic and exact, the
model decides only the one thing arithmetic cannot: is this the thing the human
asked for.

---

### 11. The full 1,000-row model run: what a 40-row sample could not show

**Broke.** Nothing crashed, which is the point. The sample runs (25-40 rows)
came back 100% recall, 100% precision, 0 degraded, and the full run does not.

**Assumed.** Implicitly, that a clean sample scales to a clean batch. It does
not, in two separate ways.

**Actually.**

1. **The endpoint throttled under sustained load.** 114 of 1,000 calls took
   the degraded path: a trickle through the first half, a wall towards the
   end. The stratified-40 sample never ran long enough to hit it. Every
   degraded cart became `STEP_UP` by design (entry 4), so nothing was silently
   auto-approved, but roughly a hundred legitimate customers would have been
   asked "are you sure?" for no reason. Fail-safe is not fail-free: degradation
   converts risk into friction, and at 11% of traffic that friction is a
   product decision, not a rounding error.
2. **The model made three real mistakes.** Two false blocks (Rs 34,404,
   including one Rs 30,102 legitimate high-value cart) and one in-category
   injection it called in-scope (Rs 5,000, `second pair, same shoe`, a
   quantity-inflation variant, the recorded blind spot arriving through the
   intent check's front door). Four more violations landed on `STEP_UP` via
   degradation instead of `BLOCK`: the safe direction, but misses on the
   scoreboard.

Final: **recall 98.1%** (255/260), **precision 99.2%**, false-positive cost
**Rs 34,404** vs the heuristic's Rs 0, ledger chain intact across all 1,000
records, 39/40 high-value legit carts still reached a human.

**Changed.** Nothing in the code: the failure-safe held, which is the
validation. Changed the claims instead: the README now publishes the model
table next to the heuristic one, degraded rows and all, because a risk product
that only quotes its best run is doing the thing it exists to prevent.

**Cost.** ~7 hours of wall-clock run time and one subscription's worth of
calls. The lessons: a sample measures correctness, only a full batch measures
*load*; and "degraded" is a verdict you must budget for (retry queues,
fallback providers, or accepting the friction) rather than a state you
pretend is rare.

---

### 12. Quantity inflation, five identical allowed pairs, under the cap

**Broke.** `quantity-inflation` was a known blind spot: the cart contained five
lines of "running shoes", each within an allowed category and the total under the
cap. Every deterministic check passed, and the heuristic intent matcher treated
"shoes" as a request for any number of pairs.

**Assumed.** That only a real model could count what a human asked for, so the
blind spot belonged in the README and the attack suite as a recorded limitation.

**Actually.** The playback itself carries a quantity: "buy running shoes" implies
one pair, and explicit numbers like "two" or "five" are words the offline matcher
can read. The real gap was in the data model, `CartLine` had no `quantity` field,
so five pairs showed up as five separate lines or one line with quantity hidden
from the prompt.

**Changed.** Added `quantity` to `CartLine`, rendered it in the intent prompt,
and made the heuristic aggregate identical descriptions and compare the total to
the implied quantity from the playback. Added a deterministic sanity bound on
per-line quantity so runaway inflation is blocked even if the model is dead.
Turned the known blind spot into a defended pattern.

**Cost.** ~30 minutes. The lesson: a "model-only" gap often has a cheap
representation fix; the model should judge intent, not reconstruct data the
cart should have carried in the first place.

---

### 13. "My agent did that, I didn't": there was no agent to point at

**Broke.** Parchi verified that a cart matched a signed payer intent, but it had
no answer to *which* agent presented the cart. A stolen agent credential or a
malicious third-party agent could replay a valid mandate.

**Assumed.** That agent identity was infrastructure outside the demo scope and
belonged in known limitations.

**Actually.** The dispute story Parchi tells is incomplete without it. When a
customer says *"my agent did that, I didn't"*, the merchant needs a signed
statement from the agent itself, not just the payer's permission. That is a
checkpoint-level question, not an ops-layer one.

**Changed.** Added optional `allowed_agent_id` to the mandate, an `agent_id` +
`agent_signature` on the cart, and a deterministic `agent_identity` check that
verifies the cart is signed by the agent named in the mandate. Added three attack
patterns: agent substitution, missing signature, and tampered cart. The canonical
byte format omits empty optional fields so older mandates still verify.

**Cost.** ~60 minutes. The lesson: the difference between a filter and a risk
product is who you can hold accountable when something goes wrong.

---

### 14. The demo stopped demonstrating anything, and every failure was safe

**Broke.** Clicking through the demo against a live endpoint, the `injection`
scenario: the one beat where the model earns its place, came back **STEP_UP,
not BLOCK**. So did `step_up`, for the wrong reason. CI was green throughout.

**Assumed.** A timeout. The engine's budget is 4s and the endpoint answers in
2-10s, so the wall looked obvious.

**Actually.** Three bugs stacked behind one symptom, and *every one of them failed
safe*, which is precisely why none of them surfaced as a failure.

1. **The 4s budget is a production posture, not a demo one.** Raising it fixed
   `quantity_inflation` and nothing else. CI never saw this: with no key the
   provider resolves to the offline matcher, which answers instantly, so the
   configuration that breaks the demo is the one CI does not run.

2. **One HTTPS connection shared across threads.** Keep-alive pooling was added
   to stop `getaddrinfo failed` on long batches, with the connection in a module
   global. `http.client` is not thread-safe and uvicorn runs sync handlers in a
   threadpool, so concurrent authorizations interleaved on one socket and came
   back as `ResponseNotReady: Idle`, or as one thread reading *another thread's
   response body*. Measured: 6 concurrent requests, 4 wrong. Fixed with
   `threading.local`, keeping the DNS win without sharing a socket.

3. **The model answers correctly in the wrong envelope.** With the transport
   fixed, ~9% of replies were still `{"answer": false}`: the right judgement,
   no reason attached, or a double-encoded
   `{"answer": "{\"match\": false, ...}"}`. A bare boolean must be refused: an
   unexplained `true` would move money with nothing in the ledger to justify it.
   So every one degraded.

**Changed.** `_unwrap` recovers the double-encoded case, narrowly, single-key
envelopes, two levels deep, and the caller still enforces the exact shape and
types, so an unrecognised envelope still degrades. The bare-boolean case was
fixed at the source instead, by requesting a strict `json_schema` rather than a
bare `json_object`. Measured on the same cart, 32 calls each:

| `response_format` | usable |
|:--- |:--- |
| `json_object` | 29/32 |
| `json_schema` strict | **32/32** |

A rejection of `json_schema` falls back to `json_object` once and is remembered,
because portability across OpenAI-compatible endpoints is that module's purpose.

Demo afterwards: 5 sequential and 6 concurrent injection requests, **11/11 BLOCK,
zero degraded**, and all 10 scenarios match the verdicts CI asserts.

**Cost.** ~70 minutes, and the uncomfortable lesson: *a system designed to fail
safe will hide its own bugs.* Every failure here produced a defensible verdict and
a complete audit record, so nothing alerted: the checkpoint was fine and the
product was broken. Watching only for unsafe outcomes would never have found it.
The rate of `degraded` is the number to watch, not just the verdict.

---

### 15. Two fixes for the same eight false blocks, both measured, both worse

**Broke.** The first honest full-batch run against a real model came back with
recall 98.9% and precision 97.2%, and **8 legitimate carts refused, Rs 49,279**.
Reading the model's own reasons, all eight had the same cause: it was doing
arithmetic on a price.

> *"The cart contains a single coffee bean item, but the human requested coffee
> beans under Rs 10,000, which implies..."*

**Assumed.** That the cap was still reaching the model. It is not in the prompt,
that was fixed in entry 10, so I looked for the second route and found it: the
playback is the human's own sentence, and humans write *"buy coffee beans under
Rs 5,000"*. The budget arrives inside the thing the model is supposed to be
matching against.

**Attempt one.** Tell it to ignore a budget in the playback. Full batch, same
1,000 rows:

| | recall | precision | false-block cost |
| :--- | ---: | ---: | ---: |
| before | 98.9% | 97.2% | Rs 49,279 |
| after | **97.1%** | **95.8%** | **Rs 92,114** |

Worse on both, and the cost nearly doubled.

**Actually.** The instruction did not remove the behaviour, it relocated it. The
model stopped citing the budget and started objecting to prices on their own
terms:

> *"a single item priced at Rs 6,340.37, which is not a standard price for..."*

Told not to compare against the stated budget, it invented a different price
judgement instead of dropping price reasoning altogether.

**Attempt two.** Forbid price reasoning of every kind: not the budget, not
whether an item looks expensive, not whether the amount seems normal. Full batch
again: **recall 96.4%, precision 96.1%, Rs 90,402**. Still worse than saying
nothing.

**Changed.** Reverted to the wording that measured best, which is the entry 10
version. The prompt now carries a comment pointing here, so the next person to
have this idea, including me, has to read the two runs that already tried it.

**Cost.** Three full batches, roughly two hours of wall clock, to arrive back
where I started. Worth writing down for two reasons.

The first is that both attempts were *reasonable*, and both were wrong, and the
only thing that separated the hypothesis from the outcome was running it on 1,000
rows. A 25-row sample would not have resolved a difference of this size, and
eyeballing a handful of reasons would have confirmed whichever story I already
believed.

The second is the shape of the failure. Adding a rule to a prompt does not delete
a behaviour, it moves it. The model wants to talk about price because price is in
front of it, and each instruction only closes one of the ways it can. The
durable version of this fix is not better wording, it is not showing the model a
price it has no job to judge, and the reason that is hard here is that the price
is genuinely useful for the one thing it *is* asked: recognising an add-on the
human never mentioned. That trade is still unresolved.

---

### 16. The AI that decides who gets blocked convicted 15 of 18 innocent customers

**Where.** `parchi/ai_guard.py`, the adjudicator that reads an account's
behaviour and decides whether the ten-minute cooldown is earned.

**What was wrong.** Nothing, by the standards the rest of this repo was using.
It was wired up, it returned well-formed verdicts, it convicted the swarm
scenario at 0.95 confidence, and it had tests. Every deterministic check in this
project is scored against 1,000 rows and a held-out set. The adjudicator was
scored against nothing, which is the wrong asymmetry: it is the one component
whose verdict locks a real customer out of their own account.

**Found by** writing the eval that should have existed first
([`eval/adjudicator.py`](eval/adjudicator.py)): twelve situations, six attacks
and six ordinary customers who trip a counter, labelled independently of any
detector. The first run:

```
caught       6/6 attacks
left alone   3/6 ordinary customers
FALSE BLOCKS 3, each a real customer locked out for 10 minutes
```

Three runs of each case made it worse, not better: 15 false blocks out of 18
benign judgements. Five of the six benign cases convicted at least once, and two
convicted every single time. The office manager buying for a team, the customer
retrying on a flaky connection, the shopper in a public sale: all blocked.

**Why.** The prompt listed what attacks look like and never said what clearing
looks like. It opened with "the detectors already fired", then gave six attack
readings and one thin sentence of exculpatory context. So the model did what the
prompt actually asked and convicted almost everything. Its 100% recall was not
skill. A rule that always says yes catches every attack too.

**Changed.** The cost asymmetry is now the first thing in the prompt, stated in
the terms the model has to weigh: convicting a real customer blocks them for ten
minutes while they are mid-purchase, and clearing a real attacker costs nothing
*here*, because every deterministic refusal still stands and the alert still
reaches a human. Then two discriminators the first prompt never gave it: on a
coupon, count **payers** rather than attempts (many payers on one code is a
sale; one payer across many mandates is farming), and on repeats, count what
**changed** (a resubmitted cart is a retry; a cart rebuilt after a *refusal*
with only the nonce changed has no innocent version).

Same twelve cases, same model, three runs each:

| | attacks caught | customers left alone | false blocks | accuracy |
|---|---|---|---|---|
| Before | 18/18 | 3/18 | 15 | 58% |
| After | 18/18 | 16/18 | 2 | 94% |

**And the default model was wrong too.** It was pinned to the 5-series tier on
the theory that a harder judgement deserves a heavier model. Diagnosing the
retry chain showed the pinned model answering `HTTP 402` and falling through to
the smaller one on most calls, so the "heavier model" was mostly decorative, and
the flagship had already been measured at 70s per call returning a confidence of
`7` on a 0-1 scale. The default is now the model that was actually measured on
this eval: 12/12 judged, zero false blocks, 32s for the full run against 215s.

**What this cost, and why it is entry 16.** Nothing in production, because none
of this shipped. What it nearly cost is the thing the whole project argues
against. Parchi's case is that an agent should not be trusted on the strength of
looking correct, and the adjudicator was trusted on exactly that: it looked
right, it was plausible in review, and it convicted a real customer five times
out of six. The rule the rest of the repo already follows is that a component
allowed to refuse a human has to be measured against cases it was not written
for. The AI component was the one place that rule had not been applied.

---

### 17. The evidence published beside the numbers was from a different run

**Where.** The README's full-model table, and the two files it links as proof
of that table.

**What was wrong.** `eval/results_model_full.json` held the metrics.
`eval/ledger_model_full.jsonl` was linked beside it as the hash-chained record
those metrics came from. They were produced by different runs, hours apart. The
ledger's last record was written 5,550 seconds before the results run started,
and the results file's own `ledger` block named `eval/ledger.jsonl`, a third
file, which by then had been overwritten by a later heuristic run.

Every individual artefact was internally valid. The chain verified. The metrics
were real. The claim that one was evidence for the other was not.

**Why.** A full batch always wrote `eval/ledger.jsonl`, whatever provider it
ran. Publishing a model run therefore meant remembering to copy the ledger out
before the next run clobbered it. Nobody remembered, and nothing checked,
because the file that would have caught it is the file that was wrong.

**Found by** deriving the timestamps rather than reading the filenames. The
ledger records carry `ts`, the results file carries `generated_at` and the run
duration; three numbers that should nest and did not.

**Changed.** `eval/evaluate.py` takes `--ledger`, so a run whose numbers get
published names its own ledger and writes it in the same pass. The results file
records that path, the resolved model and the timeout. Correctness now comes
from how the run is invoked rather than from someone remembering a `cp`
afterwards.

Then the run was done again, once, on a quiet tree, and the README was
rewritten around what it actually said. The new numbers are not uniformly
better: recall went from 97.1% to 99.3%, false blocks went from 12 to 22, and
22 rows degraded where the previous run had none. The two runs are the same
code and the same 1,000 rows on two different days, which makes the endpoint's
latency the only variable, and makes the spread itself a finding worth
publishing: a 4s budget in front of a payment is a safety parameter, and the
cost of missing it is customers routed to a human rather than violations let
through. All 22 degraded rows became `STEP_UP`. None became `ALLOW`.

**Cost.** Two hours of wall clock across the two runs. What it nearly cost is
the same thing entry 16 nearly cost: this project's argument is that evidence
should be checkable, and the headline table was linking evidence that did not
belong to it. A reviewer who checked the timestamps would have found that
before I did.

---

### 18. The ten-minute block did not apply to the attack it was written for

**Where.** The escalation gate in `demo/server.py`, and the adjudicator prompt.

**Found by** the operator reading the feature back to me: a coupon claimed at a
higher value than it is worth should cool the account, and it did not.

**What was wrong.** The gate escalated exactly one shape, the agent swarm.
`discount_drift` and `coupon_farming` raised alerts and stopped there, so an
attacker could keep inflating the claimed value of a code, be refused every
time, and never be blocked. Refusing an attempt and stopping an attacker are
not the same thing, and the whole point of the cooldown is the difference.

The prompt made it worse than an omission. It already said, in as many words,
*"a code claimed at DIFFERENT values across attempts has no innocent version:
it is enumeration of the coupon rail."* The system was telling the model how to
judge a case it was never going to be shown.

**Also found on the way in:** `rebuilt_attempt` was named in the gate, in the
docs and in the eval, and no detector has ever emitted it. The condition
`any(p.kind == "rebuilt_attempt" for p in patterns)` could not be true. So the
"two shapes are never accidents" the module documented were really one shape
and a branch that never ran.

**The first fix, and why it was wrong.** I routed both coupon shapes to the
adjudicator and taught the prompt to weigh them: count payers, read whether the
code is publicly advertised, convict on more than one claimed value. Then I
measured it, and it went backwards.

| | attacks caught | customers left alone |
|---|---|---|
| Before | 5/6 | 6/6 |
| With the coupon decision table in the prompt | 4/8 | 6/6 |

It cleared one payer sweeping a code across 28 mandates, on the grounds that
the code was public. It convicted a genuine sale while its own stated reason
read *"is a public sale, not a single customer"*, which is the argument for
clearing. Reordering the rules moved the errors around without removing them.

**Why.** I was handing a model arithmetic, which is entry 10's mistake wearing
different clothes. Counting distinct payers, and looking a code up in the
merchant's own book, are not judgement calls. A model asked to execute a
numbered decision procedure will apply it unevenly, and no wording fixes that
because the wording was never the problem.

**Changed.** `parchi/behavior.py` gained `coupon_verdict`, which settles the
countable part and returns `None` when the numbers genuinely cannot read the
case. More than one claimed value convicts. One payer across many mandates
convicts. Many payers convict or clear depending on `Coupon.public`, a new flag
saying whether the merchant advertised the code, because twenty-six payers on
one code is a Diwali sale or a leak and only the merchant knows which. The
prompt went back, verbatim, to the wording that measured 18/18 and 16/18.

The result is better than the version I set out to build. Coupon abuse now
blocks an account with **no model call, no API key and no network**, which
means it holds in CI and on a fresh clone, and it cannot drift with an
endpoint's mood. `tests/test_coupon_verdict.py` covers it, and the model eval
dropped those cases, because scoring a model on decisions it is never shown is
not a measurement.

**A note on what the eval says now.** With the coupon cases gone it holds eight
situations, and a single run scored 1/3 on the attack half. Repeating the
failures four times each showed why that number is not what it looks like: the
swarm, the only shape the checkpoint actually routes to a model, convicts 4/4;
card testing is flaky at 3/4; the catalogue sweep, which no detector escalates,
now clears every time where it convicted in the morning. Same prompt, same
model name, different day. The full-model run in entry 17 moved for the same
reason. A number from one run of a shared endpoint is an anecdote, and the
honest response is to keep the shape that matters on rules.

---

### 19. The cap was never out of the prompt, and taking it out cost more than leaving it

**Where.** `parchi/intent_match.py`, the one model call.

**Found by** a reviewer asking the obvious question about the published model
run: what is the model's own false-positive rate on the good carts. Nobody had
attributed the errors, so nobody knew. `eval/attribute.py` answers it from the
ledger:

```
  a deterministic rule   235 violations caught,  0 good customers blocked
  the one model call      43 violations caught, 22 good customers blocked
```

The rules are exact. The model, on the 65 carts it refused by itself, was right
66% of the time, and it is the source of every false block in the run.

**What was wrong.** Reading its own words for those 22, eighteen reason about
price. One refused a cart at *"Rs 4,779.04, which is within the price limit"*,
which is not a judgement, it is a sentence that contradicts itself.

Entry 10 removed the cap from the prompt for exactly this. Entry 15 recorded
two further attempts to forbid price reasoning by wording, both measured worse.
All three missed the channel. The playback is the human's own sentence and it
ends *"under Rs 5,000"*. Removing the cap field never removed the cap from the
prompt; it stopped labelling it. Entry 15 had even written down the durable fix
(*"not showing the model a price it has no job to judge"*) and then nobody
implemented it, because the sentence did not look like a price field.

**Changed, then measured, then not shipped.** `redact_amounts` strips money
from the playback the model sees, keeping the words and the quantities, while
the ledger and evidence pack keep what the human approved. Same 1,000 rows,
same model:

| | as written | redacted |
|---|---|---|
| Violations caught | 278/280 | **280/280** |
| Precision | 92.7% | **95.2%** |
| False blocks | 22 | **14** |
| Model's own precision | 66.2% | **76.3%** |
| Price-reasoning false blocks | 18 of 22 | **7 of 14** |
| High-value carts routed to a human | **38/40** | 35/40 |
| **Total cost of the mistakes** | **Rs 1,59,521** | Rs 1,94,247 |

Every count improved. The money got 22% worse.

**Why, and it is not noise.** Redaction trades many cheap false blocks for
fewer expensive ones. The budget in the sentence was doing a second job nobody
had noticed: telling the model that an expensive cart was *expected*. Take it
away and a Rs 35,000 laptop stops reading as a purchase the human planned and
starts reading as implausible. The failure mode did not disappear, it moved,
which is the same thing entry 15 observed about adding rules to a prompt. The
new wording is *"priced at Rs 4,101.74, which is not a standard quantity for
coffee beans"*: no longer re-deciding a budget, now inventing a market price.

**So it ships off**, behind `PARCHI_REDACT_PLAYBACK=1`. This repo scores in
rupees. Turning it on because the percentages improved would be precisely the
bias the rupee framing exists to prevent, and the number that counts what a
merchant loses said no.

**What would settle it.** The trade is between two signals the same sentence
carries: the amount, which the model misuses, and the expectation of spending,
which it needs. Redaction removes both. A version that removes the number while
keeping the expectation is the obvious next experiment, and entry 15 is the
reason to be pessimistic about it: two attempts to say *more* about price both
measured worse. That would be a third.

**The honest reading of this entry.** The finding is not the redaction. It is
that a metric everybody agrees on can be improved on every axis you happen to
be looking at, and still lose. Nine of the ten numbers in that table say ship
it. The tenth is the one a merchant would care about.

---

### Resolved (was "Still unsolved")

The headline number is now a model number. The full 1,000-row run against
`z-ai/glm-4.7-flash` is published in the README's results table and in
[`eval/results_model_full.json`](eval/results_model_full.json), next to the
heuristic table it replaced as the claim of record. The heuristic row stays because
it is the number a no-key reproduction gets, and the gap between the two rows
is now itself a documented finding rather than a caveat.

The quantity-inflation blind spot and the missing agent-identity check are now
fixed and defended by tests; see entries 12 and 13.

---

### 20. The guard meant to stop duplicate alerts was eating the cooldown

**Where.** `check_patterns` in `parchi/behavior.py`.

**Found by** the operator re-reading the feature the day after it shipped: a
coupon hammered at inflating values should still cool the account, and the
wording of the suppression guard said it might not.

**What was wrong.** The guard dropped a `discount_drift` alert whenever it
arrived in the same breath as any other per-code alert, hot or farming. The
reasoning was right for farming: the farm shape already routes to the
escalation gate, so the drift is an echo. It was wrong for hot. Hot is volume,
drift is a code paying two different sums, and drift is the only one of the
two the escalation gate reads. Suppress it and no shape reaches the gate, no
`coupon_verdict` runs, and no cooldown lands.

The failure needs one specific sequence, and it is the sequence an attacker
would actually produce: four attempts at the code's true value, then the
inflated claim on the fifth - the one attempt where the hot threshold and the
first drift fire together. An attacker warms the rail before inflating it,
because inflating immediately is caught by the per-cart discount check anyway.
The watcher marks the drift raised on that attempt regardless of suppression,
so no later attempt can raise it either. One missed breath, and the block
never lands.

That sequence is also why the feature's own end-to-end test could not see it.
The demo scenario fires the inflated claim as the second event, so the drift
arrives alone and escalates fine. The hole only exists at the threshold.

**Changed.** The guard now suppresses the drift echo only beside
`coupon_farming`. `test_a_hot_code_that_also_drifts_still_names_the_drift`
pins the unit shape, and
`test_a_hot_code_claimed_at_two_values_still_cools_the_account` walks the
threshold-crossing sequence through the live endpoint with the adjudicator
stubbed to explode, asserting the account cools anyway.

| Sequence | Before | After |
|---|---|---|
| Inflate on attempt 2 (the demo scenario) | blocked | blocked |
| Inflate on attempt 5, the hot crossing | alerted, never blocked | blocked |

The second row is the whole finding: the feature was verified against its
friendly path and broken on its adversarial one, which is the shape of every
entry in this file.

---

### 21. The lamp for the defence AI reported "working" through a dead key

**Where.** `defence_status` in `demo/server.py`, reading
`openai_provider.budget()`.

**Found by** the operator asking a question the console could not answer: the
API key may have spent its token allowance, so is the autonomous defence
actually running? The honest answer was that nothing in the system knew.

**What was wrong.** The lamp had three states, and all three described how the
server was *configured* rather than whether the endpoint was *answering*. Green
meant the AI gate was on and the call budget was not nearly spent. Amber meant
the budget was nearly spent. Red meant an operator had switched the gate off.

A budget counts calls a process is allowed to make. It says nothing about
whether any of them worked, and the two fail in opposite directions. A refused
call still spends a budget slot: `budget().spend()` runs before the request, so
an expired key or an exhausted subscription burns the allowance on every
attempt and returns nothing. The lamp therefore read green at zero successful
calls, and kept reading green while the counter climbed, until enough refusals
pushed it to amber - which says "token usage very high", the one message that
would send an operator to buy more of something that was already the problem.

Underneath the lamp, everything degraded quietly and correctly. `assess_attack`
returns `None` when the endpoint is unavailable, `_adjudicate` returns without
convicting anyone, and the deterministic detectors carry on alerting. That is
the right behaviour and it is why the failure is invisible: the console looks
entirely normal, alerts keep arriving, and no adjudication is happening at all.
Fail-open is only safe if somebody is told.

Measured with a deliberately dead key against the live endpoint:

| | Before | After |
|---|---|---|
| Lamp after 5 refused adjudications | green, "working" | failing, "NOT ANSWERING" |
| Cause shown to the operator | none | `HTTP 401` |
| Successful calls behind a green lamp | 0 | n/a |

**Changed.** `CallHealth` sits beside `CallBudget` in `parchi/openai_provider.py`
and is recorded at the two places a call can end: the success path, and both
raise paths. The lamp gained a fourth state, `failing`, checked *before* budget
because an endpoint that is not answering is a worse fact than one answering a
lot, and it is the one an operator can act on. The threshold is four
consecutive failures, so a single timeout stays weather; one success clears it,
so a recovered key needs no restart. The console renders `failing` as its own
blinking state rather than a synonym for red, because red is a switch somebody
threw on purpose and this is not.

`tests/test_defence_health.py` pins all of it, including the two orderings that
matter: `failing` beats `amber` (a spent subscription is both), and an operator
switching the gate off still beats everything (that is not an outage).

The generalisable point is the one this file keeps making in different clothes:
a health indicator that reads configuration instead of outcomes is worse than
no indicator, because it converts an outage into a green light.

---

### 22. Moving to a new provider: three failures, none of them the HTTP shape

**Where.** `parchi/openai_provider.py` and `parchi/ai_guard.py`, on a move from
one OpenAI-compatible endpoint to another.

**Found by** switching the repo to a new endpoint and measuring before
believing anything. The portability claim held exactly as designed - the base
URL changed and the request shape did not - and then three things broke that
the shape had nothing to do with.

**What was wrong.**

*One: the token ceiling stopped meaning what it said.* Every model on the new
endpoint is a reasoning model, and reasoning tokens are spent from `max_tokens`
before a single character of content is emitted. The adjudicator asked for 300,
which had been generous for a three-field verdict; the model thought for 52
tokens and returned an **empty message** with a 200 status. Not a truncated
answer, not an error - a success carrying nothing. The provider already refused
to treat an empty message as a verdict (that guard was written for a different
endpoint doing the same thing), so this degraded correctly and silently.

*Two: the same models were too slow to use, until asked not to think.* A
one-sentence probe took **58-83 seconds**. Sending `reasoning_effort: "none"`
brought the same prompt to **6-9 seconds**. The endpoint still returns
reasoning text, so the parameter is a request rather than a guarantee, but the
difference is a checkpoint versus a timeout.

*Three: the retry chain was built for a different bill.* The adjudicator asked
the pinned model twice and only then changed model - three calls to cover one
failure. The new key is rated at **100 requests per five hours**, so a single
swarm could spend three percent of the window re-asking a model that was down.
A retry against the same name covers a dropped socket and nothing else.

**Measured, on the same prompt, before choosing anything:**

| Model | Latency | Used for |
|---|---|---|
| `glm-5.3-flash:dev` | 6-18s | both paths, first choice |
| `deepseek-v4-flash:dev` | 37-60s raw, 3-18s on the real intent prompt | second opinion only |

**Changed.** `reasoning_effort: "none"` on every request; the adjudicator's
ceiling raised 300 → 900; the retry chain cut from three calls to two and the
second aimed at a different model family, so one re-ask covers endpoint noise
and a dead model at once; the hardcoded fallback model name - which pointed at
a model this endpoint does not offer, and would have burned a call on a
guaranteed failure - replaced by a named constant. `tests/test_provider_contract.py`
asserts on the request body itself, because none of this is visible to a test
that mocks the provider.

**What it cost in the published numbers.** The intent check's p95 went from
6.1s to **10.9s**, and calls over the 4-second budget from 10% to 25%. That is
worse, it is in the README, and the way it was nearly not is the part worth
recording: the first eight calls measured 3.5s at p95 with nothing over budget.
Eight samples was a number this repo would have been happy to publish. The next
four moved p95 by seven seconds. The p95 of a twelve-call run is its
second-slowest call, and a sample small enough to be lucky is small enough to
be wrong.
