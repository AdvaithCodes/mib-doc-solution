"""Per-field accuracy diagnostic: where are the extraction points going?"""
import csv, json, sys, collections

WEIGHTS = {"applicant_name": 5, "species_code": 6, "home_world": 5, "visa_class": 5,
           "sponsor_id": 5, "arrival_date": 4, "declared_purpose": 3,
           "risk_flags": 8, "fee_status": 4}

truth = {r["case_id"]: r for r in csv.DictReader(open(sys.argv[1]))}
preds = {}
for line in open(sys.argv[2]):
    line = line.strip()
    if line:
        r = json.loads(line)
        preds[r["case_id"]] = r


def nf(v):
    return "|".join(sorted(x.strip().lower() for x in str(v).split("|") if x.strip()))


ok = collections.Counter()
bad = collections.Counter()
blank = collections.Counter()
examples = collections.defaultdict(list)

for cid, t in truth.items():
    p = preds.get(cid)
    if not p:
        continue
    for f in WEIGHTS:
        tv, pv = t[f].strip(), str(p.get(f, "")).strip()
        if f == "risk_flags":
            match = nf(tv) == nf(pv)
        else:
            match = tv.lower() == pv.lower()
        if match:
            ok[f] += 1
        else:
            bad[f] += 1
            if not pv:
                blank[f] += 1
            if len(examples[f]) < 4:
                examples[f].append((cid, tv, pv or "<blank>"))

n = len(preds)
tot_lost = 0
print(f"{'field':<18}{'w':>3}{'ok':>6}{'wrong':>7}{'blank':>7}{'acc':>8}{'pts lost':>10}")
print("-" * 60)
for f, w in sorted(WEIGHTS.items(), key=lambda kv: -kv[1]):
    acc = ok[f] / n if n else 0
    lost = w * bad[f]
    tot_lost += lost
    print(f"{f:<18}{w:>3}{ok[f]:>6}{bad[f]:>7}{blank[f]:>7}{acc:>7.1%}{lost:>10}")

max_raw = sum(WEIGHTS.values()) * n
print("-" * 60)
print(f"raw {max_raw - tot_lost}/{max_raw} -> {50*(max_raw-tot_lost)/max_raw:.2f}/50 extraction")
print(f"\nblank is a parsing miss; wrong-but-filled is a normalization miss.\n")
for f in sorted(WEIGHTS, key=lambda k: -WEIGHTS[k] * bad[k]):
    if examples[f]:
        print(f"--- {f} (weight {WEIGHTS[f]}, {bad[f]} wrong) ---")
        for cid, tv, pv in examples[f]:
            print(f"    {cid}  truth={tv!r}  pred={pv!r}")
