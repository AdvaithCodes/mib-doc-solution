"""Fee status inference.

The literal "Fee Status" value is the least reliable thing on a fee receipt: it
is a short word that OCR mangles into "pold", "peld" or "walved", too damaged for
vocabulary snapping to recover safely. The Amount and Waiver Code fields on the
same page are numeric and structured, and they determine the answer outright.

Measured on 200 training cases:

    amount > 0                    51 cases   100% paid
    amount = 0 + waiver code      23 cases   100% waived
    amount = 0 + no waiver code   11 cases   6 unpaid / 5 unknown  -> contested
    no amount readable           115 cases   fall back to the literal reading

A zero fee with no waiver code backing it is contradictory evidence, and
FIELD_MANUAL routes contradictions to review rather than to a denial.
"""
from __future__ import annotations

import re

from mib_pipeline.evidence import Packet

_AMOUNT_RE = re.compile(r"amount\s*:?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)
_WAIVER_RE = re.compile(r"waiver\s*code\s*:?\s*([A-Za-z0-9\-/\.]+)", re.I)
# "Manual correction: fee status is paid." on a rank-1 adjudicator note.
_CORRECTION_RE = re.compile(
    r"fee\s*status\s*(?:is|:)?\s*(paid|waived|unpaid|unknown)", re.I
)
_UNPAID_RE = re.compile(r"mandatory\s*fee\s*unpaid|fee\s*unpaid|unpaid\s*fee", re.I)

_NULL_WAIVERS = {"", "N/A", "NA", "NONE", "NIL", "-", "--"}


def _amount(blob: str) -> float | None:
    m = _AMOUNT_RE.search(blob)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _waiver_code(blob: str) -> str | None:
    m = _WAIVER_RE.search(blob)
    if not m:
        return None
    code = m.group(1).strip().upper().rstrip(".")
    return None if code in _NULL_WAIVERS else code


def infer_fee_status(packet: Packet, literal: str = "") -> tuple[str, bool, bool]:
    """Return (fee_status, known, contested).

    `known` is False when nothing readable supported the answer, which keeps the
    adjudicator from approving on an unread fee. `contested` marks contradictory
    evidence, which routes to review rather than to a denial.
    """
    # A signed adjudicator note outranks the receipt: it is rank-1 evidence and
    # exists precisely to correct what the form says.
    for page in packet.pages:
        if page.doc_type != "adjudicator_note":
            continue
        note = " ".join(page.all_lines)
        m = _CORRECTION_RE.search(note)
        if m:
            return m.group(1).lower(), True, False
        if _UNPAID_RE.search(note):
            return "unpaid", True, False

    blob = " ".join(l for p in packet.pages for l in p.all_lines)
    amount = _amount(blob)

    if amount is not None:
        if amount > 0:
            return "paid", True, False
        code = _waiver_code(blob)
        if code:
            return "waived", True, False
        # Zero owed, nothing authorising the waiver: contradictory.
        return "unpaid", True, True

    if literal:
        return literal, True, False
    return "unknown", False, False
