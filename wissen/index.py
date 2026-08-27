"""Volltextindex über die Laborunterlagen.

**SQLite FTS5, kein Vektorindex.** Bewusst: FTS5 ist in Python eingebaut, braucht
kein Embedding-Modell, keinen GPU-Speicher (den Ornith ohnehin belegt) und keinen
zusätzlichen Dienst. Für Fachtexte ist Stichwortsuche stark -- gefragt wird nach
"Siliziumnitrid", "JEOL", Typbezeichnungen und Messgrößen, also nach exakten
Begriffen. Semantische Suche wäre der nächste Schritt, nicht der erste.

Ein Treffer ist immer ein ABSCHNITT mit Quellenangabe, nie ein ganzes Dokument:
das Modell soll zitieren können, was es benutzt hat.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sprachdienst import konfig

DB = Path(konfig.WURZEL / "wissen" / "index.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS dokumente (
    id        INTEGER PRIMARY KEY,
    quelle    TEXT NOT NULL,          -- Name der Quelle aus quellen.json
    pfad      TEXT NOT NULL UNIQUE,   -- eindeutige Kennung innerhalb der Quelle
    titel     TEXT,
    herkunft  TEXT,                   -- URL oder Dateipfad zum Nachschlagen
    stand     TEXT,                   -- Änderungszeit bzw. Commit
    fingerab  TEXT                    -- Inhalts-Hash: erspart erneutes Einlesen
);
CREATE VIRTUAL TABLE IF NOT EXISTS abschnitte USING fts5(
    text,
    ueberschrift,
    pfad     UNINDEXED,
    dok_id   UNINDEXED,
    nr       UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


@dataclass
class Treffer:
    text: str
    ueberschrift: str
    titel: str
    quelle: str
    herkunft: str
    punkte: float


def verbinden() -> sqlite3.Connection:
    """Zum Schreiben: legt das Schema an."""
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.executescript(SCHEMA)
    return c


def lesen() -> sqlite3.Connection:
    """Zum Suchen: NUR lesend, ohne Schema-Anlage.

    verbinden() ruft executescript(), und das nimmt eine Schreibsperre --
    bei jeder Suche. Im Dienst blockierte das die Werkzeugausfuehrung und der
    Assistent blieb stumm stehen. Suchen darf niemals schreiben.
    """
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)


def fingerabdruck_bekannt(c, pfad: str, fingerab: str) -> bool:
    r = c.execute("SELECT fingerab FROM dokumente WHERE pfad=?", (pfad,)).fetchone()
    return bool(r and r[0] == fingerab)


def dokument_setzen(c, quelle, pfad, titel, herkunft, stand, fingerab,
                    abschnitte: list[tuple[str, str]]):
    """Ersetzt ein Dokument samt Abschnitten. abschnitte = [(ueberschrift, text)]."""
    alt = c.execute("SELECT id FROM dokumente WHERE pfad=?", (pfad,)).fetchone()
    if alt:
        c.execute("DELETE FROM abschnitte WHERE dok_id=?", (alt[0],))
        c.execute("DELETE FROM dokumente WHERE id=?", (alt[0],))
    cur = c.execute(
        "INSERT INTO dokumente (quelle,pfad,titel,herkunft,stand,fingerab) "
        "VALUES (?,?,?,?,?,?)", (quelle, pfad, titel, herkunft, stand, fingerab))
    did = cur.lastrowid
    c.executemany(
        "INSERT INTO abschnitte (text,ueberschrift,pfad,dok_id,nr) VALUES (?,?,?,?,?)",
        [(t, u, pfad, did, i) for i, (u, t) in enumerate(abschnitte)])
    return did


# FTS5 hat eine eigene Abfragesprache; ein roher Fragesatz mit Umlauten,
# Fragezeichen und Bindestrichen fuehrt zu Syntaxfehlern. Deshalb wird die
# Frage in Begriffe zerlegt und mit OR verknuepft.
_WORT = re.compile(r"[^\W\d_][\w\-]*|\d+[\w\-]*", re.UNICODE)
_STOPP = {
    "und", "oder", "der", "die", "das", "den", "dem", "des", "ein", "eine",
    "einer", "eines", "einem", "einen", "ist", "sind", "war", "waren", "wie",
    "was", "wer", "wo", "wann", "warum", "welche", "welcher", "welches", "wir",
    "ihr", "sie", "er", "es", "ich", "du", "man", "mit", "von", "für", "fuer",
    "auf", "aus", "bei", "nach", "über", "ueber", "unter", "zum", "zur", "zu",
    "im", "in", "am", "an", "des", "dass", "nicht", "auch", "noch", "denn",
    "kann", "können", "koennen", "soll", "sollen", "muss", "müssen", "haben",
    "hat", "hatte", "sich", "mir", "mich", "uns", "bitte", "mal", "etwa",
    "ungefähr", "ungefaehr", "eigentlich", "kiwi",
}


def begriffe(frage: str) -> list[str]:
    w = [x.lower() for x in _WORT.findall(frage)]
    return [x for x in w if len(x) > 2 and x not in _STOPP]


def suchen(frage: str, anzahl: int = 5, c: sqlite3.Connection | None = None
           ) -> list[Treffer]:
    eigen = c is None
    try:
        c = c or lesen()
    except sqlite3.OperationalError:
        return []          # Index noch nicht angelegt
    try:
        w = begriffe(frage)
        if not w:
            return []
        # Anführungszeichen um jeden Begriff: sonst deutet FTS5 Bindestriche
        # als Operatoren und scheitert an "Siliziumnitrid-Fenster".
        ausdruck = " OR ".join(f'"{x}"' for x in w)
        zeilen = c.execute("""
            SELECT a.text, a.ueberschrift, d.titel, d.quelle, d.herkunft,
                   bm25(abschnitte, 1.0, 2.0) AS punkte
            FROM abschnitte a JOIN dokumente d ON d.id = a.dok_id
            WHERE abschnitte MATCH ?
            ORDER BY punkte LIMIT ?""", (ausdruck, anzahl)).fetchall()
        return [Treffer(*z) for z in zeilen]
    except sqlite3.OperationalError:
        return []
    finally:
        if eigen:
            c.close()


def stand() -> dict:
    c = verbinden()
    try:
        d = c.execute("SELECT COUNT(*) FROM dokumente").fetchone()[0]
        a = c.execute("SELECT COUNT(*) FROM abschnitte").fetchone()[0]
        je = c.execute("SELECT quelle, COUNT(*) FROM dokumente GROUP BY quelle").fetchall()
        return {"dokumente": d, "abschnitte": a, "quellen": dict(je)}
    finally:
        c.close()
