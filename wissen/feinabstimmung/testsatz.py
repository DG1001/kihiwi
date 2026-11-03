"""Testsatz: 60 Abschnitte ziehen, je zwei Fragen erzeugen (woertlich /
umschrieben). Ausgabe bleibt lokal: sie enthaelt Inhalte der Unterlagen.
    PYTHONPATH=. .venv/bin/python wissen/feinabstimmung/testsatz.py <aus.json>"""
import json, random, re, sqlite3, sys, urllib.request
from sprachdienst import konfig

AUS = sys.argv[1]
c = sqlite3.connect(f"file:{konfig.WURZEL / 'wissen' / 'index.db'}?mode=ro", uri=True)
CODE = re.compile(r"\.(py|js|ts|json|csv|tsv|c|h|cpp|ipynb|yaml|yml|toml|sh|xml|svg|txt)$", re.I)
zeilen = [z for z in c.execute(
    "SELECT rowid, text, pfad FROM abschnitte WHERE length(text) BETWEEN 400 AND 3000")
    if not CODE.search(z[2])]
random.seed(20261103)
gezogen = random.sample(zeilen, 75)          # Reserve fuer unbrauchbare Antworten

url = konfig.LLM_URL.rstrip("/")
modell = json.load(urllib.request.urlopen(url + "/models", timeout=10))["data"][0]["id"]
AUFTRAG = ("Abschnitt aus den Unterlagen eines Labors:\n\n{t}\n\n"
           "Schreibe zwei kurze deutsche Fragen, die genau dieser Abschnitt beantwortet, "
           "so wie jemand im Labor sie mündlich stellen würde.\n"
           "1. \"woertlich\": benutzt die Fachwörter und Namen aus dem Abschnitt.\n"
           "2. \"umschrieben\": fragt dasselbe, vermeidet aber ausdrücklich die Fachwörter, "
           "Namen, Zahlen und Bezeichner des Abschnitts und umschreibt sie.\n"
           "Antworte nur mit JSON: {{\"woertlich\": \"...\", \"umschrieben\": \"...\"}}")
paare = []
for rowid, text, pfad in gezogen:
    if len(paare) == 60:
        break
    rumpf = json.dumps({"model": modell, "temperature": 0.3, "max_tokens": 400,
                        "chat_template_kwargs": {"enable_thinking": False},
                        "messages": [{"role": "user", "content": AUFTRAG.format(t=text)}]}).encode()
    req = urllib.request.Request(url + "/chat/completions", data=rumpf,
                                 headers={"Content-Type": "application/json"})
    try:
        roh = json.load(urllib.request.urlopen(req, timeout=120))["choices"][0]["message"]["content"]
        j = json.loads(re.search(r"\{.*\}", roh, re.S).group(0))
        assert j["woertlich"].strip() and j["umschrieben"].strip()
    except Exception as e:
        print("verworfen", rowid, repr(e)[:80]); continue
    paare.append({"rowid": rowid, "pfad": pfad, "woertlich": j["woertlich"].strip(),
                  "umschrieben": j["umschrieben"].strip()})
    print(len(paare), end=" ", flush=True)
json.dump({"modell": modell, "saat": 20261103, "paare": paare},
          open(AUS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n", len(paare), "Paare")
