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

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from sprachdienst import konfig

VERZ = konfig.WURZEL / "zustand" / "lagebild"

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
            if name and _DATUM.fullmatch(datum.strip()) and name.strip() not in titel:
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
