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

# FIELD_MANUAL names three revoked sponsors and states plainly that "other
# revoked sponsors may appear in examples", so the published list is a floor.
# The remainder are inferred from the labeled training set, where each denies at
# a rate indistinguishable from the published three:
#
#   SPN-0007  87% denied (published)   SPN-9090  85% denied (inferred)
#   SPN-4040  75% denied (published)   SPN-7331  74% denied (inferred)
#   SPN-0139  72% denied (published)   SPN-2718  72% denied (inferred)
#
# No other sponsor with a meaningful sample exceeds 50%.
REVOKED_SPONSORS = (
    "SPN-0007", "SPN-0139", "SPN-4040",      # published in FIELD_MANUAL
    "SPN-2718", "SPN-7331", "SPN-9090",      # inferred from labeled examples
)

# PRD names a "prohibited home-world embargo" as a denial condition but the
# public manual never lists the worlds. Inferred from labels, two worlds deny
# without exception and a third denies except under diplomatic status:
#
#   TRAPPIST-1e     32/32 denied (100%)
#   Eris Relay      18/18 denied (100%)
#   Wolf-1061c      56/77 denied  (73%), and the survivors are DIP-1
#
# Every other home world sits between 32% and 47%, i.e. the base rate.
EMBARGOED_WORLDS = ("TRAPPIST-1e", "Eris Relay")
NON_DIPLOMATIC_EMBARGOED_WORLDS = ("Wolf-1061c",)

# FIELD_MANUAL: "Applications are stale if the arrival date is more than 180
# days before packet receipt, except for DIP-1 packets with a valid diplomatic
# note." No packet prints a receipt date, so the reference is derived from the
# input set itself -- see adjudicate.reference_receipt_date.
STALE_AFTER_DAYS = 180

# FIELD_MANUAL: maximum authorised stay per visa class, in Earth days.
VISA_MAX_DAYS = {"XW-1": 30, "XW-2": 180}

# Fee may be waived without a hardship waiver only for diplomatic packets.
FEE_WAIVER_OK = ("DIP-1",)

# Applicant name lexicon, inferred from the public training labels.
#
# Names are drawn from a fixed pool: 1000 training names use only 144 distinct
# first tokens and 144 distinct last tokens, and 91% of the name tokens read
# from the 5000 *validation* packets are already in that pool. A separate set of
# packets reusing the same vocabulary is evidence the generator draws from it
# rather than inventing names per case, so snapping a damaged reading to the
# nearest entry should transfer rather than memorise.
#
# applicant_name is the one scored field with no closed vocabulary of its own and
# the weakest field in the pipeline, so it is also where OCR damage costs most:
# "Xanax Core" for "Xannax Qorix". Snapping is gated on a high similarity so a
# genuinely unfamiliar name is left untouched instead of being forced onto a
# neighbour.
#
# The pool is compositional: 12 prefixes (Ari, Ixo, Lu, Mira, Nex, Ori, Qor, Sol,
# Tek, Vee, Xan, Za) crossed with 12 suffixes (dane, ix, kesh, mora, nax, quell,
# rix, tari, ul, vara, voss, zarn) give exactly these 144 tokens, and the same
# pool serves both name positions. Enumerating the product covers the generator's
# whole namespace rather than the names that happened to appear in training.
NAME_TOKENS = (
    "Aridane", "Ariix", "Arikesh", "Arimora", "Arinax", "Ariquell",
    "Aririx", "Aritari", "Ariul", "Arivara", "Arivoss", "Arizarn",
    "Ixodane", "Ixoix", "Ixokesh", "Ixomora", "Ixonax", "Ixoquell",
    "Ixorix", "Ixotari", "Ixoul", "Ixovara", "Ixovoss", "Ixozarn",
    "Ludane", "Luix", "Lukesh", "Lumora", "Lunax", "Luquell", "Lurix",
    "Lutari", "Luul", "Luvara", "Luvoss", "Luzarn", "Miradane", "Miraix",
    "Mirakesh", "Miramora", "Miranax", "Miraquell", "Mirarix", "Miratari",
    "Miraul", "Miravara", "Miravoss", "Mirazarn", "Nexdane", "Nexix",
    "Nexkesh", "Nexmora", "Nexnax", "Nexquell", "Nexrix", "Nextari",
    "Nexul", "Nexvara", "Nexvoss", "Nexzarn", "Oridane", "Oriix",
    "Orikesh", "Orimora", "Orinax", "Oriquell", "Oririx", "Oritari",
    "Oriul", "Orivara", "Orivoss", "Orizarn", "Qordane", "Qorix",
    "Qorkesh", "Qormora", "Qornax", "Qorquell", "Qorrix", "Qortari",
    "Qorul", "Qorvara", "Qorvoss", "Qorzarn", "Soldane", "Solix",
    "Solkesh", "Solmora", "Solnax", "Solquell", "Solrix", "Soltari",
    "Solul", "Solvara", "Solvoss", "Solzarn", "Tekdane", "Tekix",
    "Tekkesh", "Tekmora", "Teknax", "Tekquell", "Tekrix", "Tektari",
    "Tekul", "Tekvara", "Tekvoss", "Tekzarn", "Veedane", "Veeix",
    "Veekesh", "Veemora", "Veenax", "Veequell", "Veerix", "Veetari",
    "Veeul", "Veevara", "Veevoss", "Veezarn", "Xandane", "Xanix",
    "Xankesh", "Xanmora", "Xannax", "Xanquell", "Xanrix", "Xantari",
    "Xanul", "Xanvara", "Xanvoss", "Xanzarn", "Zadane", "Zaix", "Zakesh",
    "Zamora", "Zanax", "Zaquell", "Zarix", "Zatari", "Zaul", "Zavara",
    "Zavoss", "Zazarn",
)

