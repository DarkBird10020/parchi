# What broke, and what it actually was

Kept as it happened, one entry per thing that broke. This is the file the
"build challenges" answer gets written from, so it is not tidied up afterwards.

Shape: what broke → what I first assumed → what it actually was → what I changed
→ what it cost.

---

### 1. The intent check silently agreed with the rules, and I almost shipped that as a result

**Broke.** The first full scoreboard run had `rules_only` and `parchi` producing
identical numbers — 90.0% recall, same rupee costs, to the decimal. The one model
call was adding literally nothing.

**Assumed.** The engine was never reaching the intent check: the deterministic
checks short-circuit, so I thought the in-category injection rows were being
blocked earlier by the category check and the model was simply never asked.

**Actually.** The engine was calling it. The matcher was wrong. It built the set of
things the human "wanted" as *playback words ∪ allowed categories*, and the set of
things a cart line "is" as *description words ∪ its own category*. Every in-category
line therefore matched on the category token alone — which is exactly the case the
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

**Assumed.** An ordering bug in `run_all` — some other check consuming the row
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
label said `ALLOW`. Both were defensible, which is the actual problem — the case
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
85.5% and it destroyed **Rs 6,22,472** of legitimate revenue across 40 carts —
worse, on that metric, than the rules-only baseline it was supposed to improve on.

**Assumed.** That this was the honest cost of failing closed and belonged in the
video as-is.

**Actually.** I had implemented "fail closed" as `BLOCK`. But the degraded path has
no finding — the intent check did not conclude the cart was wrong, it concluded
nothing at all. Refusing a purchase on the strength of "I could not check" throws
away a customer to avoid a risk that was never established. "Never auto-approve" is
the property that matters; "refuse" is a different and more expensive property.

**Changed.** A degraded intent check on an expensive cart now returns `STEP_UP`,
not `BLOCK` — the human decides. A degraded check on a cheap cart still allows on
rules alone. (`parchi/engine.py`, and two tests that pin both halves.)

**Cost.** ~20 minutes, and it turned the worst number in the project into the best
demo beat: kill the model and Parchi degrades exactly to the rules-only baseline,
with zero rupees of false-positive cost and nothing auto-approved.

---

### 5. The tamper-evident log could be made unverifiable by deleting it

**Broke.** Found while filming-testing the demo page: I wiped `demo/ledger.jsonl`
from a shell while the server was running, ran one scenario, and the new first
record linked to `prev = f0defdb1…` — the hash of a record that no longer existed
anywhere. `verify_chain` would call that a broken chain forever after.

**Assumed.** Nothing; I noticed the hash in the UI did not start at zeros and went
looking.

**Actually.** `Ledger` reads the last hash once, at construction, and then keeps it
in memory. That is correct for an append-only file and wrong for a file that can
disappear underneath it — rotation, a wipe, a demo reset that recreates the object
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
same 1,000 rows", and the CI job runs `git diff --exit-code -- data/` to enforce it —
that job would have gone red on its very first run, on a fresh clone, with no code
change at all.

**Assumed.** Briefly, that I had changed the generator. I had not: three consecutive
runs of untouched code produced three different files.

**Actually.** `new_mandate()` mints `mandate_id` and `nonce` from `uuid.uuid4()`,
which knows nothing about the generator's seed. Every other field came from the
seeded `random.Random`, so the *distribution* was identical run to run — same case
mix, same recall, same precision — while every row's identity, and therefore every
signature, was different. That is the nastiest shape a reproducibility bug can take:
all the summary numbers agree, and only a byte comparison disagrees.

**Changed.** `new_mandate` now accepts optional `mandate_id` and `nonce`, and the
generator passes seeded values. The uuid4 default stays, with a comment saying why:
a predictable nonce is a replay vulnerability, so injectability exists for the
fixed-seed generator and nothing else. Added
`test_the_same_seed_produces_byte_identical_rows`, which also asserts a *different*
seed still differs — otherwise the "fix" could be no randomness at all.
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
back as a **list** — different Python object, identical canonical bytes only if you
normalise on the way in. `IntentMandate.from_dict` does that normalisation, and
`test_canonical_bytes_are_stable_across_a_json_round_trip` is the test that would
have caught it. Written before it could bite, because it is the single most
predictable way this project dies.

---

### Still unsolved

**The headline number in the scoreboard is not a model number.**

With no `ANTHROPIC_API_KEY` the intent check falls back to an offline lexical
matcher, and the batch it is scored on has product descriptions generated from the
same templates as the mandate playback. So the matcher sees the human's own words
echoed back at it and scores 100% — which says almost nothing about how a real
model behaves on real merchant titles like *"ASICS GEL-Venture 9 (2E Wide) Men's
Trail Runner — Piedmont Grey"*, where the human said "trail sneakers" and no word
overlaps at all.

I know the direction the number moves (false positives up, from the legitimate rows
whose titles do not echo the playback) but not the size, and I have not measured it.
Everything the repo prints is labelled with the provider that produced it —
`heuristic` or `api` — so nothing here claims to be an LLM result that is not one.
Running the batch against `--provider api` and reporting *that* table, with the
false-positive cost in rupees, is the next thing this project needs and the reason
the provider is a switch rather than an assumption.
