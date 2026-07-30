# CLAUDE.md — MIB Doc Challenge solution

## Read these first, before proposing or changing anything

1. `.claude/session-log.md` — current state, what has **not** been done yet, and
   what to do next.
2. `.claude/findings.md` — everything measured on this project, including a table
   of eighteen attempts that were tried and rejected, each with its number.
3. `.claude/learnings.md` — the process mistakes that cost the most time here.

Most obvious ideas have already been measured. Check the rejected table before
starting work; re-running one of them without new evidence wastes a session.

## Setup

```bash
source ~/dev/.venv-mib/bin/activate
export MIB_TESSERACT="$HOME/.local/tess/bin/tesseract"
```

Tesseract lives in user space via micromamba — no admin, and not on the default
PATH. The pipeline silently degrades to one OCR engine if it is missing, which is
exactly the kind of failure that hides for a full measurement cycle.

## How to measure

Never score by running the pipeline over PDFs — that takes 25 minutes. The
evidence caches in `~/dev/mib-artifacts/` hold the OCR output, and everything
downstream is replayed from them:

```bash
# all 1000 training cases, ~17 seconds
./replay.py ~/dev/mib-artifacts/train_cache_2engine.jsonl \
    ~/dev/mib-doc-challenge/data/train_labels.csv --split 300

# 5000 validation predictions, ~60 seconds
./replay.py ~/dev/mib-artifacts/val_cache.jsonl \
    ~/dev/mib-doc-challenge/data/validation_manifest.csv \
    --predict ~/dev/mib-artifacts/val_predictions.jsonl
```

Rebuild a cache only when `evidence.py` changes — rendering or OCR. Everything
else replays. `cache_evidence.py` is resumable.

`analysis/oracle.py` reports where score is being lost, in one command.

## Rules that hold regardless of what is being changed

- **The `--split 300` holdout is the honest number.** Rules and tables were fitted
  on the first 300 cases. Never compare a holdout figure to an in-sample one.
- **Report what was measured, not what was expected.** Several changes here looked
  positive and were not, and one looked neutral only because it never executed.
- **A "no change" result is a bug signature until proven otherwise.** Count the
  outputs, not the score.
- **Never read hidden text as evidence.** 21.6% of packets carry an injected
  answer key whose adjudication is wrong in 216 of 216 cases. It is quantified in
  `findings.md`, and reading it is penalised on the private set.
- **Catastrophic false approvals are a tiebreaker.** The rate is 1 per 1000. A
  change that raises it needs to buy a lot.
- **The harness imports from the shipped module.** `replay.py` once held its own
  copy of the decision logic and the two silently drifted.

## After any change that affects predictions

Regenerate, re-validate, and re-copy into the submission folder:

```bash
cd ~/dev/mib-doc-challenge
python3 scripts/validate_submission.py \
    --submission submissions/AdvaithCodes/predictions.jsonl \
    --manifest data/validation_manifest.csv
```

Check the exit code directly — not through a pipe, where `$?` reports the last
command in the chain rather than the validator.
