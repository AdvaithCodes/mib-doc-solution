"""Visual audit: render the pages behind our extraction misses.

All session the failures were judged through OCR strings. This renders the actual
page for a sample of missed fields so the damage class can be seen and counted:
blank, blurred, occluded, or simply misparsed.
"""
import sys, csv, pathlib, collections
import numpy as np

sys.path.insert(0, str(pathlib.Path.home() / "dev/mib-doc-solution"))
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

from replay import load, rule_state
from mib_pipeline.adjudicate import reference_receipt_date, _parse_date
from mib_pipeline.extract import resolve

OUT = pathlib.Path("/private/tmp/claude-502/-Users-advaith/"
                   "6a6d5d8c-90ea-49e5-99d6-a751baa9aa8a/scratchpad")
D = pathlib.Path.home() / "dev/mib-doc-challenge/data/train"
CACHE = pathlib.Path.home() / "dev/mib-artifacts/train_cache_2engine.jsonl"

# doc type most likely to carry each field
HOME = {
    "fee_status": "fee_receipt",
    "risk_flags": "biometric_slip",
    "applicant_name": "intake_form",
    "sponsor_id": "intake_form",
    "visa_class": "intake_form",
    "species_code": "intake_form",
}


def classify_damage(gray):
    """Crude damage class from pixel statistics."""
    ink = (gray < 200).sum() / gray.size
    mid = ((gray >= 200) & (gray < 250)).sum() / gray.size
    if ink < 0.002 and mid < 0.01:
        return "blank"
    # blur leaves lots of mid-grey and little hard ink
    if mid > ink * 2.5:
        return "blurred/washed"
    return "has crisp ink"


def main():
    truth = {r["case_id"]: r for r in csv.DictReader(open(D.parent / "train_labels.csv"))}
    packets = list(load(str(CACHE)))
    arrivals = []
    for pk in packets:
        rr = resolve(pk)
        if "arrival_date" in rr:
            d = _parse_date(rr["arrival_date"].value)
            if d:
                arrivals.append(d)
    ref = reference_receipt_date(arrivals)

    picks = []
    counts = collections.Counter()
    for pk in packets:
        t = truth.get(pk.case_id)
        if not t:
            continue
        record, *_ = rule_state(pk, ref)
        for field, doc_type in HOME.items():
            tv, pv = t[field].strip().lower(), str(record.get(field, "")).strip().lower()
            if field == "risk_flags":
                tv = "|".join(sorted(x for x in tv.split("|") if x))
                pv = "|".join(sorted(x for x in pv.split("|") if x))
            if tv == pv:
                continue
            page = next((p.number for p in pk.pages if p.doc_type == doc_type), None)
            if page is None:
                counts[f"{field}: no {doc_type} page in packet"] += 1
                continue
            doc = pdfium.PdfDocument(str(D / f"{pk.case_id}.pdf"))
            arr = np.array(doc[page - 1].render(scale=110 / 72).to_pil().convert("L"))
            doc.close()
            damage = classify_damage(arr)
            counts[f"{field}: {damage}"] += 1
            if damage == "has crisp ink" and len(picks) < 6:
                picks.append((pk.case_id, page, field, t[field], record.get(field, "")))

    print("damage class behind each missed field:")
    for k, n in counts.most_common(20):
        print(f"  {n:5d}  {k}")

    if not picks:
        print("\nno crisp-ink misses to render")
        return
    tiles = []
    for cid, page, field, tv, pv in picks:
        doc = pdfium.PdfDocument(str(D / f"{cid}.pdf"))
        im = doc[page - 1].render(scale=110 / 72).to_pil().convert("RGB")
        doc.close()
        im.thumbnail((520, 700))
        canvas = Image.new("RGB", (im.width, im.height + 26), "white")
        canvas.paste(im, (0, 26))
        ImageDraw.Draw(canvas).text((4, 6), f"{cid} p{page} {field}: {tv!r} -> {pv!r}", fill="black")
        tiles.append(canvas)
    W = sum(t.width for t in tiles)
    H = max(t.height for t in tiles)
    sheet = Image.new("RGB", (W, H), "white")
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.width
    sheet.save(OUT / "crisp_misses.png")
    print(f"\nwrote {OUT / 'crisp_misses.png'} ({sheet.width}x{sheet.height})")


if __name__ == "__main__":
    main()
