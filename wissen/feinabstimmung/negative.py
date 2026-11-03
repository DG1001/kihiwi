"""Schwere Negative: je Trainingsabschnitt ein aehnlicher Abschnitt aus einem
ANDEREN Dokument (Rang 3-15 nach der umschriebenen Frage, nie gleicher Text).
    negative.py <praefix-der-basis-einbettung> <index.db> <aus.json>"""
import json, random, sqlite3, sys
import numpy as np
P, DB, AUS = sys.argv[1:4]
A, ids = np.load(P + "_abschnitte.npy"), np.load(P + "_rowids.npy")
Q, tr = np.load(P + "_training.npy"), json.load(open(P + "_training.json"))
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
dok, txt = {}, {}
for r, d, t in c.execute("SELECT rowid, dok_id, text FROM abschnitte"):
    dok[r], txt[r] = d, t
random.seed(11)
neg = {}
for (rowid, d), q in zip(tr, Q):
    rang = ids[np.argsort(-(A @ q))[:60]]
    kandidaten = [int(r) for r in rang if dok.get(int(r)) != d and txt.get(int(r)) != txt[rowid]][2:15]
    if kandidaten:
        neg[rowid] = random.choice(kandidaten)
json.dump(neg, open(AUS, "w"))
print(len(neg), "von", len(tr), "mit Negativ")
