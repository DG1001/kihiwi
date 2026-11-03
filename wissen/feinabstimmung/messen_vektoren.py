"""Messung mit vorab berechneten Vektoren (Basis oder feinabgestimmt), ohne den
Index anzufassen: vektor._matrix und die Frage-Einbettung werden ersetzt.
    messen_vektoren.py <praefix> <fragen.json>"""
import json, sys
import numpy as np
from wissen import index, rerank, vektor

P, FRAGEN = sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8"))["paare"]
A, ids = np.load(P + "_abschnitte.npy"), np.load(P + "_rowids.npy")
Q = np.load(P + "_fragen.npy")
nach_text = {t: i for i, t in enumerate(json.load(open(P + "_fragen.json", encoding="utf-8")))}
vektor._matrix = (A, ids)
vektor.einbetten = lambda texte, als_frage: Q[[nach_text[t] for t in texte]]
c = index.lesen()
ziel = {p["rowid"]: c.execute("SELECT text FROM abschnitte WHERE rowid=?", (p["rowid"],)).fetchone()[0]
        for p in FRAGEN}
print("Modell:", P.rsplit("/", 1)[-1])
for art in ("woertlich", "umschrieben"):
    fragen = [p[art] for p in FRAGEN]
    v8 = v24 = 0
    for p, f in zip(FRAGEN, fragen):
        rang = list(ids[np.argsort(-(A @ Q[nach_text[f]]))[:24]])
        v8 += p["rowid"] in rang[:8]; v24 += p["rowid"] in rang
    zeilen = [f"  nur Vektoren: {v8}/60 unter 8, {v24}/60 unter 24"]
    for an in (False, True):
        rerank.AN = an
        t = rr = 0
        for p, f in zip(FRAGEN, fragen):
            er = [x.text for x in index.suchen(f, 8, c=c)]
            if ziel[p["rowid"]] in er:
                t += 1; rr += 1 / (er.index(ziel[p["rowid"]]) + 1)
        zeilen.append(f"  Suchweg {'mit' if an else 'ohne'} Reranker: {t}/60 = {100*t/60:.1f} % · MRR {rr/60:.3f}")
    print(f"[{art}]\n" + "\n".join(zeilen), flush=True)
