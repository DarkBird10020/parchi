## What changed

<!-- One or two sentences. -->

## Why

<!-- What it fixes, or which attack pattern / case type motivated it. -->

## Checkpoint safety

Every PR touching `parchi/` must answer these, because a wrong verdict here is
either a fraudulent purchase or a refused customer.

- [ ] `python tests/test_attacks.py` — all patterns still handled as specified
- [ ] `python eval/evaluate.py --gate` — no regression against the rules baseline
- [ ] If a new bypass was found, it is added to `tests/test_attacks.py` **before** the fix
- [ ] Nothing in this PR can auto-approve a payment on a degraded or error path
- [ ] Money stays in integer paise; no float arithmetic on amounts
- [ ] No secrets, keys or `.env` in the diff (check history, not just the tree)

## Notes for the reviewer

<!-- Anything CodeRabbit or a human should look at first. -->
