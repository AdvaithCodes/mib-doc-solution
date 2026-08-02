"""Can a damaged page be typed without reading its title?

410 of 4,159 training pages classified as `unknown`, and every one was a raster
page. `audit_misses.py:72` decides a document is "not in the packet" by looking
for `p.doc_type == doc_type`, so each of those 410 was counted as an *absent
document* -- that is where the "403 source document is not in the packet"
figure came from. Rendering six of them showed three fully legible FORM I-8090
intake forms and a legible FORM B-13 slip.

The cause was visible in the OCR: watermarks (`SAMPLE DENIAL`, `COPY ARTIFACT`,
`CASEWORK`, `FILED`, `REDACTED`) sort to the top of OCR row order and pushed the
real title out of the `lines[:3]` and `lines[:5]` windows `classify()` inspects.

Validation follows naidx0's method: take the pages whose type is known because
their text layer is exact, delete the title line, and re-classify blind.

This imports the shipped `classify()` -- it does not keep its own copy, because
a measurement harness that duplicates the logic it measures is how two fixes
here were once scored against code that never ran.

    ./analysis/classify_probe.py
"""
import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from mib_pipeline.evidence import classify
from replay import load

CACHE = pathlib.Path.home() / "dev/mib-artifacts/train_cache_2engine.jsonl"


def main() -> int:
    packets = list(load(str(CACHE)))

    blind_total = blind_right = blind_unknown = 0
    confusion = collections.Counter()
    for pk in packets:
        for p in pk.pages:
            if p.source != "text" or p.doc_type in ("unknown", "passport_image"):
                continue
            if len(p.lines) < 2:
                continue
            # Delete the title line: this is the degraded-page situation.
            got = classify(p.lines[1:])
            blind_total += 1
            if got == p.doc_type:
                blind_right += 1
            elif got == "unknown":
                blind_unknown += 1
            else:
                confusion[(p.doc_type, got)] += 1

    decided = blind_total - blind_unknown
    print("BLIND VALIDATION -- title line removed, type known from exact text")
    print(f"  pages         {blind_total}")
    print(f"  typed         {decided}  ({100.0 * decided / blind_total:.1f}% coverage)")
    print(f"  correct       {blind_right}  "
          f"({100.0 * blind_right / max(decided, 1):.1f}% precision)")
    print(f"  left unknown  {blind_unknown}")
    for (true, got), n in confusion.most_common(8):
        print(f"    {true} -> {got}: {n}")

    print("\nPAGES STILL UNTYPED")
    still = collections.Counter()
    for pk in packets:
        for p in pk.pages:
            if p.doc_type == "unknown":
                still[p.source] += 1
    print(f"  {sum(still.values())} unknown pages: {dict(still)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
