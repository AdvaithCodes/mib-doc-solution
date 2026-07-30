"""Render the pages behind risk_flags and fee_status misses and look at them.

These two fields gate the two largest review buckets, so every one recovered
moves a packet out of a 29-51% bucket into rules that run 85-100%. A previous
audit classified the damage by pixel statistics and produced a conclusion that
turned out to be wrong; this one renders the pages so they can be inspected
directly.

Only packets that DO contain the relevant document are sampled -- where the page
is absent there is nothing to look at.
"""
import sys, csv, pathlib, collections
import numpy as np

sys.path.insert(0, str(pathlib.Path.home() / "dev/mib-doc-solution"))
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

from replay import load, rule_state
from mib_pipeline.adjudicate import reference_receipt_date, _parse_date
from mib_pipeline.extract import resolve

D = pathlib.Path.home() / "dev/mib-doc-challenge/data/train"
CACHE = pathlib.Path.home() / "dev/mib-artifacts/train_cache_2engine.jsonl"
OUT = pathlib.Path("/private/tmp/claude-502/-Users-advaith/"
                   "6a6d5d8c-90ea-49e5-99d6-a751baa9aa8a/scratchpad")

HOME = {"risk_flags": "biometric_slip", "fee_status": "fee_receipt"}


def main():
    field = sys.argv[1] if len(sys.argv) > 1 else "risk_flags"
    doc_type = HOME[field]
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
    stats = collections.Counter()
    for pk in packets:
        t = truth.get(pk.case_id)
        if not t:
            continue
        record, *_ = rule_state(pk, ref)
        tv, pv = t[field].strip().lower(), str(record.get(field, "")).strip().lower()
        if field == "risk_flags":
            tv = "|".join(sorted(x for x in tv.split("|") if x))
            pv = "|".join(sorted(x for x in pv.split("|") if x))
        if tv == pv:
            continue
        page = next((p for p in pk.pages if p.doc_type == doc_type), None)
        if page is None:
            stats["document absent from packet"] += 1
            continue
        stats["document present but value missed"] += 1
        if len(picks) < 6:
            picks.append((pk.case_id, page.number, t[field], record.get(field, ""),
                          page.source, page.lines[:4]))

    print(f"{field}: misses where the {doc_type} page is…")
    for k, n in stats.most_common():
        print(f"  {n:5d}  {k}")

    if not picks:
        print("nothing to render")
        return

    tiles = []
    for cid, pno, tv, pv, source, lines in picks:
        doc = pdfium.PdfDocument(str(D / f"{cid}.pdf"))
        im = doc[pno - 1].render(scale=150 / 72).to_pil().convert("RGB")
        doc.close()
        im.thumbnail((560, 760))
        canvas = Image.new("RGB", (im.width, im.height + 30), "white")
        canvas.paste(im, (0, 30))
        ImageDraw.Draw(canvas).text(
            (4, 4), f"{cid} p{pno} [{source}]  truth={tv!r} got={pv!r}", fill="black")
        tiles.append(canvas)
        print(f"\n  {cid} p{pno} [{source}] truth={tv!r} got={pv!r}")
        for l in lines:
            print(f"      {l[:80]}")

    W = sum(t.width for t in tiles[:3])
    rows = [tiles[:3], tiles[3:6]]
    H = sum(max(t.height for t in r) for r in rows if r)
    sheet = Image.new("RGB", (W, H), "white")
    y = 0
    for r in rows:
        x = 0
        for t in r:
            sheet.paste(t, (x, y))
            x += t.width
        if r:
            y += max(t.height for t in r)
    sheet.save(OUT / f"audit_{field}.png")
    print(f"\nwrote {OUT / f'audit_{field}.png'}")


if __name__ == "__main__":
    main()
