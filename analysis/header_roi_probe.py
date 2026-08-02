"""Does the flag-panel trick generalise to the other forms?

The biometric panel recovered 24 risk flags at 100% precision by re-rendering
just the slip header at 400 dpi. The intake form feeds six of the eight scored
fields, and the registry extract and fee receipt feed the rest, so the same
targeted re-read might buy extraction what the panel bought classification.

This is worth answering *before* the validation cache is rebuilt, because that
rebuild takes hours and there is only time for one of them.

For each damaged page of a given form type it crops the header, re-reads it at
high resolution, and asks whether the true field value appears in the new text
when it does not appear in the text we already hold. It counts values, not
score: a value that becomes readable still has to win resolution, so this is an
upper bound on what the change could buy.

    ./analysis/header_roi_probe.py [doc_type] [n_packets]
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
from mib_pipeline.evidence import (PANEL_BOTTOM, PANEL_DPI, PANEL_LEFT,
                                   PANEL_RIGHT, PANEL_TOP, _clean, _ocr_engine,
                                   strip_injected)
from replay import load

CACHE = pathlib.Path.home() / "dev/mib-artifacts/train_cache_2engine.jsonl"
LABELS = pathlib.Path.home() / "dev/mib-doc-challenge/data/train_labels.csv"
TRAIN = pathlib.Path.home() / "dev/mib-doc-challenge/data/train"

# Which scored fields each form is the source for.
SOURCE_FIELDS = {
    "intake_form": ("applicant_name", "species_code", "home_world",
                    "visa_class", "sponsor_id", "arrival_date",
                    "declared_purpose"),
    "registry_extract": ("applicant_name", "species_code", "home_world",
                         "arrival_date"),
    "fee_receipt": ("fee_status",),
}


def squash(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def read_header(pdf_path: str, page_no: int, hidden: str) -> list[str]:
    doc = pdfium.PdfDocument(pdf_path)
    try:
        page = doc[page_no - 1]
        width, height = page.get_size()
        crop = np.array(page.render(
            scale=PANEL_DPI / 72,
            crop=(width * PANEL_LEFT, height * (1.0 - PANEL_BOTTOM),
                  width * (1.0 - PANEL_RIGHT), height * PANEL_TOP),
        ).to_pil())
    finally:
        doc.close()

    lines = []
    result, _ = _ocr_engine()(crop)
    if result:
        rows = sorted(result, key=lambda r: (min(p[1] for p in r[0]),
                                             min(p[0] for p in r[0])))
        lines.extend(r[1] for r in rows)
    if ocr_tesseract.available():
        lines.extend(ocr_tesseract.read_page(crop))
    return strip_injected(_clean(lines), hidden)


def main() -> int:
    doc_type = sys.argv[1] if len(sys.argv) > 1 else "intake_form"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 250
    fields = SOURCE_FIELDS[doc_type]

    truth = {r["case_id"]: r for r in csv.DictReader(open(LABELS))}
    packets = [p for p in load(str(CACHE)) if p.case_id in truth][:limit]

    gained = collections.Counter()
    already = collections.Counter()
    pages = 0

    for pk in packets:
        targets = [p for p in pk.pages
                   if p.doc_type == doc_type and p.source == "ocr"]
        if not targets:
            continue
        page = targets[0]
        held = squash("".join(l for p in pk.pages for l in p.all_lines))
        pages += 1
        fresh = squash("".join(read_header(str(TRAIN / f"{pk.case_id}.pdf"),
                                           page.number, page.hidden_text)))

        for field in fields:
            want = squash(truth[pk.case_id].get(field, "") or "")
            if not want or len(want) < 4:
                continue
            if want in held:
                already[field] += 1
            elif want in fresh:
                gained[field] += 1

    print(f"{doc_type}: {pages} damaged pages examined "
          f"across {len(packets)} packets\n")
    print(f"{'field':20s} {'already held':>13} {'newly readable':>15}")
    for field in fields:
        print(f"{field:20s} {already[field]:13d} {gained[field]:15d}")
    print(f"\ntrue values newly readable from the header crop: "
          f"{sum(gained.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
