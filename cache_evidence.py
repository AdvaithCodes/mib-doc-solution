#!/usr/bin/env python3
"""Cache extracted evidence so adjudication rules can be tuned in seconds.

OCR dominates runtime (~25 minutes for the training set), but adjudication and
calibration changes do not affect extraction at all. Caching the evidence layer
once turns a 25-minute experiment into a 2-second one, which is the difference
between tuning rules against the full 1000 cases and tuning them against a
subset -- and subset tuning has already produced one overfit rule.

    ./cache_evidence.py <pdf_dir> <cache.jsonl>      # slow, once
    ./replay.py <cache.jsonl> <labels.csv>           # fast, repeatedly
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from concurrent.futures import ProcessPoolExecutor

from mib_pipeline.evidence import read_packet

# Caching is a local, offline step, so it may use every core available rather
# than mirroring the 4-vCPU scoring host. Submission timing is measured from the
# real pipeline, not from here.
WORKERS = int(os.environ.get("MIB_CACHE_WORKERS", "8"))


def extract_one(pdf_path: str) -> dict:
    path = pathlib.Path(pdf_path)
    packet = read_packet(str(path), path.stem)
    return {
        "case_id": path.stem,
        "pages": [
            {
                "number": p.number,
                "doc_type": p.doc_type,
                "lines": p.lines,
                "source": p.source,
                "ocr_lines": p.ocr_lines,
                "hidden_text": p.hidden_text,
            }
            for p in packet.pages
        ],
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    pdf_dir, out_path = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    print(f"caching evidence for {len(pdfs)} packets -> {out_path}", file=sys.stderr)

    done = 0
    with out_path.open("w", encoding="utf-8") as fh:
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            for rec in pool.map(extract_one, [str(p) for p in pdfs], chunksize=4):
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                done += 1
                if done % 100 == 0:
                    print(f"  {done}/{len(pdfs)}", file=sys.stderr)
    print(f"cached {done} packets", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
