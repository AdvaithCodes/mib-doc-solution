#!/usr/bin/env bash
# Local iteration loop: run the pipeline over the training set and score it.
#
#   ./score.sh              # full 1000-case train set
#   ./score.sh 50           # first 50 cases (fast smoke test)
#
# Requires the challenge repository (for data and evaluate.py). Override with
# MIB_CHALLENGE=/path/to/mib-doc-challenge.
set -euo pipefail

CHALLENGE="${MIB_CHALLENGE:-$HOME/dev/mib-doc-challenge}"
LIMIT="${1:-}"
OUT=/tmp/mib-local
mkdir -p "$OUT"

if [ ! -d "$CHALLENGE/data/train" ]; then
  echo "challenge data not found at $CHALLENGE/data/train" >&2
  echo "set MIB_CHALLENGE to the challenge repository root" >&2
  exit 2
fi

INPUT="$CHALLENGE/data/train"
TRUTH="$CHALLENGE/data/train_labels.csv"

if [ -n "$LIMIT" ]; then
  INPUT="$OUT/subset"
  rm -rf "$INPUT" && mkdir -p "$INPUT"
  # Note: `ls | head` would SIGPIPE and, under `pipefail`, abort the script.
  i=0
  for f in "$CHALLENGE"/data/train/*.pdf; do
    ln -sf "$f" "$INPUT/$(basename "$f")"
    i=$((i + 1))
    if [ "$i" -ge "$LIMIT" ]; then break; fi
  done
  # Score against only the selected cases, otherwise every unselected case
  # counts as missing and the subset score is meaningless.
  TRUTH="$OUT/subset_labels.csv"
  python3 - "$CHALLENGE/data/train_labels.csv" "$INPUT" "$TRUTH" <<'PY'
import csv, pathlib, sys
src, subset_dir, dst = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3]
keep = {p.stem for p in subset_dir.glob("*.pdf")}
rows = [r for r in csv.DictReader(open(src)) if r["case_id"] in keep]
with open(dst, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)
PY
  echo "running on a $LIMIT-case subset"
fi

# Pin to 4 workers so local timings reflect the 4 vCPU scoring host.
MIB_WORKERS="${MIB_WORKERS:-4}" \
  python3 -m mib_pipeline "$INPUT" "$OUT/predictions.jsonl"

python3 "$CHALLENGE/scripts/evaluate.py" \
  --truth "$TRUTH" \
  --submission "$OUT/predictions.jsonl" \
  --output-json "$OUT/evaluation.json" \
  --case-scores-jsonl "$OUT/case_scores.jsonl"

echo
echo "per-case detail: $OUT/case_scores.jsonl"
