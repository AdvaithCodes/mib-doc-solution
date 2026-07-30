# Analysis scripts

The measurements behind the design decisions. Each answers one question, and
several produced results that changed or reversed a decision — they are kept so
the reasoning can be re-run rather than taken on trust.

| script | question it answers |
| --- | --- |
| `oracle.py` | which field costs us the classification gap (answer: `risk_flags`, +7.31) |
| `audit_misses.py` | what damage class sits behind every extraction miss |
| `audit_risk_fee.py` | are the gating-field misses readable, by rendering the pages |
| `private_estimate.py` | what extraction scores once unrecoverable fields leave the denominator |
| `diag_fields.py` | per-field accuracy of a predictions file |
| `recon_keytruth.py` | how the injected answer key compares to the labels |
| `cell_probe.py` | whether form templates are pixel-aligned enough for cell extraction |
| `colour_probe.py` | whether stamp colour carries decision signal |

Run from the repository root with the venv active and `MIB_TESSERACT` set. They
read the evidence caches in `~/dev/mib-artifacts/`, not the PDFs, except where
they render pages deliberately.
