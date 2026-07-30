"""Estimate the private extraction score.

EVALUATION.md removes genuinely unrecoverable fields from a case's extraction
maximum on the private labels, but the public train labels omit that column, so
our public extraction score is charged for fields whose source evidence does not
exist. This approximates the private figure by detecting the same condition --
no visible evidence for the field anywhere in the packet -- and excluding those
fields from the denominator, exactly as the private scorer does.

The estimate is a lower bound on the benefit: it only excludes fields we can
prove have no evidence, while the graders also exclude fields cut out or torn
away that we cannot detect.
"""
import csv, pathlib, re, sys, collections

SOLUTION = pathlib.Path.home() / "dev/mib-doc-solution"
sys.path.insert(0, str(SOLUTION))

from replay import load, decide
from mib_pipeline.adjudicate import reference_receipt_date, _parse_date
from mib_pipeline.extract import resolve
from mib_pipeline.schema import FIELD_WEIGHTS

D = pathlib.Path.home() / "dev/mib-doc-challenge/data/train"
CACHE = pathlib.Path.home() / "dev/mib-artifacts/train_cache_2engine.jsonl"

# Evidence a field could possibly have been read from.
EVIDENCE_CUES = {
    "fee_status": re.compile(r"fee\s*stat|amount|waiver", re.I),
    "risk_flags": re.compile(r"flag|biohazard|embargo|warrant|tamper|conflict|mismatch|illegible|rescind", re.I),
    "sponsor_id": re.compile(r"spn[\s\-]?\d|sponsor", re.I),
    "applicant_name": re.compile(r"applicant|registry\s*name|attests", re.I),
    "species_code": re.compile(r"species", re.I),
    "home_world": re.compile(r"home\s*world|registry", re.I),
    "visa_class": re.compile(r"visa", re.I),
    "arrival_date": re.compile(r"arriv|\d{4}-\d{2}-\d{2}", re.I),
    "declared_purpose": re.compile(r"purpose", re.I),
}


def main():
    truth = {r["case_id"]: r for r in csv.DictReader(open(D.parent / "train_labels.csv"))}
    packets = list(load(str(CACHE)))
    arrivals = []
    for pk in packets:
        rr = resolve(pk)
        if "arrival_date" in rr:
            d = _parse_date(rr["arrival_date"].value)
            if d:
                arrivals.append(d)
    ref = reference_receipt_date(arrivals)

    raw = pub_max = priv_max = 0
    excluded = collections.Counter()

    for pk in packets:
        t = truth.get(pk.case_id)
        if not t:
            continue
        record, _dec, _reason, _conf = decide(pk, ref)
        blob = " ".join(l for p in pk.pages for l in p.all_lines)

        for field, weight in FIELD_WEIGHTS.items():
            tv = t[field].strip().lower()
            pv = str(record.get(field, "")).strip().lower()
            if field == "risk_flags":
                tv = "|".join(sorted(x for x in tv.split("|") if x))
                pv = "|".join(sorted(x for x in pv.split("|") if x))
            correct = tv == pv

            pub_max += weight
            cue = EVIDENCE_CUES[field].search(blob)
            recoverable = bool(cue)
            if recoverable:
                priv_max += weight
            else:
                excluded[field] += 1
            if correct:
                raw += weight

    print(f"public-style   extraction: {50 * raw / pub_max:.2f} / 50   (denominator {pub_max})")
    print(f"private-style  extraction: {50 * raw / priv_max:.2f} / 50   (denominator {priv_max})")
    print(f"\nfields excluded as having no visible evidence:")
    for f, n in excluded.most_common():
        print(f"  {n:5d}  {f}")


if __name__ == "__main__":
    main()
