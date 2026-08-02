# Session log

## Submission status

**PR: https://github.com/8090-inc/mib-doc-challenge/pull/65** — OPEN, MERGEABLE,
head `c70195e`, exactly the three required files. Carries the full 2026-08-02
work.

**The only outstanding step is the Google form**, and it must record the score
**124.51** (the field was drafted at 123.87 before the upgrade landed). The entry
does not count without the form; the PR alone is not enough:
https://docs.google.com/forms/d/1ZLkHmTsYd9I87JL1sUyps2rPTe6ohEI_lTZ8Jjts6bw/viewform

Everything is consistent end to end:

| | |
| --- | --- |
| Held out (700, tables fitted on the other 300) | **123.07** |
| Public train, official `scripts/evaluate.py` | **124.51** / 150 |
| Catastrophic false approvals | 1 |
| Runtime | 1.09 s/PDF at 4 workers, 6 s budget |
| `validate_submission.py` | exit 0, 5,000 records, 0 missing |
| Solution repo | `29918dc`, pushed, CI green |
| predictions.jsonl | byte-identical to the generated file |

Deadline **2026-08-03**.

## In flight right now (2026-08-02)

**The validation cache is being rebuilt** at
`~/dev/mib-artifacts/val_cache_panel.jsonl` (5,000 packets, several hours on
this 10-core machine). It is needed only because the risk-flag panel changes
what OCR produces; every other change this session is replayed from a cache and
needed no rebuild.

The currently shipped `submissions/AdvaithCodes/predictions.jsonl` is still the
older, fully validated artefact — **it is submittable as-is at any moment**
(re-checked 2026-08-02, exit code 0, 5,000 records, 0 missing). Do not replace
it until the new cache finishes *and* the validator passes on the new file.

When the rebuild completes:

```bash
./replay.py ~/dev/mib-artifacts/val_cache_panel.jsonl \
    ~/dev/mib-doc-challenge/data/validation_manifest.csv \
    --predict ~/dev/mib-artifacts/val_predictions.jsonl
cp ~/dev/mib-artifacts/val_predictions.jsonl \
    ~/dev/mib-doc-challenge/submissions/AdvaithCodes/predictions.jsonl
cd ~/dev/mib-doc-challenge
python3 scripts/validate_submission.py \
    --submission submissions/AdvaithCodes/predictions.jsonl \
    --manifest data/validation_manifest.csv
echo "exit: $?"      # check directly, never through a pipe
```

## What changed on 2026-08-02

Honest holdout **122.38 -> 123.07**, catastrophic false approvals unchanged at 1.

| change | holdout | needs cache rebuild |
| --- | --- | --- |
| Page typing: strip watermark lines, use both engines, form-anchor vote | +0.20 | no |
| Injected answer key filtered out of OCR output | 0.00 | no |
| `Manual correction:` excluded from page typing | 0.00 public | no |
| Risk-flag panel: 400-dpi re-read of the slip header | +0.53 | **yes** |

The two zero-scoring changes are correctness, not score: the answer key was
reaching evidence through the raster on 3 packets, and a damaged intake form
carrying a correction was being typed `adjudicator_note` — rank 2 promoted to
rank 1. Blind page-typing precision went 93.8% -> 100%.

## If there is time after submitting

Ranked by measured value:

1. **~158 extraction misses** still hold the correct value in text already
   extracted, worth ~+0.88. 62 are near-miss spellings appearing once each, where
   consensus cannot break the tie. Needs a tiebreak other than frequency.
2. **The header re-read on intake forms** is measured at about +0.33 upper bound
   (`analysis/header_roi_probe.py`) and was declined only because one cache
   rebuild fitted in the time and the flag panel was the larger, measured win.
   Its downside was never measured — do that before shipping it.
3. **Re-run `analysis/oracle.py`** after any change — it locates where score is
   being lost in one command.
4. **Do not** start anything in the rejected table in `findings.md` without new
   evidence. Twenty-two attempts are listed there with their numbers.

## Current status (2026-08-02)

| | |
| --- | --- |
| Held out (700 cases, tables fitted on the other 300) | **123.07** |
| Shipped image (tables refit on all 1000) | see `findings.md` §6 |
| Catastrophic false approvals | 1 |
| Runtime | 2.55 s/PDF single-process against a 6 s budget (was 2.33 before the panel) |
| CI | green — builds `linux/amd64`, runs under scoring flags |
| Validation predictions | **regenerating** — old artefact still valid |

Holdout is the honest number. Interview bar is 105 total / 55 classification.

`replay.py` over the 1,000 training cases takes **~41 s**, not the ~17 s this
file and `CLAUDE.md` used to claim; the baseline was re-timed and was always 41 s.

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

## Findings

`.claude/findings.md` holds everything measured on this project — the data's
shape, the answer-key trap quantified, the scoring mechanics that drive the
design, the oracle result below, the full rejected list with numbers, and the
chronological order the pipeline was built in. Read it before proposing work;
most obvious ideas are already in the rejected table with a measurement.

`analysis/` holds the scripts that produced those numbers, so any of it can be
re-run rather than taken on trust.

## Open issue: where the remaining gap is

An oracle experiment (`analysis/oracle.py`) pinned it exactly:

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

