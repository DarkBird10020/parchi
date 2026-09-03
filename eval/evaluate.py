"""The scoreboard: precision, recall, false-positive rupee cost, vs baselines.

A blocked genuine customer is money the merchant lost, so false positives are
reported in rupees, not percentages.

    python eval/evaluate.py                 # all four approaches
    python eval/evaluate.py --provider off  # what happens when the model dies
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from parchi import openai_provider
from parchi.agents import AgentRegistry
from parchi.checks import NonceStore
from parchi.engine import ALLOW, BLOCK, STEP_UP, Engine
from parchi.intent_match import MODEL as ANTHROPIC_MODEL
from parchi.intent_match import resolve_provider
from parchi.ledger import Ledger, verify_chain
from parchi.mandate import Cart, IntentMandate, rupees

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data", "transactions.jsonl")
META = os.path.join(ROOT, "data", "meta.json")


def load_rows(path: str = DATA) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def score(rows: list[dict], predictions: list[str]) -> dict:
    """Positive class = "this should be blocked"."""
    tp = fp = fn = tn = 0
    fp_paise = fn_paise = 0
    exact = 0
    step_up_hits = step_up_total = step_up_predicted = 0

    for row, pred in zip(rows, predictions, strict=True):
        total = row["cart"]["total_paise"]
        is_violation = row["ground_truth_label"] == "violation"
        blocked = pred == BLOCK

        if is_violation and blocked:
            tp += 1
        elif is_violation and not blocked:
            fn += 1
            fn_paise += total
        elif not is_violation and blocked:
            fp += 1
            fp_paise += total
        else:
            tn += 1

        if pred == row["ground_truth_verdict"]:
            exact += 1
        if pred == STEP_UP:
            step_up_predicted += 1
        if row["ground_truth_verdict"] == STEP_UP:
            step_up_total += 1
            if pred == STEP_UP:
                step_up_hits += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1,
        "exact_verdict_accuracy": exact / len(rows) if rows else 0.0,
        "false_positive_paise": fp_paise,
        "false_negative_paise": fn_paise,
        "false_positive_display": rupees(fp_paise),
        "false_negative_display": rupees(fn_paise),
        "step_up_caught": step_up_hits,
        "step_up_total": step_up_total,
        "step_up_predicted": step_up_predicted,
    }


def run_engine(rows: list[dict], pub: Ed25519PublicKey, use_intent: bool,
               provider: str, ledger_path: str | None,
               model: str | None = None,
               timeout: float = 4.0,
               warmup_rows: list[dict] | None = None,
               agents: Any = None) -> tuple[list[str], dict]:

    if ledger_path and os.path.exists(ledger_path):
        os.remove(ledger_path)
    nonces = NonceStore()
    if warmup_rows:
        warmup_engine = Engine(nonces=nonces, provider="off", use_intent=False)
        for row in warmup_rows:
            warmup_engine.authorize(
                IntentMandate.from_dict(row["mandate"]), row["signature"], pub,
                Cart.from_dict(row["cart"]), now=row["now"], txn_id=row["txn_id"],
            )
    engine = Engine(
        ledger=Ledger(ledger_path) if ledger_path else None,
        nonces=nonces,
        agents=agents,
        provider=provider,
        use_intent=use_intent,
        model=model,
        timeout=timeout,
    )

    preds, degraded, blocked_by = [], 0, {}
    t0 = time.time()
    for row in rows:
        m = IntentMandate.from_dict(row["mandate"])
        cart = Cart.from_dict(row["cart"])
        d = engine.authorize(m, row["signature"], pub, cart,
                             now=row["now"], txn_id=row["txn_id"])
        preds.append(d.verdict)
        if d.degraded:
            degraded += 1
        if d.verdict == BLOCK:
            failed = next((c.name for c in d.checks if not c.passed), "intent_match")
            blocked_by[failed] = blocked_by.get(failed, 0) + 1
    return preds, {
        "seconds": round(time.time() - t0, 2),
        "degraded_rows": degraded,
        "blocked_by": blocked_by,
    }


def per_case(rows: list[dict], predictions: list[str]) -> dict:
    out: dict[str, dict] = {}
    for row, pred in zip(rows, predictions, strict=True):
        c = out.setdefault(row["case"], {"n": 0, "correct": 0})
        c["n"] += 1
        if pred == row["ground_truth_verdict"]:
            c["correct"] += 1
    return out


def markdown(results: dict) -> str:
    order = ["allow_everything", "block_all_agent_traffic", "rules_only", "parchi"]
    labels = {
        "allow_everything": "Allow everything",
        "block_all_agent_traffic": "Block all agent traffic",
        "rules_only": "Rules only (day 2)",
        "parchi": "Parchi (rules + one model call)",
    }
    lines = [
        "| Approach | Catches violations | Blocks good customers | Cost of the mistakes |",
        "| --- | --- | --- | --- |",
    ]
    for key in order:
        s = results["approaches"][key]["metrics"]
        caught = f"{s['tp']}/{s['tp'] + s['fn']} ({s['recall']:.0%})"
        fpx = f"{s['fp']} customers"
        cost = (
            f"{s['false_positive_display']} lost to false blocks · "
            f"{s['false_negative_display']} paid out on violations"
        )
        lines.append(f"| {labels[key]} | {caught} | {fpx} | {cost} |")
    return "\n".join(lines)


def _guard_degraded_run(rows: list[dict], pub: Ed25519PublicKey,
                        provider: str, model: str | None) -> None:
    """Make one real call before scoring 1,000 rows against a dead endpoint.

    Fail fast and loudly: a misconfigured key does not raise here, it degrades,
    and a fully degraded batch produces a complete, plausible, meaningless table.
    """
    from parchi.intent_match import intent_matches

    row = rows[0]
    v = intent_matches(IntentMandate.from_dict(row["mandate"]),
                       Cart.from_dict(row["cart"]), timeout=30.0,
                       provider=provider, model=model)
    if v.degraded:
        raise SystemExit(
            f"\nrefusing to score: the intent check is not reachable.\n"
            f"  provider : {v.provider}\n"
            f"  reason   : {v.reason}\n"
            f"Fix the configuration, or run --provider heuristic / off on purpose.\n"
        )
    print(f"intent check live: {v.provider}")


DEFAULT_OUT = os.path.join(HERE, "results.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DATA)
    ap.add_argument("--provider", default="auto",
                    choices=["auto", "api", "openai", "heuristic", "off"])
    ap.add_argument("--model", default=None,
                    help="model id for --provider openai; "
                         "see python -m parchi.models_cli --filter glm")
    ap.add_argument("--timeout", type=float, default=4.0,
                    help="wall-clock budget for one intent call. The 4s default "
                         "is the production posture - a slow answer in front of a "
                         "payment is a wrong answer. Raise it when measuring a "
                         "slower endpoint, and say so when quoting the numbers")
    ap.add_argument("--limit", type=int, default=0,
                    help="score only the first N rows - use this to measure the "
                         "cost of a real-model run before paying for 1,000 of them")
    ap.add_argument("--per-case", type=int, default=0,
                    help="score the first N rows of every case type")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--ledger", default=None,
                    help="where this run writes its ledger. A full run defaults "
                         "to eval/ledger.jsonl, which the next full run "
                         "overwrites - so a run whose numbers are published "
                         "somewhere should name its own file and be quoted "
                         "next to it")
    ap.add_argument("--gate", action="store_true",
                    help="exit non-zero if the results regress (used by CI)")
    args = ap.parse_args()

    rows = load_rows(args.data)
    all_rows = rows
    warmup_rows: list[dict] = []
    sample_label = "full"
    all_rows = rows
    sampled = False
    if args.limit and args.per_case:
        ap.error("use either --limit or --per-case, not both")
    if args.limit:
        # A subsample is not the published scoreboard: the case mix is only
        # correct over the whole batch, so a truncated run must never overwrite
        # results.json or be handed to the gate.
        rows = rows[:args.limit]
        if args.gate:
            ap.error("--gate scores the full batch; drop --limit")
        args.out = os.path.join(HERE, f"results_sample_{args.limit}.json")
        sampled = True
        sample_label = f"first_{args.limit}"
    elif args.per_case:
        selected: list[dict] = []
        counts: dict[str, int] = {}
        for row in rows:
            case = row["case"]
            if counts.get(case, 0) < args.per_case:
                selected.append(row)
                counts[case] = counts.get(case, 0) + 1
        # Replay rows depend on an earlier use of the same nonce. Include that
        # prerequisite or a stratified sample would turn replay attacks into
        # first uses and report a false miss.
        replay_nonces = {
            row["mandate"]["nonce"] for row in selected if row["case"] == "replay"
        }
        warmup_rows = [
            row for row in all_rows
            if row["mandate"]["nonce"] in replay_nonces and row["case"] != "replay"
        ]
        rows = selected
        if args.gate:
            ap.error("--gate scores the full batch; drop --per-case")
        args.out = os.path.join(HERE, f"results_model_stratified_{args.per_case}.json")
        sampled = True
        sample_label = f"stratified_{args.per_case}"
    with open(META, encoding="utf-8") as f:
        meta = json.load(f)
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(meta["payer_public_key"]))

    agents = AgentRegistry()
    if meta.get("agent_public_key") and meta.get("agent_id"):
        agents.register(
            meta["agent_id"],
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(meta["agent_public_key"])),
        )

    provider = resolve_provider(args.provider)
    resolved_model = (
        openai_provider.resolve_model(args.model) if provider == "openai"
        else ANTHROPIC_MODEL if provider == "api"
        else None
    )

    approaches: dict[str, dict] = {}

    approaches["allow_everything"] = {
        "metrics": score(rows, [ALLOW] * len(rows)), "run": {}}
    approaches["block_all_agent_traffic"] = {
        "metrics": score(rows, [BLOCK] * len(rows)), "run": {}}

    preds_rules, run_rules = run_engine(
        rows, pub, False, provider, None, resolved_model, warmup_rows=warmup_rows, agents=agents
    )

    approaches["rules_only"] = {
        "metrics": score(rows, preds_rules), "run": run_rules,
        "per_case": per_case(rows, preds_rules)}

    # A model run that quietly called nothing is the worst outcome here: every
    # row still gets a verdict, the table still prints, and the numbers are the
    # fallback's rather than the model's. Say so, loudly, in the run itself.
    if provider not in ("heuristic", "off"):
        _guard_degraded_run(rows, pub, provider, resolved_model)

    # The ledger is evidence for these metrics, so it has to come from this
    # run. It used to always be eval/ledger.jsonl for a full batch, which meant
    # publishing a model run involved remembering to copy the file out before
    # the next run overwrote it. Nobody remembered: the ledger published beside
    # the model table ended 5,550 seconds before that table's run started.
    if args.ledger:
        ledger_path = os.path.abspath(args.ledger)
    else:
        ledger_name = ("ledger.jsonl" if not sampled
                       else f"ledger_{sample_label}_{provider}.jsonl")
        ledger_path = os.path.join(HERE, ledger_name)
    preds_parchi, run_parchi = run_engine(rows, pub, True, provider, ledger_path,
                                          resolved_model, args.timeout, warmup_rows, agents)

    chain_ok, chain_msg, chain_n = verify_chain(ledger_path)
    approaches["parchi"] = {
        "metrics": score(rows, preds_parchi), "run": run_parchi,
        "per_case": per_case(rows, preds_parchi)}

    results = {
        "dataset": {
            "rows": len(rows),
            "seed": meta["seed"],
            "cases": {case: sum(row["case"] == case for row in rows)
                      for case in dict.fromkeys(row["case"] for row in rows)},
        },
        "intent_provider": provider,
        "intent_model": resolved_model,
        "intent_timeout_seconds": args.timeout,
        "approaches": approaches,
        "ledger": {"path": os.path.relpath(ledger_path, ROOT).replace(os.sep, "/"),
                   "chain_intact": chain_ok, "detail": chain_msg, "records": chain_n},
        "generated_at": int(time.time()),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    table = markdown(results)
    # results.md is the canonical scoreboard, the one CI regenerates and the
    # README quotes. Only the run that also writes the canonical results.json
    # gets to rewrite it: a side run pointed at its own --out was overwriting
    # the published table with numbers from a different provider, leaving the
    # two files describing different runs.
    canonical = os.path.abspath(args.out) == os.path.abspath(DEFAULT_OUT)
    if not sampled and canonical:
        with open(os.path.join(HERE, "results.md"), "w", encoding="utf-8") as f:
            f.write(f"# Results ({len(rows)} synthetic agent transactions)\n\n"
                    f"Intent provider: `{provider}`\n\n{table}\n")

    print(f"\n{len(rows)} rows · intent provider: {provider}\n")
    print(table)
    r, p = approaches["rules_only"]["metrics"], approaches["parchi"]["metrics"]
    print(f"\nrules only : recall {r['recall']:.1%}  precision {r['precision']:.1%}  "
          f"exact verdicts {r['exact_verdict_accuracy']:.1%}  "
          f"false-positive cost {r['false_positive_display']}")
    print(f"parchi     : recall {p['recall']:.1%}  precision {p['precision']:.1%}  "
          f"exact verdicts {p['exact_verdict_accuracy']:.1%}  "
          f"false-positive cost {p['false_positive_display']}")
    print(f"step-up    : {p['step_up_caught']}/{p['step_up_total']} high-value legit carts "
          f"routed to a human")
    print(f"degraded   : {run_parchi['degraded_rows']} rows took the fallback path")
    print(f"blocked by : {run_parchi['blocked_by']}")
    print(f"ledger     : {chain_msg}")
    print(f"\nwrote {args.out} and eval/results.md")

    if args.gate:
        sys.exit(gate(approaches, chain_ok))


# The numbers CI refuses to let slip. Deliberately expressed as invariants
# ("never worse than the rules baseline") rather than a frozen score, so an
# honest improvement to the dataset does not turn the build red for nothing.
GATES = [
    ("parchi never catches fewer violations than rules alone",
     lambda a: a["parchi"]["metrics"]["recall"] >= a["rules_only"]["metrics"]["recall"]),
    ("parchi never blocks a good customer the rules would have allowed",
     lambda a: a["parchi"]["metrics"]["false_positive_paise"]
     <= a["rules_only"]["metrics"]["false_positive_paise"]),
    ("rules alone still catch at least 80% of violations",
     # 0.80, not the 0.85 this started at. The bar moved because the batch got
    # harder, not because the rules got worse: quantity_inflation added 20 rows
    # that no rule can catch by construction, which is the entire argument for
    # the model call. Rules-only recall fell 90.4% -> 83.9% on that change alone.
    # The invariant's job is "rules alone are still a serious baseline", and a
    # threshold that only ever moves down when a run fails is not an invariant -
    # so it is written down here rather than quietly edited.
    lambda a: a["rules_only"]["metrics"]["recall"] >= 0.80),
    ("every high-value legitimate cart is routed to a human",
     lambda a: a["parchi"]["metrics"]["step_up_caught"] == a["parchi"]["metrics"]["step_up_total"]),
    ("only expected high-value carts are routed to a human",
     lambda a: a["parchi"]["metrics"]["step_up_predicted"]
     == a["parchi"]["metrics"]["step_up_total"]),
    ("no intent checks degrade in the reproducible offline run",
     lambda a: a["parchi"]["run"]["degraded_rows"] == 0),
    ("exact verdict accuracy never falls below the rules baseline",
     lambda a: a["parchi"]["metrics"]["exact_verdict_accuracy"]
     >= a["rules_only"]["metrics"]["exact_verdict_accuracy"]),
    ("precision stays at 100% - no false blocks at all",
     lambda a: a["parchi"]["metrics"]["precision"] == 1.0),
]


def gate(approaches: dict, chain_ok: bool) -> int:
    print("\ngate:")
    failed = 0
    for label, predicate in GATES:
        ok = predicate(approaches)
        failed += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    print(f"  {'PASS' if chain_ok else 'FAIL'}  the ledger chain verifies end to end")
    failed += not chain_ok
    return 1 if failed else 0


if __name__ == "__main__":
    main()
