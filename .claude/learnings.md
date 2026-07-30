# Learnings

Mistakes made on this project and the rule that avoids each. Most cost more time
than the fix that followed.

## Measure through the same code path you ship

`replay.py` kept its own copy of the pre-decision orchestration, duplicating
`pipeline.py`. Two fixes appeared to change nothing at all — they were never
being measured, because the harness ran the old logic. A third measurement
would have been wrong in the other direction.

**Rule:** the harness imports from the shipped module. Never let a second copy
of decision logic exist for convenience.

## A "no change" result is a bug signature, not evidence

Twice, an experiment returned numbers identical to baseline and I reported it as
"this gains nothing". Both times the code had not run: once Tesseract was never
invoked, once the fitted table was stale. Identical line counts across two caches
were evidence of *no execution*, not of no benefit.

**Rule:** before concluding an experiment is neutral, prove it executed —
count its outputs, not its score.

## Do not let an error handler hide the bug it exists to survive

`ocr_tesseract.read_page` caught `ValueError` so a packet could process without
the second engine. Tesseract emitted non-UTF-8 bytes on stderr, raising
`UnicodeDecodeError` — a `ValueError` subclass — so the engine silently returned
nothing for an entire measurement cycle.

**Rule:** catch the failures you predicted, and let unexpected ones surface at
least once before broadening the handler.

## `$?` after a pipeline is the last command's status

`brew install --cask docker | tail -30` reported success while the install had
failed on permissions. Later, `validate_submission.py ... | tail -2; echo $?`
reported 0 while the validator was exiting 2. Both times a real failure was
reported as a success.

**Rule:** capture status into a variable before piping, or check the command
directly.

## Look at the data before concluding it is unreadable

Two ceiling claims were built from OCR strings and a crude pixel heuristic, and
both were wrong. Rendering the pages and reading them found blur plus occlusion
patches where the classifier said "blank", and a different question — *is the
value already in text we extracted but not chosen?* — found 241 misses that were
resolution failures, not reading failures.

**Rule:** when concluding something is impossible, inspect the raw artefact.
An impossibility claim needs stronger evidence than a fix does.

## Watch for reasoning that conveniently ends the work

"~9 points are locked behind destroyed documents" survived until someone asked
why that would differ between solutions facing identical packets. It could not.
The story was self-serving and unfalsifiable as stated.

**Rule:** a conclusion that the remaining work is impossible deserves more
scrutiny than one that finds more work, not less.

## Truncated debug output produces confident wrong conclusions

A 150-character print of OCR output showed no manual-correction line, and that
became "RapidOCR does not read this region". The line was present, just further
along the string.

**Rule:** when a debug print drives a design decision, search the full text
rather than eyeballing a prefix.

## A locally wrong signal can be globally right

Registry `EMBARGO` sets `planetary_embargo` incorrectly 10 times in 12. Removing
it scored *worse*: setting `risk_known` keeps those packets out of a 29%-accurate
review bucket, which is worth more than the flag being right.

**Rule:** measure a signal by its effect on the objective, not by its own
precision.

## The scored quantity is not always the thing you optimised

Extraction and adjudication are scored separately. Serialising the modal value
for an unreadable field while telling the adjudicator it was never read is not
inconsistent — it is two correct answers to two different questions. Refusing
that for tidiness cost about a point.

## Fitting on a subset then reading on the same subset is not a measurement

Rules and tables were fitted on the first 300 cases; the honest number is the
remaining 700, which ran 4-6 points lower throughout. The shipped artefact refits
on all 1000, and that number is reported separately and never compared to the
holdout.
