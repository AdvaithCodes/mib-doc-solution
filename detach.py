#!/usr/bin/env python3
"""Launch a long run in its own session so it survives the parent shell.

Long pipeline runs outlast an interactive shell: the full training set takes
~25 minutes and the 5,000-case validation set takes hours. `nohup ... &` alone
does not reliably survive here, and macOS has no `setsid`, so start a new
session explicitly.

    ./detach.py /tmp/run.log ./score.sh
    ./detach.py /tmp/val.log python3 -m mib_pipeline /path/to/validation /tmp/val.jsonl

Then follow with: tail -f /tmp/run.log
"""
from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2

    log_path, command = sys.argv[1], sys.argv[2:]
    with open(log_path, "wb", buffering=0) as log:
        proc = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach from this process group
            cwd=os.getcwd(),
        )
    print(f"pid {proc.pid} -> {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
