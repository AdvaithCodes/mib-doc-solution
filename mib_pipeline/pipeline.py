"""Per-case orchestration: packet -> evidence -> record -> decision.

Adjudication is not wired in yet; every case currently routes to NEEDS_REVIEW so
extraction can be measured on its own. Reference points on the public train set:
  challenge baseline  50.77/150   (NEEDS_REVIEW everywhere, no extraction)
  contract skeleton   51.75/150
"""
from __future__ import annotations

import pathlib

from mib_pipeline.adjudicate import adjudicate, flags_from_text, read_adjudicator_note
from mib_pipeline.evidence import read_packet
from mib_pipeline.extract import resolve
from mib_pipeline.schema import Prediction

# Provisional confidence per decision route. These are placeholders: calibration
# is a separate stage that fits these against measured accuracy once decisions
# are frozen, which is the only way the 20-point Brier section pays out.
_ROUTE_CONFIDENCE = {
    "adjudicator_note": 0.90,
    "disqualifying_flag": 0.85,
    "transit_visa": 0.80,
    "revoked_sponsor": 0.80,
    "unpaid_fee": 0.75,
    "clean": 0.70,
}


def confidence_for(decision: str, reason: str, record: dict[str, str]) -> float:
    key = reason.split(":")[0]
    return _ROUTE_CONFIDENCE.get(key, 0.55)

SCORED_FIELDS = (
    "applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
    "arrival_date", "declared_purpose", "risk_flags", "fee_status",
)


def process_case(pdf_path: str) -> Prediction | None:
    path = pathlib.Path(pdf_path)
    case_id = path.stem

    packet = read_packet(str(path), case_id)
    resolved = resolve(packet)

    record = {f: (resolved[f].value if f in resolved else "") for f in SCORED_FIELDS}

    # Whether risk was actually *observed* is distinct from what we serialize.
    # Every false approval measured on train came from treating an absent flag
    # reading as "no flags present" -- fail-open on the one field that drives the
    # -4 penalty. The serialized value still defaults to "none" (the most common
    # truth value, 535/1000), but adjudication is told the difference.
    risk_known = bool(record["risk_flags"])
    if not record["risk_flags"]:
        record["risk_flags"] = "none"
    if not record["fee_status"]:
        record["fee_status"] = "unknown"

    # Risk flags are only partly stated on the page. The adjudicator note's reason
    # text names them explicitly ("Disqualifying risk flag: planetary_embargo"),
    # so mine it before adjudicating.
    note_decision, note_text = read_adjudicator_note(packet)
    if note_text:
        mined = flags_from_text(note_text)
        if mined:
            existing = {f for f in record["risk_flags"].split("|") if f and f != "none"}
            record["risk_flags"] = "|".join(sorted(existing | set(mined)))

    decision, reason = adjudicate(record, packet, risk_known=risk_known)

    # A case_id read off the page is preferred, but the filename is authoritative
    # for identifying which case this prediction answers.
    return Prediction(
        case_id=case_id,
        adjudication=decision,
        confidence=confidence_for(decision, reason, record),
        **record,
    )
