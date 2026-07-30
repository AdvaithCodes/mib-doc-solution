#!/usr/bin/env python3
"""Replay cached evidence through extraction + adjudication and score it.

Runs the whole decision stack against a cache built by cache_evidence.py, so
rule and calibration changes can be measured against all 1000 training cases in
seconds instead of 25 minutes.

    ./replay.py /tmp/train_cache.jsonl ~/dev/mib-doc-challenge/data/train_labels.csv
    ./replay.py <cache> <labels> --split 300        # fit/holdout report
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import sys

from mib_pipeline.adjudicate import (adjudicate, adjudicate_detail, flags_from_text,
                                     read_adjudicator_note, reference_receipt_date,
                                     registry_embargo, _parse_date)
from mib_pipeline.evidence import Packet, Page, classify
from mib_pipeline.extract import resolve
from mib_pipeline.fee import infer_fee_status
from mib_pipeline.pipeline import (SCORED_FIELDS, confidence_for,
                                   rule_state as pipeline_rule_state)
from mib_pipeline.schema import FIELD_WEIGHTS, Prediction

CLASSIFICATION_POINTS = {"correct": 8, "to_review": 2, "missed_review": 1,
                         "wrong": 0, "false_approval": -4}


def load(cache_path: str):
    for line in open(cache_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        packet = Packet(case_id=rec["case_id"])
        for p in rec["pages"]:
            # Recompute the document type rather than trusting the cached value:
            # classification is part of what gets tuned, and a stale doc_type
            # would silently hide its effect. Only the expensive OCR is reused.
            packet.pages.append(Page(
                number=p["number"], doc_type=classify(p["lines"]), lines=p["lines"],
                source=p["source"], hidden_text=p.get("hidden_text", ""),
                ocr_lines=p.get("ocr_lines", []),
                second_lines=p.get("second_lines", []),
            ))
        yield packet


def rule_state(packet: Packet, reference_date=None):
    """Delegate to the pipeline so harness and shipped code cannot diverge."""
    return pipeline_rule_state(packet, resolve(packet), reference_date)


_RESOLVER_CACHE: list = []


def _resolver():
    """Load the fitted table once. MIB_NO_RESOLVER=1 disables it for A/B runs."""
    if not _RESOLVER_CACHE:
        import os
        from mib_pipeline.resolver import Resolver
        _RESOLVER_CACHE.append(None if os.environ.get("MIB_NO_RESOLVER")
                               else Resolver.load())
    return _RESOLVER_CACHE[0]


def decide(packet: Packet, reference_date=None):
    record, decision, reason, denials, reviews, approvals = rule_state(
        packet, reference_date)

    resolver = _resolver()
    if resolver is not None:
        from mib_pipeline.resolver import evidence_keys
        chosen = resolver.resolve(decision, reason,
                                  evidence_keys(reason, record, reviews, approvals))
        if chosen is not None:
            return record, chosen[0], reason, chosen[1]
    return record, decision, reason, confidence_for(decision, reason, record)


def classify_outcome(truth: str, pred: str) -> str:
    if truth == pred:
        return "correct"
    if pred == "NEEDS_REVIEW":
        return "to_review"
    if truth == "NEEDS_REVIEW":
        return "missed_review"
    if pred == "APPROVED" and truth == "DENIED":
        return "false_approval"
    return "wrong"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cache")
    ap.add_argument("labels")
    ap.add_argument("--split", type=int, default=0,
                    help="report separately for the first N (fit) and the rest (holdout)")
    ap.add_argument("--routes", action="store_true", help="per-route accuracy table")
    ap.add_argument("--predict", metavar="PATH",
                    help="write predictions.jsonl (labels may be a manifest with no answers)")
    args = ap.parse_args()

    truth = {r["case_id"]: r for r in csv.DictReader(open(args.labels))}
    scored = all("adjudication" in r for r in truth.values())
    groups = {"all": [], "fit": [], "holdout": []}
    predictions = {}
    routes = collections.defaultdict(lambda: [0, 0])

    # Pass 1: the stale-application rule needs a receipt-date reference derived
    # from the whole input set, so arrival dates are collected before deciding.
    packets = list(load(args.cache))
    arrivals = []
    for pk in packets:
        rec = resolve(pk)
        if "arrival_date" in rec:
            d = _parse_date(rec["arrival_date"].value)
            if d:
                arrivals.append(d)
    reference = reference_receipt_date(arrivals)
    print(f"reference receipt date: {reference}", file=sys.stderr)

    for idx, packet in enumerate(packets):
        t = truth.get(packet.case_id)
        if not t:
            continue
        record, decision, reason, conf = decide(packet, reference)

        if args.predict:
            pred = Prediction(case_id=packet.case_id, adjudication=decision,
                              confidence=conf, **record)
            problems = pred.validate()
            if not problems:
                predictions[packet.case_id] = pred
            else:
                print(f"omitting {packet.case_id}: {problems}", file=sys.stderr)

        if not scored:
            continue

        ext_raw = ext_max = 0
        for f, w in FIELD_WEIGHTS.items():
            ext_max += w
            tv, pv = t[f].strip().lower(), str(record.get(f, "")).strip().lower()
            if f == "risk_flags":
                tv = "|".join(sorted(x for x in tv.split("|") if x))
                pv = "|".join(sorted(x for x in pv.split("|") if x))
            if tv == pv:
                ext_raw += w

        outcome = classify_outcome(t["adjudication"], decision)
        cls_raw = CLASSIFICATION_POINTS[outcome]
        brier = (conf - (1.0 if outcome == "correct" else 0.0)) ** 2

        row = (ext_raw, ext_max, cls_raw, brier, outcome)
        groups["all"].append(row)
        groups["fit" if idx < args.split else "holdout"].append(row)

        key = reason.split(":")[0]
        routes[key][1] += 1
        if outcome == "correct":
            routes[key][0] += 1

    def report(name, rows):
        if not rows:
            return
        ext = 50 * sum(r[0] for r in rows) / max(sum(r[1] for r in rows), 1)
        cls = 80 * sum(r[2] for r in rows) / (8 * len(rows))
        cal = 20 * max(0.0, 1 - 2 * (sum(r[3] for r in rows) / len(rows)))
        cat = sum(1 for r in rows if r[4] == "false_approval")
        print(f"{name:<9} n={len(rows):<5} total {ext+cls+cal:7.2f}   "
              f"ext {ext:5.2f}  cls {cls:5.2f}  cal {cal:5.2f}   catastrophic {cat}")

    if args.predict:
        out = pathlib.Path(args.predict)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for case_id in sorted(predictions):
                fh.write(predictions[case_id].finalize().to_json_line() + "\n")
        print(f"wrote {len(predictions)} predictions -> {out}", file=sys.stderr)

    if args.split:
        report("fit", groups["fit"])
        report("holdout", groups["holdout"])
    report("ALL", groups["all"])

    if args.routes:
        print(f"\n{'route':<26}{'n':>6}{'correct':>9}{'acc':>8}")
        print("-" * 50)
        for k, (c, n) in sorted(routes.items(), key=lambda kv: -kv[1][1]):
            print(f"{k:<26}{n:>6}{c:>9}{c/n:>8.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
