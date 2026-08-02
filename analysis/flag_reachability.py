"""How many true risk flags are already sitting in text we extracted?

`oracle.py` prices true `risk_flags` at +7.31 -- the entire classification gap.
Two explanations have to be told apart before any more work is spent there:

  reading failure    the flag is not in any text we hold, so it needs better OCR
  resolution failure the flag *is* in text we hold and we did not emit it

That distinction is what produced the last real gain on this project: 241
extraction misses turned out to be resolution failures, not reading failures.
This asks the same question of the flags, per flag, and separately for the
flags whose truth is *derived* by FIELD_MANUAL rules rather than printed.

    ./analysis/flag_reachability.py
"""
import collections
import csv
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from mib_pipeline.adjudicate import _parse_date, reference_receipt_date
from mib_pipeline.extract import resolve
from replay import load, rule_state

CACHE = pathlib.Path.home() / "dev/mib-artifacts/train_cache_2engine.jsonl"
LABELS = pathlib.Path.home() / "dev/mib-doc-challenge/data/train_labels.csv"

FLAGS = ("illegible_biometrics", "biohazard_red", "planetary_embargo",
         "rescinded_denial", "identity_conflict", "sponsor_mismatch",
         "active_warrant", "memory_tampering")


def truth_flags(row) -> set[str]:
    return {f.strip() for f in (row.get("risk_flags") or "").split("|")
            if f.strip() and f.strip() != "none"}


def visible_text(packet) -> str:
    return re.sub(r"[^A-Z_]", "",
                  "".join("".join(p.all_lines) for p in packet.pages).upper())


def main() -> int:
    truth = {r["case_id"]: r for r in csv.DictReader(open(LABELS))}
    packets = [p for p in load(str(CACHE)) if p.case_id in truth]

    # per flag: [in truth, we emit it, truth & printed somewhere, truth & not printed]
    stats = collections.defaultdict(lambda: [0, 0, 0, 0])
    emitted_wrong = collections.Counter()
    recoverable = collections.Counter()

    arrivals = []
    for pk in packets:
        rr = resolve(pk)
        if "arrival_date" in rr:
            d = _parse_date(rr["arrival_date"].value)
            if d:
                arrivals.append(d)
    reference = reference_receipt_date(arrivals)

    for pk in packets:
        want = truth_flags(truth[pk.case_id])
        text = visible_text(pk)
        # The shipped path, not a copy of it: risk_flags is assembled from
        # extraction plus note mining plus the registry signal.
        record, *_ = rule_state(pk, reference)
        got = {f for f in record["risk_flags"].split("|") if f and f != "none"}

        for flag in FLAGS:
            printed = flag.upper().replace("_", "") in text.replace("_", "")
            if flag in want:
                stats[flag][0] += 1
                if flag in got:
                    stats[flag][1] += 1
                elif printed:
                    stats[flag][2] += 1        # in our text, not emitted
                else:
                    stats[flag][3] += 1        # not in our text at all
                if flag not in got and printed:
                    recoverable[flag] += 1
            elif flag in got:
                emitted_wrong[flag] += 1

    print(f"{'flag':24s} {'truth':>6} {'emitted':>8} {'missed-but':>11} "
          f"{'missed-and':>11} {'false':>6}")
    print(f"{'':24s} {'':>6} {'':>8} {'-printed':>11} {'-absent':>11} {'pos':>6}")
    total_recover = 0
    for flag in FLAGS:
        n, got, printed, absent = stats[flag]
        total_recover += printed
        print(f"{flag:24s} {n:6d} {got:8d} {printed:11d} {absent:11d} "
              f"{emitted_wrong[flag]:6d}")

    print(f"\nflags present in text we already hold but not emitted: {total_recover}")
    print("by flag:")
    for flag, n in recoverable.most_common():
        print(f"   {flag:24s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
