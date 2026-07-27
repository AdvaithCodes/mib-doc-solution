"""Adjudication: a structured record plus page evidence -> a decision.

Ordering follows FIELD_MANUAL's trusted-evidence precedence. A visible, signed
Manual Adjudicator Note is rank-1 evidence and settles the case on its own; only
in its absence do the published policy rules run.

The design is deliberately fail-closed. Scoring is asymmetric: a correct call is
+8 raw, routing a decided case to NEEDS_REVIEW is +2, missing a true
NEEDS_REVIEW is +1, and a false approval is -4. Approving on thin evidence is the
single most expensive mistake available, so APPROVED requires positive support
and every unresolved doubt routes to review.
"""
from __future__ import annotations

import re
from datetime import date

from mib_pipeline import vocab
from mib_pipeline.evidence import Packet

# "Finding: DENIED." / "Finding DENIED" / a bare decision word on its own line.
_FINDING_RE = re.compile(
    r"\bfinding\b\s*[:\-]?\s*(APPROVED|DENIED|NEEDS[_\s]?REVIEW|REVIEW)", re.I
)
_BARE_DECISION_RE = re.compile(r"^\s*(APPROVED|DENIED|NEEDS[_\s]?REVIEW|REVIEW)\s*$", re.I)

# FIELD_MANUAL traps: these mark a decision mark as decorative or superseded.
_DECOY_RE = re.compile(
    r"\bsample\b|\bcopy\b|\bartifact\b|\bvoid\b|\bspecimen\b|\btemplate\b", re.I
)
_RESCIND_RE = re.compile(r"\brescind|\bcrossed\s*out|\bsupersed|\bstruck", re.I)


def _canon(word: str) -> str:
    w = word.upper().replace(" ", "_")
    if w == "REVIEW":
        return "NEEDS_REVIEW"
    return w


def read_adjudicator_note(packet: Packet) -> tuple[str | None, str]:
    """Return (decision, note_text) from visible rank-1 adjudicator notes.

    Decoy-marked notes are ignored: FIELD_MANUAL is explicit that a "sample
    denial" watermark is not a denial and that a copy artifact is not policy.
    """
    for page in packet.pages:
        if page.doc_type != "adjudicator_note":
            continue
        text = " ".join(page.lines)
        if _DECOY_RE.search(text):
            continue
        m = _FINDING_RE.search(text)
        if m:
            return _canon(m.group(1)), text
        for line in page.lines:
            m = _BARE_DECISION_RE.match(line)
            if m:
                return _canon(m.group(1)), text
    return None, ""


def flags_from_text(text: str) -> list[str]:
    """Named risk flags mentioned anywhere in authoritative visible text."""
    low = text.lower().replace(" ", "_")
    return sorted({f for f in vocab.RISK_FLAGS if f in low})


def _parse_date(value: str) -> date | None:
    try:
        y, m, d = (int(x) for x in value.split("-"))
        return date(y, m, d)
    except Exception:
        return None


def adjudicate(
    record: dict[str, str], packet: Packet, risk_known: bool = True
) -> tuple[str, str]:
    """Return (decision, reason). Reason is retained for calibration and debugging."""
    note_decision, note_text = read_adjudicator_note(packet)
    if note_decision in ("APPROVED", "DENIED", "NEEDS_REVIEW"):
        return note_decision, "adjudicator_note"

    flags = [f for f in record.get("risk_flags", "").split("|") if f and f != "none"]
    visa = record.get("visa_class", "")
    fee = record.get("fee_status", "")
    sponsor = record.get("sponsor_id", "")

    # 1. Disqualifying flags deny outright.
    disqualifying = [f for f in flags if f in vocab.DISQUALIFYING_FLAGS]
    if disqualifying:
        return "DENIED", f"disqualifying_flag:{disqualifying[0]}"

    # 2. TRANSIT-7 carries no work authorisation.
    if visa == "TRANSIT-7":
        return "DENIED", "transit_visa"

    # 3. Revoked sponsor.
    if sponsor in vocab.REVOKED_SPONSORS:
        return "DENIED", "revoked_sponsor"

    # 4. Unpaid fee denies unless a visible waiver applies.
    if fee == "unpaid":
        if not re.search(r"waiver", note_text, re.I):
            return "DENIED", "unpaid_fee"
        return "NEEDS_REVIEW", "unpaid_fee_with_waiver_claim"

    # --- everything below is a doubt, and doubt routes to review ---

    # 5. Missing sponsor, except for diplomatic packets.
    if not sponsor and visa != "DIP-1":
        return "NEEDS_REVIEW", "missing_sponsor"

    # 6. Fee waived outside the diplomatic case needs a visible hardship waiver.
    if fee == "waived" and visa not in vocab.FEE_WAIVER_OK:
        return "NEEDS_REVIEW", "waiver_unverified"

    # 7. Unknown fee status is explicitly a review trigger.
    if fee == "unknown" or not fee:
        return "NEEDS_REVIEW", "fee_unknown"

    # 8. Review-only flags.
    review_flags = [f for f in flags if f in vocab.REVIEW_FLAGS]
    if review_flags:
        return "NEEDS_REVIEW", f"review_flag:{review_flags[0]}"

    # 9. Risk was never actually observed. "No flags read" is not "no flags":
    #    on train, every measured false approval approved on an unread flag
    #    field. Approving there is the -4 case; review costs +2 at worst.
    if not risk_known:
        return "NEEDS_REVIEW", "risk_unobserved"

    # 10. Missing or unreadable arrival date.
    arrival = _parse_date(record.get("arrival_date", ""))
    if arrival is None:
        return "NEEDS_REVIEW", "arrival_date_missing"

    # 11. Any critical field we could not read at all.
    for fieldname in ("applicant_name", "species_code", "home_world", "visa_class"):
        if not record.get(fieldname):
            return "NEEDS_REVIEW", f"missing:{fieldname}"

    # 11. Positive approval: identity, sponsor, fee, visa and risk all clean.
    return "APPROVED", "clean"
