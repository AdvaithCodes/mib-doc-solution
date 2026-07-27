"""Field extraction: visible evidence lines -> a structured applicant record.

Two mechanisms carry most of the accuracy:

* Fuzzy label matching. OCR mangles the label as often as the value; the training
  survey shows "arival date" 9 times, plus "cose id", "applcant", "speciesmatch".
  Exact label matching would silently drop those rows.

* Closed-set snapping. Every scored text field except applicant_name and
  sponsor_id is drawn from a small vocabulary, so a damaged value can be pulled
  back to the nearest legal one ("wanobot" -> "xenobotany").

Candidates keep the authority of the page they came from, and resolution prefers
the most authoritative source per FIELD_MANUAL. Exact text-layer readings beat
OCR readings at equal authority.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from mib_pipeline import vocab
from mib_pipeline.evidence import Packet, Page

# Canonical field -> label spellings we expect to see. Matching is fuzzy, so
# these are anchors rather than an exhaustive list of OCR corruptions.
LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "case_id": ("case id", "caseid", "case"),
    "applicant_name": ("applicant", "applicant name", "name"),
    "species_code": ("species code", "speciescode", "species"),
    "home_world": ("home world", "homeworld", "world", "origin"),
    "visa_class": ("visa class", "visaclass", "visa"),
    "sponsor_id": ("sponsor id", "sponsorid", "sponsor"),
    "arrival_date": ("arrival date", "arrivaldate", "arival date", "arrival"),
    "declared_purpose": ("declared purpose", "declaredpurpose", "purpose"),
    "fee_status": ("fee status", "feestatus", "fee"),
    "risk_flags": ("observed flags", "observedflags", "flags", "risk flags"),
    # Non-scored fields that still inform adjudication.
    "finding": ("finding", "decision", "determination"),
    "reason": ("reason", "note", "remarks"),
    "manual_correction": ("manual correction", "correction"),
    "biometric_confidence": ("biometric confidence", "biometricconfidence"),
    "species_match": ("species match", "speciesmatch"),
    "waiver_code": ("waiver code", "waivercode", "waiver"),
    "amount": ("amount", "fee amount"),
    "barcode_payload": ("barcode payload", "barcodepayload", "barcode"),
}

_PAIR_RE = re.compile(r"^\s*([A-Za-z][A-Za-z /'\-\.]{1,30})\s*[:∶;]\s*(.*)$")
_SPONSOR_RE = re.compile(r"\bSP[NM]?[\s\-–—]?(\d{4})\b", re.I)
_DATE_RE = re.compile(r"\b(\d{4})[\-/\.](\d{1,2})[\-/\.](\d{1,2})\b")
_CASE_RE = re.compile(r"\bMIB[\s\-]?(\d{6})\b", re.I)


@dataclass
class Candidate:
    value: str
    authority: int
    exact: bool
    page: int
    doc_type: str

    def better_than(self, other: "Candidate | None") -> bool:
        if other is None:
            return True
        if self.authority != other.authority:
            return self.authority < other.authority
        if self.exact != other.exact:
            return self.exact  # exact text beats OCR at equal authority
        return self.page < other.page


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def match_label(raw: str) -> str | None:
    """Map a possibly-mangled label to a canonical field name."""
    key = _norm(raw)
    if not key or len(key) > 32:
        return None
    best, best_score = None, 0.0
    for fieldname, aliases in LABEL_ALIASES.items():
        for alias in aliases:
            score = _ratio(key, _norm(alias))
            if score > best_score:
                best, best_score = fieldname, score
    # 0.82 keeps "arivaldate"/"arrivaldate" (0.95) and "applcant"/"applicant"
    # (0.94) while rejecting unrelated headings.
    return best if best_score >= 0.82 else None


def snap(value: str, options, threshold: float = 0.6) -> str | None:
    """Pull a damaged value back to the nearest vocabulary entry."""
    key = _norm(value)
    if not key:
        return None
    best, best_score = None, 0.0
    for opt in options:
        score = _ratio(key, _norm(opt))
        # Substring containment rescues run-together OCR such as
        # "SpeclesCodaerLUNA_SECURID", where the value is embedded in noise.
        if _norm(opt) in key:
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = opt, score
    return best if best_score >= threshold else None


def normalize_field(fieldname: str, value: str) -> str | None:
    """Coerce a raw reading into the canonical form the scorer compares against."""
    value = value.strip().strip(".,;|")
    if not value:
        return None

    if fieldname == "species_code":
        return snap(value, vocab.SPECIES_CODES)
    if fieldname == "home_world":
        return snap(value, vocab.HOME_WORLDS)
    if fieldname == "declared_purpose":
        return snap(value, vocab.DECLARED_PURPOSES)
    if fieldname == "visa_class":
        return snap(value, vocab.VISA_CLASSES, threshold=0.55)
    if fieldname == "fee_status":
        return snap(value, vocab.FEE_STATUSES, threshold=0.55)

    if fieldname == "sponsor_id":
        m = _SPONSOR_RE.search(value)
        return f"SPN-{m.group(1)}" if m else None

    if fieldname == "case_id":
        m = _CASE_RE.search(value)
        return f"MIB-{m.group(1)}" if m else None

    if fieldname == "arrival_date":
        m = _DATE_RE.search(value)
        if not m:
            return None
        y, mo, d = (int(x) for x in m.groups())
        if not (1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
            return None
        return f"{y:04d}-{mo:02d}-{d:02d}"

    if fieldname == "risk_flags":
        found = []
        for flag in vocab.RISK_FLAGS:
            if _norm(flag) in _norm(value):
                found.append(flag)
        if not found:
            # "none" is a legitimate reading, distinct from "nothing found".
            return "none" if _ratio(_norm(value), "none") > 0.8 else None
        return "|".join(sorted(set(found)))

    if fieldname == "applicant_name":
        # Names are the one open field. Keep letters, spaces and hyphens; reject
        # readings that are mostly noise.
        cleaned = re.sub(r"[^A-Za-z\s'\-]", " ", value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) < 4 or " " not in cleaned:
            return None
        return " ".join(w.capitalize() for w in cleaned.split())

    return value


def split_label_value(line: str) -> tuple[str, str] | None:
    """Separate a line into (canonical_field, raw_value).

    Two layouts occur and both must be handled:

      "Applicant:Miraquell Ixovara"   OCR pages keep the colon
      "Applicant Miraix Veerix"       digital-text pages lay the value out in a
                                      second column, so extract_text() yields no
                                      separator at all

    Missing the second form costs the exact-text pages, which are the accurate
    half of the corpus.
    """
    m = _PAIR_RE.match(line)
    if m:
        fieldname = match_label(m.group(1))
        if fieldname:
            return fieldname, m.group(2)

    # Colon-free: try the longest leading word run that resolves to a label.
    words = line.split()
    for take in range(min(3, len(words) - 1), 0, -1):
        fieldname = match_label(" ".join(words[:take]))
        if fieldname:
            return fieldname, " ".join(words[take:])
    return None


def parse_page(page: Page) -> dict[str, list[Candidate]]:
    """Pull labelled values out of one page's visible lines."""
    found: dict[str, list[Candidate]] = {}
    for line in page.lines:
        pair = split_label_value(line)
        if not pair:
            continue
        fieldname, raw = pair
        value = normalize_field(fieldname, raw)
        if value is None:
            continue
        found.setdefault(fieldname, []).append(
            Candidate(value, page.authority, page.exact, page.number, page.doc_type)
        )
    return found


def resolve(packet: Packet) -> dict[str, Candidate]:
    """Collapse per-page candidates into one record using source authority."""
    best: dict[str, Candidate] = {}
    for page in packet.by_authority():
        for fieldname, candidates in parse_page(page).items():
            for cand in candidates:
                if cand.better_than(best.get(fieldname)):
                    best[fieldname] = cand
    return best
