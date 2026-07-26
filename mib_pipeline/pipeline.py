"""Per-case orchestration.

STATUS: skeleton. `process_case` currently returns a fail-closed record so the
Docker contract and scoring loop are exercisable end to end. Replace the marked
section with real evidence gathering.

Reference floor: the challenge's own baseline answers NEEDS_REVIEW for every case
and scores 50.77/150 (extraction 4.95, classification 36.80, calibration 9.02).
Anything below that is a regression.
"""
from __future__ import annotations

import pathlib

from mib_pipeline.schema import Prediction


def process_case(pdf_path: str) -> Prediction | None:
    """Produce a prediction for one packet, or None to omit the case.

    Returning None costs 10/total_cases points (0.01 per case over 1000) and is
    the correct choice when no trustworthy answer exists. Guessing an adjudication
    wrong costs far more: a false approval is -4 raw against +8 for a correct call.
    """
    path = pathlib.Path(pdf_path)
    case_id = path.stem

    # ------------------------------------------------------------------
    # TODO: real pipeline goes here.
    #
    #   evidence = gather_visible_evidence(path)   # render-first; see extract.py
    #   record   = resolve_fields(evidence)
    #   decision = adjudicate(record)              # see adjudicate.py
    #   return Prediction(case_id=case_id, **record, **decision)
    #
    # Recon notes that should shape this:
    #   * ~48% of pages carry only boilerplate text; all evidence is in the raster.
    #   * ~52% carry real digital text (fee receipts, intake forms) whose visible
    #     characters are exact and cheaper than OCR.
    #   * 26.3% of text-layer characters are hidden (white fill or off-crop) and
    #     must never become evidence. 21.6% of packets carry an injected answer
    #     key whose adjudication is wrong 100% of the time (0/216 on train).
    #   * Full render + OCR of every page costs ~1.47s/PDF against a 6s budget.
    # ------------------------------------------------------------------

    return Prediction(
        case_id=case_id,
        adjudication="NEEDS_REVIEW",
        fee_status="unknown",
        risk_flags="none",
        confidence=0.5,
    )
