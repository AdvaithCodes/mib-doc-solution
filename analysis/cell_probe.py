"""Build a value-cell map per form type, then test it on degraded pages.

Clean pages of one form type are pixel-aligned, so pixels that vary across them
are the value cells and pixels that never vary are the printed template. This
locates the cells, then checks whether a damaged page can be registered to the
same frame well enough to crop the right cell.
"""
import sys, pathlib, collections
import numpy as np
import cv2

sys.path.insert(0, str(pathlib.Path.home() / "dev/mib-doc-solution"))
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

from replay import load

D = pathlib.Path.home() / "dev/mib-doc-challenge/data/train"
CACHE = pathlib.Path.home() / "dev/mib-artifacts/train_cache_2engine.jsonl"
OUT = pathlib.Path("/private/tmp/claude-502/-Users-advaith/"
                   "6a6d5d8c-90ea-49e5-99d6-a751baa9aa8a/scratchpad")
DPI = 110


def render(cid, page):
    doc = pdfium.PdfDocument(str(D / f"{cid}.pdf"))
    try:
        return np.array(doc[page - 1].render(scale=DPI / 72).to_pil().convert("L"))
    finally:
        doc.close()


def main():
    packets = list(load(str(CACHE)))
    by_type = collections.defaultdict(list)
    degraded = collections.defaultdict(list)
    for pk in packets:
        for p in pk.pages:
            if p.source == "text" and len(p.lines) >= 6:
                by_type[p.doc_type].append((pk.case_id, p.number))
            elif p.source == "ocr":
                degraded[p.doc_type].append((pk.case_id, p.number))

    doc_type = "intake_form"
    clean = by_type[doc_type][:40]
    print(f"{doc_type}: {len(by_type[doc_type])} clean, {len(degraded[doc_type])} rasterised")

    imgs = [render(c, p) for c, p in clean]
    shape = collections.Counter(i.shape for i in imgs).most_common(1)[0][0]
    imgs = [i for i in imgs if i.shape == shape]
    print(f"using {len(imgs)} clean pages at {shape}")

    ink = np.stack([(i < 200).astype(np.float32) for i in imgs])
    freq = ink.mean(axis=0)
    template = freq > 0.9          # always-present printed matter
    variable = (freq > 0.08) & (freq <= 0.9)   # value regions
    print(f"template px {template.sum()}, variable px {variable.sum()}")

    # group variable pixels into cells
    mask = (variable * 255).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (25, 5)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cells = [tuple(stats[i][:4]) for i in range(1, n) if stats[i][4] > 150]
    cells.sort(key=lambda b: (b[1], b[0]))
    print(f"\nvalue cells found: {len(cells)}")
    for x, y, w, h in cells[:10]:
        print(f"  cell x={x:4d} y={y:4d} w={w:4d} h={h:3d}")

    # can a damaged page be registered to this frame?
    print("\nregistering rasterised pages to the clean frame:")
    ref = np.stack(imgs).mean(axis=0).astype(np.float32)
    for cid, pno in degraded[doc_type][:6]:
        im = render(cid, pno)
        if im.shape != shape:
            print(f"  {cid} p{pno}: shape {im.shape} != {shape}")
            continue
        shift, response = cv2.phaseCorrelate(ref, im.astype(np.float32))
        a, b = np.stack(ink).mean(axis=0) > 0.9, im < 200
        overlap = (a & b).sum() / max(a.sum(), 1)
        print(f"  {cid} p{pno}: shift=({shift[0]:+.1f},{shift[1]:+.1f}) "
              f"response={response:.3f}  template-ink recovered={overlap:.2f}")

    vis = Image.fromarray(255 - (freq * 255).astype(np.uint8)).convert("RGB")
    draw = ImageDraw.Draw(vis)
    for x, y, w, h in cells:
        draw.rectangle([x, y, x + w, y + h], outline=(255, 0, 0))
    vis.save(OUT / "fee_cells.png")
    print(f"\nwrote {OUT / 'fee_cells.png'}")


if __name__ == "__main__":
    main()
