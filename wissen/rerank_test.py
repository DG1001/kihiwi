"""Prueft den Reranker-Klienten und seine Einbindung in die Suche.

    .venv/bin/python -m wissen.rerank_test

Braucht keinen laufenden Reranker: ein kleiner HTTP-Server im Test spielt ihn.
"""
import json
import logging
import sqlite3
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from . import index, rerank, vektor


class _Spieler(BaseHTTPRequestHandler):
    """Antwortet wie llama-server --reranking; Verhalten je Server einstellbar."""
    def do_POST(self):
        self.server.anfragen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        if self.server.warten:
            time.sleep(self.server.warten)
        if self.server.status != 200:
            self.send_response(self.server.status); self.end_headers(); return
        texte = self.server.anfragen[-1]["documents"]
        # Punkte: Laenge des Textes -- der laengste gewinnt
        rumpf = json.dumps({"results": [{"index": i, "relevance_score": float(len(t))}
                                        for i, t in enumerate(texte)]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.end_headers(); self.wfile.write(rumpf)

    def log_message(self, *a):
        pass


class _StillerServer(HTTPServer):
    def handle_error(self, request, client_address):
        pass                    # abgebrochene Verbindungen sind hier gewollt


def _server(status=200, warten=0.0):
    s = _StillerServer(("127.0.0.1", 0), _Spieler)
    s.anfragen, s.status, s.warten = [], status, warten
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_port}/v1/rerank"


def klient():
    s, url = _server()
    try:
        # Reihenfolge nach den Punkten, bester zuerst
        assert rerank.ordnen("Frage", ["kurz", "am allerlaengsten", "mittellang"], url=url) == [1, 2, 0]
        a = s.anfragen[-1]
        assert a["query"] == "Frage" and len(a["documents"]) == 3
        # Lange Texte gehen gekuerzt hinaus (die Messung lief mit 1500 Zeichen)
        rerank.ordnen("Frage", ["x" * 5000, "y"], url=url)
        assert len(s.anfragen[-1]["documents"][0]) == rerank.ZEICHEN
        # Nichts zu ordnen: keine Anfrage
        n = len(s.anfragen)
        assert rerank.ordnen("Frage", [], url=url) == [] and len(s.anfragen) == n
        # Abgeschaltet
        assert rerank.ordnen("Frage", ["a", "b"], url="") is None
    finally:
        s.shutdown()

    # Fehler und Zeitueberschreitung: None, und danach eine Weile gar nicht
    # fragen -- sonst kostet ein haengender Reranker jede Suche den Timeout.
    s, url = _server(status=500)
    try:
        rerank._still_bis = 0.0
        assert rerank.ordnen("Frage", ["a", "b"], url=url) is None
    finally:
        s.shutdown()
    s, url = _server(warten=1.0)
    try:
        rerank._still_bis = 0.0
        t0 = time.time()
        assert rerank.ordnen("Frage", ["a", "b"], url=url, timeout=0.2) is None
        assert time.time() - t0 < 0.9
        n = len(s.anfragen)
        assert rerank.ordnen("Frage", ["a", "b"], url=url, timeout=0.2) is None
        assert len(s.anfragen) == n                     # in der Pause nicht gefragt
    finally:
        rerank._still_bis = 0.0
        s.shutdown()
    s, url = _server()
    try:
        rerank._still_bis = 0.0
        # Kaputte Antwort (fehlender Index) -> None statt Ausnahme
        alt = _Spieler.do_POST
        def kaputt(self):
            self.server.anfragen.append(None)
            rumpf = b'{"results": [{"relevance_score": 1.0}]}'
            self.send_response(200); self.end_headers(); self.wfile.write(rumpf)
        _Spieler.do_POST = kaputt
        try:
            assert rerank.ordnen("Frage", ["a", "b"], url=url) is None
        finally:
            _Spieler.do_POST = alt
    finally:
        rerank._still_bis = 0.0
        s.shutdown()


def suche(t: Path):
    """index.suchen holt mehr Kandidaten, laesst sie ordnen und gibt die ersten
    `anzahl` zurueck; ohne Reranker bleibt es bei der Mischung."""
    db = t / "index.db"
    c = sqlite3.connect(db)
    c.executescript(index.SCHEMA)
    for i in range(30):
        index.dokument_setzen(c, "probe", f"probe:d{i}.md", f"Dokument {i}", "", "", f"f{i}",
                              [(f"Teil {i}", f"Pumpe " + "Wort " * i)])
    c.commit()
    alt = (vektor.aehnlich, rerank.ordnen, rerank.AN)
    gesehen = {}
    try:
        vektor.aehnlich = lambda frage, anzahl: []
        rerank.AN = True

        def umgekehrt(frage, texte, **_):
            gesehen["n"] = len(texte)
            return list(range(len(texte)))[::-1]
        rerank.ordnen = lambda frage, texte, **_: None       # Reranker fehlt
        ohne = [x.titel for x in index.suchen("Pumpe", 5, c=c)]
        rerank.ordnen = umgekehrt
        mit = [x.titel for x in index.suchen("Pumpe", 5, c=c)]
        assert len(ohne) == 5 and len(mit) == 5
        assert gesehen["n"] == rerank.KANDIDATEN, gesehen   # 24 Kandidaten geordnet
        assert mit != ohne and mit[0] not in ohne, (mit, ohne)
        # Abgeschaltet: genau die Mischung, ohne Aufruf
        rerank.AN = False
        gesehen.clear()
        assert [x.titel for x in index.suchen("Pumpe", 5, c=c)] == ohne and not gesehen
    finally:
        vektor.aehnlich, rerank.ordnen, rerank.AN = alt
        c.close()


def unit():
    logging.disable(logging.WARNING)             # die Warnungen sind hier gewollt
    try:
        klient()
    finally:
        logging.disable(logging.NOTSET)
    with tempfile.TemporaryDirectory() as t:
        suche(Path(t))


if __name__ == "__main__":
    unit()
    print("ok")
