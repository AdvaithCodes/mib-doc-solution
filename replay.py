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

from mib_pipeline.adjudicate import adjudicate, flags_from_text, read_adjudicator_note
from mib_pipeline.evidence import Packet, Page, classify
from mib_pipeline.extract import resolve
from mib_pipeline.fee import infer_fee_status
from mib_pipeline.pipeline import SCORED_FIELDS, confidence_for
from mib_pipeline.schema import FIELD_WEIGHTS

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
            ))
        yield packet


def decide(packet: Packet):
    resolved = resolve(packet)
    record = {f: (resolved[f].value if f in resolved else "") for f in SCORED_FIELDS}
    risk_known = bool(record["risk_flags"])
    if not record["risk_flags"]:
        record["risk_flags"] = "none"
    record["fee_status"], fee_known, fee_contested = infer_fee_status(
        packet, literal=record["fee_status"])
    _, note_text = read_adjudicator_note(packet)
    if note_text:
        mined = flags_from_text(note_text)
        if mined:
            have = {f for f in record["risk_flags"].split("|") if f and f != "none"}
            record["risk_flags"] = "|".join(sorted(have | set(mined)))
    decision, reason = adjudicate(record, packet, risk_known=risk_known,
                                  fee_known=fee_known, fee_contested=fee_contested)
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
    args = ap.parse_args()

    truth = {r["case_id"]: r for r in csv.DictReader(open(args.labels))}
    groups = {"all": [], "fit": [], "holdout": []}
    routes = collections.defaultdict(lambda: [0, 0])

    for idx, packet in enumerate(load(args.cache)):
        t = truth.get(packet.case_id)
        if not t:
            continue
        record, decision, reason, conf = decide(packet)

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
