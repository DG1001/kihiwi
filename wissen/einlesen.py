"""Quellen einlesen: lokale Ordner, Git-Repos, Nextcloud (WebDAV, nur lesend).

Konfiguriert in `wissen/quellen.json`. Jede Quelle wird zu Dokumenten, jedes
Dokument zu Abschnitten -- ein Treffer soll zitierbar sein, nicht "irgendwo in
dieser 80-Seiten-PDF".

Nextcloud wird ausdruecklich nur GELESEN. Der Assistent darf Unterlagen
benutzen, aber nichts daran veraendern.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from sprachdienst import konfig
from . import index

# Wie beim Vokabular: die echte Quellenliste nennt interne Repos und liegt
# deshalb nicht im Repository. Ohne sie greift die Beispieldatei.
QUELLEN = (konfig.WURZEL / "wissen" / "quellen.json"
           if (konfig.WURZEL / "wissen" / "quellen.json").exists()
           else konfig.WURZEL / "wissen" / "quellen.beispiel.json")
REPOS = konfig.WURZEL / "wissen" / "repos"

TEXT_ENDUNGEN = {".md", ".txt", ".rst", ".org", ".csv", ".tsv", ".json", ".yaml",
                 ".yml", ".toml", ".ini", ".cfg", ".py", ".sh", ".c", ".h",
                 ".cpp", ".java", ".js", ".ts", ".sql", ".tex", ".log"}
PDF_ENDUNGEN = {".pdf"}
MAX_BYTES = 8 * 1024 * 1024        # groessere Dateien sind Daten, keine Unterlagen

ABSCHNITT_ZIEL = 900               # Zeichen; ein Absatz-Block, kein Satz
ABSCHNITT_MAX = 1600


# ------------------------------------------------------------------ Textholen
def _pdf_text(pfad: Path) -> str | None:
    """PDF-Text, seitenweise mit Seitenmarke.

    **pypdfium2, nicht pypdf.** Bei den Manuskripten hier verliert pypdf die
    Leerzeichen: "SpaltfelderfallensteilerabdieFEMM-Rechnung". Solcher Text ist
    fuer die Volltextsuche unerreichbar -- kein Suchbegriff kommt darin als Wort
    vor. Gemessen ueber 28 PDFs: 1380 Woerter mit 22+ Buchstaben am Stueck gegen
    111, und pypdfium2 ist dabei sechsmal schneller (0,3 s gegen 1,8 s).
    Weder `extraction_mode="layout"` noch ein kleineres `space_width` half --
    die Leerzeichen fehlen im Inhaltsstrom, pypdf kann sie nicht erfinden.

    pypdf bleibt als Rueckfallebene: ohne sie liefe der Index sonst gar nicht,
    wenn jemand pypdfium2 nicht installiert hat. Der Rueckfall ist LAUT, sonst
    wird stillschweigend schlechter Text indiziert und niemand merkt es.
    """
    seiten = []
    try:
        import pypdfium2 as pdfium
        dok = pdfium.PdfDocument(str(pfad))
        for n in range(len(dok)):
            t = dok[n].get_textpage().get_text_bounded().strip()
            if t:
                seiten.append(f"[Seite {n + 1}]\n{t}")
    except ImportError:
        # print wie im Rest des Moduls -- einlesen laeuft ueber ./dienste.sh
        # wissen einlesen, und die Ausgabe soll dort sichtbar sein.
        print(f"    pypdfium2 fehlt — pypdf verliert bei manchen PDFs die "
              f"Leerzeichen: {pfad.name}")
        from pypdf import PdfReader
        for n, s in enumerate(PdfReader(str(pfad)).pages, 1):
            t = (s.extract_text() or "").strip()
            if t:
                seiten.append(f"[Seite {n}]\n{t}")
    return "\n\n".join(seiten) or None


def text_aus(pfad: Path) -> str | None:
    endung = pfad.suffix.lower()
    try:
        if pfad.stat().st_size > MAX_BYTES:
            return None
        if endung in TEXT_ENDUNGEN:
            return pfad.read_text(encoding="utf-8", errors="replace")
        if endung in PDF_ENDUNGEN:
            return _pdf_text(pfad)
    except Exception:
        return None
    return None


_UEBERSCHRIFT = re.compile(r"^(#{1,6})\s+(.*)$|^\[Seite (\d+)\]$", re.M)

# Quelltext wird an Symbolen geschnitten, nicht an "#".
#
# Vorher lief Quelltext durch dieselbe Markdown-Zerlegung -- und in Python
# leitet "#" einen KOMMENTAR ein. Damit wurde jede Kommentarzeile zur
# Ueberschrift: im Index standen Ueberschriften wie
# "----------------------------------------" und "genau der Fehler, den man nur
# einmal macht". Das Feld wiegt in BM25 doppelt, war also mit Zufallstext
# gefuellt. Geschnitten wurde an Kommentaren statt an Funktionen, und wo keine
# Kommentare standen, gar nicht -- daher der 178.602-Zeichen-Abschnitt.
# Betroffen waren 879 von 2495 Abschnitten, gut ein Drittel des Index.
# Hochzaehlen, sobald sich die Zerlegung aendert -- erzwingt neues Einlesen.
ZERLEGER_FASSUNG = 4
CODE_ENDUNGEN = {".py", ".sh", ".c", ".h", ".cpp", ".java", ".js", ".ts", ".sql"}
_SYMBOL = re.compile(
    r"^(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)"          # Python
    r"|^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"   # JS/TS
    r"|^(?:[A-Za-z_][\w:<>,\s*&]*?)\b([A-Za-z_]\w*)\s*\([^;]*\)\s*\{"  # C/Java
    r"|^([A-Za-z_]\w*)\s*\(\)\s*\{",                              # sh
    re.M)


def _zerlegen_code(text: str, titel: str) -> list[tuple[str, str]]:
    """Blockweise an Symbolen auf Spaltenposition 0."""
    marken = [(m.start(), next(g for g in m.groups() if g))
              for m in _SYMBOL.finditer(text)]
    if not marken:
        return [(titel, text)]
    bloecke = []
    if marken[0][0] > 0:
        # Alles vor dem ersten Symbol: Importe, Modul-Docstring, Konstanten.
        # Traegt oft die Messwerte -- FENSTER_NM = 50.0 steht genau dort.
        bloecke.append((f"{titel} — Kopf und Konstanten", text[:marken[0][0]]))
    for i, (pos, name) in enumerate(marken):
        ende = marken[i + 1][0] if i + 1 < len(marken) else len(text)
        bloecke.append((f"{titel} — {name}", text[pos:ende]))
    return bloecke


# Ein zusammenhaengender "Wort"-Lauf ohne Leerzeichen laenger als das ist kein
# Text mehr. Der groesste Abschnitt im Index war eine Zeile mit 178.484 Zeichen:
# `BILD_B64 = "iVBORw0KGgo..."`, ein eingebettetes PNG. Suchbar ist daran
# nichts, aber es verzerrte jede Statistik und blieb allen Schnittregeln
# gewachsen -- auch der zeilenweisen, denn es ist EINE Zeile.
DATENLAUF_AB = 500


def _ist_datenblock(text: str) -> bool:
    """Base64-Anhaenge, Bilddaten, kodierte Felder -- kein durchsuchbarer Text."""
    laengster = max((len(w) for w in text.split()), default=0)
    return laengster > DATENLAUF_AB and laengster > len(text) * 0.5


# Messdaten: CSV und TSV. Auch hier leitet "#" einen Kommentar ein, nicht eine
# Ueberschrift -- die Markdown-Zerlegung machte aus jeder Kopfzeile einen
# eigenen Abschnitt ("groesse: Radiales Strahlprofil an der Probenebene", Text
# identisch zur Ueberschrift). Genau derselbe Fehler wie zuvor bei Python.
#
# Der Kopf ist bei diesen Dateien das Wertvolle: er sagt, WAS gemessen wurde,
# in welcher Einheit, mit welcher Konfiguration. Er gehoert an einem Stueck
# zusammen -- und mit den Spaltennamen, sonst steht die Beschreibung ohne die
# Groessen da, auf die sie sich bezieht.
DATEN_ENDUNGEN = {".csv", ".tsv"}


def _zerlegen_daten(text: str, titel: str) -> list[tuple[str, str]]:
    zeilen = text.splitlines()
    i = 0
    while i < len(zeilen) and (not zeilen[i].strip() or zeilen[i].lstrip().startswith("#")):
        i += 1
    kopf = "\n".join(zeilen[:i]).strip()
    spalten = zeilen[i] if i < len(zeilen) else ""
    rest = "\n".join(zeilen[i + 1:]).strip()
    aus = []
    if kopf or spalten:
        aus.append((titel, (kopf + "\n" + spalten).strip()))
    if rest:
        # Die Zahlen bleiben drin: in diesem Labor steht die Auslegung in den
        # Daten, und eine Suche nach einem Wert soll sie finden. Aber als
        # eigener Abschnitt, damit sie die Beschreibung nicht verdraengen.
        aus.append((f"{titel} — Messwerte", rest))
    return aus


def _nach_zeilen(ueb: str, block: str) -> list[tuple[str, str]]:
    """Letzte Instanz fuer Bloecke ohne jede Struktur -- Datenfelder, Tabellen.
    Stumpf nach Zeichenzahl an Zeilengrenzen, damit kein Abschnitt mehr
    unbegrenzt wachsen kann."""
    aus, puffer = [], ""
    for zeile in block.splitlines(keepends=True):
        if len(puffer) + len(zeile) > ABSCHNITT_ZIEL and puffer:
            aus.append((ueb, puffer.strip())); puffer = ""
        puffer += zeile
    if puffer.strip():
        aus.append((ueb, puffer.strip()))
    return aus


def zerlegen(text: str, titel: str, endung: str = "") -> list[tuple[str, str]]:
    """Zerlegt in (ueberschrift, text). Schneidet an Ueberschriften und
    Absaetzen, nicht an fester Zeichenzahl -- ein mitten im Satz getrennter
    Abschnitt ist als Zitat wertlos."""
    if endung.lower() in CODE_ENDUNGEN:
        bloecke = _zerlegen_code(text, titel)
        marken = True          # nur fuer den Zweig unten
    elif endung.lower() in DATEN_ENDUNGEN:
        bloecke = _zerlegen_daten(text, titel)
        marken = True
    else:
        marken = [(m.start(), (m.group(2) or f"Seite {m.group(3)}").strip())
                  for m in _UEBERSCHRIFT.finditer(text)]
    if endung.lower() in CODE_ENDUNGEN | DATEN_ENDUNGEN:
        pass
    elif not marken:
        bloecke = [(titel, text)]
    else:
        bloecke = []
        if marken[0][0] > 0:
            bloecke.append((titel, text[:marken[0][0]]))
        for i, (pos, ueb) in enumerate(marken):
            ende = marken[i + 1][0] if i + 1 < len(marken) else len(text)
            bloecke.append((ueb, text[pos:ende]))

    aus: list[tuple[str, str]] = []
    for ueb, block in bloecke:
        block = block.strip()
        if not block:
            continue
        if len(block) <= ABSCHNITT_MAX:
            aus.append((ueb, block)); continue
        puffer = ""
        for absatz in re.split(r"\n\s*\n", block):
            if len(puffer) + len(absatz) > ABSCHNITT_ZIEL and puffer:
                aus.append((ueb, puffer.strip())); puffer = ""
            puffer += absatz + "\n\n"
        if puffer.strip():
            aus.append((ueb, puffer.strip()))
    # Harte Obergrenze: was auch nach der Absatztrennung zu gross ist, hat
    # keine Absaetze -- Datenfelder, Messwerttabellen, Bilddaten. Vorher blieb
    # so etwas ungeteilt und wurde zum 178.602-Zeichen-Abschnitt.
    endgueltig = []
    for u, t in aus:
        if _ist_datenblock(t):
            continue
        endgueltig += _nach_zeilen(u, t) if len(t) > ABSCHNITT_MAX else [(u, t)]
    return [(u, t) for u, t in endgueltig if len(t) > 40]


# ------------------------------------------------------------------ Quellen
def _dateien(wurzel: Path, muster, aus):
    for p in wurzel.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(wurzel)
        if any(teil in aus for teil in rel.parts):
            continue
        if muster and not any(p.match(m) for m in muster):
            continue
        if p.suffix.lower() not in TEXT_ENDUNGEN | PDF_ENDUNGEN:
            continue
        yield p, rel


def lokal(q: dict, c) -> int:
    wurzel = Path(q["pfad"]).expanduser()
    if not wurzel.is_dir():
        print(f"    Ordner fehlt: {wurzel}"); return 0
    aus = set(q.get("aus", [])) | {".git", ".venv", "node_modules", "__pycache__"}
    n = 0
    for p, rel in _dateien(wurzel, q.get("muster"), aus):
        # Der relative Pfad statt des Dateinamens: sonst heissen alle
        # Laborprotokolle "protokoll.md" und die Quellenangabe sagt nichts
        # darueber, aus welcher Sitzung sie stammen.
        n += _eintragen(c, q["name"], f"{q['name']}:{rel}", str(rel), str(p),
                        str(int(p.stat().st_mtime)), text_aus(p))
    return n


def _head(ziel: Path) -> str:
    return subprocess.run(["git", "-C", str(ziel), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def git(q: dict, c) -> int:
    ziel = REPOS / q["name"]
    REPOS.mkdir(parents=True, exist_ok=True)
    if ziel.exists():
        vorher = _head(ziel)
        r = subprocess.run(["git", "-C", str(ziel), "pull", "--ff-only", "-q"],
                           capture_output=True, text=True, timeout=180)
        nachher = _head(ziel)
        if vorher and nachher and vorher != nachher:
            anzahl = subprocess.run(
                ["git", "-C", str(ziel), "rev-list", "--count", f"{vorher}..{nachher}"],
                capture_output=True, text=True).stdout.strip()
            NOTIZ[q["name"]] = (f"{anzahl} neuer Commit" if anzahl == "1"
                                else f"{anzahl} neue Commits")
            print(f"    {vorher} -> {nachher}")
        else:
            NOTIZ[q["name"]] = "unverändert"
    else:
        NOTIZ[q["name"]] = "frisch geklont"
        r = subprocess.run(["git", "clone", "--depth", "1", "-q", q["url"], str(ziel)],
                           capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        NOTIZ[q["name"]] = "Abruf fehlgeschlagen"
        print(f"    git fehlgeschlagen: {r.stderr.strip()[:120]}"); return 0
    stand = _head(ziel)
    aus = set(q.get("aus", [])) | {".git", "node_modules", "__pycache__"}
    n = 0
    for p, rel in _dateien(ziel, q.get("muster"), aus):
        herkunft = q.get("web", "").rstrip("/") + f"/blob/HEAD/{rel}" if q.get("web") else str(p)
        n += _eintragen(c, q["name"], f"{q['name']}:{rel}", p.name, herkunft,
                        stand, text_aus(p))
    return n


def _webdav(url: str, kopf: dict, methode: str, tiefe: str | None = None):
    req = urllib.request.Request(url, method=methode, headers=dict(kopf))
    if tiefe is not None:
        req.add_header("Depth", tiefe)
    return urllib.request.urlopen(req, timeout=60).read()


def nextcloud(q: dict, c) -> int:
    """Nur lesend: PROPFIND zum Auflisten, GET zum Holen. Kein PUT, kein DELETE."""
    pw = os.environ.get(q.get("passwort_env", "KIHIWI_NC_PASS"), "")
    if not pw:
        print(f"    kein Passwort in ${q.get('passwort_env','KIHIWI_NC_PASS')} — übersprungen")
        return 0
    kopf = {"Authorization": "Basic " + base64.b64encode(
        f"{q['nutzer']}:{pw}".encode()).decode()}
    basis = q["url"].rstrip("/")
    try:
        roh = _webdav(basis + "/", kopf, "PROPFIND", tiefe="infinity")
    except urllib.error.HTTPError as e:
        print(f"    Nextcloud antwortet {e.code} — übersprungen"); return 0
    except Exception as e:
        print(f"    Nextcloud nicht erreichbar: {e}"); return 0

    n = 0
    for antwort in ET.fromstring(roh).iter("{DAV:}response"):
        href = antwort.findtext("{DAV:}href") or ""
        if href.endswith("/"):
            continue
        name = urllib.parse.unquote(href.rsplit("/", 1)[-1])
        if Path(name).suffix.lower() not in TEXT_ENDUNGEN | PDF_ENDUNGEN:
            continue
        stand = antwort.findtext(".//{DAV:}getlastmodified") or ""
        url = urllib.parse.urljoin(basis + "/", href.split("/dav/")[-1]) \
            if "/dav/" in href else basis + "/" + name
        try:
            daten = _webdav(urllib.parse.urljoin(q["url"], href), kopf, "GET")
        except Exception:
            continue
        tmp = konfig.WURZEL / "wissen" / ".tmp"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(daten)
        tmp_named = tmp.with_suffix(Path(name).suffix)
        tmp.rename(tmp_named)
        n += _eintragen(c, q["name"], f"{q['name']}:{name}", name,
                        q.get("web", url), stand, text_aus(tmp_named))
        tmp_named.unlink(missing_ok=True)
    return n


# Was in diesem Lauf gesehen wurde -- alles andere fliegt danach aus dem Index.
GESEHEN: set[str] = set()

# Freitext je Quelle fuer den Bericht ("unveraendert", "2 neue Commits").
# Nur git() fuellt das; bei lokalen Ordnern sagen die Zahlen schon alles.
NOTIZ: dict[str, str] = {}


def _eintragen(c, quelle, pfad, titel, herkunft, stand, text) -> int:
    GESEHEN.add(pfad)
    if not text or not text.strip():
        return 0
    # Die Fassung der Zerlegung gehoert IN den Fingerabdruck. Sonst aendert
    # sich der Schnitt, `einlesen` meldet "0 Dokumente geaendert" und der Index
    # behaelt die alte Aufteilung -- genau so ist der Quelltext-Schnitt beim
    # ersten Versuch wirkungslos geblieben.
    fp = hashlib.sha256(
        f"{ZERLEGER_FASSUNG}\n".encode() + text.encode("utf-8", "replace")
    ).hexdigest()[:16]
    if index.fingerabdruck_bekannt(c, pfad, fp):
        return 0
    # Endung aus dem Titel: der traegt den Dateinamen, auch bei Nextcloud und
    # git, wo kein lokaler Pfad existiert.
    absch = zerlegen(text, titel, Path(titel).suffix)
    if not absch:
        return 0
    index.dokument_setzen(c, quelle, pfad, titel, herkunft, stand, fp, absch)
    return 1


ARTEN = {"lokal": lokal, "git": git, "nextcloud": nextcloud}


def alles(nur: str | None = None) -> list[dict]:
    """Liest alle aktiven Quellen ein und gibt je Quelle Bilanz zurueck.

    Der Rueckgabewert existiert, damit der Sprachdienst nach einem Abgleich
    sagen kann, was sich geaendert hat -- auf der Kommandozeile genuegten die
    Ausgaben.
    """
    bericht: list[dict] = []
    try:
        konf = json.loads(QUELLEN.read_text(encoding="utf-8"))
    except OSError:
        print(f"Keine Quellen konfiguriert ({QUELLEN})"); return bericht
    c = index.verbinden()
    try:
        for q in konf.get("quellen", []):
            if nur and q["name"] != nur:
                continue
            if not q.get("aktiv", True):
                print(f"  {q['name']}: abgeschaltet"); continue
            f = ARTEN.get(q.get("art"))
            if not f:
                print(f"  {q['name']}: unbekannte Art {q.get('art')!r}"); continue
            print(f"  {q['name']} ({q['art']}) ...")
            GESEHEN.clear()
            NOTIZ.pop(q["name"], None)
            n = f(q, c)
            entfernt = index.aufraeumen(c, q["name"], set(GESEHEN))
            c.commit()
            notiz = NOTIZ.get(q["name"], "")
            print(f"    {n} Dokument(e) neu oder geändert"
                  + (f", {entfernt} entfernt" if entfernt else "")
                  + (f" ({notiz})" if notiz else ""))
            bericht.append({"name": q["name"], "art": q["art"], "neu": n,
                            "entfernt": entfernt, "notiz": notiz})
    finally:
        c.close()
    return bericht
