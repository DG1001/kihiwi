"""Alle Abschnitte und die Testfragen mit einem e5-Modell einbetten (GPU).
Wie im Dienst: "passage: " + text[:2000], "query: " + frage, Mittelwert, normiert, 512 Token.
    einbetten.py <modellordner> <ausgabe-praefix> <index.db> <fragen.json> [training.jsonl]"""
import json, sqlite3, sys, time
import numpy as np, torch, torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer

MODELL, AUS, DB, FRAGEN = sys.argv[1:5]
tok = AutoTokenizer.from_pretrained(MODELL)
m = AutoModel.from_pretrained(MODELL, torch_dtype=torch.float16).cuda().eval()

@torch.no_grad()
def einbetten(texte, stapel=64):
    aus = []
    for i in range(0, len(texte), stapel):
        b = tok(texte[i:i + stapel], max_length=512, truncation=True, padding=True,
                return_tensors="pt").to("cuda")
        h = m(**b).last_hidden_state.float()
        maske = b["attention_mask"][..., None].float()
        aus.append(F.normalize((h * maske).sum(1) / maske.sum(1), dim=-1).cpu().numpy())
    return np.vstack(aus).astype(np.float32)

import os
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
zeilen = c.execute("SELECT rowid, text FROM abschnitte ORDER BY rowid").fetchall()
t0 = time.time()
if not os.path.exists(AUS + "_abschnitte.npy"):        # schon da: nur die Fragen
    # Nach Laenge sortiert einbetten: weniger Polsterung, deutlich schneller
    reihenfolge = sorted(range(len(zeilen)), key=lambda i: len(zeilen[i][1]))
    V = einbetten(["passage: " + zeilen[i][1][:2000] for i in reihenfolge])
    A = np.zeros_like(V); A[reihenfolge] = V
    np.save(AUS + "_abschnitte.npy", A)
    np.save(AUS + "_rowids.npy", np.array([z[0] for z in zeilen], dtype=np.int64))
fragen = [p[a] for p in json.load(open(FRAGEN, encoding="utf-8"))["paare"]
          for a in ("woertlich", "umschrieben")]
np.save(AUS + "_fragen.npy", einbetten(["query: " + f for f in fragen]))
json.dump(fragen, open(AUS + "_fragen.json", "w", encoding="utf-8"), ensure_ascii=False)
if len(sys.argv) > 5:
    tr = [json.loads(z) for z in open(sys.argv[5], encoding="utf-8") if z.strip()]
    np.save(AUS + "_training.npy", einbetten(["query: " + j["umschrieben"] for j in tr]))
    json.dump([[j["rowid"], j["dok_id"]] for j in tr], open(AUS + "_training.json", "w"))
print(f"{len(zeilen)} Abschnitte, {len(fragen)} Fragen, {time.time() - t0:.0f} s")
