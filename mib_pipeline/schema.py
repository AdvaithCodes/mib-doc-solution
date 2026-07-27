"""Prediction record shape and serialization.

Mirrors schemas/submission.schema.json in the challenge repository. Keeping this
in one place means an invalid record cannot reach the output file.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field

ADJUDICATIONS = ("APPROVED", "DENIED", "NEEDS_REVIEW")

# Schema-constrained fields, from schemas/submission.schema.json.
SPONSOR_RE = re.compile(r"^SPN-[0-9]{4}$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

# Placeholders for unread constrained fields. Deliberately implausible so they
# read as "not recovered" rather than as a real sponsor or arrival date.
SENTINEL_SPONSOR = "SPN-0000"
SENTINEL_DATE = "1900-01-01"
FEE_STATUSES = ("paid", "waived", "unpaid", "unknown")

# Raw extraction weights, from EVALUATION.md. Useful for prioritising work:
# risk_flags alone is worth more than arrival_date and declared_purpose combined.
FIELD_WEIGHTS = {
    "applicant_name": 5,
    "species_code": 6,
    "home_world": 5,
    "visa_class": 5,
    "sponsor_id": 5,
    "arrival_date": 4,
    "declared_purpose": 3,
    "risk_flags": 8,
    "fee_status": 4,
}


@dataclass
class Prediction:
    case_id: str
    applicant_name: str = ""
    species_code: str = ""
    home_world: str = ""
    visa_class: str = ""
    sponsor_id: str = ""
    arrival_date: str = ""
    declared_purpose: str = ""
    risk_flags: str = "none"
    fee_status: str = "unknown"
    adjudication: str = "NEEDS_REVIEW"
    confidence: float = 0.5

    def validate(self) -> list[str]:
        """Return a list of contract violations; empty means the record is valid."""
        problems = []
        if not self.case_id:
            problems.append("empty case_id")
        if self.adjudication not in ADJUDICATIONS:
            problems.append(f"bad adjudication {self.adjudication!r}")
        if self.fee_status not in FEE_STATUSES:
            problems.append(f"bad fee_status {self.fee_status!r}")
        if not isinstance(self.confidence, (int, float)):
            problems.append("confidence not numeric")
        elif not 0.0 <= float(self.confidence) <= 1.0:
            problems.append(f"confidence {self.confidence} out of range")
        if not self.risk_flags:
            problems.append("risk_flags empty (use 'none')")
        return problems

    def finalize(self) -> "Prediction":
        """Fill schema-required placeholders for fields we could not read.

        schemas/submission.schema.json constrains two fields beyond "string":
        sponsor_id must match ^SPN-[0-9]{4}$ and arrival_date must be a date.
        An empty string fails both, and scripts/validate_submission.py exits 2
        on the whole submission -- so an unread field cannot simply be blank.

        Scoring is unaffected: a sentinel is wrong in exactly the same way an
        empty string is. This runs after adjudication, never before, so a
        sentinel can never be mistaken for a sponsor that exists or a date that
        was actually read.
        """
        if not SPONSOR_RE.match(self.sponsor_id or ""):
            self.sponsor_id = SENTINEL_SPONSOR
        if not DATE_RE.match(self.arrival_date or ""):
            self.arrival_date = SENTINEL_DATE
        return self

    def to_json_line(self) -> str:
        d = asdict(self)
        d["confidence"] = round(float(d["confidence"]), 4)
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))
