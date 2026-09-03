"""Which half of the checkpoint produced which number.

"Rules plus one model call" is only worth saying if the model half is carrying
weight, and a headline figure for the whole system hides that. This walks a
published ledger and attributes every refusal to the thing that actually
refused it: a named deterministic check, or the single intent call that runs
only after every rule has passed.

A block with a failed check in its record was settled by that rule. A block
with no failed check is the model's, because the engine short-circuits and only
reaches the intent call when the rules are all satisfied.

    python eval/attribute.py
    python eval/attribute.py --ledger eval/ledger_model_redacted.jsonl

Needs no key and no network. It reads files that are already in the repo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The two case types in the dataset that are legitimate purchases. Everything
# else is a labelled violation.
GOOD_CASES = {"in_scope", "high_value_legit"}


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data", "transactions.jsonl"))
    ap.add_argument("--ledger", default=os.path.join(HERE, "ledger_model_full.jsonl"))
    args = ap.parse_args()

    rows = load(args.data)
    recs = load(args.ledger)
    if len(rows) != len(recs):
        print(f"note: scoring the first {min(len(rows), len(recs))} rows; "
              f"the dataset has {len(rows)} and this ledger has {len(recs)}")

    rule_caught = rule_blocked_good = 0
    model_caught = model_blocked_good = 0
    tp = fp = fn = tn = 0
    reasons: list[tuple[str, str]] = []

    # Deliberately not strict: a run still in progress has fewer
    # ledger records than the dataset has rows, and reading it early
    # is exactly what this is for.
    for row, rec in zip(rows, recs, strict=False):
        good = row["case"] in GOOD_CASES
        refused = rec["verdict"] == "BLOCK"
        if refused and not good:
            tp += 1
        elif refused and good:
            fp += 1
        elif not refused and not good:
            fn += 1
        else:
            tn += 1

        if not refused:
            continue
        settled_by_rule = any(not c.get("passed") for c in (rec.get("checks") or []))
        if settled_by_rule:
            rule_blocked_good += good
            rule_caught += not good
        else:
            model_blocked_good += good
            model_caught += not good
            if good:
                reasons.append((row["mandate"]["prompt_playback"],
                                (rec.get("intent") or {}).get("reason", "")))

    n = tp + fp + fn + tn
    print(f"\nledger: {os.path.relpath(args.ledger, ROOT)}   rows scored: {n}\n")
    print("confusion matrix")
    print("                       predicted refuse   predicted allow")
    print(f"  actually a violation      {tp:5d}              {fn:5d}")
    print(f"  actually fine             {fp:5d}              {tn:5d}")
    if tp + fp:
        print(f"\n  precision {tp / (tp + fp):.1%}   recall {tp / (tp + fn):.1%}"
              f"   false-positive rate on good carts {fp / (fp + tn):.2%}")

    print("\nwho settled it")
    print(f"{'':22s} {'violations caught':>18s} {'good customers blocked':>24s}  precision")
    for name, caught, blocked in (("a deterministic rule", rule_caught, rule_blocked_good),
                                  ("the one model call", model_caught, model_blocked_good)):
        total = caught + blocked
        prec = f"{caught / total:.1%}" if total else "n/a"
        print(f"  {name:20s} {caught:18d} {blocked:24d}  {prec}")

    if reasons:
        priced = sum(1 for _, why in reasons
                     if any(w in why.lower() for w in
                            ("price", "rs ", "budget", "limit", "exceed",
                             "cost", "spend", "amount")))
        print(f"\nof the model's {len(reasons)} false blocks, {priced} reason about price")
        for playback, why in reasons[:5]:
            print(f'  playback "{playback}"')
            print(f"    -> {why[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
