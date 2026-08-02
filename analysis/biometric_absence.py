"""Is a missing biometric slip itself evidence of `illegible_biometrics`?

`illegible_biometrics` is 22.3% of packets and 117 of our missed flags -- the
single largest hole in the classification score, which `oracle.py` prices at
+7.31. findings.md recorded that 75 of those 117 packets "contain no biometric
slip at all" and concluded they were unreachable for anyone.

That conclusion rests on the same `doc_type` test that `audit_misses.py:72`
used to decide documents were absent, and which the classifier work showed was
counting unclassifiable pages as missing documents. So it is worth re-asking
with the question inverted: a packet whose biometric slip is missing or
unreadable is not a packet with no evidence -- the absence *is* the observation,
and the flag means precisely "the biometrics could not be read".

This measures P(illegible_biometrics | no readable slip) against the base rate.
A detector must clear ~50% precision to break even, because a flag is worth 8
raw when right and corrupts a field worth 8 raw when wrong.

    ./analysis/biometric_absence.py
"""
import collections
import csv
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from replay import load

CACHE = pathlib.Path.home() / "dev/mib-artifacts/train_cache_2engine.jsonl"
LABELS = pathlib.Path.home() / "dev/mib-doc-challenge/data/train_labels.csv"

FLAG = "illegible_biometrics"
_OBSERVED = re.compile(r"OBSERVEDFLAGS[:\s]*([A-Z_,\s]*)", re.I)


def truth_flags(row) -> set[str]:
    return {f.strip() for f in (row.get("risk_flags") or "").split("|") if f.strip()}


def main() -> int:
    truth = {r["case_id"]: r for r in csv.DictReader(open(LABELS))}
    packets = [p for p in load(str(CACHE)) if p.case_id in truth]

    # Four populations, by what the packet shows about its biometric slip.
    buckets = collections.defaultdict(lambda: [0, 0])  # bucket -> [n, n_with_flag]
    observed_values = collections.Counter()

    for pk in packets:
        flags = truth_flags(truth[pk.case_id])
        has_flag = FLAG in flags

        slips = [p for p in pk.pages if p.doc_type == "biometric_slip"]
        if not slips:
            bucket = "no slip page in packet"
        else:
            text = "".join("".join(p.all_lines) for p in slips)
            squashed = re.sub(r"[^A-Z0-9_,]", "", text.upper())
            match = _OBSERVED.search(squashed)
            if not match:
                bucket = "slip present, no readable 'Observed flags'"
            else:
                value = match.group(1).strip(" ,")
                observed_values[value[:40]] += 1
                if not value:
                    bucket = "slip present, 'Observed flags' empty"
                else:
                    bucket = "slip present, flags readable"

        buckets[bucket][0] += 1
        buckets[bucket][1] += has_flag

    base = sum(b[1] for b in buckets.values()) / max(sum(b[0] for b in buckets.values()), 1)
    print(f"base rate P({FLAG}) = {100*base:.1f}%\n")
    print(f"{'population':46s} {'n':>5} {'with flag':>10} {'precision':>10}")
    for name, (n, k) in sorted(buckets.items(), key=lambda kv: -kv[1][0]):
        print(f"{name:46s} {n:5d} {k:10d} {100.0*k/max(n,1):9.1f}%")

    print("\nmost common readable 'Observed flags' values:")
    for value, n in observed_values.most_common(12):
        print(f"   {value!r:44s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
