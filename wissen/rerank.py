"""Reranker: ordnet die Kandidaten der Suche neu.

bge-reranker-v2-m3 (Q8) ueber `llama-server --reranking` auf der GPU, als
eigener Dienst aus dienste.sh. Gemessen am 03.11.2026 an 60 Fragenpaaren,
richtiger Abschnitt unter den ersten acht: umschriebene Fragen 56,7 -> 68,3 %,
woertliche 95,0 -> 98,3 %, gut 0,2 s je Suche (Zahlen in technisch.md). Auf
der CPU war es zehnmal langsamer und frass den Speicher -- deshalb ein Server
auf der GPU statt einer Bibliothek im Prozess.

Faellt er aus, bleibt es bei der Reihenfolge der Mischung (RRF). Die Suche
darf an ihm nie haengen, genau wie am Vektorteil.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request

URL = os.environ.get("KIHIWI_RERANK", "http://127.0.0.1:8912/v1/rerank")
AN = URL not in ("", "0", "aus", "nein")
KANDIDATEN = int(os.environ.get("KIHIWI_RERANK_KANDIDATEN", "24"))
ZEICHEN = 1500          # so gemessen; ganze Abschnitte brachten nichts mehr
TIMEOUT = float(os.environ.get("KIHIWI_RERANK_TIMEOUT", "3"))
PAUSE_S = 60            # nach einem Ausfall so lange nicht fragen

log = logging.getLogger("kihiwi.rerank")
_still_bis = 0.0


def ordnen(frage: str, texte: list[str], url: str | None = None,
           timeout: float | None = None) -> list[int] | None:
    """Indizes von `texte`, bester zuerst. None, wenn der Reranker fehlt,
    abgeschaltet ist oder nicht sauber antwortet -- dann gilt die bisherige
    Reihenfolge.

    Nach einem Fehler wird PAUSE_S lang nicht gefragt: ein haengender Server
    kostete sonst jede Suche den vollen Timeout."""
    global _still_bis
    url = URL if url is None else url
    if not url or url in ("0", "aus", "nein"):
        return None
    if not texte:
        return []
    if time.time() < _still_bis:
        return None
    rumpf = json.dumps({"query": frage, "documents": [t[:ZEICHEN] for t in texte]}).encode()
    req = urllib.request.Request(url, data=rumpf, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT if timeout is None else timeout) as a:
            ergebnis = json.load(a)["results"]
        punkte = {int(e["index"]): float(e["relevance_score"]) for e in ergebnis}
        if sorted(punkte) != list(range(len(texte))):
            raise ValueError(f"{len(punkte)} Punkte fuer {len(texte)} Texte")
    except Exception as e:                       # noqa: BLE001 -- nie die Suche mitreissen
        log.warning("Reranker uebersprungen: %r", e)
        _still_bis = time.time() + PAUSE_S
        return None
    return sorted(range(len(texte)), key=lambda i: -punkte[i])
