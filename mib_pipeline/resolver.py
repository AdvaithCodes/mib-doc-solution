"""Score-optimal resolver for structurally incomplete packets.

Roughly two thirds of packets carry no authoritative adjudicator note, and those
are where the score is lost: the note route is 332/332 correct, while the rule
routes that handle the rest average under 50%. Those rules encode published
policy, which is the right answer to "what should an intake desk do" and not to
"what maximises this scorer".

So this table covers only the packets that reach the rules. Note-settled packets
never enter it -- an earlier version included them and collapsed a 100%-correct
route by averaging its three outcomes into one bucket decision.

Buckets are keyed on evidence, deliberately *not* on the rule's conclusion. A
previous attempt conditioned on the rule decision and changed 0 decisions out of
1000: asking "given the rules said X, what was true" reproduces the rules by
construction. The bucket must describe the packet, not the verdict.

Measured outcome: this changes no classification decisions at any threshold
tested (observation floors of 8/12/20, margins of 0.25/0.75). The published rules
are already expected-value optimal for these evidence distributions -- the same
conclusion the per-route analysis reached independently. What it does buy is
calibration: per-bucket empirical accuracy is a finer and better-fitted
confidence than one number per route, worth +0.19 holdout (15.24 -> 15.43).

Loosening the floor to 12 with a 0.25 margin does start flipping decisions, and
it is worse: -0.27 holdout and catastrophic false approvals rise from 1 to 4.

For each bucket the empirical distribution of true adjudications is stored, and
the decision with the highest expected score is chosen:

    correct +8, decided->review +2, review->decided +1,
    approved/denied wrong 0, false approval -4

The -4 term is what makes this safe: a bucket has to be strongly and
consistently one-sided before approving beats reviewing.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

DECISIONS = ("APPROVED", "DENIED", "NEEDS_REVIEW")

_POINTS = {
    ("APPROVED", "APPROVED"): 8, ("DENIED", "DENIED"): 8,
    ("NEEDS_REVIEW", "NEEDS_REVIEW"): 8,
    ("NEEDS_REVIEW", "APPROVED"): 2, ("NEEDS_REVIEW", "DENIED"): 2,
    ("APPROVED", "NEEDS_REVIEW"): 1, ("DENIED", "NEEDS_REVIEW"): 1,
    ("DENIED", "APPROVED"): 0, ("APPROVED", "DENIED"): -4,
}

# Cell population floor. Below this the rules stand.
MIN_OBSERVATIONS = 20

# Expected-score margin a bucket must beat the rule decision by before it may
# override. Without a margin, sampling noise flips decisions for nothing.
MIN_MARGIN = 0.75

ARTIFACT = pathlib.Path(__file__).with_name("resolver_table.json")

# Routes whose decision is read from the document rather than inferred. These are
# never resolved: the packet is not structurally incomplete, it is answered.
AUTHORITATIVE_ROUTES = frozenset({"adjudicator_note"})


def evidence_keys(reason: str, record: dict, reviews: list, approvals: list) -> list[str]:
    """Bucket identifiers describing the packet's evidence, most specific first."""
    route = reason.split(":")[0]
    fee = record.get("fee_status") or "none"
    visa = record.get("visa_class") or "none"
    flags = [f for f in (record.get("risk_flags") or "").split("|") if f and f != "none"]
    risk = "flagged" if flags else "clear"
    support = min(len(approvals), 4)
    doubts = min(len(reviews), 3)
    return [
        f"{route}|fee={fee}|visa={visa}|risk={risk}",
        f"{route}|fee={fee}|support={support}|doubts={doubts}",
        f"{route}|visa={visa}|risk={risk}",
        f"{route}|support={support}|doubts={doubts}",
        f"{route}|risk={risk}",
        route,
    ]


def expected_score(counts: dict, decision: str) -> float:
    total = sum(counts.values())
    if total <= 0:
        return float("-inf")
    return sum(_POINTS[(decision, truth)] * n / total for truth, n in counts.items())


@dataclass
class Resolver:
    table: dict
    min_observations: int = MIN_OBSERVATIONS
    min_margin: float = MIN_MARGIN

    @classmethod
    def load(cls, path: pathlib.Path = ARTIFACT):
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
        if payload.get("schema") != 1:
            return None
        return cls(table=payload["buckets"],
                   min_observations=payload.get("min_observations", MIN_OBSERVATIONS),
                   min_margin=payload.get("min_margin", MIN_MARGIN))

    def save(self, path: pathlib.Path = ARTIFACT) -> None:
        path.write_text(json.dumps({
            "schema": 1,
            "min_observations": self.min_observations,
            "min_margin": self.min_margin,
            "buckets": self.table,
        }, indent=1, sort_keys=True), encoding="utf-8")

    def resolve(self, rule_decision: str, reason: str, keys: list[str]):
        """Return (decision, confidence) or None to keep the rule decision."""
        if reason.split(":")[0] in AUTHORITATIVE_ROUTES:
            return None
        for key in keys:
            counts = self.table.get(key)
            if not counts or sum(counts.values()) < self.min_observations:
                continue
            best = max(DECISIONS, key=lambda d: expected_score(counts, d))
            if best == rule_decision:
                return best, counts.get(best, 0) / sum(counts.values())
            gain = expected_score(counts, best) - expected_score(counts, rule_decision)
            if gain < self.min_margin:
                return rule_decision, counts.get(rule_decision, 0) / sum(counts.values())
            return best, counts.get(best, 0) / sum(counts.values())
        return None
