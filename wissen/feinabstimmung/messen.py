"""Ende zu Ende ueber index.suchen: richtiger Abschnitt unter den ersten acht.
    PYTHONPATH=. .venv/bin/python wissen/feinabstimmung/messen.py <fragen.json>
    KIHIWI_RERANK=0 ... schaltet den Reranker ab."""
import json, sys, time
from wissen import index, rerank

FRAGEN = json.load(open(sys.argv[1], encoding="utf-8"))["paare"]
c = index.lesen()
ziel = {p["rowid"]: c.execute("SELECT text FROM abschnitte WHERE rowid=?", (p["rowid"],)).fetchone()[0]
        for p in FRAGEN}
index.suchen("Aufwärmen", 8, c=c)
print("Reranker", "an" if rerank.AN else "aus")
for art in ("woertlich", "umschrieben"):
    treffer, rr, zeiten = 0, 0.0, []
    for p in FRAGEN:
        t0 = time.time()
        er = [t.text for t in index.suchen(p[art], 8, c=c)]
        zeiten.append(time.time() - t0)
        if ziel[p["rowid"]] in er:
            treffer += 1; rr += 1 / (er.index(ziel[p["rowid"]]) + 1)
    zeiten.sort()
    print(f"{art}: {treffer}/{len(FRAGEN)} = {100*treffer/len(FRAGEN):.1f} % · MRR {rr/len(FRAGEN):.3f}"
          f" · Median {1000*zeiten[len(zeiten)//2]:.0f} ms, max {1000*zeiten[-1]:.0f} ms")
