"""Bilder aus den Git-Quellen: Katalog und Suche fuer die Buehne.

Bilder haben keinen Text und stehen deshalb nicht im Index. Auffindbar werden
sie ueber das, was um sie herum steht: Dateiname, Ordner und -- wo eine
Markdown-Datei auf sie verweist -- die Bildunterschrift. Daraus entsteht je
Bild eine Zeile Beschreibung, und die wird eingebettet wie ein Abschnitt.

Gesucht wird ueber die Vektoren, nicht ueber Woerter: "Schaltplan" muss
`netzteil_schaltbild.png` treffen, und das leistet kein Wortvergleich.

PDFs stehen mit im Katalog. Ihr Text liegt zwar im Index, aber wer "zeig mir
das Blockschaltbild" sagt, will die Seite sehen und keinen Auszug hoeren.

Nur Git-Quellen: was hier gezeigt wird, liegt unter `wissen/repos` und hat
einen Commit-Stand. Erzeugte Bilder gehoeren nicht in diesen Katalog -- die
Herkunft steht auf der Buehne, und sie muss stimmen.
"""
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import numpy as np

from . import einlesen, index, vektor

BILD_ENDUNGEN = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
ZEIG_ENDUNGEN = BILD_ENDUNGEN | {".pdf"}
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml", ".gif": "image/gif", ".webp": "image/webp",
        ".pdf": "application/pdf"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS bilder (
    pfad         TEXT PRIMARY KEY,   -- quelle:rel, wie bei dokumente
    quelle       TEXT NOT NULL,
    rel          TEXT NOT NULL,
    unterschrift TEXT,               -- aus ![...](...) einer Markdown-Datei
    text         TEXT NOT NULL,      -- was eingebettet wurde
    herkunft     TEXT,
    stand        TEXT,               -- Commit der Quelle beim Katalogisieren
    v            BLOB NOT NULL,
    modell       TEXT
);
"""

# Unter dieser Aehnlichkeit gilt der beste Treffer als geraten. e5 drueckt
# alle Werte in ein schmales Band. Gemessen an den eigenen Quellen am
# 04.10.2026: gemeinte Treffer 0,823 bis 0,898, Unsinn ("Wetter morgen",
# "Rezept fuer Apfelkuchen") 0,784 bis 0,801. Der Abstand
# ist schmal -- deshalb entscheidet die Schwelle nur ueber den gesprochenen
# Satz, die Auswahl steht in beiden Faellen auf der Buehne.
SICHER_AB = 0.815

_VERWEIS = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def _woerter(rel: str) -> str:
    """`aufbau/netzteil/messung_last_spannung.png` ->
    `messung last spannung, netzteil, aufbau`. Der Name zuerst: er
    sagt am meisten, die Ordner ordnen nur ein."""
    p = Path(rel)
    teile = [p.stem] + [t for t in reversed(p.parts[:-1])]
    sauber = []
    for t in teile:
        t = re.sub(r"(?<=[a-zäöü])(?=[A-ZÄÖÜ])", " ", t)     # camelCase
        t = re.sub(r"[_\-.]+", " ", t).strip()
        if t:
            sauber.append(t)
    return ", ".join(sauber)


def _klartext(unterschrift: str) -> str:
    """Markdown und Formeln aus der Unterschrift -- eingebettet wird Sprache."""
    t = re.sub(r"\$[^$]*\$", " ", unterschrift)
    t = re.sub(r"[*_`]+", "", t)
    return re.sub(r"\s+", " ", t).strip()


def unterschriften(wurzel: Path) -> dict[str, str]:
    """rel -> Bildunterschrift, aus allen Markdown-Dateien der Quelle. Bei
    mehreren Verweisen auf dasselbe Bild gewinnt die laengste: die kurze ist
    meist ein Alt-Text, die lange beschreibt."""
    wurzel = wurzel.resolve()
    aus: dict[str, str] = {}
    for md in wurzel.rglob("*.md"):
        if ".git" in md.parts:
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _VERWEIS.finditer(text):
            alt, ziel = _klartext(m.group(1)), unquote(m.group(2))
            if not alt or "://" in ziel:
                continue
            p = (md.parent / ziel).resolve()
            if not p.is_relative_to(wurzel) or not p.is_file():
                continue
            rel = p.relative_to(wurzel).as_posix()
            if len(alt) > len(aus.get(rel, "")):
                aus[rel] = alt
    return aus


def _git_quellen() -> list[dict]:
    try:
        konf = json.loads(einlesen.QUELLEN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [q for q in konf.get("quellen", [])
            if q.get("art") == "git" and q.get("aktiv", True)]


def _sammeln(repos: Path, quellen: list[dict]) -> list[dict]:
    eintraege = []
    for q in quellen:
        wurzel = repos / q["name"]
        if not wurzel.is_dir():
            continue
        stand = einlesen._head(wurzel)
        texte = unterschriften(wurzel)
        web = q.get("web", "").rstrip("/")
        for p in sorted(wurzel.rglob("*")):
            if (".git" in p.parts or not p.is_file()
                    or p.suffix.lower() not in ZEIG_ENDUNGEN):
                continue
            rel = p.relative_to(wurzel).as_posix()
            unter = texte.get(rel, "")
            art = "Dokument" if p.suffix.lower() == ".pdf" else "Bild"
            text = f"{art}: {_woerter(rel)}" + (f". {unter}" if unter else "")
            eintraege.append({
                "pfad": f"{q['name']}:{rel}", "quelle": q["name"], "rel": rel,
                "unterschrift": unter, "text": text[:2000], "stand": stand,
                "herkunft": f"{web}/blob/HEAD/{rel}" if web else str(p)})
    return eintraege


def aufbauen(repos: Path | None = None, quellen: list[dict] | None = None,
             c: sqlite3.Connection | None = None) -> dict:
    """Katalog auf den Stand der Repos bringen. Eingebettet wird nur, was neu
    ist oder eine andere Beschreibung bekommen hat."""
    repos = repos or einlesen.REPOS
    quellen = _git_quellen() if quellen is None else quellen
    t0 = time.time()
    eintraege = _sammeln(repos, quellen)
    eigen = c is None
    c = c or index.verbinden()
    try:
        c.executescript(SCHEMA)
        alt = {p: t for p, t in c.execute("SELECT pfad, text FROM bilder")}
        offen = [e for e in eintraege if alt.get(e["pfad"]) != e["text"]]
        if offen:
            V = vektor.einbetten([e["text"] for e in offen], als_frage=False)
            c.executemany(
                "INSERT OR REPLACE INTO bilder (pfad,quelle,rel,unterschrift,"
                "text,herkunft,stand,v,modell) VALUES (?,?,?,?,?,?,?,?,?)",
                [(e["pfad"], e["quelle"], e["rel"], e["unterschrift"], e["text"],
                  e["herkunft"], e["stand"], V[i].tobytes(), vektor.MODELL)
                 for i, e in enumerate(offen)])
        # Der Stand wandert mit, auch wenn sich die Beschreibung nicht aendert:
        # er steht auf der Buehne und soll der Commit sein, aus dem das Bild kommt.
        c.executemany("UPDATE bilder SET stand=?, herkunft=? WHERE pfad=?",
                      [(e["stand"], e["herkunft"], e["pfad"]) for e in eintraege])
        gesehen = {e["pfad"] for e in eintraege}
        weg = [p for p in alt if p not in gesehen]
        c.executemany("DELETE FROM bilder WHERE pfad=?", [(p,) for p in weg])
        c.commit()
    finally:
        if eigen:
            c.close()
    return {"bilder": len(eintraege), "neu": len(offen), "entfernt": len(weg),
            "sekunden": time.time() - t0}


def anzahl(c: sqlite3.Connection | None = None) -> int:
    eigen = c is None
    try:
        c = c or index.lesen()
        return c.execute("SELECT COUNT(*) FROM bilder").fetchone()[0]
    except sqlite3.OperationalError:        # Tabelle oder Datei fehlt noch
        return 0
    finally:
        if eigen and c is not None:
            c.close()


def suchen(frage: str, anzahl: int = 6,
           c: sqlite3.Connection | None = None) -> list[dict]:
    """Die aehnlichsten Bilder, bestes zuerst. Leer, wenn es keinen Katalog
    gibt. `sicher` am ersten Treffer sagt, ob er gemeint sein duerfte."""
    eigen = c is None
    try:
        c = c or index.lesen()
        zeilen = c.execute("SELECT quelle, rel, unterschrift, herkunft, stand, v "
                           "FROM bilder").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        if eigen and c is not None:
            c.close()
    if not zeilen or not frage.strip():
        return []
    V = np.vstack([np.frombuffer(z[5], dtype=np.float32) for z in zeilen])
    s = V @ vektor.einbetten([frage], als_frage=True)[0]
    treffer = []
    for j in np.argsort(-s)[:anzahl]:
        quelle, rel, unter, herkunft, stand, _ = zeilen[j]
        treffer.append({
            "quelle": quelle, "rel": rel, "titel": Path(rel).name,
            "unterschrift": unter or "", "herkunft": herkunft or "",
            "stand": stand or "", "pdf": rel.lower().endswith(".pdf"),
            "punkte": round(float(s[j]), 4), "sicher": bool(s[j] >= SICHER_AB)})
    return treffer


def datei(quelle: str, rel: str, repos: Path | None = None) -> Path | None:
    """Der Pfad zum Ausliefern -- oder None, wenn er nicht ausgeliefert werden
    darf. Nur Dateien UNTERHALB der Quelle, nur zeigbare Endungen, nichts aus
    `.git`. Geprueft wird der aufgeloeste Pfad: ein Symlink im Repo, der nach
    draussen zeigt, faellt damit ebenso durch wie `../`."""
    repos = (repos or einlesen.REPOS).resolve()
    if not quelle or "/" in quelle or "\\" in quelle or quelle.startswith("."):
        return None
    wurzel = (repos / quelle).resolve()
    if wurzel.parent != repos or not wurzel.is_dir():
        return None
    p = (wurzel / rel).resolve()
    if not p.is_relative_to(wurzel) or ".git" in p.relative_to(wurzel).parts:
        return None
    if p.suffix.lower() not in ZEIG_ENDUNGEN or not p.is_file():
        return None
    return p
