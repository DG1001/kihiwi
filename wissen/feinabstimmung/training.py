"""Feinabstimmung von multilingual-e5-large auf Frage-Abschnitt-Paare (InfoNCE).
In-Batch-Negative plus je ein schweres Negativ aus einem ANDEREN Dokument.
Wortvektoren eingefroren (spart ~3 GB Optimiererzustand), Gradient-Checkpointing.
    training.py <basis> <fragen.jsonl> <negative.json> <index.db> <ausgabe> [epochen] [lr]"""
import json, random, sqlite3, subprocess, sys, time
import torch, torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer

BASIS, FRAGEN, NEG, DB, AUS = sys.argv[1:6]
EPOCHEN = int(sys.argv[6]) if len(sys.argv) > 6 else 1
LR = float(sys.argv[7]) if len(sys.argv) > 7 else 1e-5
STAPEL, TEMP, P_LEN, Q_LEN, MIN_FREI_GB = 16, 0.01, 384, 64, 3.0

def frei_gb():
    for z in open("/proc/meminfo"):
        if z.startswith("MemAvailable:"):
            return int(z.split()[1]) / 2**20
    return 99.0

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
text = lambda r: c.execute("SELECT text FROM abschnitte WHERE rowid=?", (r,)).fetchone()[0]
paare = []
neg = {int(k): v for k, v in json.load(open(NEG)).items()}
for z in open(FRAGEN, encoding="utf-8"):
    j = json.loads(z)
    for art in ("woertlich", "umschrieben"):
        if neg.get(j["rowid"]):
            paare.append((j[art], j["rowid"], neg[j["rowid"]]))
print(len(paare), "Paare", flush=True)

tok = AutoTokenizer.from_pretrained(BASIS)
m = AutoModel.from_pretrained(BASIS).cuda()
m.gradient_checkpointing_enable()
m.embeddings.word_embeddings.weight.requires_grad_(False)
m.train()
opt = torch.optim.AdamW([p for p in m.parameters() if p.requires_grad], lr=LR, weight_decay=0.01)

def kodieren(texte, laenge):
    b = tok(texte, max_length=laenge, truncation=True, padding=True, return_tensors="pt").to("cuda")
    with torch.autocast("cuda", dtype=torch.bfloat16):
        h = m(**b).last_hidden_state
    maske = b["attention_mask"][..., None].float()
    return F.normalize((h.float() * maske).sum(1) / maske.sum(1), dim=-1)

def stapel_bilden(paare):
    # Kein Abschnitt zweimal im selben Stapel: sonst waere ein Positiv zugleich Negativ
    random.shuffle(paare)
    stapel, rest = [], list(paare)
    while rest:
        s, gesehen, weiter = [], set(), []
        for p in rest:
            if len(s) < STAPEL and p[1] not in gesehen and p[2] not in gesehen:
                s.append(p); gesehen.update((p[1], p[2]))
            else:
                weiter.append(p)
        stapel.append(s); rest = weiter
    return stapel

random.seed(7)
schritte = sum(len(stapel_bilden(list(paare))) for _ in range(EPOCHEN))
plan = torch.optim.lr_scheduler.LambdaLR(
    opt, lambda s: min(1.0, (s + 1) / max(1, schritte // 10)) * max(0.0, 1 - s / schritte))
random.seed(7)
t0, schritt = time.time(), 0
for ep in range(EPOCHEN):
    for s in stapel_bilden(list(paare)):
        if frei_gb() < MIN_FREI_GB:
            print(f"ABBRUCH: nur {frei_gb():.1f} GB frei", flush=True); sys.exit(2)
        q = kodieren(["query: " + p[0] for p in s], Q_LEN)
        d = kodieren(["passage: " + text(p[1])[:2000] for p in s]
                     + ["passage: " + text(p[2])[:2000] for p in s], P_LEN)
        logits = q @ d.T / TEMP
        loss = F.cross_entropy(logits, torch.arange(len(s), device="cuda"))
        loss.backward()
        torch.nn.utils.clip_grad_norm_(m.parameters(), 1.0)
        opt.step(); plan.step(); opt.zero_grad(set_to_none=True)
        schritt += 1
        if schritt % 20 == 0 or schritt == schritte:
            print(f"Epoche {ep+1} Schritt {schritt}/{schritte} Verlust {loss.item():.3f} "
                  f"frei {frei_gb():.1f} GB {time.time()-t0:.0f} s", flush=True)
m.save_pretrained(AUS); tok.save_pretrained(AUS)
print("gespeichert", AUS, flush=True)
