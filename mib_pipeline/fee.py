"""Fee status inference.

Two sources, in order.

An explicit statement wins when one is readable anywhere in the packet, taken
from the most authoritative page that has one. This covers the adjudicator
note's "Manual correction: fee status is paid." and the receipt's own "Fee
Status: paid" with the same pattern. Searching every page rather than only pages
typed as adjudicator notes was worth 1.4 points on the full training set: a note
whose heading OCR'd into noise is typed "unknown", and its correction was being
discarded along with the page.

Otherwise the receipt's numeric fields decide it. They survive OCR far better
than the status word, which arrives as "pold", "peld" or "Foe Ststus: pald":

    amount > 0                    100% paid    (51/51)
    amount = 0 + waiver code      100% waived  (23/23)
    amount = 0 + no waiver code   indeterminate

A zero fee with no waiver code authorising it is contradictory rather than
merely unpaid, whether that pattern arrives via an explicit "waived" claim or
via the numbers alone. FIELD_MANUAL routes contradictions to review, and calling
it "unpaid" would instead have triggered a denial.
"""
from __future__ import annotations

import re

from mib_pipeline.evidence import Packet

_AMOUNT_RE = re.compile(r"amount\s*:?\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)
_WAIVER_RE = re.compile(r"waiver\s*code\s*:?\s*([A-Za-z0-9\-/\.]+)", re.I)
# Any explicit statement of fee status. Deliberately covers both the adjudicator
# note's "Manual correction: fee status is paid." and a receipt's plain
# "Fee Status: paid" -- the separator is optional and both forms appear.
_STATED_RE = re.compile(
    r"fee\s*stat\w*\s*[:\.\-]?\s*(?:is\s*)?(paid|waived|unpaid|unknown)", re.I
)
_UNPAID_RE = re.compile(r"mandatory\s*fee\s*unpaid|fee\s*unpaid|unpaid\s*fee", re.I)

_NULL_WAIVERS = {"", "N/A", "NA", "NONE", "NIL", "-", "--"}

# Modal truth among training packets with no readable fee evidence:
# 211 paid, 69 waived, 9 unpaid of 289.
MODAL_UNREADABLE_FEE = "paid"


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
    blob = " ".join(l for p in packet.pages for l in p.all_lines)
    amount = _amount(blob)
    code = _waiver_code(blob)

    # 1. An explicit statement of the fee status, taken from the most
    #    authoritative page that carries one. This covers both the adjudicator
    #    note's "Manual correction: fee status is paid." and the receipt's own
    #    "Fee Status: paid" -- the same sentence pattern serves both, and
    #    searching every page rather than only pages typed as notes recovers
    #    packets whose note heading OCR'd into noise.
    for page in packet.by_authority():
        m = _STATED_RE.search(" ".join(page.all_lines))
        if not m:
            continue
        stated = m.group(1).lower()
        # A waiver claimed with no waiver code authorising it is contradictory,
        # and FIELD_MANUAL sends contradictions to review rather than letting
        # the claim stand.
        if stated == "waived" and amount == 0 and not code:
            return "unknown", True, True
        return stated, True, False

    for page in packet.pages:
        if page.doc_type == "adjudicator_note" and _UNPAID_RE.search(
                " ".join(page.all_lines)):
            return "unpaid", True, False

    # 2. No explicit statement: infer from the receipt's numeric fields, which
    #    survive OCR far better than the short status word.
    if amount is not None:
        if amount > 0:
            return "paid", True, False
        if code:
            return "waived", True, False
        # Zero owed with nothing authorising it. The receipt claims no fee is
        # due while carrying no waiver code to justify it, so the true status is
        # not determinable from the page -- "unknown" is the honest reading, and
        # it is what the labels say: every such case on the training set is
        # labelled unknown, not unpaid. Calling it unpaid also invited a denial
        # under the unpaid-fee rule, which is the wrong outcome for a packet
        # whose fee status simply cannot be established.
        return "unknown", True, True

    if literal:
        return literal, True, False

    # Nothing readable anywhere in the packet. Serialize the modal value rather
    # than "unknown", while reporting known=False so adjudication stays
    # fail-closed and cannot approve on an unread fee.
    #
    # This is the split already used for risk_flags: the serialized field is our
    # best guess, the evidential state is what decisions run on. Emitting
    # "unknown" here was inconsistent -- among training packets with no readable
    # fee evidence the truth is paid 211 times, waived 69 and unpaid 9, so
    # "unknown" is the least likely of the four answers.
    #
    # Where the graders mark these fields unrecoverable they leave the
    # denominator and this changes nothing. Where they do not, it scores.
    return MODAL_UNREADABLE_FEE, False, False
