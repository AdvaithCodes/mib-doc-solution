"""Oracle experiment: how much classification do the gating fields actually buy?

risk_flags and fee_status gate the two largest review buckets. If our
classification gap is downstream of missing those two fields, then handing the
pipeline their true values should close most of it. If it does not, the gap is
somewhere else entirely and further extraction work on them is wasted.

Each oracle is applied on its own and then together, so the leverage of each is
visible separately.
"""
import csv, pathlib, sys, collections

sys.path.insert(0, str(pathlib.Path.home() / "dev/mib-doc-solution"))

from replay import load, classify_outcome
from mib_pipeline.adjudicate import (adjudicate_detail, flags_from_text,
                                     read_adjudicator_note, reference_receipt_date,
                                     registry_embargo, _parse_date)
from mib_pipeline.extract import resolve
from mib_pipeline.fee import infer_fee_status
from mib_pipeline.pipeline import SCORED_FIELDS

POINTS = {"correct": 8, "to_review": 2, "missed_review": 1,
          "wrong": 0, "false_approval": -4}
CACHE = pathlib.Path.home() / "dev/mib-artifacts/train_cache_2engine.jsonl"
LABELS = pathlib.Path.home() / "dev/mib-doc-challenge/data/train_labels.csv"


def run(packets, truth, reference, oracle_risk=False, oracle_fee=False):
    raw = 0
    routes = collections.Counter()
    for pk in packets:
        t = truth.get(pk.case_id)
        if not t:
            continue
        resolved = resolve(pk)
        record = {f: (resolved[f].value if f in resolved else "") for f in SCORED_FIELDS}
        risk_known = bool(record["risk_flags"])
        if not record["risk_flags"]:
            record["risk_flags"] = "none"
        record["fee_status"], fee_known, fee_contested = infer_fee_status(
            pk, literal=record["fee_status"])
        _, note = read_adjudicator_note(pk)
        mined = set(flags_from_text(note)) if note else set()
        if registry_embargo(pk):
            mined.add("planetary_embargo")
            risk_known = True
        if mined:
            have = {f for f in record["risk_flags"].split("|") if f and f != "none"}
            record["risk_flags"] = "|".join(sorted(have | mined))

        if oracle_risk:
            record["risk_flags"] = t["risk_flags"]
            risk_known = True
        if oracle_fee:
            record["fee_status"] = t["fee_status"]
            fee_known, fee_contested = True, False

        decision, reason, *_ = adjudicate_detail(
            record, pk, risk_known=risk_known, fee_known=fee_known,
            fee_contested=fee_contested, reference_date=reference)
        outcome = classify_outcome(t["adjudication"], decision)
        raw += POINTS[outcome]
        if outcome != "correct":
            routes[reason.split(":")[0]] += 1
    return 80 * raw / (8 * len(packets)), routes


def main():
    truth = {r["case_id"]: r for r in csv.DictReader(open(LABELS))}
    packets = [p for p in load(str(CACHE)) if p.case_id in truth]
    arrivals = []
    for pk in packets:
        rr = resolve(pk)
        if "arrival_date" in rr:
            d = _parse_date(rr["arrival_date"].value)
            if d:
                arrivals.append(d)
    ref = reference_receipt_date(arrivals)

    base, base_routes = run(packets, truth, ref)
    risk, _ = run(packets, truth, ref, oracle_risk=True)
    fee, _ = run(packets, truth, ref, oracle_fee=True)
    both, both_routes = run(packets, truth, ref, oracle_risk=True, oracle_fee=True)

    print(f"classification, {len(packets)} training cases\n")
    print(f"  as shipped                        {base:6.2f} / 80")
    print(f"  + true risk_flags                 {risk:6.2f}   ({risk-base:+.2f})")
    print(f"  + true fee_status                 {fee:6.2f}   ({fee-base:+.2f})")
    print(f"  + both                            {both:6.2f}   ({both-base:+.2f})")
    print(f"\n  strobl (published)                 68.52")
    print(f"\nremaining wrong decisions with both oracles, by route:")
    for k, n in both_routes.most_common(8):
        print(f"  {n:4d}  {k}")


if __name__ == "__main__":
    main()
