# MIB Doc Challenge — Solution

An offline, CPU-only document processing pipeline for the
[8090 MIB Doc Challenge](https://github.com/8090-inc/mib-doc-challenge).

Given a directory of scanned PDF case packets, it extracts each applicant's
record and adjudicates the case as `APPROVED`, `DENIED`, or `NEEDS_REVIEW`,
writing one JSON object per line to the requested output path.

> **Status: work in progress.** Scores and architecture notes below are updated
> as the pipeline develops.

## Design principle

Evidence comes from what is **visible** on the rendered page.

The challenge data contains deliberate adversarial material: white-on-white
text, content outside the page crop, hidden PDF text layers, fake system
prompts, barcode instructions, and decoy fields labeled "answer key". Per the
MIB field manual's evidence precedence, none of these are trusted evidence.

This pipeline therefore renders every page and reads pixels. The embedded PDF
text layer is retained only as a diagnostic signal and is never permitted to
become prediction evidence. Where trusted evidence is absent, the pipeline
reports the field as unknown and routes the case to `NEEDS_REVIEW` rather than
filling the gap from an untrusted channel.

## Requirements

- Docker (to build and run the submission image)
- Python 3.12 (for local development only)

The runtime has no system package dependencies. All PDF rendering, image
processing, and OCR is provided by pinned Python wheels, so the container and
a local virtualenv execute identical code paths.

## Build and run

```bash
docker build --platform linux/amd64 -t mib-submission .

mkdir -p /tmp/mib-output
docker run --rm \
  --network none \
  --cpus 4 \
  --memory 8g \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=2g \
  --mount type=bind,src=/path/to/pdfs,dst=/input,readonly \
  --mount type=bind,src=/tmp/mib-output,dst=/output \
  mib-submission /input /output/predictions.jsonl
```

The image takes exactly two arguments: an input PDF directory and an output
predictions path. It requires no network access, no API keys, and no external
services at runtime.

## Local development

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt

python -m mib_pipeline /path/to/data/train /tmp/predictions.jsonl
```

## Reproducing the reported score

From a clean checkout of this repository and of the challenge repository:

```bash
python3 scripts/evaluate.py \
  --truth data/train_labels.csv \
  --submission /tmp/mib-output/predictions.jsonl \
  --output-json /tmp/mib-output/evaluation.json
```

Reported scores are measured on the public 1,000-case training set. They are
local measurements against public labels, not leaderboard or private test
results.

## Results

| Split | Total | Extraction | Classification | Calibration |
| --- | ---: | ---: | ---: | ---: |
| Public train (n=1000) | 123.84 | 43.68 | 64.18 | 15.98 |
| Held out (n=700, never tuned on) | 122.35 | 43.66 | 63.04 | 15.65 |

The held-out row is the honest one: rules and tables were fitted on the first
300 cases only. The shipped image refits the calibration tables on all 1,000,
which changes no decisions and only sharpens confidence.

Zero catastrophic false approvals. The held-out figure is the honest one: rules
were tuned against the first 300 cases only.

## Continuous integration

`.github/workflows/docker.yml` builds the image for `linux/amd64` and runs it
under the scoring flags above on every push, so contract violations — writes
outside `/tmp`, network access, argument handling, architecture mismatch — fail
in CI rather than during scoring.

## Attribution

Three policy rules -- embargoed home worlds, revoked sponsors beyond the three
published, and the stale-application rule -- were identified by reading
[strobl/mib-doc-solution](https://github.com/strobl/mib-doc-solution), a public
MIT-licensed entry to this challenge, and are reused with attribution per the
challenge rules. No code was copied; the constants were re-derived from the
public training labels. See `NOTICE.md`.

## Third-party components

All dependencies are pinned in `requirements.txt`. Licenses for bundled
components, including OCR model artifacts, are recorded in `NOTICE.md`.

## License

MIT — see `LICENSE`.
