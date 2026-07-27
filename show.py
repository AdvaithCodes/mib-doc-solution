#!/usr/bin/env python3
"""Inspect one packet: what the pipeline saw, what it decided, what was true.

    ./show.py MIB-000006              # one case
    ./show.py --interesting           # a guided tour of the instructive cases
    ./show.py MIB-000003 --open       # also open the PDF in a viewer

Reads the evidence cache when available (instant) and falls back to processing
the PDF directly. Hidden text is printed in its own section: it is never used as
evidence, and seeing it beside the visible page is the fastest way to understand
what the challenge is actually testing.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import subprocess
import sys

from mib_pipeline.evidence import Packet, Page, classify, read_packet
from mib_pipeline.schema import FIELD_WEIGHTS

CHALLENGE = pathlib.Path.home() / "dev/mib-doc-challenge"
CACHE = pathlib.Path("/tmp/train_cache.jsonl")

# Cases chosen to show one distinct behaviour each.
TOUR = [
    ("MIB-000003", "injected answer key: hidden text says APPROVED, truth is DENIED"),
    ("MIB-000006", "clean digital text plus a signed adjudicator note"),
    ("MIB-000002", "a decoy 'DENIAL / COPY ARTIFACT' stamp on an approved packet"),
    ("MIB-000009", "a destroyed page: evidence genuinely unrecoverable"),
    ("MIB-000090", "OCR damage: 'Foe Ststus: pald' recovered by closed-set snapping"),
]

RULE = "-" * 78


def load_truth() -> dict:
    path = CHALLENGE / "data/train_labels.csv"
    if not path.exists():
        return {}
    return {r["case_id"]: r for r in csv.DictReader(open(path))}


def packet_for(case_id: str) -> Packet | None:
    if CACHE.exists():
        for line in CACHE.open(encoding="utf-8"):
            line = line.strip()
            if not line or f'"{case_id}"' not in line:
                continue
            rec = json.loads(line)
            if rec["case_id"] != case_id:
                continue
            packet = Packet(case_id=case_id)
            for p in rec["pages"]:
                packet.pages.append(Page(
                    number=p["number"], doc_type=classify(p["lines"]),
                    lines=p["lines"], source=p["source"],
                    hidden_text=p.get("hidden_text", ""),
                    ocr_lines=p.get("ocr_lines", []),
                ))
            return packet
    pdf = CHALLENGE / f"data/train/{case_id}.pdf"
    if not pdf.exists():
        return None
    return read_packet(str(pdf), case_id)


def show(case_id: str, truth: dict, note: str = "", open_pdf: bool = False) -> None:
    packet = packet_for(case_id)
    if packet is None:
        print(f"{case_id}: not found")
        return

    from replay import decide  # imported here so --help stays fast

    record, decision, reason, confidence = decide(packet)
    t = truth.get(case_id, {})

    print(f"\n{RULE}\n  {case_id}" + (f"   -- {note}" if note else "") + f"\n{RULE}")

    if t:
        print(f"\n  {'field':<18}{'TRUTH':<26}{'PREDICTED':<26}")
        for f in FIELD_WEIGHTS:
            tv, pv = t.get(f, ""), str(record.get(f, ""))
            mark = "" if tv.strip().lower() == pv.strip().lower() else "  <-- miss"
            print(f"  {f:<18}{tv[:24]:<26}{pv[:24]:<26}{mark}")
        ok = "correct" if t.get("adjudication") == decision else "WRONG"
        print(f"\n  adjudication      truth={t.get('adjudication'):<14} "
              f"predicted={decision:<14} [{ok}]")
    else:
        print(f"\n  predicted adjudication: {decision}")
    print(f"  decided by: {reason}   confidence {confidence:.2f}")

    print(f"\n  pages ({len(packet.pages)}), most authoritative first:")
    for page in packet.by_authority():
        print(f"\n    [page {page.number}]  {page.doc_type}  "
              f"(authority {page.authority}, {'exact text' if page.exact else 'OCR'})")
        for line in page.lines:
            print(f"        {line[:88]}")
        if not page.lines:
            print("        (no readable text on this page)")

    hidden = [(p.number, p.hidden_text.strip()) for p in packet.pages if p.hidden_text.strip()]
    if hidden:
        print(f"\n  HIDDEN TEXT -- never used as evidence:")
        seen = set()
        for number, text in hidden:
            snippet = " ".join(text.split())[:300]
            if snippet in seen:
                continue
            seen.add(snippet)
            print(f"    [page {number}] {snippet}")
        if t:
            print(f"\n    (the packet's true adjudication is {t.get('adjudication')})")

    if open_pdf:
        pdf = CHALLENGE / f"data/train/{case_id}.pdf"
        if pdf.exists():
            subprocess.run(["open", str(pdf)], check=False)
            print(f"\n  opened {pdf}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_id", nargs="?")
    ap.add_argument("--interesting", action="store_true", help="guided tour")
    ap.add_argument("--open", action="store_true", help="also open the PDF")
    args = ap.parse_args()

    truth = load_truth()
    if args.interesting:
        for case_id, note in TOUR:
            show(case_id, truth, note)
        return 0
    if not args.case_id:
        ap.error("give a case id, or --interesting")
    show(args.case_id.upper(), truth, open_pdf=args.open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
