"""Closed-set domain vocabularies, inferred from the public labeled examples.

PRD.md: "Candidates are expected to infer missing details from labeled examples."

These are policy vocabularies, not answers. Nothing here is keyed to a case ID,
a filename, or a specific packet; the same tables apply unchanged to unseen data.
That distinction matters for the anti-gaming audit.

Every scored text field except applicant_name and sponsor_id draws from a small
closed set, which is what makes OCR damage recoverable: "wanobot" snaps back to
"xenobotany" and "Wol-1061c" to "Wolf-1061c".
"""
from __future__ import annotations

SPECIES_CODES = (
    "ALPHA_DRACONIAN", "ANDROMEDAN", "AQUARIAN_MANTIS", "ARCTURIAN",
    "CENTAURI_SYNTH", "JOVIAN_GASFORM", "KAIJU_MICRO", "LUNA_SECURID",
    "ORION_GRAYS", "SIRIUS_AVIAN", "TRIANGULAN", "VENUSIAN_MYCELIAL",
)

HOME_WORLDS = (
    "Barnard-c", "Eris Relay", "Europa Station", "Gliese-581g", "Kepler-186f",
    "Luyten-b", "Mars Dome-7", "Proxima-b", "Sirius Outpost", "TRAPPIST-1e",
    "Titan Freeport", "Wolf-1061c", "Zeta Reticuli",
)

DECLARED_PURPOSES = (
    "archive audit", "cultural exchange", "diplomatic", "field repair",
    "medical consult", "reactor maintenance", "research", "transit",
    "translation", "xenobotany",
)

VISA_CLASSES = ("XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7")

FEE_STATUSES = ("paid", "waived", "unpaid", "unknown")

# FIELD_MANUAL: disqualifying flags force DENIED.
DISQUALIFYING_FLAGS = (
    "memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red",
)

# FIELD_MANUAL: review-only flags route to NEEDS_REVIEW; combinations can deny.
REVIEW_FLAGS = (
    "identity_conflict", "sponsor_mismatch", "illegible_biometrics",
    "rescinded_denial",
)

RISK_FLAGS = DISQUALIFYING_FLAGS + REVIEW_FLAGS

# FIELD_MANUAL names three revoked sponsors and warns that others appear in
# examples, so this is a floor rather than a complete list.
REVOKED_SPONSORS = ("SPN-0007", "SPN-0139", "SPN-4040")

# FIELD_MANUAL: maximum authorised stay per visa class, in Earth days.
VISA_MAX_DAYS = {"XW-1": 30, "XW-2": 180}

# Fee may be waived without a hardship waiver only for diplomatic packets.
FEE_WAIVER_OK = ("DIP-1",)
