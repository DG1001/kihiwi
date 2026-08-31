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

import hashlib
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
    schlagwoerter,                    -- erschlossen, nicht aus der Quelle
    pfad     UNINDEXED,
    dok_id   UNINDEXED,
    nr       UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
-- Erschlossene Schlagwoerter, nach Inhalts-Hash des ABSCHNITTS.
-- Getrennt von der FTS-Tabelle, damit sie ein erneutes Einlesen ueberleben:
-- sonst waere jeder `wissen einlesen` ein neuer Modelldurchlauf.
-- Kurzfassung je DOKUMENT, damit das Modell die Landkarte sehen kann statt nur
-- acht Ausschnitte. An den Fingerabdruck gebunden: aendert sich die Datei,
-- verfaellt die Kurzfassung. Abgeleitet -- nie Quelle fuer eine Zahl.
CREATE TABLE IF NOT EXISTS kurzfassung (
    pfad     TEXT PRIMARY KEY,
    fingerab TEXT,
    text     TEXT NOT NULL,
    modell   TEXT,
    stand    TEXT
);
CREATE TABLE IF NOT EXISTS erschliessung (
    hash    TEXT PRIMARY KEY,
    woerter TEXT NOT NULL,
    modell  TEXT,
    stand   TEXT
);
"""


@dataclass
class Treffer:
    text: str
    ueberschrift: str
    schlagwoerter: str          # erschlossen, nie zitierfaehig
    titel: str
    quelle: str
    herkunft: str
    punkte: float


def verbinden() -> sqlite3.Connection:
    """Zum Schreiben: legt das Schema an."""
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    if _migrieren(c):
        print("    Index umgebaut (Schlagwortspalte) — alle Quellen werden neu gelesen")
    return c


def lesen() -> sqlite3.Connection:
    """Zum Suchen: NUR lesend, ohne Schema-Anlage.

    verbinden() ruft executescript(), und das nimmt eine Schreibsperre --
    bei jeder Suche. Im Dienst blockierte das die Werkzeugausfuehrung und der
    Assistent blieb stumm stehen. Suchen darf niemals schreiben.
    """
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    # WAL: sonst sperrt ein laufender Indexlauf jede Suche aus. Der Modus haengt
    # an der Datei, nicht an der Verbindung -- verbinden() setzt ihn.
    return c


def fingerabdruck_bekannt(c, pfad: str, fingerab: str) -> bool:
    r = c.execute("SELECT fingerab FROM dokumente WHERE pfad=?", (pfad,)).fetchone()
    return bool(r and r[0] == fingerab)


def abschnitt_hash(text: str) -> str:
    """Kennung eines Abschnitts fuer die Erschliessung -- ueber den INHALT, nicht
    ueber Pfad und Nummer. Verschiebt sich ein Abschnitt im Dokument oder wird
    die Datei umbenannt, bleiben die Schlagwoerter gueltig; aendert sich der
    Text, verfallen sie von selbst."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _migrieren(c):
    """Alte Indizes ohne Schlagwortspalte umbauen.

    FTS5 kennt kein ALTER TABLE ADD COLUMN. Die Abschnitte werden verworfen und
    die Fingerabdruecke geleert, damit `wissen einlesen` alles neu liest --
    das ist billig. Die Tabelle `erschliessung` bleibt: sie haengt am
    Inhalts-Hash, nicht an Zeilennummern, und ist das Teure.
    """
    spalten = [r[1] for r in c.execute("PRAGMA table_info(abschnitte)")]
    if not spalten or "schlagwoerter" in spalten:
        return False
    c.execute("DROP TABLE abschnitte")
    c.executescript(SCHEMA)
    c.execute("UPDATE dokumente SET fingerab = NULL")
    c.commit()
    return True


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
    # Schon erschlossene Schlagwoerter gleich mitnehmen: nach einem erneuten
    # Einlesen sollen sie sofort wieder wirken, ohne Modelldurchlauf.
    zeilen = []
    for i, (u, t) in enumerate(abschnitte):
        r = c.execute("SELECT woerter FROM erschliessung WHERE hash=?",
                      (abschnitt_hash(t),)).fetchone()
        zeilen.append((t, u, r[0] if r else "", pfad, did, i))
    c.executemany(
        "INSERT INTO abschnitte (text,ueberschrift,schlagwoerter,pfad,dok_id,nr) "
        "VALUES (?,?,?,?,?,?)", zeilen)
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


# Der Index faltet Umlaute weg (`tokenize = unicode61 remove_diacritics 2`):
# "Sekundaerelektronen" steht dort als "sekundarelektronen". Eine Anfrage in
# ae/oe/ue-Schreibweise trifft das nie -- gemessen: "Sekundärelektronen" 8
# Treffer, "Sekundaerelektronen" null. Betrifft besonders die Tastatureingabe
# (fremdes Tastaturlayout, Eile) und alles, was ohne Umlaute durchgereicht wird.
_UMLAUTPAAR = re.compile(r"ae|oe|ue")
_UMSCHRIFT_MAX = 3          # 2^3 Varianten; mehr lohnt den Aufwand nicht


def _faltungen(wort: str) -> list[str]:
    """Alle Schreibweisen, die aus ae/oe/ue entstehen koennen.

    Nicht einfach alles ersetzen: in "rueckstreuelektronen" ist das erste "ue"
    ein Umlaut, das zweite (streu+elektronen) keiner. Sequentielles Ersetzen
    machte daraus "ruckstreulektronen" -- falsch, und es fand nichts. Weil die
    Suche mit ODER verknuepft, kostet eine ueberfluessige Variante nichts: sie
    trifft einfach nicht.
    """
    stellen = [m.start() for m in _UMLAUTPAAR.finditer(wort)]
    if not stellen or len(stellen) > _UMSCHRIFT_MAX:
        return []
    aus = set()
    for maske in range(1, 1 << len(stellen)):
        z, versatz = wort, 0
        for i, pos in enumerate(stellen):
            if maske >> i & 1:
                p = pos - versatz
                z = z[:p] + z[p] + z[p + 2:]   # "ae" -> "a"
                versatz += 1
        aus.add(z)
    return sorted(aus)


def begriffe(frage: str) -> list[str]:
    """Suchbegriffe aus einer Frage.

    **Zweibuchstabige Abkuerzungen bleiben drin, wenn sie gross geschrieben
    oder Formelzeichen sind.** Die Laengengrenze warf `SE`, `HV`, `WD` und `Bz`
    weg -- allesamt Fachbegriffe hier -- und die Suche lieferte darauf null
    Treffer, nicht etwa schlechte. Gewoehnliche kurze Woerter fallen weiter
    heraus: sie stehen klein im Satz und in `_STOPP`.
    """
    aus = []
    for roh in _WORT.findall(frage):
        x = roh.lower()
        if x in _STOPP:
            continue
        kurz_erlaubt = len(roh) == 2 and (roh[0].isupper() or any(z.isdigit() for z in roh))
        if len(x) > 2 or kurz_erlaubt:
            aus.append(x)
    # Die gefaltete Form ZUSAETZLICH, nicht ersetzend: "Aerosol" wuerde sonst
    # zu "Arosol" und faende nichts mehr. Als zusaetzlicher OR-Begriff kostet
    # eine unsinnige Variante nichts -- sie trifft einfach nichts.
    for x in list(aus):
        for g in _faltungen(x):
            if len(g) > 2 and g not in aus:
                aus.append(g)
    return aus


# Deutsche Komposita: die Spracherkennung schreibt "Rasterelektronenmikroskop
# Auflösung" gern als ein Wort zusammen, und danach findet weder FTS5 noch eine
# Suchmaschine etwas. Zerlegt wird an den BEKANNTEN Fachbegriffen -- ohne
# Woerterbuch, dafuer ohne Falschtrennungen.
KOMPOSITUM_AB = 14      # kuerzere Woerter lohnen die Zerlegung nicht


def _vokabeln() -> list[str]:
    from sprachdienst import stt
    return [b for b in stt.begriffe() if len(b) >= 5 and " " not in b]


def zerlege(wort: str, vokabeln=None) -> list[str]:
    """Zerlegt ein Kompositum an bekannten Fachbegriffen. Sonst [wort]."""
    vok = sorted(vokabeln if vokabeln is not None else _vokabeln(),
                 key=len, reverse=True)
    k = wort.lower()
    for b in vok:
        bl = b.lower()
        i = k.find(bl)
        if i < 0 or len(bl) == len(k):
            continue
        teile = []
        if i > 0:
            teile += zerlege(wort[:i], vok)
        teile.append(wort[i:i + len(bl)])
        if i + len(bl) < len(wort):
            teile += zerlege(wort[i + len(bl):], vok)
        # Fugenlaute und Reste unter drei Zeichen wegwerfen.
        return [t for t in teile if len(t) >= 3]
    return [wort]


def aufbrechen(frage: str, mit_original: bool = True) -> str:
    """Bricht lange Komposita einer Anfrage auf.

    `mit_original=True` haengt die Bestandteile an -- richtig fuer FTS5, das
    die Begriffe mit ODER verknuepft.

    `mit_original=False` ERSETZT das Kompositum durch seine Teile -- noetig fuer
    Suchmaschinen, die mit UND verknuepfen: dort macht ein unauffindbares Wort
    die ganze Anfrage leer, egal wie gut die uebrigen sind.
    """
    vok = _vokabeln()
    aus = []
    for w in frage.split():
        rein = re.sub(r"\W", "", w)
        teile = zerlege(rein, vok) if len(rein) >= KOMPOSITUM_AB else [w]
        if len(teile) > 1:
            if mit_original:
                aus.append(w)
            aus.extend(teile)
        else:
            aus.append(w)
    return " ".join(dict.fromkeys(aus))


def auszug(text: str, begriffe: list[str], laenge: int = 600) -> str:
    """Ausschnitt UM die Fundstelle, nicht der Anfang des Abschnitts.

    Vorher gingen die ersten 600 Zeichen ans Modell. Gemessen ueber acht
    Fachfragen enthielten **9 % der gelieferten Auszuege keinen einzigen
    Suchbegriff** -- der Treffer war richtig, die gezeigte Stelle nutzlos, und
    das Modell antwortete "steht nicht in den Unterlagen" auf etwas, das
    dasteht.

    Betrifft nicht nur die uebergrossen Abschnitte: auch in einem 900-Zeichen-
    Block kann der Begriff hinten stehen. Messwerttabellen und Codedateien
    haben keine Absatzgrenzen, an denen einlesen.py schneiden koennte -- der
    laengste Abschnitt im Index hat 178.602 Zeichen.
    """
    if len(text) <= laenge:
        return text
    tief = text.lower()
    stellen = [i for i in (tief.find(b.lower()) for b in begriffe) if i >= 0]
    if not stellen:
        return text[:laenge]
    # Ein Drittel Vorlauf, zwei Drittel danach: der Kontext nach einer
    # Fundstelle traegt meist mehr (Messwert, Definition, Fortsetzung).
    a = max(0, min(stellen) - laenge // 3)
    aus = text[a:a + laenge]
    return ("…" if a else "") + aus + ("…" if a + laenge < len(text) else "")


def suchen(frage: str, anzahl: int = 5, c: sqlite3.Connection | None = None
           ) -> list[Treffer]:
    eigen = c is None
    try:
        c = c or lesen()
    except sqlite3.OperationalError:
        return []          # Index noch nicht angelegt
    try:
        w = begriffe(aufbrechen(frage))
        if not w:
            return []
        # Anführungszeichen um jeden Begriff: sonst deutet FTS5 Bindestriche
        # als Operatoren und scheitert an "Siliziumnitrid-Fenster".
        ausdruck = " OR ".join(f'"{x}"' for x in w)
        zeilen = c.execute("""
            SELECT a.text, a.ueberschrift, a.schlagwoerter,
                   d.titel, d.quelle, d.herkunft,
                   bm25(abschnitte, 1.0, 2.0, 1.5) AS punkte
            FROM abschnitte a JOIN dokumente d ON d.id = a.dok_id
            WHERE abschnitte MATCH ?
            ORDER BY punkte LIMIT ?""", (ausdruck, anzahl)).fetchall()
        return [Treffer(*z) for z in zeilen]
    except sqlite3.OperationalError:
        return []
    finally:
        if eigen:
            c.close()


def aufraeumen(c, quelle: str, gesehen: set[str]) -> int:
    """Entfernt Dokumente einer Quelle, die es nicht mehr gibt.

    Ohne das bleibt alles im Index, was einmal drin war -- geloeschte Dateien
    genauso wie nachtraeglich ausgeschlossene. Gemessen wurde genau das: die
    Rechercheergebnisse blieben auffindbar, nachdem sie aus der Quelle
    genommen worden waren, und gewannen bei Zahlenfragen sogar gegen die
    Primaerquellen.
    """
    weg = [r[0] for r in c.execute(
        "SELECT pfad FROM dokumente WHERE quelle=?", (quelle,)).fetchall()
        if r[0] not in gesehen]
    for pfad in weg:
        did = c.execute("SELECT id FROM dokumente WHERE pfad=?", (pfad,)).fetchone()
        if did:
            c.execute("DELETE FROM abschnitte WHERE dok_id=?", (did[0],))
            c.execute("DELETE FROM dokumente WHERE id=?", (did[0],))
    return len(weg)


def stand() -> dict:
    c = verbinden()
    try:
        d = c.execute("SELECT COUNT(*) FROM dokumente").fetchone()[0]
        a = c.execute("SELECT COUNT(*) FROM abschnitte").fetchone()[0]
        je = c.execute("SELECT quelle, COUNT(*) FROM dokumente GROUP BY quelle").fetchall()
        return {"dokumente": d, "abschnitte": a, "quellen": dict(je)}
    finally:
        c.close()
