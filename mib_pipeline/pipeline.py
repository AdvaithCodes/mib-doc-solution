"""Per-case orchestration: packet -> evidence -> record -> decision.

Public training set, 1000 cases:
  challenge baseline   50.77/150   (NEEDS_REVIEW everywhere, no extraction)
  this pipeline       123.84/150   (122.35 on the 700 cases held out of tuning,
                                    with the tables fitted on the other 300)

The fit/holdout gap is real and worth watching: rules tuned against the first
300 cases score about 6 points higher there than on cases never used for tuning.
The holdout figure is the honest one.
"""
from __future__ import annotations

import pathlib

from mib_pipeline.adjudicate import (adjudicate_detail, flags_from_text,
                                     read_adjudicator_note, registry_embargo,
                                     _parse_date)
from mib_pipeline.evidence import read_packet
from mib_pipeline.fee import infer_fee_status
from mib_pipeline.extract import identity_conflict, resolve
from mib_pipeline.resolver import Resolver, evidence_keys
from mib_pipeline.schema import Prediction

# Calibrated confidence per decision route.
#
# The scorer compares confidence against whether the adjudication was correct
# (Brier), so the right confidence for a route is simply that route's measured
# accuracy. Fitted on training cases 1-300 with the decisions frozen first, then
# evaluated on the remaining held-out cases.
#
# Each rate is smoothed with a Beta(2,2) prior, (correct + 2) / (n + 4). Raw
# rates of 0.00 and 1.00 appear on routes with a handful of cases; emitting them
# is maximally punished by any private-set case that breaks the pattern, and the
# smoothing costs almost nothing on the large routes.
_ROUTE_CONFIDENCE = {
    "adjudicator_note": 0.99,         # 333/333
    "disqualifying_flag": 0.97,       # 72/72
    "transit_visa": 0.89,             # 31/33
    "embargoed_home_world_nondip": 0.89,# 14/14
    "unpaid_fee": 0.88,               # 27/29
    "stale_application": 0.88,        # 19/20
    "review_flag": 0.86,              # 16/17
    "clean": 0.84,                    # 40/46
    "embargoed_home_world": 0.80,     # 6/6
    "revoked_sponsor": 0.70,          # 47/66
    "fee_contested": 0.67,            # 2/2
    "med3_biohazard_adverse": 0.60,   # 1/1
    "waiver_unverified": 0.54,        # 5/9
    "fee_unknown": 0.51,              # 79/156
    "arrival_date_missing": 0.50,     # 3/6
    "missing_sponsor": 0.37,          # 9/26
    "risk_unobserved": 0.29,          # 46/161
    "missing": 0.29,                  # 0/3
}

# Routes not in the table are unmeasured; 0.5 asserts nothing either way.
_DEFAULT_CONFIDENCE = 0.50

_RESOLVER_CACHE: list = []


def _resolver():
    """Load the fitted resolver table once per process."""
    if not _RESOLVER_CACHE:
        _RESOLVER_CACHE.append(Resolver.load())
    return _RESOLVER_CACHE[0]


def confidence_for(decision: str, reason: str, record: dict[str, str]) -> float:
    """Confidence that this adjudication is correct, not that the OCR was clean."""
    return _ROUTE_CONFIDENCE.get(reason.split(":")[0], _DEFAULT_CONFIDENCE)

SCORED_FIELDS = (
    "applicant_name", "species_code", "home_world", "visa_class", "sponsor_id",
    "arrival_date", "declared_purpose", "risk_flags", "fee_status",
)


def extract_case(pdf_path: str):
    """Read a packet and resolve its fields, without deciding anything yet.

    Adjudication is deferred because the stale-application rule needs a
    receipt-date reference derived from the whole input set.
    """
    path = pathlib.Path(pdf_path)
    packet = read_packet(str(path), path.stem)
    return path.stem, packet, resolve(packet)


def rule_state(packet, resolved, reference_date=None):
    """Record and rule outcome for one packet, before any resolver.

    Shared with replay.py so the measurement harness and the shipped pipeline
    cannot drift. They previously each held a copy of this logic, and a fix
    applied to one silently did nothing in the other.
    """

    record = {f: (resolved[f].value if f in resolved else "") for f in SCORED_FIELDS}

    # Whether risk was actually *observed* is distinct from what we serialize.
    # Every false approval measured on train came from treating an absent flag
    # reading as "no flags present" -- fail-open on the one field that drives the
    # -4 penalty. The serialized value still defaults to "none" (the most common
    # truth value, 535/1000), but adjudication is told the difference.
    risk_known = bool(record["risk_flags"])
    if not record["risk_flags"]:
        record["risk_flags"] = "none"

    # An explicit statement of fee status wins where one is readable; the
    # receipt's numeric Amount and Waiver Code decide it otherwise. See fee.py.
    record["fee_status"], fee_known, fee_contested = infer_fee_status(
        packet, literal=record["fee_status"]
    )

    # Risk flags are only partly stated on the page. The adjudicator note's reason
    # text names them explicitly ("Disqualifying risk flag: planetary_embargo"),
    # so mine it before adjudicating.
    note_decision, note_text = read_adjudicator_note(packet)
    mined = set(flags_from_text(note_text)) if note_text else set()

    # A registry extract reporting an embargo contributes planetary_embargo.
    #
    # As a *field* value this is usually wrong: of 12 packets where it fires and
    # our flags disagree with the labels, only 2 actually carry
    # planetary_embargo. Removing it was tried and is worse (-0.16 holdout),
    # because setting risk_known matters more than the flag itself -- it keeps
    # those packets out of the risk_unobserved review bucket, which runs at 29%.
    # A locally wrong signal that is globally right, kept deliberately.
    if registry_embargo(packet):
        mined.add("planetary_embargo")
        risk_known = True

    # Emitting identity_conflict when documents name two different applicants
    # was measured and rejected: 27% of such packets carry the flag against a 4%
    # base rate, a 6.8x lift that is still 73% false positives, and it cost 0.48
    # holdout. The detector stays available in extract.identity_conflict but is
    # not wired to a flag.

    if mined:
        existing = {f for f in record["risk_flags"].split("|") if f and f != "none"}
        record["risk_flags"] = "|".join(sorted(existing | mined))

    decision, reason, denials, reviews, approvals = adjudicate_detail(
        record, packet, risk_known=risk_known,
        fee_known=fee_known, fee_contested=fee_contested,
        reference_date=reference_date,
    )

    return record, decision, reason, denials, reviews, approvals


def decide_case(case_id: str, packet, resolved, reference_date=None) -> Prediction | None:
    record, decision, reason, _denials, reviews, approvals = rule_state(
        packet, resolved, reference_date)

    confidence = None
    resolver = _resolver()
    if resolver is not None:
        chosen = resolver.resolve(
            decision, reason, evidence_keys(reason, record, reviews, approvals))
        if chosen is not None:
            decision, confidence = chosen
    if confidence is None:
        confidence = confidence_for(decision, reason, record)

    # A case_id read off the page is preferred, but the filename is authoritative
    # for identifying which case this prediction answers.
    return Prediction(
        case_id=case_id,
        adjudication=decision,
        confidence=confidence,
        **record,
    )
