"""Vektorindex neben der Volltextsuche -- gemessen, nicht geglaubt.

**Warum ueberhaupt.** Ueber 60 Fragenpaare gemessen, Ziel in den ersten acht:

    Fragen mit den Fachwoertern des Abschnitts   ohne Woerter des Abschnitts
      FTS5 mit Schlagwoertern   83,3 %             23,3 %
      nur Vektoren              75,0 %             41,7 %
      MISCHUNG (RRF)            90,0 %             38,3 %

Die Volltextsuche gewinnt, wo das Wort woertlich dasteht -- eine Zahl, ein
Bezeichner, `FENSTER_NM`. Der Vektorindex gewinnt, wo jemand die Sache
umschreibt. **Die Mischung ist bei woertlichen Fragen besser als beide
Einzelverfahren** und bei umschriebenen fast so gut wie Vektoren allein. Nicht
entweder-oder.

**Warum RRF und keine Punktemischung.** BM25-Punkte und Kosinusaehnlichkeit
haben keine gemeinsame Skala; sie zu addieren verlangt einen Faktor, den man
auf 60 Fragen ueberanpasst. Reciprocal Rank Fusion benutzt nur die RANGPLAETZE
und hat keinen zu stellenden Regler.

**Kosten, ehrlich.** 2,24 GB Modell, 1180 s CPU fuers einmalige Einbetten des
Korpus, 53 ms je Frage zur Laufzeit. Das Modell wird erst geladen, wenn
wirklich gesucht wird.

**Seit dem 03.11.2026 feinabgestimmt** (wissen/einbettung.json, Ablauf in
wissen/feinabstimmung/): umschriebene Fragen mit Reranker 68,3 -> 80,0 %.
Die Bildersuche bleibt beim Original (BILDER_MODELL).
"""
from __future__ import annotations

import json
import os

import sqlite3
import time
from datetime import datetime, timezone

import numpy as np

from . import index

VORGABE = "intfloat/multilingual-e5-large"
EINSTELLUNG_DATEI = index.konfig.WURZEL / "wissen" / "einbettung.json"


def einstellung(datei=EINSTELLUNG_DATEI) -> dict:
    """Lokale Wahl des Einbettungsmodells, nicht im Repo (Muster:
    einbettung.beispiel.json). Ohne Datei das Vorgabemodell.

    Eine kaputte Datei faellt NICHT still auf die Vorgabe zurueck: dann
    vergliche die Suche Fragen des einen Modells mit Abschnitten des anderen."""
    try:
        roh = open(datei, encoding="utf-8").read()
    except FileNotFoundError:
        return {}
    try:
        e = json.loads(roh)
    except json.JSONDecodeError as f:
        raise ValueError(f"{datei} ist kein gueltiges JSON: {f}") from None
    e = {k: v for k, v in e.items() if not k.startswith("_")}
    if e.get("pfad"):
        e["pfad"] = os.path.expanduser(e["pfad"])
    return e


EINSTELLUNG = einstellung()
# Jeder Vektor traegt den Namen seines Modells. Gesucht und gezaehlt wird nur
# mit denen des eingestellten; die uebrigen gelten als fehlend und werden beim
# naechsten Nachziehen neu eingebettet. Zwei Modelle mischen sich so nie.
MODELL = EINSTELLUNG.get("modell", VORGABE)


def bilder_modell(e: dict) -> str:
    """Modell fuer den Bilderkatalog: das Original, auch wenn fuer die
    Abschnitte ein eigenes eingestellt ist. Die Feinabstimmung vom 03.11.2026
    auf Fliesstext machte die Stichwort-Eintraege der Bilder ("Bild: netzteil
    schaltbild, optik") schlechter: 5 statt 7 von 7 echten Bilderfragen."""
    return e.get("bilder_modell", VORGABE)


BILDER_MODELL = bilder_modell(EINSTELLUNG)
# Modellablage; enthaelt nichts Eigenes. Nicht unter /tmp: das wird beim
# Neustart geleert, und die erste Wissensfrage danach lud 2,1 GB neu -- 43 s,
# die Antwort lief in die 45-s-Grenze und wurde abgebrochen (04.10.2026).
ZWISCHEN = os.path.expanduser("~/.cache/kihiwi-embed")
DIM = 1024

_matrix: tuple[np.ndarray, np.ndarray] | None = None   # (V, rowids)


# onnxruntime laesst seine Threadpools BUSY-WAITEN. Ohne Begrenzung nimmt es
# einen Thread je Kern (hier 20) und haelt sie am Spinnen, auch wenn nichts zu
# rechnen ist -- gemessen: nach einem Einbettungslauf im Sprachdienst 103
# Threads, 83 % CPU dauerhaft, und der Eventloop kam so selten durch, dass
# eine HTTP-Anfrage 12,9 s brauchte und die Anzeige stehenblieb.
#
# Vier Threads: die Suche bettet EINE Frage ein (53 ms), da bringt mehr
# Nebenlaeufigkeit nichts. Der Stapellauf wird dadurch langsamer -- das ist
# der richtige Tausch, denn er laeuft im Hintergrund, waehrend der Assistent
# antworten koennen muss.
# Uebersteuerbar, seit am 21.09.2026 auffiel, dass ein Stapellauf auf 20
# Kernen drei benutzt. Die Begruendung oben gilt fuer den SPRACHDIENST, wo
# der Threadpool im selben Prozess sitzt wie der Eventloop. Das Einlesen
# ueber die Kommandozeile ist ein eigener Prozess -- dort schadet das
# Spinnen niemandem, es nimmt nur CPU, die vLLM ohnehin nicht braucht.
#
# Die Vorgabe bleibt bewusst bei 4: wer sie erhoeht, muss wissen, dass er es
# im richtigen Prozess tut. wissen/__main__.py setzt sie fuer Stapellaeufe.
THREADS = int(os.environ.get("KIHIWI_VEKTOR_THREADS", "4"))

_modelle: dict = {}


def _laden(name: str | None = None):
    """Erst beim ersten Gebrauch. 2,24 GB will man nicht beim Start bezahlen,
    wenn niemand die Unterlagen durchsucht -- und das Bildermodell nicht, wenn
    niemand ein Bild sucht."""
    name = name or MODELL
    if name not in _modelle:
        from fastembed import TextEmbedding
        if name == MODELL and EINSTELLUNG.get("pfad"):
            # Ein eigenes Modell (feinabgestimmtes e5) als ONNX im Ordner
            # `pfad`. Mittelwert ueber die Token wie beim Original -- am
            # 03.11.2026 nachgeprueft: fastembed, gespeicherte Vektoren und
            # transformers mit Mittelwert stimmen auf 1e-5 ueberein.
            from fastembed.common.model_description import ModelSource, PoolingType
            if MODELL not in {m["model"] for m in TextEmbedding.list_supported_models()}:
                TextEmbedding.add_custom_model(
                    model=MODELL, pooling=PoolingType.MEAN, normalization=True,
                    sources=ModelSource(hf=MODELL), dim=DIM, model_file="model.onnx",
                    additional_files=["model.onnx_data"])
            _modelle[name] = TextEmbedding(MODELL, specific_model_path=EINSTELLUNG["pfad"],
                                           threads=THREADS)
        else:
            _modelle[name] = TextEmbedding(name, cache_dir=ZWISCHEN, threads=THREADS)
    return _modelle[name]


def einbetten(texte: list[str], als_frage: bool, modell: str | None = None) -> np.ndarray:
    """e5 verlangt die Praefixe; ohne sie verliert das Modell messbar.
    modell: None fuer die Abschnitte, BILDER_MODELL fuer den Bilderkatalog."""
    marke = "query: " if als_frage else "passage: "
    m = _laden(modell)
    V = np.array(list(m.embed([marke + t for t in texte])), dtype=np.float32)
    # Ein Modell, das NaN liefert, hat hier schon einmal eine stille Null
    # erzeugt, die wie ein Messergebnis aussah. Nie wieder ungeprueft.
    if np.isnan(V).any():
        raise RuntimeError(f"{modell or MODELL} liefert NaN auf dieser Maschine")
    return V / np.linalg.norm(V, axis=1, keepdims=True)


def bestand(c: sqlite3.Connection | None = None) -> tuple[int, int]:
    """(mit Vektor, gesamt)."""
    eigen = c is None
    c = c or index.lesen()
    try:
        g = c.execute("SELECT COUNT(*) FROM abschnitte").fetchone()[0]
        # Ueber die Hashes zaehlen: die Zuordnung Abschnitt->Vektor laeuft
        # ueber den Inhalt, nicht ueber die Zeilennummer.
        vorhanden = {r[0] for r in c.execute("SELECT hash FROM vektoren WHERE modell = ?",
                                             (MODELL,))}
        m = sum(1 for (t,) in c.execute("SELECT text FROM abschnitte")
                if index.abschnitt_hash(t) in vorhanden)
        return m, g
    finally:
        if eigen:
            c.close()


def nachtragen(block: int = 64) -> dict:
    """Fehlende Abschnitte einbetten. Fortsetzbar, blockweise gespeichert."""
    c = index.verbinden()
    vorhanden = {r[0] for r in c.execute("SELECT hash FROM vektoren WHERE modell = ?", (MODELL,))}
    offen = [(h, t) for h, t in
             ((index.abschnitt_hash(t), t)
              for (t,) in c.execute("SELECT text FROM abschnitte"))
             if h not in vorhanden]
    # Doppelte Abschnitte (gleicher Text) nur einmal einbetten.
    offen = list({h: t for h, t in offen}.items())
    if not offen:
        c.close()
        return {"offen": 0, "erledigt": 0, "sekunden": 0.0}
    t0 = time.time()
    stand = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fertig = 0
    for i in range(0, len(offen), block):
        teil = offen[i:i + block]
        V = einbetten([t[:2000] for _, t in teil], als_frage=False)
        c.executemany("INSERT OR REPLACE INTO vektoren (hash,v,modell,stand) "
                      "VALUES (?,?,?,?)",
                      [(h, V[j].tobytes(), MODELL, stand)
                       for j, (h, _) in enumerate(teil)])
        c.commit()
        fertig += len(teil)
        print(f"    {fertig}/{len(offen)}")
    c.close()
    global _matrix
    _matrix = None                      # neu laden
    return {"offen": len(offen), "erledigt": fertig, "sekunden": time.time() - t0}


def _matrix_laden():
    """Vektoren in der Reihenfolge der Abschnitte. 2774 x 1024 sind 11 MB."""
    global _matrix
    if _matrix is not None:
        return _matrix
    c = index.lesen()
    try:
        roh = {h: v for h, v in c.execute("SELECT hash, v FROM vektoren WHERE modell = ?",
                                           (MODELL,))}
        zeilen = c.execute("SELECT rowid, text FROM abschnitte ORDER BY rowid").fetchall()
    finally:
        c.close()
    V, ids = [], []
    for rid, t in zeilen:
        b = roh.get(index.abschnitt_hash(t))
        if b is not None:
            V.append(np.frombuffer(b, dtype=np.float32))
            ids.append(rid)
    _matrix = ((np.vstack(V) if V else np.zeros((0, DIM), np.float32)),
               np.array(ids, dtype=np.int64))
    return _matrix


def aehnlich(frage: str, anzahl: int) -> list[int]:
    """rowids der aehnlichsten Abschnitte. Leer, wenn nichts eingebettet ist."""
    V, ids = _matrix_laden()
    if not len(ids):
        return []
    q = einbetten([frage], als_frage=True)[0]
    s = V @ q
    return [int(ids[j]) for j in np.argsort(-s)[:anzahl]]


def verschmelzen(*listen: list[int], anzahl: int, k: int = 60) -> list[int]:
    """Reciprocal Rank Fusion: nur Rangplaetze, kein zu stellender Regler."""
    punkte: dict[int, float] = {}
    for L in listen:
        for rang, r in enumerate(L, 1):
            punkte[r] = punkte.get(r, 0.0) + 1.0 / (k + rang)
    return [r for r, _ in sorted(punkte.items(), key=lambda x: -x[1])][:anzahl]
