#!/usr/bin/env python3
"""Fit the score-optimal resolver table.

    ./fit_resolver.py <cache.jsonl> <labels.csv> [--fit-cases 300]

Fitted on the first N training cases only, so the remainder stays a clean
holdout. Note-settled packets are excluded: they are already answered and never
reach the resolver at runtime. The table is committed to the repository; nothing
is learned at scoring time.
"""
from __future__ import annotations

import argparse
import collections
import csv
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from mib_pipeline.adjudicate import reference_receipt_date, _parse_date
from mib_pipeline.extract import resolve as resolve_fields
from mib_pipeline.resolver import AUTHORITATIVE_ROUTES, Resolver, evidence_keys
from replay import load, rule_state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cache")
    ap.add_argument("labels")
    ap.add_argument("--fit-cases", type=int, default=300)
    args = ap.parse_args()

    truth = {r["case_id"]: r for r in csv.DictReader(open(args.labels))}
    packets = list(load(args.cache))

    arrivals = []
    for pk in packets:
        rr = resolve_fields(pk)
        if "arrival_date" in rr:
            d = _parse_date(rr["arrival_date"].value)
            if d:
                arrivals.append(d)
    reference = reference_receipt_date(arrivals)

    table = collections.defaultdict(collections.Counter)
    fitted = skipped = 0
    for index, packet in enumerate(packets):
        if index >= args.fit_cases:
            break
        t = truth.get(packet.case_id)
        if not t:
            continue
        record, _decision, reason, _denials, reviews, approvals = rule_state(packet, reference)
        if reason.split(":")[0] in AUTHORITATIVE_ROUTES:
            skipped += 1
            continue
        for key in evidence_keys(reason, record, reviews, approvals):
            table[key][t["adjudication"]] += 1
        fitted += 1

    resolver = Resolver(table={k: dict(v) for k, v in table.items()})
    resolver.save()
    usable = sum(1 for v in table.values()
                 if sum(v.values()) >= resolver.min_observations)
    print(f"fitted on {fitted} rule-decided cases "
          f"({skipped} note-settled cases excluded)", file=sys.stderr)
    print(f"{len(table)} buckets, {usable} above the "
          f"{resolver.min_observations}-observation floor", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
