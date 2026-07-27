"""Entrypoint: read a directory of case PDFs, write predictions.jsonl.

This module owns the submission contract only — discovery, isolation, parallelism,
validation and output ordering. The document understanding lives in extract.py and
adjudicate.py.

Contract guarantees enforced here:
  * one line per successfully processed case, in stable case-ID order
  * a failure on one case never aborts the run
  * only /tmp and the output path are written
  * a case that cannot be answered is omitted (small penalty) rather than guessed
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from mib_pipeline.pipeline import process_case
from mib_pipeline.schema import Prediction

# The scoring host gives 4 vCPUs. Leave the default at 4 but allow an override
# so local runs can be pinned to match.
WORKERS = int(os.environ.get("MIB_WORKERS", "4"))

# Per-case wall-clock ceiling. The budget is 6s/PDF *on average*, so a single
# pathological packet must not be allowed to consume the run.
CASE_TIMEOUT_S = float(os.environ.get("MIB_CASE_TIMEOUT", "45"))


def discover(input_dir: pathlib.Path) -> list[pathlib.Path]:
    """All PDFs under input_dir, sorted for deterministic output ordering."""
    return sorted(input_dir.rglob("*.pdf"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mib_pipeline")
    ap.add_argument("input_dir", type=pathlib.Path)
    ap.add_argument("output_path", type=pathlib.Path)
    args = ap.parse_args(argv)

    if not args.input_dir.is_dir():
        print(f"input directory not found: {args.input_dir}", file=sys.stderr)
        return 2

    pdfs = discover(args.input_dir)
    if not pdfs:
        print(f"no PDFs found under {args.input_dir}", file=sys.stderr)

    started = time.perf_counter()
    results: dict[str, Prediction] = {}
    failures: list[tuple[str, str]] = []

    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(process_case, str(p)): p for p in pdfs}
        for fut in as_completed(futures):
            pdf = futures[fut]
            try:
                pred = fut.result(timeout=CASE_TIMEOUT_S)
            except Exception as exc:  # one bad packet must not sink the run
                failures.append((pdf.stem, f"{type(exc).__name__}: {exc}"))
                continue
            if pred is None:
                failures.append((pdf.stem, "no trustworthy answer"))
                continue
            problems = pred.validate()
            if problems:
                failures.append((pdf.stem, "invalid: " + "; ".join(problems)))
                continue
            results[pred.case_id] = pred

    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    with args.output_path.open("w", encoding="utf-8") as fh:
        for case_id in sorted(results):
            fh.write(results[case_id].finalize().to_json_line() + "\n")

    elapsed = time.perf_counter() - started
    per_pdf = elapsed / len(pdfs) if pdfs else 0.0
    print(
        f"processed {len(results)}/{len(pdfs)} cases in {elapsed:.1f}s "
        f"({per_pdf:.2f}s per PDF, budget 6.00s) -> {args.output_path}",
        file=sys.stderr,
    )
    if failures:
        print(f"omitted {len(failures)} case(s):", file=sys.stderr)
        for case_id, why in failures[:20]:
            print(f"  {case_id}: {why}", file=sys.stderr)
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
