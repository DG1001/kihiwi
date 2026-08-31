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
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

import numpy as np

from . import index

MODELL = "intfloat/multilingual-e5-large"
ZWISCHEN = "/tmp/kihiwi-embed"     # Modellablage; enthaelt nichts Eigenes
DIM = 1024

_modell = None
_matrix: tuple[np.ndarray, np.ndarray] | None = None   # (V, rowids)


def _laden():
    """Erst beim ersten Gebrauch. 2,24 GB will man nicht beim Start bezahlen,
    wenn niemand die Unterlagen durchsucht."""
    global _modell
    if _modell is None:
        from fastembed import TextEmbedding
        _modell = TextEmbedding(MODELL, cache_dir=ZWISCHEN)
    return _modell


def einbetten(texte: list[str], als_frage: bool) -> np.ndarray:
    """e5 verlangt die Praefixe; ohne sie verliert das Modell messbar."""
    marke = "query: " if als_frage else "passage: "
    m = _laden()
    V = np.array(list(m.embed([marke + t for t in texte])), dtype=np.float32)
    # Ein Modell, das NaN liefert, hat hier schon einmal eine stille Null
    # erzeugt, die wie ein Messergebnis aussah. Nie wieder ungeprueft.
    if np.isnan(V).any():
        raise RuntimeError(f"{MODELL} liefert NaN auf dieser Maschine")
    return V / np.linalg.norm(V, axis=1, keepdims=True)


def bestand(c: sqlite3.Connection | None = None) -> tuple[int, int]:
    """(mit Vektor, gesamt)."""
    eigen = c is None
    c = c or index.lesen()
    try:
        g = c.execute("SELECT COUNT(*) FROM abschnitte").fetchone()[0]
        # Ueber die Hashes zaehlen: die Zuordnung Abschnitt->Vektor laeuft
        # ueber den Inhalt, nicht ueber die Zeilennummer.
        vorhanden = {r[0] for r in c.execute("SELECT hash FROM vektoren")}
        m = sum(1 for (t,) in c.execute("SELECT text FROM abschnitte")
                if index.abschnitt_hash(t) in vorhanden)
        return m, g
    finally:
        if eigen:
            c.close()


def nachtragen(block: int = 64) -> dict:
    """Fehlende Abschnitte einbetten. Fortsetzbar, blockweise gespeichert."""
    c = index.verbinden()
    vorhanden = {r[0] for r in c.execute("SELECT hash FROM vektoren")}
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
        roh = {h: v for h, v in c.execute("SELECT hash, v FROM vektoren")}
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
