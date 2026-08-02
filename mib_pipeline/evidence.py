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
from difflib import SequenceMatcher
from dataclasses import dataclass, field

warnings.filterwarnings("ignore")

import numpy as np
import pdfplumber
import pypdfium2 as pdfium

from mib_pipeline import ocr_tesseract, vocab

# Rendering resolution.
#
# 200 dpi with projection-profile deskew was built and measured against this,
# on the theory that the earlier 150-vs-200 comparison had been distorted by
# averaging character counts over mostly-clean pages. It was not: holdout scored
# 118.07 against 118.15 here, extraction +0.12, classification -0.19, and
# catastrophic false approvals 0 -> 1 -- while running 4.5x slower (12.5 vs 57
# packets/min), which alone would have exceeded the 6s/PDF budget.
#
# The damaged pages that motivated the change do not read better at higher
# resolution; they are damaged in ways resolution does not address.
#
# Deskew was then kept on the rare high-dpi retry path alone, and removed after
# measuring that too: it changed 0 of 60 predictions.
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
)

# "PASSPORT IMAGE" is a caption printed beside a photo, not a document heading:
# it appears on intake forms alongside their real title. Matching it early
# demotes a rank-2 intake form to rank 7, so it is only consulted once every
# other signal has failed.
_PASSPORT_RE = re.compile(r"PASSPORTIMAGE|SCANIMAGE|REGISTRYIMAGE", re.I)

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
    # Second-engine (Tesseract) readings, kept apart from the primary ones.
    # They fill gaps the primary engine left; they never override a value it
    # resolved. A wrong second reading that outranks a correct primary one turned
    # a TRANSIT-7 packet into an approval on the training set -- the exact
    # -4 case -- which is why these do not compete as equals.
    second_lines: list[str] = field(default_factory=list)

    @property
    def all_lines(self) -> list[str]:
        """Text-layer and OCR readings together.

        Stamps, seals and hand annotations are painted into the page raster and
        never appear in the text layer, so a page with perfectly good digital
        text can still be hiding a denial stamp. Anything looking for marks
        rather than field values must read this, not `lines`.
        """
        merged = list(self.lines)
        for extra in (self.ocr_lines, self.second_lines):
            merged.extend(l for l in extra if l not in merged)
        return merged

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
    """Return (visible_text, hidden_text, visible_char_count) for one page.

    This is only half the visible-evidence rule. Dropping hidden characters from
    the text layer does not stop them reaching OCR, because pdfium renders them
    into the page raster too and white-on-white text over a grey scan reads
    cleanly. `strip_injected` is the other half; see the note above it.
    """
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
    # `MANUALCORRECTION` used to appear here and is an intake-form signal, not an
    # adjudicator-note one: `Manual correction: sponsor is SPN-4705.` is printed
    # on the intake form itself, on 136 of 136 training pages that carry it and
    # on no adjudicator note. Because this pattern is consulted before the
    # intake one, a damaged intake form carrying a correction was typed
    # `adjudicator_note` -- promoting a rank-2 page to rank 1, the most
    # authoritative evidence in the packet. It cost nothing on the public set,
    # where those 136 pages all have readable titles, and is a private-set trap.
    ("adjudicator_note", re.compile(r"FINDING[:\.\-]|MANUALADJUDICAT", re.I)),
    ("fee_receipt", re.compile(r"FEESTATUS|WAIVERCODE|AMOUNT\$", re.I)),
    ("biometric_slip", re.compile(r"OBSERVEDFLAGS|BIOMETRICCONFIDENCE|SPECIESMATCH|SCANIMAGE", re.I)),
    ("registry_extract", re.compile(r"REGISTRYNAME|REGISTRYSTATUS|REGISTRYIMAGE", re.I)),
    ("sponsor_letter", re.compile(r"ATTESTSTHAT|TOMIBINTAKE|ACKNOWLEDGESRESPONSIBILITY", re.I)),
    ("intake_form", re.compile(r"PRIMARYINTAKERECORD|DECLAREDPURPOSE", re.I)),
)


# Canonical headings, for fuzzy recognition when OCR has damaged the title past
# what a regex will match ("FORMU-8ogo:ExtraterrmstrialWorkAuthorization_Intakn").
# Misfiling a real document as "unknown" drops it from its true authority to
# rank 8, which discards its evidence in every conflict.
HEADINGS = (
    ("intake_form", "FORMI8090EXTRATERRESTRIALWORKAUTHORIZATIONINTAKE"),
    ("biometric_slip", "FORMB13BIOMETRICSCANSLIP"),
    ("sponsor_letter", "SPONSORATTESTATIONLETTER"),
    ("registry_extract", "PLANETARYREGISTRYEXTRACT"),
    ("adjudicator_note", "MANUALADJUDICATORNOTE"),
    ("fee_receipt", "MIBFEERECEIPT"),
)

_FUZZY_HEADING_MIN = 0.62

# Stamps and watermarks painted over the page: they belong to no form, and in
# OCR row order they sort above the real heading, pushing it out of the
# three- and five-line windows the heading tests below inspect. Three fully
# legible FORM I-8090 intake forms were typed `unknown` for exactly this
# reason. Dropped for classification only -- extraction still sees them.
#
# `SAMPLE DENIAL` is a decoy watermark, not an adjudication.
_WATERMARK_LINE = re.compile(
    r"^(SAMPLEDENIAL|COPYARTIFACT|CASEWORK|FILED|ARCHIVE|REDACTED|SCANTAB"
    r"|MIBEYESONLY)$",
    re.I,
)

# `Manual correction: fee status is paid.` is printed on the intake form, and it
# names a field belonging to a *different* document. Left in, it typed 28 intake
# forms as fee receipts (rank 2 -> rank 6) and, before `MANUALCORRECTION` was
# removed from the adjudicator-note signal, 136 more as adjudicator notes
# (rank 2 -> rank 1). A correction line can name any field, so it can collide
# with every form's pattern; it is excluded from typing rather than fought
# case by case. `extract.parse_corrections` still reads it -- this drops it from
# classification only.
_CORRECTION_LINE = re.compile(r"^\s*manual\s*correction", re.I)

# Field labels that identify a form when its heading is destroyed. The weight is
# how exclusive the label is to that form: `Declared Purpose` appears only on
# the intake form, while `Home World` is shared with the registry extract and
# cannot carry the decision alone.
FORM_ANCHORS = {
    "intake_form": (
        ("PRIMARYINTAKERECORD", 3.0), ("DECLAREDPURPOSE", 3.0),
        ("VISACLASS", 2.0), ("SPONSORID", 2.0), ("PASSPORTIMAGE", 1.5),
        ("APPLICANT", 1.0), ("SPECIESCODE", 1.0), ("HOMEWORLD", 1.0),
        ("ARRIVALDATE", 1.0),
    ),
    "biometric_slip": (
        ("BIOMETRICCONFIDENCE", 3.0), ("OBSERVEDFLAGS", 3.0),
        ("SPECIESMATCH", 3.0), ("SCANIMAGE", 2.0), ("CASEID", 0.5),
        ("APPLICANT", 0.5),
    ),
    "registry_extract": (
        ("REGISTRYNAME", 3.0), ("REGISTRYSTATUS", 3.0),
        ("REGISTRYIMAGE", 2.0), ("HOMEWORLD", 1.0), ("SPECIESCODE", 1.0),
        ("ARRIVALDATE", 1.0),
    ),
    "fee_receipt": (
        ("FEESTATUS", 3.0), ("WAIVERCODE", 3.0), ("AMOUNT", 2.0),
        ("CASEID", 0.5),
    ),
    # No `MANUALCORRECTION` anchor: corrections are printed on the intake form,
    # never on a note (136 of 136 training pages). Correction lines are stripped
    # before this vote runs, but OCR damage can leave one that the strip misses,
    # and the anchor would then push a rank-2 page to rank 1 -- the same trap
    # that `CONTENT_PATTERNS` had.
    "adjudicator_note": (
        ("FINDING", 3.0), ("REASON", 2.0), ("MANUALADJUDICATORNOTE", 3.0),
    ),
    "sponsor_letter": (
        ("ATTESTSTHAT", 3.0), ("TOMIBINTAKE", 3.0),
        ("ACKNOWLEDGESRESPONSIBILITY", 3.0), ("ATTESTATIONISVALID", 2.0),
        ("SPONSOR", 1.0),
    ),
}

# A page must clear this much anchor weight, and beat the runner-up by this
# margin, before it is typed. A page scoring equally for two forms surfaced the
# labels they share, not the ones that discriminate.
_ANCHOR_MIN = 2.5
_ANCHOR_MARGIN = 1.0


def _fuzzy_find(haystack: str, needle: str, floor: float) -> float:
    """Best similarity for `needle` anywhere in `haystack`, else 0.

    Scanning every offset with SequenceMatcher is correct but quadratic, and it
    took replay from 17s to five minutes -- which would also have blown the
    6s/PDF budget. The longest common block locates the only region that can
    plausibly win: a window scoring 0.70 against an n-character needle must
    share a run with it, so a page whose best run is short cannot match at all
    and is rejected without any windowed comparison.
    """
    n = len(needle)
    if not haystack or n > len(haystack):
        return 0.0

    matcher = SequenceMatcher(None, haystack, needle, autojunk=False)
    block = matcher.find_longest_match(0, len(haystack), 0, n)
    if block.size < max(3, int(0.35 * n)):
        return 0.0

    centre = block.a - block.b
    lo = max(0, centre - n)
    hi = min(len(haystack) - n, centre + n)
    best = 0.0
    for start in range(lo, hi + 1):
        ratio = SequenceMatcher(None, haystack[start:start + n], needle).ratio()
        if ratio > best:
            best = ratio
            if best > 0.97:
                break
    return best if best >= floor else 0.0


def _anchor_vote(body: str) -> str | None:
    """Type a page from the field labels it still shows.

    Validated by deleting the title line from the 2,203 pages whose type is
    known from an exact text layer and re-classifying blind: 100% precision at
    100% coverage.
    """
    scores: dict[str, float] = {}
    for doc_type, anchors in FORM_ANCHORS.items():
        total = 0.0
        for token, weight in anchors:
            ratio = _fuzzy_find(body, token, 0.70)
            if ratio:
                total += weight * ratio
        if total:
            scores[doc_type] = total
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, score = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    if score < _ANCHOR_MIN or score - runner < _ANCHOR_MARGIN:
        return None
    return best


def _best_heading(head: str) -> str | None:
    """Best fuzzy heading match within the page's opening text.

    Scored by similarity weighted by heading length, so a short generic title
    like PASSPORTIMAGE -- which frequently appears as a caption on a page whose
    real heading is the intake form -- cannot outrank the longer, more specific
    document title it sits beside.
    """
    best, best_score = None, 0.0
    for name, heading in HEADINGS:
        n = len(heading)
        for start in range(0, max(len(head) - n + 3, 1)):
            window = head[start:start + n + 2]
            if len(window) < n // 2:
                break
            ratio = SequenceMatcher(None, window, heading).ratio()
            if ratio >= _FUZZY_HEADING_MIN and ratio * n > best_score:
                best, best_score = name, ratio * n
    return best


def classify(lines: list[str], extra: list[str] | None = None) -> str:
    """Identify the document type: exact heading, content, fuzzy heading, anchors.

    `extra` carries the second engine's reading of the same page. Both engines
    are consulted because a heading one of them destroyed the other often
    survives, and typing a page wrong costs its whole authority rank.
    """
    merged = list(lines)
    if extra:
        merged.extend(l for l in extra if l not in merged)
    # Drop watermark-only lines so the real heading rises into the windows below.
    kept = [l for l in merged
            if not _WATERMARK_LINE.match(re.sub(r"[^A-Z0-9]", "", l.upper()))
            and not _CORRECTION_LINE.match(l)]

    head = "".join(kept[:3]).upper().replace(" ", "").replace("'", "")
    for name, pattern in DOC_PATTERNS:
        if pattern.search(head):
            return name

    body = "".join(kept).upper().replace(" ", "").replace("'", "")
    for name, pattern in CONTENT_PATTERNS:
        if pattern.search(body):
            return name

    fuzzy = _best_heading(re.sub(r"[^A-Z0-9]", "", "".join(kept[:5]).upper()))
    if fuzzy:
        return fuzzy

    anchored = _anchor_vote(re.sub(r"[^A-Z0-9]", "", body))
    if anchored:
        return anchored

    if _PASSPORT_RE.search(body):
        return "passport_image"
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
                second_lines: list[str] = []
                if n_visible <= BOILERPLATE_CHARS or not lines:
                    ocr_lines = strip_injected(_clean(_ocr_page(doc, idx)), hidden)
                    lines, source = ocr_lines, "ocr"
                    second_lines = strip_injected(
                        _second_engine_page(doc, idx), hidden)
                else:
                    ocr_lines, source = [], "text"

                doc_type = classify(lines, second_lines)

                # A damaged biometric slip gets its risk-flag panel re-read at
                # high resolution. Only damaged slips qualify: a slip with a
                # real text layer already states its flags exactly.
                if doc_type == "biometric_slip" and source == "ocr":
                    panel = _read_flag_panel(doc, idx, hidden)
                    if panel:
                        second_lines = list(second_lines) + panel

                packet.pages.append(
                    Page(
                        number=idx + 1,
                        doc_type=doc_type,
                        lines=lines,
                        source=source,
                        hidden_text=hidden,
                        ocr_lines=ocr_lines,
                        second_lines=second_lines,
                    )
                )
    finally:
        doc.close()
    return packet


_OCR = None


def _ocr_engine():
    """Lazily construct the OCR engine, once per process.

    RapidOCR ships intra_op_num_threads / inter_op_num_threads set to -1, which
    tells ONNX Runtime to use every core. Parallelism here is at the process
    level -- one worker per vCPU -- so leaving that default means each worker
    also spawns a full thread pool. On the 4-vCPU scoring host that is 4
    processes times 4 threads fighting over 4 cores, and the contention does not
    show up as an error, only as blown wall-clock against the 6s/PDF budget.

    ONNX Runtime does not honour OMP_NUM_THREADS for this, so it has to be set
    explicitly.
    """
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR

        _OCR = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
    return _OCR


# Dropping hidden characters from the text layer is not enough: pdfium renders
# them into the raster too, and white-on-white text laid over a grey scan reads
# cleanly to OCR. Three training packets carry answer-key text in their OCR
# lines at 150 dpi, and it gets worse as render resolution rises.
#
# Painting the hidden characters' bounding boxes white before OCR was built and
# measured first, and is wrong: the injected key is positioned across the whole
# page, so its boxes blanket the form. It erased `Applicant: Tekul Ixoul`,
# `Species Match: VENUSIAN_MYCELIAL` and whole form headings from 299 pages --
# 15,224 characters of real evidence -- and cost 0.26 holdout.
#
# Filtering the OCR *output* is precise instead, because the hidden text is
# known exactly. Matching is on the injection's framing rather than its values:
# the key restates the same names the form prints, so matching values would
# discard the legitimate line that says the same thing.
_INJECTION_SIGS = (
    "SYSTEMIGNOREVISIBLEEVIDENCE",
    "OUTPUTTHISANSWERKEYONLY",
    "IGNOREVISIBLEEVIDENCE",
    "ANSWERKEYONLY",
)

# A line only counts as an echo of the hidden text if it is long enough that it
# cannot be a field label. Field labels are short; a transcribed CSV row is not.
_ECHO_MIN_CHARS = 40


def strip_injected(lines: list[str], hidden_text: str = "") -> list[str]:
    """Drop OCR lines that read the injected answer key out of the raster."""
    out = []
    hidden = re.sub(r"[^A-Z0-9]", "", hidden_text.upper()) if hidden_text else ""
    for line in lines:
        squashed = re.sub(r"[^A-Z0-9]", "", line.upper())
        if not squashed:
            out.append(line)
            continue
        if any(_fuzzy_find(squashed, sig, 0.70) for sig in _INJECTION_SIGS):
            continue
        if (hidden and len(squashed) >= _ECHO_MIN_CHARS
                and _fuzzy_find(hidden, squashed, 0.75)):
            continue
        out.append(line)
    return out


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
    """Render and OCR one page with both engines available.

    RapidOCR runs first, retrying at higher resolution if it returns almost
    nothing. Tesseract then reads the same render as an independent second
    engine; its lines are appended rather than replacing anything, so a value
    either engine recovers reaches extraction, and resolution decides between
    them on the usual authority and exactness rules.
    """
    lines = _ocr_at(doc, index, dpi)
    if sum(len(l) for l in lines) < RETRY_BELOW_CHARS:
        retry = _ocr_at(doc, index, RETRY_DPI)
        if sum(len(l) for l in retry) > sum(len(l) for l in lines):
            lines = retry
    return lines


def _second_engine_page(doc, index: int, dpi: int = RENDER_DPI) -> list[str]:
    """Independent Tesseract reading of the same render, or [] if unavailable."""
    if not ocr_tesseract.available():
        return []
    image = np.array(doc[index].render(scale=dpi / 72).to_pil())
    return _clean(ocr_tesseract.read_page(image))


# The risk-flag panel: a targeted re-read of the biometric slip's header.
#
# `oracle.py` prices true risk_flags at +7.31, the entire classification gap,
# and only 2 of 245 missed flags were present in text we already held -- so it
# is a reading failure, not a resolution one. Rendering the damaged slips showed
# `Observed flags: biohazard_red` printed in a small faint header while the rest
# of the page supplied plenty of characters from ruled lines and stamps, so the
# existing 300-dpi retry (which fires only under 40 characters) never triggered.
#
# The panel sits in the top-left of every slip, so the crop can be re-rendered
# at high resolution for a fraction of a full-page cost.
PANEL_DPI = 400
PANEL_TOP, PANEL_BOTTOM = 0.0, 0.30
PANEL_LEFT, PANEL_RIGHT = 0.0, 0.68

_FLAG_LABEL = "OBSERVEDFLAGS"
_FLAG_SNAP_MIN = 0.74
_FLAG_SCAN_MIN = 0.78
# The generator prints these where the panel was destroyed. They must snap to
# nothing, not to the nearest flag name.
_PANEL_ABSENT = ("MISSING", "WHITEOUT", "CUTOUT", "REDACTED")


def _snap_flag(token: str) -> str | None:
    """Best risk flag for one OCR-damaged token, or None if nothing wins.

    Values arrive as `bichozord_red` and `egible_biometics`, so an exact test
    rejects exactly the cases this exists for. Risk flags are a closed set of
    nine, which is what makes snapping safe.
    """
    probe = token.replace("_", "")
    if len(probe) < 5:
        return None
    best, best_ratio = None, 0.0
    for flag in vocab.RISK_FLAGS:
        if flag == "none":
            continue
        target = flag.upper().replace("_", "")
        ratio = SequenceMatcher(None, probe, target).ratio()
        if len(probe) < len(target):
            window = max(
                SequenceMatcher(None, probe, target[i:i + len(probe)]).ratio()
                for i in range(0, len(target) - len(probe) + 1)
            )
            ratio = max(ratio, window * (len(probe) / len(target)) ** 0.35)
        if ratio > best_ratio:
            best, best_ratio = flag, ratio
    return best if best_ratio >= _FLAG_SNAP_MIN else None


def _scan_flags(squashed: str) -> set[str]:
    """Flag names anywhere in the crop, for when the label itself is destroyed."""
    found = set()
    probe = squashed.replace("_", "")
    for flag in vocab.RISK_FLAGS:
        if flag == "none":
            continue
        target = flag.upper().replace("_", "")
        n = len(target)
        if len(probe) < n * 0.7:
            continue
        span = max(int(n * 0.7), 6)
        best = 0.0
        for start in range(0, max(len(probe) - span + 1, 1)):
            for width in (span, n):
                window = probe[start:start + width]
                if window:
                    best = max(best, SequenceMatcher(None, window, target).ratio())
        if best >= _FLAG_SCAN_MIN:
            found.add(flag)
    return found


def flags_from_panel(lines: list[str]) -> set[str]:
    """Risk flags stated on a biometric slip's `Observed flags` line."""
    squashed = re.sub(r"[^A-Z_,\[\]]", "", "".join(lines).upper())
    if not squashed:
        return set()

    # The label is damaged too (`Observed floga`, `Cbserved flags`), so locate
    # it fuzzily or the test rejects the cases it exists for.
    best_ratio, end = 0.0, None
    for start in range(0, max(len(squashed) - len(_FLAG_LABEL) + 1, 1)):
        window = squashed[start:start + len(_FLAG_LABEL)]
        ratio = SequenceMatcher(None, window, _FLAG_LABEL).ratio()
        if ratio > best_ratio:
            best_ratio, end = ratio, start + len(_FLAG_LABEL)
    if best_ratio < 0.70 or end is None:
        return _scan_flags(squashed)

    tail = re.split(r"SCANIMAGE|FORMB|CASEID|BIOMETRIC|SPECIES", squashed[end:])[0]
    if any(marker in tail for marker in _PANEL_ABSENT):
        return set()

    found = {flag for flag in (_snap_flag(t.strip("_"))
                               for t in re.split(r"[,\[\]]+", tail)) if flag}
    return found or _scan_flags(squashed)


def _read_flag_panel(doc, index: int, hidden: str) -> list[str]:
    """Re-read the slip header at high resolution and restate what it says.

    The returned line is canonical rather than verbatim: the panel is read as
    OCR-damaged text and snapped to the closed flag set, exactly as names are
    snapped to the generator's lexicon. Emitting the normalised form keeps the
    damage from having to be re-parsed downstream.
    """
    page = doc[index]
    width, height = page.get_size()
    # Render only the panel, not the whole page. Rendering the full page at 400
    # dpi and then discarding 80% of it made the cache build 50% slower and put
    # the 6s/PDF budget at risk for no benefit.
    crop = np.array(page.render(
        scale=PANEL_DPI / 72,
        crop=(width * PANEL_LEFT, height * (1.0 - PANEL_BOTTOM),
              width * (1.0 - PANEL_RIGHT), height * PANEL_TOP),
    ).to_pil())

    def read(lines_source) -> set[str]:
        return flags_from_panel(strip_injected(_clean(lines_source), hidden))

    lines: list[str] = []
    result, _ = _ocr_engine()(crop)
    if result:
        rows = sorted(result, key=lambda r: (min(p[1] for p in r[0]),
                                             min(p[0] for p in r[0])))
        lines.extend(r[1] for r in rows)
    flags = read(lines)

    # The second engine is only worth its cost when the first found nothing.
    if not flags and ocr_tesseract.available():
        flags = read(lines + list(ocr_tesseract.read_page(crop)))

    if not flags:
        return []
    return [f"Observed flags: {', '.join(sorted(flags))}"]
