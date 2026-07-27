#!/usr/bin/env python3
"""Live progress and evidence statistics for a cache_evidence.py run.

    ./monitor.py /tmp/val_cache.jsonl 5000            # live, refreshes every 5s
    ./monitor.py /tmp/val_cache.jsonl 5000 --once     # single snapshot

Beyond progress, this reports what the evidence actually looks like. The
training set is the only thing our rules are tuned against, so a validation set
that differs in OCR share, document mix or injection rate is an early warning
that the tuning will not transfer -- worth knowing before the deadline, not
after.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import subprocess
import sys
import time

# Reference statistics measured on the 1000-case public training set.
TRAIN_REFERENCE = {
    "pages_per_packet": 4.16,
    "ocr_page_share": 0.48,
    "injected_share": 0.216,
}

BAR_WIDTH = 44


def human_time(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


class Tracker:
    """Incrementally parses the cache file and accumulates statistics."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.offset = 0
        self.count = 0
        self.pages = 0
        self.ocr_pages = 0
        self.injected = 0
        self.doc_types: collections.Counter = collections.Counter()
        self.empty_pages = 0
        self.started = time.time()
        self.history: collections.deque = collections.deque(maxlen=60)

    def poll(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            fh.seek(self.offset)
            for line in fh:
                if not line.endswith("\n"):
                    break  # partial final line; re-read it next poll
                self.offset += len(line.encode("utf-8"))
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                self.count += 1
                has_hidden = False
                for page in rec.get("pages", []):
                    self.pages += 1
                    if page.get("source") == "ocr":
                        self.ocr_pages += 1
                    if not page.get("lines"):
                        self.empty_pages += 1
                    self.doc_types[page.get("doc_type", "unknown")] += 1
                    if page.get("hidden_text", "").strip():
                        has_hidden = True
                if has_hidden:
                    self.injected += 1
        self.history.append((time.time(), self.count))

    def rate_per_min(self) -> float:
        """Packets/minute over the recent window, which tracks slowdowns."""
        if len(self.history) < 2:
            return 0.0
        (t0, c0), (t1, c1) = self.history[0], self.history[-1]
        if t1 <= t0:
            return 0.0
        return (c1 - c0) / (t1 - t0) * 60


def workers_alive() -> int:
    try:
        out = subprocess.run(["pgrep", "-f", "cache_evidence"],
                             capture_output=True, text=True).stdout
        return len([l for l in out.splitlines() if l.strip()])
    except Exception:
        return -1


def load_average() -> str:
    try:
        one, five, fifteen = os.getloadavg()
        return f"{one:.1f} / {five:.1f} / {fifteen:.1f}"
    except Exception:
        return "n/a"


def delta(actual: float, reference: float) -> str:
    """Flag a distribution that has drifted from the training reference."""
    if reference == 0:
        return ""
    diff = (actual - reference) / reference
    marker = "  <-- differs from train" if abs(diff) > 0.20 else ""
    return f"{diff:+.0%}{marker}"


def render(tracker: Tracker, total: int) -> str:
    count = tracker.count
    frac = min(count / total, 1.0) if total else 0.0
    filled = int(BAR_WIDTH * frac)
    bar = "#" * filled + "." * (BAR_WIDTH - filled)

    rate = tracker.rate_per_min()
    elapsed = time.time() - tracker.started
    remaining = (total - count) / rate * 60 if rate > 0 else 0

    pages_per = tracker.pages / count if count else 0
    ocr_share = tracker.ocr_pages / tracker.pages if tracker.pages else 0
    inj_share = tracker.injected / count if count else 0

    lines = [
        f"  evidence cache  {tracker.path}",
        "",
        f"  [{bar}]  {frac:6.1%}",
        f"  {count} / {total} packets       {tracker.pages} pages",
        "",
        f"  rate       {rate:6.1f} packets/min",
        f"  elapsed    {human_time(elapsed):>8}   (this monitor session)",
        f"  remaining  {human_time(remaining):>8}" if rate > 0 else "  remaining       --",
        f"  workers    {workers_alive():>8}",
        f"  load avg   {load_average()}",
        "",
        "  evidence profile" + " " * 12 + "this run     vs train",
        f"    pages per packet         {pages_per:8.2f}   "
        f"{delta(pages_per, TRAIN_REFERENCE['pages_per_packet'])}",
        f"    pages needing OCR        {ocr_share:8.1%}   "
        f"{delta(ocr_share, TRAIN_REFERENCE['ocr_page_share'])}",
        f"    packets with hidden text {inj_share:8.1%}   "
        f"{delta(inj_share, TRAIN_REFERENCE['injected_share'])}",
        f"    pages with no text read  {tracker.empty_pages:8d}",
        "",
        "  document types",
    ]
    total_pages = max(tracker.pages, 1)
    for name, n in tracker.doc_types.most_common(8):
        share = n / total_pages
        lines.append(f"    {name:<22}{n:7d}  {share:6.1%}  "
                     + "#" * int(share * 30))
    if not tracker.doc_types:
        lines.append("    (waiting for first records)")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cache")
    ap.add_argument("total", type=int)
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("--interval", type=float, default=5.0)
    args = ap.parse_args()

    tracker = Tracker(pathlib.Path(args.cache))
    if args.once:
        # A rate needs two samples; take a short second one so --once still
        # reports throughput and ETA rather than a meaningless zero.
        tracker.poll()
        time.sleep(2.0)

    try:
        while True:
            tracker.poll()
            frame = render(tracker, args.total)
            if args.once:
                print(frame)
                return 0
            # Home the cursor and clear to end of screen: redrawing in place
            # avoids the flicker of a full clear.
            sys.stdout.write("\033[H\033[J" + frame + "\n")
            sys.stdout.flush()
            if tracker.count >= args.total:
                print("\n  complete.")
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n  (monitor stopped; the run continues)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
