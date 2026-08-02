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

## A diagnostic that shares a bug with the pipeline confirms itself

`audit_misses.py:72` decided a document was "not in the packet" by testing
`p.doc_type == doc_type`. Any page too damaged to *classify* therefore counted
as a document that was not there, and the resulting "403 source document is not
in the packet at all" became a settled fact in findings.md. It was wrong: 410
pages typed `unknown`, and rendering six of them showed three fully legible
intake forms. The diagnostic inherited the classifier's blind spot and then
reported it as a property of the data.

**Rule:** when a diagnostic attributes a failure to the *input*, check whether
it reached that conclusion through the same code that failed.

## Enforcing a rule on one representation does not enforce it

The visible-evidence rule was enforced on the text layer. pdfium also renders
hidden characters into the raster, so the injected answer key reached OCR on 3
packets anyway — the one thing this design exists to prevent, live for months
under a rule everyone believed was total.

The first fix made it worse: painting the hidden characters' boxes white before
OCR erased 15,224 characters of real evidence from 299 pages, because the key is
positioned across the whole page and its boxes cover the form. Filtering the OCR
output on the injection's *framing* — never its values, which the form legitimately
prints too — cost nothing.

**Rule:** a rule about content has to be enforced on every representation of
that content, and the cheapest place to enforce it is rarely the raw pixels.

## Match the tolerance of the test to the damage you are testing for

The flag panel first read `Observed flags` with an exact substring test and
recovered 7 flags. The values arrive as `bichozord_red` and `egible_biometics`,
and the *label* arrives as `Observed floga` — so the test rejected precisely the
damaged cases it was built to catch. Snapping to the closed nine-flag set and
locating the label fuzzily took it to 24 at the same 100% precision.

**Rule:** an exact test on OCR output silently defines the population it can
help as the population that did not need help.

## Fitting on a subset then reading on the same subset is not a measurement

Rules and tables were fitted on the first 300 cases; the honest number is the
remaining 700, which ran 4-6 points lower throughout. The shipped artefact refits
on all 1000, and that number is reported separately and never compared to the
holdout.
