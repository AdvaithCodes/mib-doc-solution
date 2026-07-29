"""Tesseract as a second, independent OCR engine.

Two engines are worth more than two passes of one. RapidOCR (PP-OCR detection
plus recognition) and Tesseract (classical LSTM line recognition) fail in
uncorrelated ways, which is exactly what a second opinion needs -- rendering the
same page at 150 and 200 dpi was measured and gained nothing, because the errors
there are the same errors.

Measured examples from the training set:

    MIB-000090 p5   RapidOCR "Fee Statue: peld"   Tesseract "Fee Status: paid"
    MIB-000019 p4   RapidOCR omits the region     Tesseract "Manual correction:
                                                   applicant is Oridane ..."

The second is the more valuable: several intake forms carry a struck-through name
plus a visible manual correction, and RapidOCR does not read that region at all.
Without this engine those packets look like an unresolvable identity conflict
rather than a page that plainly states the answer.

The binary is apt-installed in the image. Locally it can live anywhere on PATH,
or be pointed at explicitly with MIB_TESSERACT.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import numpy as np

# psm 6 assumes a uniform block of text, psm 4 a single column of variable-size
# text. They disagree usefully on these documents: psm 4 recovered a struck-through
# applicant name that psm 6 missed, and psm 6 recovered a manual-correction line
# that psm 4 dropped. Both are cheap, so both run.
PAGE_SEGMENTATION_MODES = (6, 4)

_BINARY: str | None = None
_CHECKED = False


def binary() -> str | None:
    """Path to the tesseract executable, or None when unavailable."""
    global _BINARY, _CHECKED
    if not _CHECKED:
        _CHECKED = True
        explicit = os.environ.get("MIB_TESSERACT")
        _BINARY = explicit if explicit and os.path.exists(explicit) else shutil.which("tesseract")
    return _BINARY


def available() -> bool:
    return binary() is not None


def read_page(image: np.ndarray, timeout: float = 20.0) -> list[str]:
    """OCR one rendered page, returning text lines.

    The image is piped in on stdin rather than written to a temp file: it avoids
    a disk round-trip per page, and it sidesteps the question of which
    directories are writable, since the scoring container mounts a read-only
    root with only /tmp available.

    Returns an empty list rather than raising if the engine is missing or fails.
    This is a supplementary reading; a packet must still process without it.
    """
    exe = binary()
    if exe is None:
        return []

    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="PNG")
    payload = buffer.getvalue()

    lines: list[str] = []
    for psm in PAGE_SEGMENTATION_MODES:
        try:
            result = subprocess.run(
                [exe, "stdin", "stdout", "--psm", str(psm), "-l", "eng"],
                input=payload, capture_output=True, timeout=timeout,
            )
        except (subprocess.SubprocessError, OSError):
            continue
        if result.returncode != 0:
            continue
        # decode leniently: tesseract emits stray non-UTF-8 bytes on damaged scans
        for line in result.stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and line not in lines:
                lines.append(line)
    return lines
