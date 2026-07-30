"""Build a per-packet colour summary and test the green-stamp signal."""
import csv, json, pathlib, sys, collections
from concurrent.futures import ProcessPoolExecutor

import numpy as np

D = pathlib.Path.home() / "dev/mib-doc-challenge/data/train"
SOLUTION = pathlib.Path.home() / "dev/mib-doc-solution"


def colour(cid):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(D / f"{cid}.pdf"))
    green = red = 0
    try:
        for i in range(len(doc)):
            a = np.array(doc[i].render(scale=100 / 72).to_pil())[:, :, :3].astype(np.int16)
            R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
            green = max(green, int(((G - R > 60) & (G - B > 60)).sum()))
            red = max(red, int(((R - G > 60) & (R - B > 60)).sum()))
    finally:
        doc.close()
    return cid, green, red


def main():
    truth = {x["case_id"]: x for x in csv.DictReader(open(D.parent / "train_labels.csv"))}
    ids = sorted(truth)
    out = {}
    with ProcessPoolExecutor(max_workers=8) as pool:
        for cid, g, r in pool.map(colour, ids, chunksize=8):
            out[cid] = {"green": g, "red": r}
    json.dump(out, open("/tmp/colour.json", "w"))

    green = [c for c, v in out.items() if v["green"] > 500]
    print(f"green packets: {len(green)} / {len(ids)}")
    print(f"  truth: {dict(collections.Counter(truth[x]['adjudication'] for x in green))}")

    sys.path.insert(0, str(SOLUTION))
    from replay import load, decide
    from mib_pipeline.adjudicate import reference_receipt_date, _parse_date
    from mib_pipeline.extract import resolve

    pks = {p.case_id: p for p in load(str(pathlib.Path.home() /
           "dev/mib-artifacts/train_cache_2engine.jsonl"))}
    arrivals = []
    for pk in pks.values():
        rr = resolve(pk)
        if "arrival_date" in rr:
            d = _parse_date(rr["arrival_date"].value)
            if d:
                arrivals.append(d)
    ref = reference_receipt_date(arrivals)

    right = wrong = 0
    routes = collections.Counter()
    for cid in green:
        if cid not in pks:
            continue
        _, dec, reason, _ = decide(pks[cid], ref)
        if dec == truth[cid]["adjudication"]:
            right += 1
        else:
            wrong += 1
            routes[reason.split(":")[0]] += 1
    print(f"\ncurrently correct on green packets: {right}, wrong: {wrong}")
    print(f"  wrong via: {dict(routes)}")


if __name__ == "__main__":
    main()
