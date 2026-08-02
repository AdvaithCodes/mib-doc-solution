"""Does rotating a poorly-read page actually recover text?

`rotation_probe.py` used Tesseract's orientation detector and came back
inconclusive: 42% of raster pages defeated OSD entirely, which on a degraded
synthetic says more about the noise than the orientation.

This is the direct test. Every page that takes our OCR path is rendered once
and OCR'd at all four rotations; the readings are scored by how much *known
document vocabulary* they contain, not by raw character count, because noise
OCR produces plenty of characters. If the pages are stored sideways, some
rotation other than 0 will win on a meaningful share of them.

    ./analysis/rotation_probe2.py [n_packets]
"""
import collections
import pathlib
import re
import sys

import numpy as np
import pdfplumber
import pypdfium2 as pdfium

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from mib_pipeline.evidence import (BOILERPLATE_CHARS, RENDER_DPI, _clean,
                                   _ocr_engine, _split_visible)

TRAIN = pathlib.Path.home() / "dev/mib-doc-challenge/data/train"

# Labels and headings the generator prints on these forms. A rotation that is
# correct will surface these; a wrong one produces noise that does not match.
VOCAB = re.compile(
    r"APPLICANT|SPECIES|HOMEWORLD|HOME WORLD|PURPOSE|SPONSOR|REGISTRY|BIOMETRIC"
    r"|FEE|AMOUNT|WAIVER|STATUS|VISA|CLASS|ARRIVAL|DATE|FLAGS|OBSERVED"
    r"|INTAKE|FORM|SLIP|LETTER|EXTRACT|RECEIPT|NOTE|ADJUDICAT|SCAN|CONFIDENCE"
    r"|ATTEST|PLANETARY|AUTHORIZATION|MIB",
    re.I,
)


def score(lines: list[str]) -> int:
    """How much real document vocabulary this reading contains."""
    return sum(len(m.group(0)) for m in VOCAB.finditer(" ".join(lines)))


def ocr_array(image: np.ndarray) -> list[str]:
    result, _ = _ocr_engine()(image)
    if not result:
        return []
    rows = sorted(result, key=lambda r: (min(p[1] for p in r[0]), min(p[0] for p in r[0])))
    return [r[1] for r in rows]


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 60

    wins = collections.Counter()
    # Pages our current upright pass reads badly -- the ones the miss audit
    # blames on destroyed ink. If rotation is the real cause, it shows up here.
    poor_wins = collections.Counter()
    poor_pages = 0
    pages = 0
    gain_examples = []

    for pdf in sorted(TRAIN.glob("*.pdf"))[:limit]:
        doc = pdfium.PdfDocument(str(pdf))
        try:
            with pdfplumber.open(str(pdf)) as pl:
                for idx, page in enumerate(pl.pages):
                    text, _hidden, n_visible = _split_visible(page)
                    lines = _clean(text.splitlines())
                    if not (n_visible <= BOILERPLATE_CHARS or not lines):
                        continue
                    pages += 1
                    base = np.array(doc[idx].render(scale=RENDER_DPI / 72).to_pil())

                    scores = {}
                    for k, angle in enumerate((0, 90, 180, 270)):
                        img = np.rot90(base, k=k) if k else base
                        scores[angle] = score(_clean(ocr_array(np.ascontiguousarray(img))))

                    best = max(scores, key=lambda a: scores[a])
                    wins[best] += 1

                    if scores[0] < 40:  # our upright pass read almost nothing
                        poor_pages += 1
                        poor_wins[best] += 1
                        if best != 0 and scores[best] > scores[0] + 20:
                            gain_examples.append(
                                (pdf.name, idx, scores[0], best, scores[best]))
        finally:
            doc.close()

    print(f"packets {limit}   raster pages {pages}")
    print("\nbest rotation over ALL raster pages:")
    for angle, n in sorted(wins.items(), key=lambda kv: -kv[1]):
        print(f"  {angle:>4}deg  {n:5d}  {100.0*n/max(pages,1):5.1f}%")

    print(f"\npages our upright pass reads poorly (vocab score < 40): {poor_pages}")
    print("best rotation among those:")
    for angle, n in sorted(poor_wins.items(), key=lambda kv: -kv[1]):
        print(f"  {angle:>4}deg  {n:5d}  {100.0*n/max(poor_pages,1):5.1f}%")

    print(f"\npages a rotation clearly rescues (+20 vocab): {len(gain_examples)}")
    for name, idx, s0, best, sb in gain_examples[:15]:
        print(f"  {name} p{idx}  0deg={s0:3d} -> {best}deg={sb:3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
