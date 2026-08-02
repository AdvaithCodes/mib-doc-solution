"""Can a high-resolution crop of the header recover the risk flags?

`flag_reachability.py` showed only 2 of 245 missed flags sit in text we already
hold, so this is a reading failure. Rendering five damaged biometric slips
showed why: `Observed flags: biohazard_red` and `Observed flags:
illegible_biometrics` are *printed* on the page, in a small faint header block,
while the rest of the page supplies plenty of characters from ruled lines and
stamps -- so the existing 300-dpi retry, which fires only when a page yields
under 40 characters, never triggers.

Two of the five were genuinely unreachable (`[RISK PANEL MISSING]`, and a line
truncated at `Obse`), so the ceiling here is well short of all 245.

This crops the header band and re-reads it at higher resolution, and reports
how many true flags that recovers against how many false ones it invents.
Precision is what matters: a flag is worth 8 raw when right and corrupts a
field worth 8 raw when wrong, so anything under ~50% loses points.

    ./analysis/roi_probe.py [dpi]
"""
import collections
import csv
import pathlib
import re
import sys

import numpy as np
import pypdfium2 as pdfium

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from mib_pipeline import ocr_tesseract
from mib_pipeline.adjudicate import _parse_date, reference_receipt_date
from mib_pipeline.evidence import (PANEL_BOTTOM, PANEL_LEFT, PANEL_RIGHT,
                                   PANEL_TOP, _clean, _ocr_engine,
                                   flags_from_panel)
from mib_pipeline.extract import resolve
from replay import load, rule_state

CACHE = pathlib.Path.home() / "dev/mib-artifacts/train_cache_2engine.jsonl"
LABELS = pathlib.Path.home() / "dev/mib-doc-challenge/data/train_labels.csv"
TRAIN = pathlib.Path.home() / "dev/mib-doc-challenge/data/train"

# Crop geometry and the snapping logic both come from the shipped module, so
# this probe cannot drift from what actually runs.
HEADER_TOP, HEADER_BOTTOM = PANEL_TOP, PANEL_BOTTOM
HEADER_LEFT, HEADER_RIGHT = PANEL_LEFT, PANEL_RIGHT


def read_crop(pdf_path: str, page_no: int, dpi: int) -> list[str]:
    doc = pdfium.PdfDocument(pdf_path)
    try:
        image = np.array(doc[page_no - 1].render(scale=dpi / 72).to_pil())
    finally:
        doc.close()
    h, w = image.shape[:2]
    crop = image[int(h * HEADER_TOP):int(h * HEADER_BOTTOM),
                 int(w * HEADER_LEFT):int(w * HEADER_RIGHT)]
    crop = np.ascontiguousarray(crop)

    lines = []
    result, _ = _ocr_engine()(crop)
    if result:
        rows = sorted(result, key=lambda r: (min(p[1] for p in r[0]),
                                             min(p[0] for p in r[0])))
        lines.extend(r[1] for r in rows)
    if ocr_tesseract.available():
        lines.extend(ocr_tesseract.read_page(crop))
    return _clean(lines)


flags_in = flags_from_panel


def main() -> int:
    dpi = int(sys.argv[1]) if len(sys.argv) > 1 else 400

    truth = {r["case_id"]: r for r in csv.DictReader(open(LABELS))}
    packets = [p for p in load(str(CACHE)) if p.case_id in truth]

    arrivals = []
    for pk in packets:
        rr = resolve(pk)
        if "arrival_date" in rr:
            d = _parse_date(rr["arrival_date"].value)
            if d:
                arrivals.append(d)
    reference = reference_receipt_date(arrivals)

    recovered = collections.Counter()
    invented = collections.Counter()
    examined = 0
    unreadable = 0

    for pk in packets:
        slips = [p for p in pk.pages
                 if p.doc_type == "biometric_slip" and p.source == "ocr"]
        if not slips:
            continue
        want = {f.strip() for f in (truth[pk.case_id].get("risk_flags") or "").split("|")
                if f.strip() and f.strip() != "none"}
        record, *_ = rule_state(pk, reference)
        got = {f for f in record["risk_flags"].split("|") if f and f != "none"}

        examined += 1
        lines = read_crop(str(TRAIN / f"{pk.case_id}.pdf"), slips[0].number, dpi)
        found = flags_in(lines)
        if not found:
            unreadable += 1
        for flag in found - got:
            if flag in want:
                recovered[flag] += 1
            else:
                invented[flag] += 1

    total_r, total_i = sum(recovered.values()), sum(invented.values())
    print(f"dpi {dpi}   damaged slips examined {examined}   "
          f"no flags line found {unreadable}")
    print(f"\nnew flags beyond what we already emit:")
    print(f"  correct  {total_r}")
    print(f"  wrong    {total_i}")
    denom = total_r + total_i
    print(f"  precision {100.0 * total_r / max(denom, 1):.1f}%  "
          f"(needs >50% to break even)")
    print("\nby flag (correct / wrong):")
    for flag in sorted(set(recovered) | set(invented)):
        print(f"   {flag:24s} {recovered[flag]:4d} / {invented[flag]:<4d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
