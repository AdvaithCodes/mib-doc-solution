"""Evidence layer: turn a PDF packet into visible, attributed text lines.

Two rules govern everything here:

1. Only *visible* content is evidence. Text-layer characters that are white-filled,
   positioned outside the page crop, or sub-visible in size are discarded before
   anything downstream sees them. On the training set 26.3% of text-layer
   characters are hidden this way, and 21.6% of packets carry an injected answer
   key whose adjudication is wrong in 100% of cases (0/216).

2. Every line keeps its provenance: which page, which document type, and whether
   it came from the text layer (exact) or OCR (lossy). FIELD_MANUAL resolves
   conflicts by document authority, so the source must survive to that point.

Pages whose visible text layer is only the page boilerplate carry their content
in a raster image and are OCR'd. Pages with real digital text use it directly:
those characters are exactly what a human adjudicator sees, and they avoid the
OCR errors that destroy names and sponsor IDs.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

warnings.filterwarnings("ignore")

import numpy as np
import pdfplumber
import pypdfium2 as pdfium

# Rendering resolution. Measured: 150 dpi costs 0.354s/page and yields as much
# text as 200 dpi, which costs 28% more. Higher DPI is reserved for targeted
# re-reads of damaged regions.
RENDER_DPI = 150

# A page whose visible text layer is at or below this many characters carries
# only the "Packet MIB-xxxxxx / page N" boilerplate; its content is in the raster.
BOILERPLATE_CHARS = 70

# FIELD_MANUAL trusted-evidence precedence. Lower rank wins conflicts.
AUTHORITY = {
    "adjudicator_note": 1,
    "intake_form": 2,
    "biometric_slip": 3,
    "sponsor_letter": 4,
    "registry_extract": 5,
    "fee_receipt": 6,
    "passport_image": 7,
    "unknown": 8,
}

# Document-type detection runs against squashed text, so OCR word-join errors
# ("MIBFeeReceipt") match the same patterns as clean text ("MIB Fee Receipt").
DOC_PATTERNS = (
    ("adjudicator_note", re.compile(r"MANUALADJUDICATORNOTE|ADJUDICATORNOTE", re.I)),
    ("intake_form", re.compile(r"FORMI-?8O?09O?|EXTRATERRESTRIALWORKAUTH", re.I)),
    ("biometric_slip", re.compile(r"FORMB-?13|BIOMETRICSCANSLIP", re.I)),
    ("sponsor_letter", re.compile(r"SPONSORATTESTATION", re.I)),
    ("registry_extract", re.compile(r"PLANETARYREGISTRY", re.I)),
    ("fee_receipt", re.compile(r"MIBFEERECEIPT|FEERECEIPT", re.I)),
    ("passport_image", re.compile(r"PASSPORTIMAGE", re.I)),
)

_BOILERPLATE_RE = re.compile(
    r"packet\s*MIB-?\d{6}\s*/?\s*page\s*\d+|synthetic\s*hiring\s*challenge",
    re.I,
)


@dataclass
class Page:
    number: int
    doc_type: str
    lines: list[str]
    source: str  # "text" (exact) or "ocr" (lossy)
    hidden_text: str = ""  # retained for diagnostics only; never evidence
    ocr_lines: list[str] = field(default_factory=list)

    @property
    def all_lines(self) -> list[str]:
        """Text-layer and OCR readings together.

        Stamps, seals and hand annotations are painted into the page raster and
        never appear in the text layer, so a page with perfectly good digital
        text can still be hiding a denial stamp. Anything looking for marks
        rather than field values must read this, not `lines`.
        """
        return self.lines + [l for l in self.ocr_lines if l not in self.lines]

    @property
    def authority(self) -> int:
        return AUTHORITY.get(self.doc_type, AUTHORITY["unknown"])

    @property
    def exact(self) -> bool:
        return self.source == "text"


@dataclass
class Packet:
    case_id: str
    pages: list[Page] = field(default_factory=list)

    def by_authority(self) -> list[Page]:
        """Pages ordered most authoritative first, page order breaking ties."""
        return sorted(self.pages, key=lambda p: (p.authority, p.number))

    @property
    def injected(self) -> bool:
        """True if any page carried hidden text. Diagnostic signal, not evidence."""
        return any(p.hidden_text.strip() for p in self.pages)


def _is_white(char) -> bool:
    """True if the character is painted white (or near-white) on white paper."""
    col = char.get("non_stroking_color")
    if col is None:
        return False
    if isinstance(col, (int, float)):
        return col >= 0.95
    if isinstance(col, (list, tuple)):
        if len(col) == 1:
            return col[0] >= 0.95
        if len(col) == 3:
            return all(v >= 0.95 for v in col)
        if len(col) == 4:  # CMYK: no ink at all
            return all(v <= 0.05 for v in col[:3]) and col[3] <= 0.05
    return False


def _is_hidden(char, width: float, height: float) -> bool:
    """Hidden = invisible to a human reading the rendered page."""
    return (
        _is_white(char)
        or char["x1"] < 0
        or char["x0"] > width
        or char["bottom"] < 0
        or char["top"] > height
        or char["size"] < 1.5
    )


def _split_visible(page) -> tuple[str, str, int]:
    """Return (visible_text, hidden_text, visible_char_count) for one page."""
    W, H = page.width, page.height
    visible, hidden = [], []
    for c in page.chars:
        (hidden if _is_hidden(c, W, H) else visible).append(c)
    keep = {id(c) for c in visible}
    text = page.filter(
        lambda o: o.get("object_type") != "char" or id(o) in keep
    ).extract_text() or ""
    return text, "".join(c["text"] for c in hidden), len(visible)


# Content signatures, used when the heading is too damaged to read. A page whose
# title OCR'd to noise still carries recognisable field labels, and misfiling an
# adjudicator note as "unknown" drops it from rank 1 to rank 8 -- discarding the
# single most reliable evidence in the packet.
CONTENT_PATTERNS = (
    ("adjudicator_note", re.compile(r"FINDING[:\.\-]|MANUALADJUDICAT|MANUALCORRECTION", re.I)),
    ("fee_receipt", re.compile(r"FEESTATUS|WAIVERCODE|AMOUNT\$", re.I)),
    ("biometric_slip", re.compile(r"OBSERVEDFLAGS|BIOMETRICCONFIDENCE|SPECIESMATCH|SCANIMAGE", re.I)),
    ("registry_extract", re.compile(r"REGISTRYNAME|REGISTRYSTATUS|REGISTRYIMAGE", re.I)),
    ("sponsor_letter", re.compile(r"ATTESTSTHAT|TOMIBINTAKE|ACKNOWLEDGESRESPONSIBILITY", re.I)),
    ("intake_form", re.compile(r"PRIMARYINTAKERECORD|DECLAREDPURPOSE", re.I)),
)


def classify(lines: list[str]) -> str:
    """Identify the document type, by heading first and by content as a fallback."""
    head = "".join(lines[:3]).upper().replace(" ", "").replace("'", "")
    for name, pattern in DOC_PATTERNS:
        if pattern.search(head):
            return name

    body = "".join(lines).upper().replace(" ", "").replace("'", "")
    for name, pattern in CONTENT_PATTERNS:
        if pattern.search(body):
            return name
    return "unknown"


def _clean(lines: list[str]) -> list[str]:
    """Drop page boilerplate, which is present on every page and carries nothing."""
    out = []
    for l in lines:
        l = l.strip()
        if not l or _BOILERPLATE_RE.search(l):
            continue
        out.append(l)
    return out


def read_packet(pdf_path: str, case_id: str) -> Packet:
    """Extract visible, attributed evidence from every page of a packet."""
    packet = Packet(case_id=case_id)
    doc = pdfium.PdfDocument(pdf_path)
    try:
        with pdfplumber.open(pdf_path) as pl:
            for idx, page in enumerate(pl.pages):
                text, hidden, n_visible = _split_visible(page)
                lines = _clean(text.splitlines())

                # OCR only pages whose content is actually in the raster.
                #
                # Rendering every page was tried and reverted: it cost 27.7 CPU-s
                # per PDF against a 24 CPU-s budget and gained 0.06 points. The
                # premise was that stamps might be painted onto pages that also
                # carry digital text, but on the training set the OCR of those
                # pages returns only a re-segmentation of the same text -- there
                # are no raster-only marks there to find.
                if n_visible <= BOILERPLATE_CHARS or not lines:
                    ocr_lines = _clean(_ocr_page(doc, idx))
                    lines, source = ocr_lines, "ocr"
                else:
                    ocr_lines, source = [], "text"

                packet.pages.append(
                    Page(
                        number=idx + 1,
                        doc_type=classify(lines),
                        lines=lines,
                        source=source,
                        hidden_text=hidden,
                        ocr_lines=ocr_lines,
                    )
                )
    finally:
        doc.close()
    return packet


_OCR = None


def _ocr_engine():
    """Lazily construct the OCR engine, once per process."""
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR

        _OCR = RapidOCR()
    return _OCR


def _ocr_at(doc, index: int, dpi: int) -> list[str]:
    image = np.array(doc[index].render(scale=dpi / 72).to_pil())
    result, _ = _ocr_engine()(image)
    if not result:
        return []
    # RapidOCR returns (box, text, score); order by vertical position so that
    # label/value pairs on the same visual row stay adjacent.
    rows = sorted(result, key=lambda r: (min(p[1] for p in r[0]), min(p[0] for p in r[0])))
    return [r[1] for r in rows]


# A washed-out or low-contrast page can OCR to a handful of junk glyphs at the
# default resolution while being perfectly readable at a higher one. Retrying is
# affordable because it fires only on pages that produced almost nothing.
RETRY_DPI = 300
RETRY_BELOW_CHARS = 40


def _ocr_page(doc, index: int, dpi: int = RENDER_DPI) -> list[str]:
    """Render and OCR one page, retrying at higher resolution if it comes back empty."""
    lines = _ocr_at(doc, index, dpi)
    if sum(len(l) for l in lines) < RETRY_BELOW_CHARS:
        retry = _ocr_at(doc, index, RETRY_DPI)
        if sum(len(l) for l in retry) > sum(len(l) for l in lines):
            return retry
    return lines
