# Session log

## Current status (2026-07-30)

Submission is complete and validated. Deadline is **2026-08-03**.

| | |
| --- | --- |
| Held out (700 cases, tables fitted on the other 300) | **122.38** |
| Shipped image (tables refit on all 1000) | 123.87 |
| Catastrophic false approvals | 1 |
| Runtime | ~1.3-3.6 s/PDF against a 6 s budget |
| CI | green — builds `linux/amd64`, runs under scoring flags |
| Validation predictions | 5000 records, `validate_submission.py` exit 0 |

Holdout is the honest number. Interview bar is 105 total / 55 classification.

## Where things live

- `~/dev/mib-doc-solution` — this repo, pushed to `github.com/AdvaithCodes/mib-doc-solution`
- `~/dev/mib-doc-challenge` — upstream clone: data, official scorer, and the
  prepared submission folder at `submissions/AdvaithCodes/` (untracked)
- `~/dev/mib-artifacts/` — evidence caches and the generated predictions, kept
  out of `/tmp` because it clears on reboot
- `~/.local/tess/bin/tesseract` — user-space Tesseract via micromamba, no admin
  needed. Export `MIB_TESSERACT` to point at it.

## Working loop

```bash
source ~/dev/.venv-mib/bin/activate
export MIB_TESSERACT="$HOME/.local/tess/bin/tesseract"

# score all 1000 train cases in ~17s from cache
./replay.py ~/dev/mib-artifacts/train_cache_2engine.jsonl \
    ~/dev/mib-doc-challenge/data/train_labels.csv --split 300

# regenerate the 5000 validation predictions in ~60s
./replay.py ~/dev/mib-artifacts/val_cache.jsonl \
    ~/dev/mib-doc-challenge/data/validation_manifest.csv \
    --predict ~/dev/mib-artifacts/val_predictions.jsonl
```

Rebuild a cache only when `evidence.py` changes (OCR/rendering); everything
downstream is replayed. `cache_evidence.py` is resumable.

## What remains to submit

The challenge clone is upstream, not a fork. To submit:

```bash
cd ~/dev/mib-doc-challenge
gh repo fork 8090-inc/mib-doc-challenge --remote-name fork --clone=false
git checkout -b submission/AdvaithCodes
git add submissions/AdvaithCodes && git commit -m "Submission: AdvaithCodes"
git push fork submission/AdvaithCodes
gh pr create --repo 8090-inc/mib-doc-challenge \
  --head AdvaithCodes:submission/AdvaithCodes \
  --title "Submission: AdvaithCodes" --body-file submissions/AdvaithCodes/SUBMISSION.md
```

**The PR alone does not count** — the Google submission form linked in
`SUBMISSION.md` is also required.

## Findings

`.claude/findings.md` holds everything measured on this project — the data's
shape, the answer-key trap quantified, the scoring mechanics that drive the
design, the oracle result below, the full rejected list with numbers, and the
chronological order the pipeline was built in. Read it before proposing work;
most obvious ideas are already in the rejected table with a measurement.

`analysis/` holds the scripts that produced those numbers, so any of it can be
re-run rather than taken on trust.

## Open issue: where the remaining gap is

An oracle experiment (`scratchpad/oracle.py`) pinned it exactly:

```
as shipped          64.18 / 80
+ true risk_flags   71.49   (+7.31)   above strobl's published 68.52
+ true fee_status   65.73   (+1.55)
+ both              73.93
```

The whole classification gap is **risk-flag detection**. Not adjudication, not
resolution, not calibration.

It resists closing because of the economics: a flag is worth 8 raw when right and
corrupts a field worth 8 raw when wrong, so a detector needs >50% precision to
break even and ~75% to matter. Every derived-flag signal measured lands at
15-30%: cross-document name disagreement (27% against a 4% base), sponsor
mismatch (15%), registry status (17%), biometric confidence (F1 0.43). The best
of them was implemented and cost 0.48 holdout.

Of 117 missed `illegible_biometrics`, 75 packets contain no biometric slip at
all, so those are unreachable for anyone.

## The one live vein

~158 extraction misses still hold the correct value in text already extracted,
worth about +0.88. 62 are near-miss spellings appearing once each, where
consensus voting cannot break the tie and authority decides — often wrongly.
That needs a tiebreak other than frequency.

Everything else measured is in the rejected table in `findings.md`. Do not retry
those without new evidence; each cost real time and each has a number.
