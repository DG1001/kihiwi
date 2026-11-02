"""Das Lagebild: der Projektstand fuer das normale Gespraech.

Vier Fragen kommen im Labor immer wieder: Wie ist der Stand? Was steht als
Naechstes an? Wo hakt es? Was kann ich vorzeigen? Die Wissenssuche liefert dazu
acht Ausschnitte zu EINER Stelle -- gefragt ist aber ein Bild, das sich aus
vielen Dokumenten, Commits und Protokollen zusammensetzt. Das Lagebild ist
dieses Bild: nachts erzeugt, mit Verweis auf jede Quelle, ausdruecklich als
abgeleitet gekennzeichnet. Design: docs/superpowers/specs/2026-11-02-lagebild-design.md

Ins Gespraech geht nur wenig davon. Der Modellserver liest jeden Prompt
praktisch neu ein (ein Slot, kaum Wiederverwendung des Anfangs), jedes Token
kostet also bei jeder Antwort Zeit. Deshalb steht immer nur der Kern ("Worum es
geht") im Prompt, bei einer der vier Fragen der passende Abschnitt dazu; das
ganze Lagebild steht auf der Buehne.

Ablage unter zustand/lagebild/ (nicht im Repository):
    <JJJJ-MM-TT>T<HHMM>.md            das Lagebild, das juengste gilt
    <JJJJ-MM-TT>T<HHMM>.eingaben.json was eingeflossen ist
    korrekturen.jsonl                 Korrekturen per Stimme
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from sprachdienst import konfig, llm
from . import einlesen, index

VERZ = konfig.WURZEL / "zustand" / "lagebild"

# Welche Dateien eines Labors als Statusdokumente taugen, steht in der
# Konfiguration, nicht im Code: die echten Dateinamen verraten, woran gearbeitet
# wird, und das Repository ist oeffentlich. Ohne eigene Datei greift das Muster.
KONFIG = (konfig.WURZEL / "wissen" / "lagebild.json"
          if (konfig.WURZEL / "wissen" / "lagebild.json").exists()
          else konfig.WURZEL / "wissen" / "lagebild.beispiel.json")
VORGABEN = {"status": [], "vorzeigbar": [], "tage": 14, "commits_max": 80,
            "eingabe_zeichen": 120_000, "dokument_zeichen": 12_000,
            "modelle": ["qwen3.8-flash-next", "nemotron-3.5-super"],
            "nachts_bis": "06:00"}

# Reihenfolge und Ueberschriften sind fest: Erzeugung, Pruefung und Anzeige
# richten sich danach.
ABSCHNITTE = [("worum", "Worum es geht"), ("stand", "Stand"),
              ("naechstes", "Als Nächstes"),
              ("probleme", "Probleme und Herausforderungen"),
              ("vorzeigbar", "Vorzeigbar")]
UEBERSCHRIFT = dict(ABSCHNITTE)

# Zeichen statt Token, damit der Code ohne Tokenizer auskommt. Gemessen fuer
# deutsche Prosa mit Qwen3.8-Flash: 4,2 Zeichen je Token.
KERN_ZEICHEN = 1600          # ~400 Token, steht in jedem Prompt
ABSCHNITT_ZEICHEN = 2000     # ~500 Token, nur bei den vier Fragen
LAGEBILD_ZEICHEN = 10_000    # ~2.500 Token, nur auf der Buehne
KORREKTUR_TAGE = 14

KORREKTUREN = "korrekturen.jsonl"
_DATEI = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{4})\.md$")
_KLAMMER = re.compile(r"\[([^\[\]]+)\]")
_DATUM = re.compile(r"\d{2}\.\d{2}\.\d{4}")
KEINE_ANGABEN = "Keine Angaben in den Quellen."
DATUM_UNBEKANNT = "Datum unbekannt"


# --------------------------------------------------------- Lesen

def abschnitte(text: str) -> dict[str, str]:
    """Text je Abschnitt, Schluessel aus ABSCHNITTE. Unbekannte Ueberschriften
    (etwa der Korrekturblock der Erzeugung) fallen weg."""
    nach_titel = {t.lower(): k for k, t in ABSCHNITTE}
    aus: dict[str, list[str]] = {}
    jetzt_bei = None
    for zeile in text.splitlines():
        if zeile.startswith("## "):
            jetzt_bei = nach_titel.get(zeile[3:].strip().lower())
            if jetzt_bei:
                aus[jetzt_bei] = []
            continue
        if zeile.startswith("# "):
            jetzt_bei = None
            continue
        if jetzt_bei:
            aus[jetzt_bei].append(zeile)
    return {k: "\n".join(v).strip() for k, v in aus.items()}


def aktuell(verz: Path = VERZ) -> dict | None:
    """Das juengste Lagebild, oder None, wenn es noch keines gibt."""
    if not verz.is_dir():
        return None
    dateien = sorted(p for p in verz.iterdir() if _DATEI.match(p.name))
    if not dateien:
        return None
    p = dateien[-1]
    text = p.read_text(encoding="utf-8")
    return {"datum": date.fromisoformat(_DATEI.match(p.name).group(1)),
            "pfad": p, "text": text, "abschnitte": abschnitte(text)}


def _kuerzen(text: str, zeichen: int) -> str:
    """Auf ganze Zeilen kuerzen: ein halber Punkt mit abgeschnittenem Verweis
    waere eine Angabe ohne Quelle."""
    if len(text) <= zeichen:
        return text
    schnitt = text.rfind("\n", 0, zeichen)
    return text[:schnitt if schnitt > 0 else zeichen].rstrip()


def _quellen(text: str) -> list[str]:
    """Titel aus Verweisen der Form [Titel, TT.MM.JJJJ; Titel, TT.MM.JJJJ]."""
    titel = []
    for klammer in _KLAMMER.findall(text):
        for teil in klammer.split(";"):
            name, _, datum = teil.strip().rpartition(",")
            gueltig = _DATUM.fullmatch(datum.strip()) or datum.strip() == DATUM_UNBEKANNT
            if name and gueltig and name.strip() not in titel:
                titel.append(name.strip())
    return titel


def _stand(d: date) -> str:
    return d.strftime("%d.%m.%Y")


# --------------------------------------------------------- Korrekturen

def _korrekturen_lesen(verz: Path) -> list[dict]:
    p = verz / KORREKTUREN
    if not p.exists():
        return []
    return [json.loads(z) for z in p.read_text(encoding="utf-8").splitlines() if z.strip()]


def _korrekturen_schreiben(verz: Path, liste: list[dict]) -> None:
    verz.mkdir(parents=True, exist_ok=True)
    (verz / KORREKTUREN).write_text(
        "".join(json.dumps(k, ensure_ascii=False) + "\n" for k in liste), encoding="utf-8")


def korrektur_hinzu(text: str, jetzt: datetime, verz: Path = VERZ) -> dict:
    liste = _korrekturen_lesen(verz)
    # Nummern werden nie wiederverwendet: eine zurueckgenommene K2 bleibt K2.
    k = {"id": f"K{len(liste) + 1}", "zeit": jetzt.isoformat(timespec="seconds"),
         "text": text, "status": "offen", "durch": None}
    _korrekturen_schreiben(verz, liste + [k])
    return k


def korrektur_zuruecknehmen(verz: Path = VERZ) -> dict | None:
    liste = _korrekturen_lesen(verz)
    for k in reversed(liste):
        if k["status"] == "offen":
            k["status"] = "zurueckgenommen"
            _korrekturen_schreiben(verz, liste)
            return k
    return None


def offene_korrekturen(jetzt: datetime, verz: Path = VERZ) -> list[dict]:
    """Offene Korrekturen; was aelter als KORREKTUR_TAGE ist, laeuft dabei ab."""
    liste = _korrekturen_lesen(verz)
    offen, geaendert = [], False
    for k in liste:
        if k["status"] != "offen":
            continue
        if jetzt - datetime.fromisoformat(k["zeit"]) > timedelta(days=KORREKTUR_TAGE):
            k["status"] = "abgelaufen"
            geaendert = True
        else:
            offen.append(k)
    if geaendert:
        _korrekturen_schreiben(verz, liste)
    return offen


def korrekturen_abloesen(abloesungen: dict[str, str], verz: Path = VERZ) -> None:
    """{"K2": "Titel, TT.MM.JJJJ"}: eine neuere Quelle hat das Thema aufgegriffen."""
    liste = _korrekturen_lesen(verz)
    for k in liste:
        if k["id"] in abloesungen and k["status"] == "offen":
            k["status"] = "abgeloest"
            k["durch"] = abloesungen[k["id"]]
    _korrekturen_schreiben(verz, liste)


# --------------------------------------------------------- Fuers Gespraech

def prompt_block(jetzt: datetime, verz: Path = VERZ) -> str:
    """Der Kern fuer jeden Prompt der Schlussantwort; leer, wenn es nichts gibt."""
    teile = []
    a = aktuell(verz)
    if a and a["abschnitte"].get("worum"):
        teile.append(f"Lagebild vom {_stand(a['datum'])} (automatisch abgeleitet, "
                     f"nicht geprüft) – worum es geht:\n"
                     + _kuerzen(a["abschnitte"]["worum"], KERN_ZEICHEN))
    korr = offene_korrekturen(jetzt, verz)
    if korr:
        zeilen = [f"- {datetime.fromisoformat(k['zeit']).strftime('%d.%m. %H:%M')}: {k['text']}"
                  for k in korr]
        teile.append("Korrekturen des Nutzers, sie gelten vor dem Lagebild:\n"
                     + "\n".join(zeilen))
    return "\n\n".join(teile)


def abschnitt_fuer(art: str, verz: Path = VERZ) -> tuple[str, str, list[str], date] | None:
    a = aktuell(verz)
    if not a:
        return None
    text = _kuerzen(a["abschnitte"].get(art) or KEINE_ANGABEN, ABSCHNITT_ZEICHEN)
    return UEBERSCHRIFT[art], text, _quellen(text), a["datum"]


def lage_frage(art: str, frage: str, jetzt: datetime,
               verz: Path = VERZ) -> tuple[dict, str, list[str]] | None:
    """(Buehnennachricht, Frage an das Modell, Quellentitel) fuer eine der vier
    Lagefragen; None, wenn es noch kein Lagebild gibt."""
    teil = abschnitt_fuer(art, verz)
    if not teil:
        return None
    titel, text, quellen, d = teil
    stand = _stand(d)
    buehne = {"typ": "lagebild", "titel": titel, "text": text, "stand": stand}
    alt = (f"Nenne dabei, dass das Lagebild vom {stand} ist.\n"
           if (jetzt.date() - d).days > 2 else "")
    f = (f"Aus dem Lagebild vom {stand} (automatisch abgeleitet), Abschnitt „{titel}“:\n\n"
         f"{text}\n\n{alt}Beantworte damit in höchstens zwei Sätzen: {frage}")
    return buehne, f, quellen


def ganzes_lagebild(verz: Path = VERZ) -> dict | None:
    a = aktuell(verz)
    if not a:
        return None
    return {"typ": "lagebild", "titel": "Lagebild", "text": a["text"],
            "stand": _stand(a["datum"])}


# --------------------------------------------------------- Eingaben

def konfiguration() -> dict:
    try:
        eigen = json.loads(KONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        eigen = {}
    return {**VORGABEN, **{k: v for k, v in eigen.items() if not k.startswith("_")}}


def _passt(pfad: str, muster: list[str]) -> bool:
    """Muster ohne "/" gelten fuer den Dateinamen, Muster mit "/" fuer den Pfad
    in der Quelle (mit fuehrendem "/"). Gegen den ganzen Pfad traf "*STAND*"
    jede Datei in einem Ordner "Altbestand" und "spalt_abstand.json"."""
    rel = "/" + pfad.partition(":")[2].lower()
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(rel if "/" in m else name, m.lower()) for m in muster)


def _git_datum(repo: Path, rel: str) -> date | None:
    r = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%cs", "--", rel],
                       capture_output=True, text=True, timeout=30)
    try:
        return date.fromisoformat(r.stdout.strip())
    except ValueError:
        return None


def _dokument_datum(pfad: str, herkunft: str, repos: Path) -> date | None:
    """Letzte Aenderung: bei Git-Quellen aus der Historie, sonst die Dateizeit.
    None, wenn sich nichts feststellen laesst (etwa bei Nextcloud)."""
    quelle, _, rel = pfad.partition(":")
    if (repos / quelle / ".git").exists():
        return _git_datum(repos / quelle, rel)
    p = Path(herkunft) if herkunft else None
    if p and p.is_file():
        return date.fromtimestamp(p.stat().st_mtime)
    return None


def _dokument_text(c: sqlite3.Connection, pfad: str, zeichen: int) -> str:
    zeilen = c.execute("SELECT text FROM abschnitte WHERE pfad=? "
                       "ORDER BY CAST(nr AS INTEGER)", (pfad,)).fetchall()
    return _kuerzen("\n\n".join(z[0] for z in zeilen), zeichen)


def _commits(repo: Path, seit: datetime, anzahl: int) -> list[tuple[date, str, str]]:
    r = subprocess.run(["git", "-C", str(repo), "log", "--no-merges",
                        f"--since={seit.isoformat()}", f"-n{anzahl}",
                        "--format=%cs%x1f%s%x1f%b%x1e"],
                       capture_output=True, text=True, timeout=60)
    aus = []
    for satz in r.stdout.split("\x1e"):
        teile = satz.strip("\n").split("\x1f")
        if len(teile) == 3:
            aus.append((date.fromisoformat(teile[0]), teile[1].strip(), teile[2].strip()[:500]))
    return aus


def _zusammenfassung(text: str) -> str:
    """Abschnitt "## Zusammenfassung" eines Protokolls, ohne den Hinweiskasten."""
    m = re.search(r"^## Zusammenfassung\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return ""
    return "\n".join(z for z in m.group(1).splitlines()
                     if not z.lstrip().startswith(">")).strip()


def eingaben_sammeln(konf: dict, c: sqlite3.Connection, repos: Path, aufnahmen: Path,
                     jetzt: datetime, verz: Path = VERZ) -> list[dict]:
    """Alle Eingaben fuer ein Lagebild, deterministisch und mit Datum.

    Korrekturen behalten ihre Kennung (K…), alles andere bekommt E1, E2, … in
    der Reihenfolge Statusdokumente, Commits (neueste zuerst), Protokolle,
    Vorzeigbares. Aktive Git-Quellen sind die, die im Index stehen -- inaktive
    entfernt der Abgleich von dort, ihr Klon kann aber noch auf der Platte liegen.
    """
    aus = [{"id": k["id"], "art": "korrektur", "titel": "Korrektur",
            "datum": datetime.fromisoformat(k["zeit"]).date(), "text": k["text"]}
           for k in offene_korrekturen(jetzt, verz)]
    weitere: list[dict] = []
    dokumente = c.execute("SELECT pfad, titel, herkunft FROM dokumente ORDER BY pfad").fetchall()
    gesehen = set()
    status = konf.get("status", [])
    for pfad, titel, herkunft in dokumente:
        if status and _passt(pfad, status):
            gesehen.add(pfad)
            weitere.append({"art": "status", "titel": titel,
                            "datum": _dokument_datum(pfad, herkunft, repos),
                            "text": _dokument_text(c, pfad, konf["dokument_zeichen"])})

    seit = jetzt - timedelta(days=konf["tage"])
    quellen = sorted({q for (q,) in c.execute("SELECT DISTINCT quelle FROM dokumente")})
    commits = []
    for q in quellen:
        if (repos / q / ".git").exists():
            commits += _commits(repos / q, seit, konf["commits_max"])
    commits.sort(key=lambda x: x[0], reverse=True)
    for d, betreff, text in commits[:konf["commits_max"]]:
        weitere.append({"art": "commit", "titel": betreff, "datum": d,
                        "text": f"{betreff}\n{text}".strip()})

    if aufnahmen.is_dir():
        for p in sorted(aufnahmen.glob("*/protokoll.md"), reverse=True):
            wann = datetime.fromtimestamp(p.stat().st_mtime).astimezone()
            if wann < seit:
                continue
            z = _zusammenfassung(p.read_text(encoding="utf-8"))
            if z:
                weitere.append({"art": "protokoll", "titel": f"Gesprächsprotokoll {p.parent.name}",
                                "datum": wann.date(), "text": z})

    vz = konf.get("vorzeigbar", [])
    if vz:
        for pfad, titel, herkunft in dokumente:
            if pfad not in gesehen and _passt(pfad, vz):
                weitere.append({"art": "vorzeigbar", "titel": titel,
                                "datum": _dokument_datum(pfad, herkunft, repos),
                                "text": _dokument_text(c, pfad, 1500)})
        try:
            bilder = c.execute("SELECT pfad, rel, unterschrift FROM bilder ORDER BY pfad").fetchall()
        except sqlite3.OperationalError:           # Bilderkatalog noch nicht angelegt
            bilder = []
        for pfad, rel, unterschrift in bilder:
            if _passt(pfad, vz):
                stamm = Path(rel).stem
                weitere.append({"art": "vorzeigbar", "titel": Path(rel).name,
                                "datum": _dokument_datum(pfad, "", repos),
                                "text": f"{unterschrift or stamm}. Zeigen mit: Bildanzeige {stamm}"})

    for i, e in enumerate(weitere, 1):
        e["id"] = f"E{i}"
    return aus + [{"id": e["id"], **{k: e[k] for k in ("art", "titel", "datum", "text")}}
                  for e in weitere]


def budget_einhalten(eingaben: list[dict], max_zeichen: int) -> list[dict]:
    """Haelt die Eingaben unter max_zeichen: erst fallen die aeltesten Commits,
    dann werden alle uebrigen Texte gleichmaessig gekuerzt. Korrekturen bleiben."""
    def summe(liste):
        return sum(len(x["text"]) for x in liste if x["art"] != "korrektur")
    aus = [dict(x) for x in eingaben]
    while summe(aus) > max_zeichen:
        commits = [x for x in aus if x["art"] == "commit"]
        if not commits:
            break
        aeltester = min(commits, key=lambda x: x["datum"])
        aus.remove(aeltester)
    gesamt = summe(aus)
    if gesamt > max_zeichen:
        anteil = max_zeichen / gesamt
        for x in aus:
            if x["art"] != "korrektur":
                x["text"] = x["text"][:int(len(x["text"]) * anteil)]
    return aus


# --------------------------------------------------------- Erzeugen und pruefen

SYSTEM_ERZEUGUNG = """Du schreibst das Lagebild eines Forschungsprojekts: eine knappe Zusammenfassung
des aktuellen Stands für das Team. Grundlage sind NUR die Eingaben in der Nachricht.

Gliederung, genau diese Überschriften in dieser Reihenfolge:
## Worum es geht
## Stand
## Als Nächstes
## Probleme und Herausforderungen
## Vorzeigbar

Regeln:
- Je Abschnitt höchstens sechs Punkte, jeder als Zeile "- …", ein bis zwei Sätze.
- "Worum es geht" erklärt in wenigen Punkten Ziel und Ansatz des Projekts.
- "Stand" nennt, woran gearbeitet wird und was seit dem vorigen Lagebild fertig wurde.
- Jeder Punkt endet mit den Kennungen der Eingaben, auf denen er beruht, z. B. [E3]
  oder [E3, K1]. Ein Punkt ohne Kennung wird verworfen.
- Schreibe nichts, was nicht in den Eingaben steht. Lieber ein Punkt weniger.
- Korrekturen (Kennung K…) stammen vom Nutzer und gehen allem anderen vor.
- Das vorige Lagebild dient nur zum Vergleich. Zitiere es nie.
- Steht zu einem Abschnitt nichts in den Eingaben, schreibe darunter nur:
  Keine Angaben in den Quellen.

Zum Schluss ein Abschnitt
## Korrekturen
mit einer Zeile je Korrektur: "K1: abgelöst durch E7", wenn eine neuere Eingabe das
Thema der Korrektur aufgreift, sonst "K1: gilt weiter"."""


def _datum_text(d: date | None) -> str:
    return _stand(d) if d else DATUM_UNBEKANNT


async def erzeugen(eingaben: list[dict], vorher: str | None, fragen) -> str:
    """Laesst das Modell das Lagebild schreiben. fragen(system, nutzer) -> str."""
    teile = ["Eingaben:"]
    for e in eingaben:
        teile.append(f"[{e['id']}] {e['art']}: {e['titel']} ({_datum_text(e['datum'])})\n"
                     f"{e['text']}")
    if vorher:
        teile.append("Voriges Lagebild, nur zum Vergleich, nicht zitieren:\n" + vorher)
    return await fragen(SYSTEM_ERZEUGUNG, "\n\n".join(teile))


_KOPF = re.compile(r"^\s*(?:#{1,6}\s*|\*\*)(.+?)(?:\*\*)?\s*:?\s*$")
_PUNKT = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)\s*$")
_IDS = re.compile(r"\[\s*([EK]\d+(?:\s*[,;]\s*[EK]\d+)*)\s*\]")
_ABLOESUNG = re.compile(r"^\s*\[?(K\d+)\]?\s*:\s*abgel(?:ö|oe)st\s+durch\s*\[?(E\d+)\]?",
                        re.I)


def _abschnitt_von(titel: str) -> str | None:
    t = titel.strip().lower().replace("ae", "ä")
    for k, u in ABSCHNITTE:
        if t == u.lower():
            return k
    return "korrekturen" if t == "korrekturen" else None


def pruefen(roh: str, eingaben: list[dict], jetzt: datetime) -> tuple[str | None, dict[str, str], int]:
    """Prueft die Modellausgabe im Code: (Lagebild oder None, Abloesungen, entfernt).

    Gilt nur, was auf eine Eingabe verweist, die es gab. Kennungen werden durch
    [Titel, Datum] ersetzt; Punkte ohne gueltigen Verweis fallen weg. Bleibt in
    keinem Abschnitt etwas uebrig, ist das Ergebnis None, und das alte Lagebild
    gilt weiter.
    """
    nach_id = {e["id"]: e for e in eingaben}
    punkte: dict[str, list[str]] = {k: [] for k, _ in ABSCHNITTE}
    abl: dict[str, str] = {}
    entfernt, bei = 0, None
    for zeile in roh.splitlines():
        if not zeile.strip():
            continue
        m = _KOPF.match(zeile)
        if m and _abschnitt_von(m.group(1)):
            bei = _abschnitt_von(m.group(1))
            continue
        if bei == "korrekturen":
            a = _ABLOESUNG.match(zeile)
            if a:
                k, e = nach_id.get(a.group(1)), nach_id.get(a.group(2))
                if (k and e and k["art"] == "korrektur" and e["art"] != "korrektur"
                        and e["datum"] and e["datum"] > k["datum"]):
                    abl[k["id"]] = f"{e['titel']}, {_datum_text(e['datum'])}"
            continue
        if bei is None or zeile.strip() == KEINE_ANGABEN:
            continue
        p = _PUNKT.match(zeile)
        if not p:
            entfernt += 1                  # Fliesstext ohne Punkt: nicht pruefbar
            continue
        satz, gueltig = p.group(1), False

        def ersetzen(mt):
            nonlocal gueltig
            ids = [i for i in re.split(r"\s*[,;]\s*", mt.group(1)) if i in nach_id]
            if not ids:
                return ""
            gueltig = True
            return "[" + "; ".join(f"{nach_id[i]['titel']}, {_datum_text(nach_id[i]['datum'])}"
                                   for i in ids) + "]"
        satz = _IDS.sub(ersetzen, satz).strip()
        if gueltig:
            punkte[bei].append(f"- {satz}")
        else:
            entfernt += 1
    if not any(punkte.values()):
        return None, {}, entfernt

    kopf = (f"# Lagebild vom {_stand(jetzt.date())}\n\n> Automatisch abgeleitet, nicht "
            f"geprüft. Erzeugt um {jetzt:%H:%M} aus {len(eingaben)} Eingaben.\n\n")

    def bauen():
        return kopf + "\n\n".join(
            f"## {u}\n" + ("\n".join(punkte[k]) if punkte[k] else KEINE_ANGABEN)
            for k, u in ABSCHNITTE) + "\n"
    text = bauen()
    while len(text) > LAGEBILD_ZEICHEN:
        laengster = max(punkte, key=lambda k: sum(len(x) for x in punkte[k]))
        punkte[laengster].pop()
        entfernt += 1
        text = bauen()
    return text, abl, entfernt


def ablegen(text: str, eingaben: list[dict], entfernt: int, jetzt: datetime,
            verz: Path = VERZ) -> Path:
    verz.mkdir(parents=True, exist_ok=True)
    name = f"{jetzt:%Y-%m-%dT%H%M}"
    p = verz / f"{name}.md"
    p.write_text(text, encoding="utf-8")
    # Ohne die Texte: nachvollziehbar, was eingeflossen ist, ohne die Unterlagen
    # ein zweites Mal abzulegen.
    (verz / f"{name}.eingaben.json").write_text(json.dumps({
        "erzeugt": jetzt.isoformat(timespec="seconds"), "entfernt": entfernt,
        "eingaben": [{"id": e["id"], "art": e["art"], "titel": e["titel"],
                      "datum": e["datum"].isoformat() if e["datum"] else None}
                     for e in eingaben]}, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


# --------------------------------------------------------- Lauf

OK, FEHLER, BELEGT, FALSCHES_MODELL = 0, 1, 3, 4
WER = "kihiwi-lagebild"
BELEGUNG_URL = os.environ.get("BELEGUNG_URL", "http://127.0.0.1:8930")
LOG = konfig.WURZEL / "logs" / "lagebild.log"


def belegung_frei(status: dict | None, wer: str) -> bool:
    """Streng: haelt jemand anderes eine EXKLUSIVE Belegung -- gleich fuer welches
    Modell --, wird gewartet. Nicht erreichbar heisst nicht verboten, wie beim
    Modell-Waechter: ein Ausfall der Buchfuehrung soll nichts lahmlegen."""
    if status is None:
        return True
    return not any(b.get("art") == "exklusiv" and b.get("wer") != wer
                   for b in status.get("belegt_von", []))


def _anfrage(pfad: str, daten: dict | None = None, timeout: float = 5) -> dict | None:
    rumpf = json.dumps(daten).encode() if daten is not None else None
    req = urllib.request.Request(BELEGUNG_URL + pfad, data=rumpf,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as a:
            return json.load(a)
    except (OSError, ValueError):
        return None


def _status() -> dict | None:
    return _anfrage("/status")


def belegen(wer: str, profil: str, dauer: int) -> bool | None:
    """True bei Zuschlag, False wenn belegt, None wenn die Stelle fehlt."""
    a = _anfrage("/belegen", {"wer": wer, "profil": profil, "art": "exklusiv",
                              "dauer": dauer, "zweck": "Lagebild"})
    if a is None:
        return None
    return bool(a.get("zuschlag")) and not a.get("wechsel_noetig")


def freigeben(wer: str) -> None:
    _anfrage("/freigeben", {"wer": wer})


def _modell_auf_server() -> str | None:
    try:
        with urllib.request.urlopen(konfig.LLM_URL.rstrip("/") + "/models", timeout=5) as a:
            return json.load(a)["data"][0]["id"]
    except (OSError, ValueError, KeyError, IndexError):
        return None


def _protokollieren(*teile) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%F %T} " + " ".join(str(t) for t in teile) + "\n")


async def erneuern(abgleich: bool = True, nacht: bool = False, jetzt: datetime | None = None,
                   verz: Path = VERZ) -> int:
    """Ein Lagebild erzeugen. Nachts wird bei Belegung gewartet (alle 15 min bis
    nachts_bis), tags gibt der Lauf sofort auf. Ein Modell wechselt er nie."""
    konf = konfiguration()
    t0 = time.time()
    modell = _modell_auf_server()
    if modell not in konf["modelle"]:
        _protokollieren("falsches Modell:", modell)
        return FALSCHES_MODELL
    bis = datetime.strptime(konf["nachts_bis"], "%H:%M").time()
    while True:
        status = _status()
        if belegung_frei(status, WER):
            # Mit dem GELADENEN Profil anmelden: mit einem anderen verlangt die
            # Stelle einen Wechsel, und den macht dieser Lauf nie.
            profil = ((status or {}).get("profil")
                      or os.environ.get("KIHIWI_PROFIL", "qwen38flash"))
            if belegen(WER, profil, 3600) is not False:
                break
            # Abgelehnt: trotzdem freigeben. Die Stelle traegt die Anmeldung
            # unter Umstaenden schon ein, und sie sperrte dann eine Stunde lang.
            freigeben(WER)
        if not nacht or datetime.now().time() >= bis:
            _protokollieren("belegt, kein Lagebild")
            return BELEGT
        await asyncio.sleep(900)
    try:
        if abgleich:
            await asyncio.to_thread(einlesen.alles)
        jetzt = jetzt or datetime.now().astimezone()
        c = index.lesen()
        try:
            eingaben = eingaben_sammeln(konf, c, einlesen.REPOS, konfig.AUFNAHMEN, jetzt, verz)
        finally:
            c.close()
        eingaben = budget_einhalten(eingaben, konf["eingabe_zeichen"])
        vorher = aktuell(verz)
        roh = await erzeugen(eingaben, vorher["text"] if vorher else None,
                             lambda sy, nu: llm.antwort_text(sy, nu, max_tokens=3500,
                                                             temperatur=0.2, timeout=900))
        text, abl, entfernt = pruefen(roh, eingaben, jetzt)
        if text is None:
            _protokollieren("gescheitert: kein gueltiger Punkt", len(eingaben), "Eingaben")
            return FEHLER
        ablegen(text, eingaben, entfernt, jetzt, verz)
        korrekturen_abloesen(abl, verz)
        _protokollieren("ok:", len(eingaben), "Eingaben,", entfernt, "entfernt,",
                        len(abl), "Korrekturen abgeloest,", f"{time.time() - t0:.0f} s")
        return OK
    except Exception as e:                       # noqa: BLE001 -- Nachtlauf, nie haengen
        _protokollieren("gescheitert:", repr(e))
        return FEHLER
    finally:
        freigeben(WER)


def main(argv: list[str]) -> int:
    befehl = argv[0] if argv else "zeigen"
    if befehl == "erneuern":
        return asyncio.run(erneuern(abgleich="--ohne-abgleich" not in argv,
                                    nacht="--nacht" in argv))
    if befehl == "zeigen":
        a = aktuell()
        print(a["text"] if a else "Noch kein Lagebild.")
        return OK
    print("./dienste.sh lagebild [erneuern [--ohne-abgleich] [--nacht] | zeigen]")
    return FEHLER


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
