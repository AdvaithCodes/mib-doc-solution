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
from datetime import date
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
        # Four short, mutually distinct options, reached only via a fee-status
        # label, so context is already tight. OCR damage on words this short
        # drops the similarity ratio hard -- "pold"/"paid" and "peld"/"paid"
        # both score 0.50 -- so the threshold has to sit below that to recover
        # them. Competing options score lower still, and the best match wins.
        return snap(value, vocab.FEE_STATUSES, threshold=0.45)

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
        if not 1900 <= y <= 2100:
            return None
        try:
            # A real calendar date, not just plausible digits. OCR turns "30"
            # into "31" happily, and the submission schema requires
            # format: date -- "2026-06-31" fails validation and would take the
            # whole file down with it.
            date(y, mo, d)
        except ValueError:
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


# Fields recoverable from a page without a usable label, because the value
# itself is recognisable: a closed vocabulary, or a strict identifier pattern.
_SWEEPABLE = ("species_code", "home_world", "declared_purpose", "visa_class",
              "sponsor_id", "arrival_date", "applicant_name")

# Sponsor letters name the applicant in prose rather than as a labelled field:
#   "Sponsor SPN-2887 attests that Ixotari Tekrix is expected on Earth for ..."
# Registry extracts use a "Registry Name" label the alias table already covers.
_PROSE_NAME_RE = re.compile(
    r"attests?\s+that\s+([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){1,2})\s+is\b"
)


def sweep_page(page: Page, wanted: set[str]) -> dict[str, Candidate]:
    """Recover values from a page whose labels are too damaged to parse.

    OCR fuses labels onto values with no separator at all --
    "SpeclesCodaerLUNA_SECURID" -- and the labelled parser drops the line
    entirely even though LUNA_SECURID is sitting in plain sight. Here the value
    is matched directly, which works because every sweepable field is either a
    closed vocabulary or a strict identifier pattern.

    This runs only for fields no labelled candidate produced, and its results
    are ranked below any labelled reading from the same page, so a clean
    "Species Code: X" always wins over a sweep that finds Y elsewhere.
    """
    found: dict[str, Candidate] = {}
    blob = " ".join(page.lines)
    squashed = _norm(blob)

    def add(fieldname: str, value: str) -> None:
        # +1 keeps a swept value below a labelled one at the same authority.
        found[fieldname] = Candidate(
            value, page.authority + 1, page.exact, page.number, page.doc_type)

    for fieldname in wanted & set(_SWEEPABLE):
        if fieldname == "sponsor_id":
            m = _SPONSOR_RE.search(blob)
            if m:
                add(fieldname, f"SPN-{m.group(1)}")
        elif fieldname == "arrival_date":
            value = normalize_field("arrival_date", blob)
            if value:
                add(fieldname, value)
        elif fieldname == "applicant_name":
            m = _PROSE_NAME_RE.search(blob)
            if m:
                value = normalize_field("applicant_name", m.group(1))
                if value:
                    add(fieldname, value)
        else:
            options = {"species_code": vocab.SPECIES_CODES,
                       "home_world": vocab.HOME_WORLDS,
                       "declared_purpose": vocab.DECLARED_PURPOSES,
                       "visa_class": vocab.VISA_CLASSES}[fieldname]
            # Exact containment only: a fuzzy match against a whole page of text
            # would fire on almost anything.
            for option in options:
                if _norm(option) and _norm(option) in squashed:
                    add(fieldname, option)
                    break
    return found


# Two readings this similar are the same value seen twice, not a disagreement.
_SAME_VALUE_RATIO = 0.6


def _prefer_clean_reading(chosen: Candidate, all_of_them: list[Candidate]) -> Candidate:
    """Upgrade a damaged reading to an exact one of the same value.

    An applicant's name appears on the intake form, the registry extract, the
    sponsor letter and the biometric slip. Authority decides which page wins a
    *conflict*, but when a damaged OCR page and a clean digital page name the
    same person there is no conflict -- only one accurate reading and one
    corrupted one. Taking "Qorvoss Qomora" from a mangled intake form over
    "Qorvoss Qormora" from an exact registry extract is losing information for
    no reason.

    Similarity is required, so a genuine second applicant in the packet still
    loses to the authoritative page rather than silently replacing it.
    """
    if chosen.exact:
        return chosen
    for other in all_of_them:
        if not other.exact:
            continue
        if _ratio(_norm(other.value), _norm(chosen.value)) >= _SAME_VALUE_RATIO:
            return other
    return chosen


def resolve(packet: Packet) -> dict[str, Candidate]:
    """Collapse per-page candidates into one record using source authority."""
    best: dict[str, Candidate] = {}
    seen: dict[str, list[Candidate]] = {}
    for page in packet.by_authority():
        for fieldname, candidates in parse_page(page).items():
            for cand in candidates:
                seen.setdefault(fieldname, []).append(cand)
                if cand.better_than(best.get(fieldname)):
                    best[fieldname] = cand

    # Names are the only open-vocabulary field, so they cannot be snapped back
    # to a legal value the way species codes and home worlds can. A cross-page
    # clean reading is the closest available equivalent.
    if "applicant_name" in best:
        best["applicant_name"] = _prefer_clean_reading(
            best["applicant_name"], seen.get("applicant_name", []))

    missing = {f for f in _SWEEPABLE if f not in best}
    if missing:
        for page in packet.by_authority():
            for fieldname, cand in sweep_page(page, missing).items():
                if cand.better_than(best.get(fieldname)):
                    best[fieldname] = cand
    return best
