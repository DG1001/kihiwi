"""Trainingsfragen: je Abschnitt eine woertliche und eine umschriebene Frage.
Dokumente aus dem Testsatz bleiben draussen. Schreibt fortlaufend (wiederaufnehmbar).
    PYTHONPATH=. .venv/bin/python wissen/feinabstimmung/trainingsfragen.py <testsatz.json> <aus.jsonl> <anzahl>"""
import json, random, re, sqlite3, sys, threading, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from sprachdienst import konfig

TEST, AUS, ANZAHL = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
c = sqlite3.connect(f"file:{konfig.WURZEL / 'wissen' / 'index.db'}?mode=ro", uri=True)
test_rowids = [p["rowid"] for p in json.load(open(TEST, encoding="utf-8"))["paare"]]
marken = ",".join("?" * len(test_rowids))
test_doks = {r[0] for r in c.execute(f"SELECT dok_id FROM abschnitte WHERE rowid IN ({marken})", test_rowids)}
CODE = re.compile(r"\.(py|js|ts|json|csv|tsv|c|h|cpp|ipynb|yaml|yml|toml|sh|xml|svg|txt)$", re.I)
zeilen = [z for z in c.execute("SELECT rowid, text, pfad, dok_id FROM abschnitte "
                               "WHERE length(text) BETWEEN 400 AND 3000")
          if not CODE.search(z[2]) and z[3] not in test_doks]
random.seed(4711)
gezogen = random.sample(zeilen, min(len(zeilen), ANZAHL + 100))
fertig = set()
if AUS.exists():
    fertig = {json.loads(z)["rowid"] for z in AUS.read_text(encoding="utf-8").splitlines() if z.strip()}
print(f"{len(zeilen)} Abschnitte ausserhalb der {len(test_doks)} Testdokumente; {len(fertig)} schon fertig", flush=True)

url = konfig.LLM_URL.rstrip("/")
modell = json.load(urllib.request.urlopen(url + "/models", timeout=10))["data"][0]["id"]
AUFTRAG = ("Abschnitt aus den Unterlagen eines Labors:\n\n{t}\n\n"
           "Schreibe zwei kurze deutsche Fragen, die genau dieser Abschnitt beantwortet, "
           "so wie jemand im Labor sie mündlich stellen würde.\n"
           "1. \"woertlich\": benutzt die Fachwörter und Namen aus dem Abschnitt.\n"
           "2. \"umschrieben\": fragt dasselbe, vermeidet aber ausdrücklich die Fachwörter, "
           "Namen, Zahlen und Bezeichner des Abschnitts und umschreibt sie.\n"
           "Antworte nur mit JSON: {{\"woertlich\": \"...\", \"umschrieben\": \"...\"}}")
sperre = threading.Lock()
zaehler = {"ok": len(fertig), "verworfen": 0}

def eins(z):
    rowid, text, pfad, dok = z
    if zaehler["ok"] >= ANZAHL:
        return
    # Ohne Nachdenken: mit ihm verbrauchte Qwen3.8-Flash die 300 Token schon
    # vor der Antwort, und jede Anfrage fiel durch.
    rumpf = json.dumps({"model": modell, "temperature": 0.3, "max_tokens": 400,
                        "chat_template_kwargs": {"enable_thinking": False},
                        "messages": [{"role": "user", "content": AUFTRAG.format(t=text)}]}).encode()
    req = urllib.request.Request(url + "/chat/completions", data=rumpf,
                                 headers={"Content-Type": "application/json"})
    try:
        roh = json.load(urllib.request.urlopen(req, timeout=180))["choices"][0]["message"]["content"]
        j = json.loads(re.search(r"\{.*\}", roh, re.S).group(0))
        w, u = j["woertlich"].strip(), j["umschrieben"].strip()
        assert w and u
    except Exception:
        with sperre:
            zaehler["verworfen"] += 1
        return
    with sperre:
        if zaehler["ok"] >= ANZAHL:
            return
        with AUS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"rowid": rowid, "dok_id": dok, "woertlich": w, "umschrieben": u},
                               ensure_ascii=False) + "\n")
        zaehler["ok"] += 1
        if zaehler["ok"] % 100 == 0 or zaehler["ok"] == 5:
            print(zaehler, flush=True)

with ThreadPoolExecutor(1) as ex:   # ein Platz im Modellserver: mehr hilft nicht, Kiwi wartete nur laenger
    list(ex.map(eins, [z for z in gezogen if z[0] not in fertig]))
print("fertig", zaehler, flush=True)
