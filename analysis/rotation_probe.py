"""Are the raster pages stored rotated?

naidx0's submission memo reports that ~13% of scanned pages are stored rotated
while the PDF `/Rotate` entry reads 0, so an upright OCR pass returns noise.
Our pipeline has no rotation handling at all, and our own miss audit blames 409
pages on "destroyed ink" and 403 documents on "not in the packet". Both are
consistent with sideways pages that OCR to junk.

This runs Tesseract's orientation detector (`--psm 0`) over the pages that
actually take our OCR path, and reports the distribution of detected rotations.

    ./analysis/rotation_probe.py [n_packets]
"""
import collections
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from mib_pipeline.evidence import BOILERPLATE_CHARS, RENDER_DPI, _clean, _split_visible

TRAIN = pathlib.Path.home() / "dev/mib-doc-challenge/data/train"
TESS = os.environ.get("MIB_TESSERACT", "tesseract")

_ANGLE_RE = re.compile(r"Rotate:\s*(\d+)")
_CONF_RE = re.compile(r"Orientation confidence:\s*([\d.]+)")


def osd(image: np.ndarray) -> tuple[int | None, float]:
    """Tesseract's detected rotation for one page image, with its confidence."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        Image.fromarray(image).save(fh.name)
        path = fh.name
    try:
        proc = subprocess.run(
            [TESS, path, "stdout", "--psm", "0"],
            capture_output=True, timeout=60,
        )
        out = proc.stdout.decode("utf-8", "replace")
        angle = _ANGLE_RE.search(out)
        conf = _CONF_RE.search(out)
        return (int(angle.group(1)) if angle else None,
                float(conf.group(1)) if conf else 0.0)
    except (subprocess.TimeoutExpired, OSError):
        return None, 0.0
    finally:
        os.unlink(path)


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 100

    angles = collections.Counter()
    confident = collections.Counter()
    raster_pages = 0
    checked = 0

    for pdf in sorted(TRAIN.glob("*.pdf"))[:limit]:
        doc = pdfium.PdfDocument(str(pdf))
        try:
            with pdfplumber.open(str(pdf)) as pl:
                for idx, page in enumerate(pl.pages):
                    text, _hidden, n_visible = _split_visible(page)
                    lines = _clean(text.splitlines())
                    if not (n_visible <= BOILERPLATE_CHARS or not lines):
                        continue  # digital-text page; never rendered
                    raster_pages += 1
                    image = np.array(doc[idx].render(scale=RENDER_DPI / 72).to_pil())
                    angle, conf = osd(image)
                    checked += 1
                    angles[angle] += 1
                    if conf >= 1.0:
                        confident[angle] += 1
        finally:
            doc.close()

    print(f"packets scanned      {limit}")
    print(f"raster (OCR'd) pages {raster_pages}")
    print(f"OSD ran on           {checked}")
    print("\ndetected rotation (all):")
    for angle, n in sorted(angles.items(), key=lambda kv: -kv[1]):
        share = 100.0 * n / max(checked, 1)
        print(f"  {str(angle):>6}  {n:5d}  {share:5.1f}%")
    print("\ndetected rotation (orientation confidence >= 1.0):")
    total_conf = sum(confident.values())
    for angle, n in sorted(confident.items(), key=lambda kv: -kv[1]):
        share = 100.0 * n / max(total_conf, 1)
        print(f"  {str(angle):>6}  {n:5d}  {share:5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
