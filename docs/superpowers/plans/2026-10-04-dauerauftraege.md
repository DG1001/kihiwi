# Daueraufträge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kiwi führt Daueraufträge aus, die bei einem neuen Commit, einem neuen Protokoll oder nach Zeitplan Stoff sammeln, ihn vom Modell bewerten lassen und einen belegten Befund in die Ablage legen.

**Architecture:** Zwei neue Module ohne Abhängigkeit vom Dienst: `sprachdienst/auftraege.py` (Auftragsdatei, Warteschlange, Zustand, Prüfprotokoll, Lagebericht) und `sprachdienst/auftrag_lauf.py` (Stoff sammeln, bewerten, Belege prüfen, Befund schreiben). `gateway.py` verbindet beide mit Abgleich, Nachbereitung, Leerlaufprüfung und Stimme. Das Modell bekommt fest gesammelten Stoff in einem Aufruf; es benutzt keine Werkzeuge.

**Tech Stack:** Python 3.12, nur Standardbibliothek. `asyncio`, `http.client`, `sqlite3` (über `wissen/index.py`), `git` als Unterprozess. Tests als einfache `assert`-Skripte, kein pytest.

**Spec:** `docs/superpowers/specs/2026-10-04-dauerauftraege-design.md`

## Global Constraints

- **Dieses Repo ist öffentlich, die eingelesenen Quellen sind privat.** In Code, Kommentaren, Tests, Doku und Commit-Texten stehen nur erfundene Beispiele. Keine Dateinamen, Pfade, Zitate, Zahlen oder Projektbegriffe aus `wissen/repos/` oder `aufnahmen/`.
- `auftraege.json` und `befunde/` sind **nicht versioniert**; im Repo liegt `auftraege.beispiel.json` mit lauter `"aktiv": false`.
- Keine neuen Abhängigkeiten. Kein pytest: Tests laufen mit `.venv/bin/python -m sprachdienst.auftraege_test`.
- Ein Dauerauftrag **liest und berichtet**. Kein Schreiben in Quellen oder Wissensindex; `befunde/` ist keine Quelle des Index.
- Stoff je Lauf höchstens 24.000 Zeichen. Zeitgrenze je Modellaufruf 120 s.
- Kiwi spricht **nicht** von selbst über Befunde.
- Bezeichner, Kommentare und Meldungen auf Deutsch, im Stil der umgebenden Dateien (Umlaute in Kommentaren als ae/oe/ue, in gesprochenen Texten echt).
- Doku in `fachlich.md`, `technisch.md`, `entwicklung.md`.
- Commits auf Deutsch, mit der Zeile `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. **Kein `git push`** — das entscheidet der Nutzer.
- Nach Änderungen an `gateway.py` oder `absicht.py`: `./dienste.sh namen` muss „0 Fund(e)" melden.

## Review Focus

1. **Auftragsdatei wird im Betrieb bearbeitet und ist einen Moment lang halb geschrieben.** Erwartet: kein Absturz der Hintergrundschleife, kein Auftrag läuft, nach der Korrektur laufen sie wieder. → Test in Task 1 (abgeschnittenes JSON), Schleife in Task 7 fängt Ausnahmen.
2. **Das Modell packt sein JSON in einen Code-Zaun oder schreibt einen Satz davor.** Erwartet: wird trotzdem ausgewertet. → Test in Task 4.
3. **Ein Zitat weicht nur in Leerraum, Zeilenumbruch oder Großschreibung vom Stoff ab.** Erwartet: gilt als belegt. → Test in Task 4.
4. **Ein Commit bringt eine riesige Datei.** Erwartet: Stoff bleibt unter der Grenze, der Befund vermerkt die Kürzung. → Test in Task 3.
5. **`zustand/auftraege.json` ist nach einem Absturz leer oder kaputt.** Erwartet: Start mit leerem Bestand statt Ausnahme. → Test in Task 2.

---

## Dateien

| Datei | Aufgabe |
|---|---|
| `sprachdienst/auftraege.py` (neu) | `Auftrag`, `laden()`, `Bestand`, `protokollieren()`, `zuordnen()`, `lage_text()`, `lage_satz()` |
| `sprachdienst/auftrag_lauf.py` (neu) | `Stueck`, `stoff_stand()`, `stoff_protokoll()`, `stoff_thema()`, `begrenzen()`, `auswerten()`, `lauf()`, `befund_schreiben()` |
| `sprachdienst/auftraege_test.py` (neu) | alle Tests ohne Dienst und Modell |
| `sprachdienst/llm.py` | `frage_abbrechbar()` |
| `wissen/einlesen.py` | Commit-Bereich im Bericht von `alles()` |
| `sprachdienst/absicht.py` | drei Auslösewörter |
| `sprachdienst/gateway.py` | Hintergrundschleife, Anstöße, Vorrang der Stimme, Ablage, Sprachbefehle |
| `sprachdienst/klient.html` | Bühnentyp `auftragslage`, Art `befund`, Merker |
| `auftraege.beispiel.json`, `.gitignore` | Vorlage und Ausnahmen |

---

### Task 1: Auftragsdatei laden und prüfen

**Files:**
- Create: `sprachdienst/auftraege.py`
- Create: `sprachdienst/auftraege_test.py`
- Create: `auftraege.beispiel.json`
- Modify: `.gitignore` (am Ende)

**Interfaces:**
- Consumes: `sprachdienst.konfig.WURZEL` (ein `Path` auf das Projektverzeichnis)
- Produces:
  - `auftraege.Auftrag` — eingefrorene Dataclass: `name: str`, `art: str` (`"stand"|"protokoll"|"thema"`), `ziel: str`, `aktiv: bool`, `quelle: str`, `dazu: tuple[str, ...]`, `suchen: tuple[str, ...]`, `alle_stunden: int`
  - `auftraege.Geladen` — Dataclass: `auftraege: list[Auftrag]`, `abgleich_minuten: int`, `fehler: list[str]`
  - `auftraege.laden(pfad: Path | None = None, git_quellen: set[str] | None = None) -> Geladen`
  - Konstanten `auftraege.DATEI`, `BEISPIEL`, `ZUSTAND`, `PROTOKOLL`, `BEFUNDE` (alle `Path`)

- [ ] **Step 1: Test schreiben**

`sprachdienst/auftraege_test.py`:

```python
"""Prueft die Dauerauftraege ohne Dienst und ohne Modell.

    .venv/bin/python -m sprachdienst.auftraege_test

Alle Beispiele sind erfunden: dieses Repo ist oeffentlich, die eingelesenen
Quellen sind es nicht.
"""
import asyncio
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from . import auftraege

GUT = {"abgleich_minuten": 30, "auftraege": [
    {"name": "stand-beispiel", "art": "stand", "quelle": "beispielrepo",
     "ziel": "Prüfe neue Commits gegen die Spezifikation.",
     "dazu": ["Spezifikation"]},
    {"name": "protokoll-nachhalten", "art": "protokoll",
     "ziel": "Ziehe Zusagen und offene Punkte heraus."},
    {"name": "thema-beispiel", "art": "thema", "aktiv": False,
     "ziel": "Melde neue Veröffentlichungen.",
     "suchen": ["beispielbegriff"], "alle_stunden": 24}]}


def _datei(t, inhalt):
    p = Path(t) / "a.json"
    p.write_text(inhalt if isinstance(inhalt, str) else json.dumps(inhalt),
                 encoding="utf-8")
    return p


def _mit(**aenderung):
    """GUT mit einem geaenderten ersten Auftrag."""
    d = json.loads(json.dumps(GUT))
    d["auftraege"][0].update(aenderung)
    return d


def test_laden():
    with tempfile.TemporaryDirectory() as t:
        g = auftraege.laden(_datei(t, GUT), {"beispielrepo"})
        assert g.fehler == [] and g.abgleich_minuten == 30, g
        assert [a.name for a in g.auftraege] == [
            "stand-beispiel", "protokoll-nachhalten", "thema-beispiel"]
        assert g.auftraege[0].dazu == ("Spezifikation",)
        assert g.auftraege[0].aktiv is True and g.auftraege[2].aktiv is False
        assert g.auftraege[2].suchen == ("beispielbegriff",)

        # Unbekannte Quelle: nur dieser Auftrag faellt, die uebrigen laufen.
        g = auftraege.laden(_datei(t, GUT), {"anderes"})
        assert [a.name for a in g.auftraege] == [
            "protokoll-nachhalten", "thema-beispiel"]
        assert len(g.fehler) == 1 and "stand-beispiel" in g.fehler[0]

        # Ohne Quellenliste wird die Quelle nicht geprueft.
        assert len(auftraege.laden(_datei(t, GUT)).auftraege) == 3
        # Ein ABGESCHALTETER Auftrag mit unbekannter Quelle ist kein Fehler.
        g = auftraege.laden(_datei(t, _mit(aktiv=False)), {"anderes"})
        assert g.fehler == [] and len(g.auftraege) == 3

        # Fehler in der Datei: es laeuft KEIN Auftrag.
        doppelt = json.loads(json.dumps(GUT))
        doppelt["auftraege"].append(dict(doppelt["auftraege"][1]))
        kaputt = [
            '{"auftraege": [',                      # halb geschrieben
            "[]",                                   # kein Objekt
            {"auftraege": "x"},
            doppelt,
            _mit(name="Stand Beispiel"),
            _mit(art="morgenlage"),
            _mit(ziel="  "),
            _mit(quelle=""),
            _mit(dazu="Spezifikation"),
            _mit(aktiv="ja"),
            {**GUT, "abgleich_minuten": -1},
            {"auftraege": [{"name": "t", "art": "thema", "ziel": "z"}]},
            {"auftraege": [{"name": "t", "art": "thema", "ziel": "z",
                            "suchen": ["a"], "alle_stunden": 0}]},
        ]
        for k in kaputt:
            g = auftraege.laden(_datei(t, k), {"beispielrepo"})
            assert g.auftraege == [] and g.fehler, k

        g = auftraege.laden(Path(t) / "fehlt.json")
        assert g.auftraege == [] and g.fehler

    # Die Vorlage im Repo ist gueltig und laesst nichts laufen.
    g = auftraege.laden(auftraege.BEISPIEL)
    assert g.fehler == [], g.fehler
    assert g.auftraege and not any(a.aktiv for a in g.auftraege)
    print("laden: ok")


def alle():
    test_laden()


if __name__ == "__main__":
    alle()
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `.venv/bin/python -m sprachdienst.auftraege_test`
Expected: `ImportError: cannot import name 'auftraege'`

- [ ] **Step 3: Modul und Vorlage schreiben**

`sprachdienst/auftraege.py`:

```python
"""Dauerauftraege: was sie sind, was ansteht, was gelaufen ist.

Ein Dauerauftrag bleibt bestehen. Er wird von einem Ereignis oder einem
Zeitplan ausgeloest, prueft etwas und legt einen Befund ab -- er liest und
berichtet, er aendert nichts.

Dieses Modul kennt den Sprachdienst nicht. Es haelt die Auftragsdatei, die
Warteschlange und den Zustand; den Lauf selbst macht `auftrag_lauf.py`, und
`gateway.py` verbindet beide mit Abgleich, Nachbereitung und Stimme.

Die Auftragsdatei ist NICHT versioniert: die Ziele verraten, woran gearbeitet
wird. Im Repo liegt `auftraege.beispiel.json` als Vorlage und Rueckfall --
dasselbe Muster wie bei `quellen.json` und `vokabular.txt`.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from . import konfig

log = logging.getLogger("kihiwi.auftraege")

DATEI = konfig.WURZEL / "auftraege.json"
BEISPIEL = konfig.WURZEL / "auftraege.beispiel.json"
ZUSTAND = konfig.WURZEL / "zustand" / "auftraege.json"
PROTOKOLL = konfig.WURZEL / "zustand" / "auftraege.log.jsonl"
BEFUNDE = konfig.WURZEL / "befunde"

ARTEN = ("stand", "protokoll", "thema")
# Der Name wird gesprochen ("Auftragslauf stand beispiel") und ist Ordnername
# unter befunde/ -- deshalb eng gefasst.
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Auftrag:
    name: str
    art: str
    ziel: str
    aktiv: bool = True
    quelle: str = ""                    # nur stand: Git-Quelle aus quellen.json
    dazu: tuple[str, ...] = ()          # nur stand: Suchbegriffe fuer Vergleichsstoff
    suchen: tuple[str, ...] = ()        # nur thema: Suchanfragen
    alle_stunden: int = 24              # nur thema


@dataclass
class Geladen:
    auftraege: list[Auftrag]
    abgleich_minuten: int
    fehler: list[str]


def _auftrag(d, i: int) -> Auftrag:
    if not isinstance(d, dict):
        raise ValueError(f"Eintrag {i + 1} ist kein Objekt")
    name = d.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError(f"Eintrag {i + 1}: name {name!r} — erlaubt sind "
                         "Kleinbuchstaben, Ziffern und Bindestriche")
    art = d.get("art")
    if art not in ARTEN:
        raise ValueError(f"{name}: art {art!r} unbekannt")
    ziel = d.get("ziel")
    if not isinstance(ziel, str) or not ziel.strip():
        raise ValueError(f"{name}: ziel fehlt")
    aktiv = d.get("aktiv", True)
    if not isinstance(aktiv, bool):
        raise ValueError(f"{name}: aktiv muss true oder false sein")

    def liste(feld: str) -> tuple[str, ...]:
        w = d.get(feld, [])
        if not isinstance(w, list) or any(
                not isinstance(x, str) or not x.strip() for x in w):
            raise ValueError(f"{name}: {feld} muss eine Liste von Texten sein")
        return tuple(w)

    quelle = d.get("quelle", "")
    if art == "stand" and (not isinstance(quelle, str) or not quelle):
        raise ValueError(f"{name}: quelle fehlt")
    suchen = liste("suchen")
    if art == "thema" and not suchen:
        raise ValueError(f"{name}: suchen fehlt")
    stunden = d.get("alle_stunden", 24)
    if type(stunden) is not int or stunden < 1:
        raise ValueError(f"{name}: alle_stunden muss eine ganze Zahl ab 1 sein")
    return Auftrag(name, art, ziel.strip(), aktiv,
                   quelle if art == "stand" else "", liste("dazu"), suchen, stunden)


def laden(pfad: Path | None = None,
          git_quellen: set[str] | None = None) -> Geladen:
    """Die Auftragsdatei lesen und pruefen.

    Ein Fehler in der Datei heisst: es laeuft KEIN Auftrag. Ein halb gelesener
    Bestand ist schlimmer als keiner -- niemand merkt, dass die Haelfte fehlt.
    Einzige Ausnahme ist eine unbekannte Quelle: die haengt an `quellen.json`
    und nicht an dieser Datei, also faellt nur der eine Auftrag.
    """
    pfad = pfad or (DATEI if DATEI.exists() else BEISPIEL)
    try:
        roh = json.loads(pfad.read_text(encoding="utf-8"))
        if not isinstance(roh, dict) or not isinstance(roh.get("auftraege"), list):
            raise ValueError('erwartet wird {"auftraege": [...]}')
        minuten = roh.get("abgleich_minuten", 30)
        if type(minuten) is not int or minuten < 0:
            raise ValueError("abgleich_minuten muss eine ganze Zahl ab 0 sein")
        alle = [_auftrag(d, i) for i, d in enumerate(roh["auftraege"])]
        namen = [a.name for a in alle]
        doppelt = sorted({n for n in namen if namen.count(n) > 1})
        if doppelt:
            raise ValueError(f"Name doppelt: {', '.join(doppelt)}")
    except (OSError, ValueError) as e:
        return Geladen([], 0, [f"{pfad.name}: {e}"])

    fehler, gut = [], []
    for a in alle:
        # Nur aktive pruefen: ein abgeschalteter Auftrag mit unbekannter Quelle
        # stoert niemanden -- und die Vorlage im Repo nennt eine erfundene.
        if (a.art == "stand" and a.aktiv and git_quellen is not None
                and a.quelle not in git_quellen):
            fehler.append(f"{a.name}: Quelle {a.quelle!r} ist keine aktive "
                          "Git-Quelle in quellen.json")
            continue
        gut.append(a)
    return Geladen(gut, minuten, fehler)
```

`auftraege.beispiel.json`:

```json
{
  "abgleich_minuten": 30,
  "auftraege": [
    {"name": "stand-beispiel", "art": "stand", "aktiv": false,
     "quelle": "beispielrepo",
     "ziel": "Prüfe neue Commits gegen die Spezifikation und die Liste offener Entscheidungen. Melde Widersprüche und berührte offene Punkte.",
     "dazu": ["Spezifikation", "offene Entscheidungen"]},
    {"name": "protokoll-nachhalten", "art": "protokoll", "aktiv": false,
     "ziel": "Ziehe Zusagen, offene Punkte und Widersprüche zu früheren Protokollen heraus."},
    {"name": "thema-beispiel", "art": "thema", "aktiv": false,
     "ziel": "Melde neue Veröffentlichungen und Produktankündigungen.",
     "suchen": ["beispielbegriff eins", "beispielbegriff zwei"],
     "alle_stunden": 24}
  ]
}
```

An `.gitignore` anhängen:

```
# Dauerauftraege: die Ziele verraten, woran gearbeitet wird, und die Befunde
# tragen Auszuege aus den eigenen Quellen. Die .beispiel-Datei liegt im Repo.
auftraege.json
befunde/
```

- [ ] **Step 4: Test laufen lassen**

Run: `.venv/bin/python -m sprachdienst.auftraege_test`
Expected: `laden: ok`

- [ ] **Step 5: Commit**

```bash
git add sprachdienst/auftraege.py sprachdienst/auftraege_test.py auftraege.beispiel.json .gitignore
git commit -m "Daueraufträge: Auftragsdatei laden und prüfen

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Bestand — Warteschlange, Zustand, Prüfprotokoll, Namenszuordnung

**Files:**
- Modify: `sprachdienst/auftraege.py` (anhängen)
- Modify: `sprachdienst/auftraege_test.py`

**Interfaces:**
- Consumes: `auftraege.Auftrag`, `auftraege.ZUSTAND`, `auftraege.PROTOKOLL` aus Task 1
- Produces:
  - `auftraege.Bestand(pfad: Path | None = None)` mit
    - `einreihen(name: str, ausloeser: dict) -> bool` — `True`, wenn neu eingereiht; `False`, wenn mit einem wartenden Eintrag zusammengelegt
    - `naechster(aktive: set[str], jetzt: float | None = None) -> dict | None` — Eintrag `{"name", "ausloeser", "abbrueche", "nicht_vor"}`
    - `fertig(name: str) -> None`
    - `abgebrochen(name: str, jetzt: float | None = None) -> None`
    - `vertagen(name: str, sekunden: float, jetzt: float | None = None) -> None`
    - `pause(name: str, an: bool) -> None`, `pausiert: list[str]`
    - `lauf_merken(name: str, urteil: str, kurz: str = "", jetzt: float | None = None) -> None`, `laeufe: dict[str, dict]` mit `{"zeit", "urteil", "kurz"}`
    - `befund_merken(auftrag: str, datei: str, kurz: str, jetzt: float | None = None) -> None`
    - `ungelesene() -> list[dict]` — `{"auftrag", "datei", "kurz", "zeit", "gelesen"}`, nur wenn die Datei noch existiert
    - `alle_gelesen() -> None`
    - `gesehen_fuer(name: str) -> set[str]`, `gesehen_dazu(name: str, adressen: list[str]) -> None`
    - `thema_faellig(a: Auftrag, jetzt: float | None = None) -> bool`
    - `abgleich: float`, `abgleich_merken(jetzt: float) -> None`
    - `schlange: list[dict]`
  - `auftraege.protokollieren(eintrag: dict, pfad: Path | None = None) -> None`
  - `auftraege.zuordnen(gesprochen: str, namen: list[str]) -> list[str]`
  - `auftraege.sprechname(name: str) -> str`

Auslöser je Art: `stand` → `{"vorher": str, "nachher": str}` (`vorher` leer heißt: nur der letzte Commit); `protokoll` → `{"datei": str}`; `thema` → `{"zeit": float}`.

- [ ] **Step 1: Test schreiben**

In `sprachdienst/auftraege_test.py` vor `def alle():` einfügen:

```python
def test_bestand():
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "zustand.json"
        b = auftraege.Bestand(p)
        assert b.schlange == [] and b.naechster({"a"}) is None

        # Einreihen und Zusammenlegen: drei Commits, waehrend einer wartet,
        # sind EIN Lauf ueber den ganzen Bereich.
        assert b.einreihen("a", {"vorher": "111", "nachher": "222"}) is True
        assert b.einreihen("a", {"vorher": "222", "nachher": "333"}) is False
        assert b.einreihen("b", {"datei": "x/protokoll.md"}) is True
        assert len(b.schlange) == 2
        assert b.schlange[0]["ausloeser"] == {"vorher": "111", "nachher": "333"}
        # Andere Arten: der neuere Ausloeser ersetzt den wartenden.
        b.einreihen("b", {"datei": "y/protokoll.md"})
        assert b.schlange[1]["ausloeser"] == {"datei": "y/protokoll.md"}

        # Reihenfolge, nur aktive, nicht pausierte.
        assert b.naechster({"a", "b"})["name"] == "a"
        assert b.naechster({"b"})["name"] == "b"
        b.pause("a", True)
        assert b.naechster({"a", "b"})["name"] == "b"
        b.pause("a", False)
        assert b.naechster({"a", "b"})["name"] == "a"

        # Drei Abbrueche in Folge: zehn Minuten Ruhe.
        jetzt = 1000.0
        b.abgebrochen("a", jetzt); b.abgebrochen("a", jetzt)
        assert b.naechster({"a"}, jetzt)["name"] == "a"
        b.abgebrochen("a", jetzt)
        assert b.naechster({"a"}, jetzt + 599) is None
        assert b.naechster({"a"}, jetzt + 601)["name"] == "a"
        b.vertagen("b", 60, jetzt)
        assert b.naechster({"b"}, jetzt + 30) is None

        # Uebersteht Schreiben und Lesen.
        b.lauf_merken("a", "fund", "Ein Satz.", jetzt)
        befund = Path(t) / "befund.md"; befund.write_text("x")
        b.befund_merken("a", str(befund), "Ein Satz.", jetzt)
        b.befund_merken("a", str(Path(t) / "geloescht.md"), "Weg.", jetzt)
        b.gesehen_dazu("t", ["https://example.org/1", "https://example.org/2"])
        b.abgleich_merken(jetzt)
        c = auftraege.Bestand(p)
        assert [e["name"] for e in c.schlange] == ["a", "b"]
        assert c.laeufe["a"] == {"zeit": jetzt, "urteil": "fund", "kurz": "Ein Satz."}
        assert c.gesehen_fuer("t") == {"https://example.org/1", "https://example.org/2"}
        assert c.abgleich == jetzt
        # Ein geloeschter Befund zaehlt nicht mehr als ungelesen.
        assert [u["kurz"] for u in c.ungelesene()] == ["Ein Satz."]
        c.alle_gelesen()
        assert auftraege.Bestand(p).ungelesene() == []

        c.fertig("a")
        assert [e["name"] for e in auftraege.Bestand(p).schlange] == ["b"]

        # Zeitplan fuer thema.
        th = auftraege.Auftrag("t", "thema", "z", suchen=("x",), alle_stunden=2)
        d = auftraege.Bestand(Path(t) / "neu.json")
        assert d.thema_faellig(th, jetzt) is True          # nie gelaufen
        d.lauf_merken("t", "nichts", "", jetzt)
        assert d.thema_faellig(th, jetzt + 7199) is False
        assert d.thema_faellig(th, jetzt + 7201) is True
        d.einreihen("t", {"zeit": jetzt})
        assert d.thema_faellig(th, jetzt + 7201) is False  # wartet schon

        # Kaputte oder leere Zustandsdatei: leerer Bestand, keine Ausnahme.
        for inhalt in ("", "{", "[]", '{"schlange": "x"}'):
            p.write_text(inhalt)
            e = auftraege.Bestand(p)
            assert e.schlange == [] and e.laeufe == {}, inhalt
            e.einreihen("a", {"zeit": 1.0})                # und schreibbar

        # Pruefprotokoll: eine Zeile je Lauf.
        log = Path(t) / "log.jsonl"
        auftraege.protokollieren({"auftrag": "a", "urteil": "fund"}, log)
        auftraege.protokollieren({"auftrag": "b", "urteil": "nichts"}, log)
        zeilen = [json.loads(z) for z in log.read_text().splitlines()]
        assert [z["urteil"] for z in zeilen] == ["fund", "nichts"]
        assert all("zeit" in z for z in zeilen)
    print("bestand: ok")


def test_zuordnen():
    namen = ["stand-hauptrepo", "stand-nebenrepo", "protokoll-nachhalten",
             "thema-wettbewerb"]
    z = auftraege.zuordnen
    assert z("stand-hauptrepo", namen) == ["stand-hauptrepo"]
    # Die Spracherkennung zieht Zusammensetzungen auseinander.
    assert z("Stand Haupt Repo", namen) == ["stand-hauptrepo"]
    assert z("bitte den Auftrag Protokoll nachhalten fortsetzen", namen) == [
        "protokoll-nachhalten"]
    assert z("Wettbewerb", namen) == ["thema-wettbewerb"]
    assert z("tema wetbewerb", namen) == ["thema-wettbewerb"]      # verhoert
    assert z("stand", namen) == ["stand-hauptrepo", "stand-nebenrepo"]
    assert z("Kaffeemaschine", namen) == []
    assert z("", namen) == []
    assert auftraege.sprechname("stand-hauptrepo") == "stand hauptrepo"
    print("zuordnen: ok")
```

Und `alle()` erweitern:

```python
def alle():
    test_laden()
    test_bestand()
    test_zuordnen()
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `.venv/bin/python -m sprachdienst.auftraege_test`
Expected: `laden: ok`, dann `AttributeError: module 'sprachdienst.auftraege' has no attribute 'Bestand'`

- [ ] **Step 3: Implementieren**

In `sprachdienst/auftraege.py` die Importe ergänzen (`import difflib`, `import time`) und anhängen:

```python
# ------------------------------------------------------------------ Bestand
ABBRUECHE_MAX = 3        # so viele Abbrueche in Folge, dann Ruhe
RUHE_S = 600
GESEHEN_MAX = 2000       # Adressen je Themenauftrag


class Bestand:
    """Was ansteht und was gelaufen ist. Ueberdauert einen Neustart.

    Ein Eintrag bleibt in der Schlange, bis `fertig()` ihn herausnimmt. Ein
    Lauf, der beim Neustart des Dienstes gerade rechnete, steht deshalb noch
    darin und laeuft danach einfach wieder an.
    """

    def __init__(self, pfad: Path | None = None):
        self.pfad = pfad or ZUSTAND
        self.schlange: list[dict] = []
        self.pausiert: list[str] = []
        self.laeufe: dict[str, dict] = {}
        self.befunde: list[dict] = []
        self.gesehen: dict[str, list[str]] = {}
        self.abgleich: float = 0.0
        self._laden()

    def _laden(self):
        try:
            d = json.loads(self.pfad.read_text(encoding="utf-8"))
            schlange, pausiert = d.get("schlange", []), d.get("pausiert", [])
            laeufe, befunde = d.get("laeufe", {}), d.get("befunde", [])
            gesehen, abgleich = d.get("gesehen", {}), d.get("abgleich", 0.0)
            if not (isinstance(schlange, list) and isinstance(pausiert, list)
                    and isinstance(laeufe, dict) and isinstance(befunde, list)
                    and isinstance(gesehen, dict)
                    and isinstance(abgleich, (int, float))):
                raise ValueError("unerwarteter Aufbau")
        except FileNotFoundError:
            return
        except (OSError, ValueError, AttributeError) as e:
            # Leer anfangen statt scheitern: ein Dienst, der wegen einer
            # kaputten Zustandsdatei nicht startet, ist schlimmer als eine
            # verlorene Warteschlange.
            log.warning("Auftragszustand unlesbar (%s) — beginne leer", e)
            return
        self.schlange, self.pausiert = schlange, pausiert
        self.laeufe, self.befunde = laeufe, befunde
        self.gesehen, self.abgleich = gesehen, float(abgleich)

    def _sichern(self):
        try:
            self.pfad.parent.mkdir(parents=True, exist_ok=True)
            # Erst daneben schreiben, dann tauschen: ein Absturz mitten im
            # Schreiben laesst sonst eine halbe Datei zurueck.
            neben = self.pfad.with_suffix(".neu")
            neben.write_text(json.dumps({
                "schlange": self.schlange, "pausiert": self.pausiert,
                "laeufe": self.laeufe, "befunde": self.befunde,
                "gesehen": self.gesehen, "abgleich": self.abgleich,
            }, ensure_ascii=False, indent=1), encoding="utf-8")
            neben.replace(self.pfad)
        except OSError as e:
            log.warning("Auftragszustand nicht gesichert: %s", e)

    # --- Warteschlange -----------------------------------------------------
    def _eintrag(self, name: str) -> dict | None:
        return next((e for e in self.schlange if e["name"] == name), None)

    def einreihen(self, name: str, ausloeser: dict) -> bool:
        e = self._eintrag(name)
        if e is None:
            self.schlange.append({"name": name, "ausloeser": dict(ausloeser),
                                  "abbrueche": 0, "nicht_vor": 0.0})
            self._sichern()
            return True
        # Derselbe Auftrag wartet schon: zusammenlegen statt doppelt laufen.
        # Bei Commits waechst der Bereich -- der Anfang bleibt, das Ende rueckt.
        if "nachher" in ausloeser and "nachher" in e["ausloeser"]:
            e["ausloeser"]["nachher"] = ausloeser["nachher"]
        else:
            e["ausloeser"] = dict(ausloeser)
        self._sichern()
        return False

    def naechster(self, aktive: set[str], jetzt: float | None = None) -> dict | None:
        jetzt = time.time() if jetzt is None else jetzt
        for e in self.schlange:
            if (e["name"] in aktive and e["name"] not in self.pausiert
                    and e.get("nicht_vor", 0.0) <= jetzt):
                return e
        return None

    def fertig(self, name: str):
        self.schlange = [e for e in self.schlange if e["name"] != name]
        self._sichern()

    def abgebrochen(self, name: str, jetzt: float | None = None):
        """Der Lauf musste der Stimme weichen. Er bleibt in der Schlange; wer
        dreimal in Folge weichen musste, wartet zehn Minuten -- sonst startet
        er in einem lebhaften Gespraech bei jeder Atempause neu."""
        e = self._eintrag(name)
        if e is None:
            return
        e["abbrueche"] = e.get("abbrueche", 0) + 1
        if e["abbrueche"] >= ABBRUECHE_MAX:
            e["abbrueche"] = 0
            e["nicht_vor"] = (time.time() if jetzt is None else jetzt) + RUHE_S
        self._sichern()

    def vertagen(self, name: str, sekunden: float, jetzt: float | None = None):
        e = self._eintrag(name)
        if e is not None:
            e["nicht_vor"] = (time.time() if jetzt is None else jetzt) + sekunden
            self._sichern()

    def pause(self, name: str, an: bool):
        if an and name not in self.pausiert:
            self.pausiert.append(name)
        elif not an and name in self.pausiert:
            self.pausiert.remove(name)
        self._sichern()

    # --- Laeufe und Befunde ------------------------------------------------
    def lauf_merken(self, name: str, urteil: str, kurz: str = "",
                    jetzt: float | None = None):
        self.laeufe[name] = {"zeit": time.time() if jetzt is None else jetzt,
                             "urteil": urteil, "kurz": kurz}
        self._sichern()

    def befund_merken(self, auftrag: str, datei: str, kurz: str,
                      jetzt: float | None = None):
        self.befunde.append({"auftrag": auftrag, "datei": datei, "kurz": kurz,
                             "zeit": time.time() if jetzt is None else jetzt,
                             "gelesen": False})
        self._sichern()

    def ungelesene(self) -> list[dict]:
        """Aelteste zuerst. Ein in der Ablage geloeschter Befund zaehlt nicht."""
        return [b for b in self.befunde
                if not b.get("gelesen") and Path(b["datei"]).exists()]

    def alle_gelesen(self):
        # Geloeschte gleich mit aufraeumen, sonst waechst die Liste fuer immer.
        self.befunde = [b for b in self.befunde if Path(b["datei"]).exists()]
        for b in self.befunde:
            b["gelesen"] = True
        self._sichern()

    # --- Themen ------------------------------------------------------------
    def gesehen_fuer(self, name: str) -> set[str]:
        return set(self.gesehen.get(name, []))

    def gesehen_dazu(self, name: str, adressen: list[str]):
        alt = self.gesehen.get(name, [])
        neu = alt + [a for a in adressen if a and a not in alt]
        self.gesehen[name] = neu[-GESEHEN_MAX:]
        self._sichern()

    def thema_faellig(self, a: Auftrag, jetzt: float | None = None) -> bool:
        if self._eintrag(a.name) is not None:
            return False
        jetzt = time.time() if jetzt is None else jetzt
        letzter = self.laeufe.get(a.name, {}).get("zeit", 0.0)
        return jetzt - letzter >= a.alle_stunden * 3600

    def abgleich_merken(self, jetzt: float):
        self.abgleich = jetzt
        self._sichern()


def protokollieren(eintrag: dict, pfad: Path | None = None):
    """Eine Zeile je Lauf -- auch ohne Fund, auch gescheitert. Wer wissen will,
    warum kein Befund kam, findet es hier und nicht im Dienstprotokoll."""
    pfad = pfad or PROTOKOLL
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        with open(pfad, "a", encoding="utf-8") as f:
            f.write(json.dumps({"zeit": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                **eintrag}, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("Pruefprotokoll nicht geschrieben: %s", e)


# ------------------------------------------------------------------ Namen
_FUELLER = re.compile(r"\b(bitte|mal|den|die|das|dem|der|auftrag|auftraege|"
                      r"aufträge|fortsetzen|weiter|wieder|aufnehmen|jetzt)\b")


def _norm(s: str) -> str:
    s = re.sub(r"[-_]+", " ", s.lower())
    s = re.sub(r"[^a-z0-9äöüß ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def sprechname(name: str) -> str:
    return name.replace("-", " ")


def zuordnen(gesprochen: str, namen: list[str]) -> list[str]:
    """Welche Auftraege sind gemeint? Leer, einer oder mehrere.

    Der Aufrufer fragt nach, wenn es nicht genau einer ist. Geraten wird hier
    nicht -- ein falsch zugeordneter Name pausiert den falschen Auftrag.
    """
    g = re.sub(r"\s+", " ", _FUELLER.sub(" ", _norm(gesprochen))).strip()
    if not g:
        return []
    # Leerzeichen zaehlen nicht: die Spracherkennung zieht "hauptrepo" zu
    # "Haupt Repo" auseinander.
    eng = g.replace(" ", "")
    kandidaten = [(n, _norm(n), _norm(n).replace(" ", "")) for n in namen]
    genau = [n for n, _, e in kandidaten if e == eng]
    if genau:
        return genau
    teil = [n for n, _, e in kandidaten if eng in e or e in eng]
    if teil:
        return teil
    return [n for n, _, e in kandidaten
            if difflib.SequenceMatcher(None, eng, e).ratio() >= 0.75]
```

- [ ] **Step 4: Test laufen lassen**

Run: `.venv/bin/python -m sprachdienst.auftraege_test`
Expected: `laden: ok`, `bestand: ok`, `zuordnen: ok`

Scheitert `z("tema wetbewerb", …)`, die Schwelle `0.75` **nicht** senken, sondern prüfen, ob `_FUELLER` ein Wort der Probe verschluckt.

- [ ] **Step 5: Commit**

```bash
git add sprachdienst/auftraege.py sprachdienst/auftraege_test.py
git commit -m "Daueraufträge: Warteschlange, Zustand, Prüfprotokoll, Namenszuordnung

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Stoff sammeln und begrenzen

**Files:**
- Create: `sprachdienst/auftrag_lauf.py`
- Modify: `sprachdienst/auftraege_test.py`

**Interfaces:**
- Consumes: `auftraege.Auftrag` aus Task 1. Eine Suchfunktion `suche(frage: str, anzahl: int) -> list`, deren Treffer die Attribute `quelle`, `titel`, `ueberschrift`, `text`, `herkunft` tragen — im Dienst ist das `wissen.index.suchen`, im Test eine Attrappe.
- Produces:
  - `auftrag_lauf.Stueck` — Dataclass `kennung: str`, `herkunft: str`, `text: str`
  - `auftrag_lauf.stoff_stand(auftrag, ausloeser: dict, repo: Path, suche) -> list[Stueck]`
  - `auftrag_lauf.stoff_protokoll(auftrag, ausloeser: dict, suche) -> list[Stueck]`
  - `auftrag_lauf.stoff_thema(auftrag, treffer: list[dict], gesehen: set[str]) -> list[Stueck]` — `treffer` mit `titel`, `text`, `url`
  - `auftrag_lauf.begrenzen(stuecke: list[Stueck], max_zeichen: int = 24000) -> tuple[list[Stueck], list[str]]` — vergibt die Kennungen `S1`, `S2`, …; zweiter Wert sind Hinweise auf Gekürztes
  - `auftrag_lauf.MAX_ZEICHEN = 24000`

Die `stoff_*`-Funktionen lassen `kennung` leer; erst `begrenzen()` vergibt sie.

- [ ] **Step 1: Test schreiben**

In `sprachdienst/auftraege_test.py` den Import erweitern (`from . import auftraege, auftrag_lauf`) und vor `def alle():` einfügen:

```python
_GIT_UMGEBUNG = {**os.environ, "GIT_AUTHOR_NAME": "Probe", "GIT_AUTHOR_EMAIL": "p@example.org",
                 "GIT_COMMITTER_NAME": "Probe", "GIT_COMMITTER_EMAIL": "p@example.org"}


def _git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=_GIT_UMGEBUNG).stdout.strip()


def _proberepo(wurzel: Path) -> tuple[str, str, str]:
    """Drei Commits. Gibt ihre Kurzkennungen zurueck."""
    wurzel.mkdir(parents=True)
    _git(wurzel, "init", "-q", "-b", "main")
    (wurzel / "liesmich.md").write_text("# Probe\n\nErste Fassung.\n")
    _git(wurzel, "add", "-A"); _git(wurzel, "commit", "-q", "-m", "Anfang")
    eins = _git(wurzel, "rev-parse", "--short", "HEAD")
    (wurzel / "liesmich.md").write_text("# Probe\n\nZweite Fassung mit Grenzwert 42.\n")
    (wurzel / "werte.txt").write_text("schwelle = 42\n")
    _git(wurzel, "add", "-A")
    _git(wurzel, "commit", "-q", "-m", "Grenzwert auf 42 gesetzt\n\nVorher stand 40 im Text.")
    zwei = _git(wurzel, "rev-parse", "--short", "HEAD")
    (wurzel / "gross.txt").write_text("zeile mit inhalt\n" * 20000)      # 340 kB
    _git(wurzel, "add", "-A"); _git(wurzel, "commit", "-q", "-m", "Grosse Datei")
    drei = _git(wurzel, "rev-parse", "--short", "HEAD")
    return eins, zwei, drei


def _suche_attrappe(frage, anzahl):
    alle = [SimpleNamespace(quelle="beispielrepo", titel="spezifikation.md",
                            ueberschrift="Grenzwerte", herkunft="spezifikation.md",
                            text="Der Grenzwert der Schwelle betraegt 40."),
            SimpleNamespace(quelle="protokolle", titel="protokoll.md",
                            ueberschrift="Zusagen", herkunft="/alt/protokoll.md",
                            text="Zugesagt wurde die Lieferung bis Freitag.")]
    return alle[:anzahl]


def test_stoff():
    L = auftrag_lauf
    stand = auftraege.Auftrag("stand-beispiel", "stand", "Prüfe.",
                              quelle="beispielrepo", dazu=("Grenzwert",))
    with tempfile.TemporaryDirectory() as t:
        repo = Path(t) / "repo"
        eins, zwei, drei = _proberepo(repo)

        # Bereich: Commit-Text, je geaenderte Datei ein Stueck, Vergleichsstoff.
        s = L.stoff_stand(stand, {"vorher": eins, "nachher": zwei}, repo, _suche_attrappe)
        assert all(x.kennung == "" for x in s)
        assert "Grenzwert auf 42 gesetzt" in s[0].text and "Vorher stand 40" in s[0].text
        assert "Commits" in s[0].herkunft and eins in s[0].herkunft
        diffs = [x for x in s if "Änderung an" in x.herkunft]
        assert sorted(x.herkunft.split("Änderung an ")[1] for x in diffs) == [
            "liesmich.md", "werte.txt"]
        assert any("+schwelle = 42" in x.text for x in diffs)
        dazu = [x for x in s if "Grenzwerte" in x.herkunft]
        assert len(dazu) == 1 and "betraegt 40" in dazu[0].text

        # Ohne "vorher": nur der letzte Commit.
        s = L.stoff_stand(stand, {"vorher": "", "nachher": zwei}, repo, _suche_attrappe)
        assert "Grenzwert auf 42" in s[0].text and "Anfang" not in s[0].text

        # Riesige Datei: das einzelne Stueck ist gekappt und sagt es.
        s = L.stoff_stand(stand, {"vorher": zwei, "nachher": drei}, repo, _suche_attrappe)
        gross = next(x for x in s if x.herkunft.endswith("gross.txt"))
        assert len(gross.text) < 3200 and gross.text.endswith("[gekürzt]")

        # Unbekannter Commit: kein Stoff, keine Ausnahme.
        assert L.stoff_stand(stand, {"vorher": "", "nachher": "0000000"},
                             repo, lambda f, n: []) == []

        # Protokoll: der Text zuerst, dazu Aehnliches -- aber nicht es selbst.
        prot = Path(t) / "neu" / "protokoll.md"
        prot.parent.mkdir()
        prot.write_text("# Protokoll\n\n## Zusagen\n\nDie Lieferung kommt erst Montag.\n")
        pa = auftraege.Auftrag("protokoll-nachhalten", "protokoll", "Zusagen.")
        selbst = lambda f, n: [SimpleNamespace(
            quelle="protokolle", titel="protokoll.md", ueberschrift="Zusagen",
            herkunft=str(prot), text="Die Lieferung kommt erst Montag.")] + _suche_attrappe(f, n)
        s = L.stoff_protokoll(pa, {"datei": str(prot)}, selbst)
        assert "erst Montag" in s[0].text and s[0].herkunft.startswith("Protokoll")
        assert len(s) >= 2 and all(str(prot) not in x.herkunft for x in s[1:])
        assert any("bis Freitag" in x.text for x in s[1:])
        assert L.stoff_protokoll(pa, {"datei": str(Path(t) / "fehlt.md")}, selbst) == []

    # Thema: nur was neu ist, jede Adresse einmal.
    th = auftraege.Auftrag("thema-beispiel", "thema", "Neues.", suchen=("x",))
    treffer = [{"titel": "Alt", "text": "kennen wir", "url": "https://example.org/alt"},
               {"titel": "Neu", "text": "frisch", "url": "https://example.org/neu"},
               {"titel": "Neu doppelt", "text": "frisch", "url": "https://example.org/neu"},
               {"titel": "Ohne Adresse", "text": "x", "url": ""}]
    s = L.stoff_thema(th, treffer, {"https://example.org/alt"})
    assert [x.herkunft for x in s] == ["https://example.org/neu"]
    assert s[0].text == "Neu\nfrisch"
    assert L.stoff_thema(th, treffer[:1], {"https://example.org/alt"}) == []

    # Begrenzen: Kennungen, Obergrenze, Hinweise.
    viele = [L.Stueck("", f"quelle {i}", "x" * 5000) for i in range(8)]
    behalten, hinweise = L.begrenzen(viele)
    assert [x.kennung for x in behalten] == [f"S{i + 1}" for i in range(len(behalten))]
    assert sum(len(x.text) for x in behalten) <= L.MAX_ZEICHEN
    assert len(behalten) == 5 and behalten[-1].text.endswith("[gekürzt]")
    assert any("3 Stück(e) weggelassen" in h and "quelle 7" in h for h in hinweise)
    assert any("S5" in h and "gekürzt" in h for h in hinweise)
    klein, hinweise = L.begrenzen([L.Stueck("", "a", "kurz")])
    assert klein[0].kennung == "S1" and hinweise == []
    assert L.begrenzen([]) == ([], [])
    print("stoff: ok")
```

`alle()` um `test_stoff()` erweitern.

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `.venv/bin/python -m sprachdienst.auftraege_test`
Expected: `ImportError: cannot import name 'auftrag_lauf'`

- [ ] **Step 3: Implementieren**

`sprachdienst/auftrag_lauf.py`:

```python
"""Ein Lauf eines Dauerauftrags: Stoff sammeln, bewerten, Belege pruefen.

**Der Stoff wird hier gesammelt, nicht vom Modell.** Ein Versuch mit einem
fertigen Agenten-Laufwerk am 04.10.2026 schickte bei jedem Schritt rund 53.000
Token Repo-Abbild mit; das Modell brauchte 99 s nur zum Einlesen und lief ins
Zeitlimit, waehrend der Sprachpfad auf denselben Platz wartete. Deshalb: fest
verdrahtet sammeln, hart begrenzen, EIN Aufruf.

Das Modul kennt den Sprachdienst nicht. Es bekommt einen Auftrag, einen
Ausloeser und eine Funktion, die das Modell fragt -- und laesst sich so ohne
Dienst und ohne Modell pruefen.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("kihiwi.auftrag")

MAX_ZEICHEN = 24000      # rund 6.000 Token Stoff je Lauf
COMMITS_MAX = 2000
DATEI_MAX = 3000         # je geaenderter Datei
DAZU_MAX = 2500          # je Vergleichsstueck
PROTOKOLL_MAX = 12000
REST_MIN = 500           # kuerzer lohnt ein angeschnittenes Stueck nicht
GEKUERZT = "\n[gekürzt]"


@dataclass
class Stueck:
    kennung: str         # S1, S2, ... -- vergibt begrenzen()
    herkunft: str
    text: str


def _kappen(text: str, grenze: int) -> str:
    return text if len(text) <= grenze else text[:grenze] + GEKUERZT


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        log.warning("git %s: %s", args[0], r.stderr.strip()[:160])
        return ""
    return r.stdout


def _dazu(begriffe, suche, ausser: set[str], je: int = 2) -> list[Stueck]:
    """Vergleichsstoff aus dem Wissensindex. Derselbe Abschnitt nur einmal."""
    aus, schon = [], set()
    for b in begriffe:
        for t in suche(b, je):
            if t.herkunft in ausser or t.text in schon:
                continue
            schon.add(t.text)
            aus.append(Stueck("", f"{t.quelle}: {t.titel} — {t.ueberschrift}",
                              _kappen(t.text, DAZU_MAX)))
    return aus


def stoff_stand(auftrag, ausloeser: dict, repo: Path, suche) -> list[Stueck]:
    """Commit-Texte, je geaenderte Datei der Diff, dazu Vergleichsstoff."""
    vorher, nachher = ausloeser.get("vorher") or "", ausloeser["nachher"]
    fmt = "--format=%h %an %ad%n%s%n%b"
    if vorher:
        bereich = f"{vorher}..{nachher}"
        texte = _git(repo, "log", fmt, "--date=short", bereich)
        diff = _git(repo, "diff", "--unified=2", vorher, nachher)
    else:
        bereich = nachher
        texte = _git(repo, "log", "-1", fmt, "--date=short", nachher)
        diff = _git(repo, "show", "--format=", "--unified=2", nachher)
    if not texte.strip():
        return []
    stuecke = [Stueck("", f"{auftrag.quelle}: Commits {bereich}",
                      _kappen(texte.strip(), COMMITS_MAX))]
    for teil in re.split(r"(?m)^(?=diff --git )", diff):
        m = re.match(r"diff --git a/(.+?) b/", teil)
        if m:
            stuecke.append(Stueck("", f"{auftrag.quelle}: Änderung an {m.group(1)}",
                                  _kappen(teil.strip(), DATEI_MAX)))
    return stuecke + _dazu(auftrag.dazu, suche, set())


def stoff_protokoll(auftrag, ausloeser: dict, suche) -> list[Stueck]:
    """Das neue Protokoll, dazu Aehnliches aus frueheren und aus den Unterlagen."""
    datei = Path(ausloeser["datei"])
    try:
        text = datei.read_text(encoding="utf-8").strip()
    except OSError:
        return []
    if not text:
        return []
    # Gesucht wird mit den Ueberschriften: sie nennen die Themen, und vier
    # Anfragen reichen, um das Naheliegende zu finden.
    fragen = [m.group(1) for m in re.finditer(r"(?m)^#{1,3} +(.+)$", text)][:4]
    return ([Stueck("", f"Protokoll {datei.parent.name}", _kappen(text, PROTOKOLL_MAX))]
            + _dazu(fragen or [text[:200]], suche, {str(datei)}))


def stoff_thema(auftrag, treffer: list[dict], gesehen: set[str]) -> list[Stueck]:
    """Suchtreffer, die dieser Auftrag noch nicht gesehen hat."""
    aus, schon = [], set(gesehen)
    for t in treffer:
        url = t.get("url") or ""
        if not url or url in schon:
            continue
        schon.add(url)
        aus.append(Stueck("", url, f"{t.get('titel', '')}\n{t.get('text', '')}".strip()))
    return aus


def begrenzen(stuecke: list[Stueck], max_zeichen: int = MAX_ZEICHEN
              ) -> tuple[list[Stueck], list[str]]:
    """Kennungen vergeben und auf die Obergrenze bringen.

    Die Reihenfolge ist die Rangfolge: was vorn steht, bleibt. Was nicht mehr
    passt, wird im Hinweis beim Namen genannt -- ein Befund, der verschweigt,
    was er nicht gesehen hat, liest sich wie ein vollstaendiger.
    """
    behalten, hinweise, rest = [], [], max_zeichen
    weg = []
    for s in stuecke:
        kennung = f"S{len(behalten) + 1}"
        if len(s.text) <= rest:
            behalten.append(Stueck(kennung, s.herkunft, s.text))
            rest -= len(s.text)
        elif rest >= REST_MIN and not weg:
            platz = rest - len(GEKUERZT)
            behalten.append(Stueck(kennung, s.herkunft, s.text[:platz] + GEKUERZT))
            hinweise.append(f"{kennung} ({s.herkunft}) gekürzt")
            rest = 0
        else:
            weg.append(s.herkunft)
    if weg:
        hinweise.append(f"{len(weg)} Stück(e) weggelassen: " + "; ".join(weg))
    return behalten, hinweise
```

- [ ] **Step 4: Test laufen lassen**

Run: `.venv/bin/python -m sprachdienst.auftraege_test`
Expected: vier Zeilen, zuletzt `stoff: ok`

- [ ] **Step 5: Commit**

```bash
git add sprachdienst/auftrag_lauf.py sprachdienst/auftraege_test.py
git commit -m "Daueraufträge: Stoff sammeln und begrenzen

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Bewerten, Belege prüfen, Befund schreiben

**Files:**
- Modify: `sprachdienst/auftrag_lauf.py` (anhängen)
- Modify: `sprachdienst/auftraege_test.py`

**Interfaces:**
- Consumes: `Stueck`, `begrenzen()` aus Task 3; `auftraege.Auftrag` aus Task 1
- Produces:
  - `auftrag_lauf.Ergebnis` — Dataclass `urteil: str` (`"fund"|"nichts"|"nichts zu prüfen"|"gescheitert"`), `kurz: str`, `belegt: list[dict]`, `unbelegt: list[dict]`, `dauer: float`, `hinweise: list[str]`. Punkte sind `{"aussage", "beleg", "stueck", "herkunft"}`.
  - `auftrag_lauf.auswerten(roh: str, stuecke: list[Stueck]) -> dict` — `{"kurz", "belegt", "unbelegt"}`; wirft `ValueError` bei unbrauchbarer Antwort
  - `async auftrag_lauf.lauf(auftrag, stuecke: list[Stueck], hinweise: list[str], frage_modell) -> Ergebnis` — `frage_modell` ist `async (system: str, frage: str) -> str`; ein `asyncio.CancelledError` daraus wird durchgereicht
  - `auftrag_lauf.ausloeser_text(auftrag, ausloeser: dict) -> str`
  - `auftrag_lauf.befund_schreiben(auftrag, ausloeser: dict, ergebnis: Ergebnis, modell: str, ordner: Path, jetzt: float | None = None) -> Path`

- [ ] **Step 1: Test schreiben**

Vor `def alle():` einfügen:

```python
def _antworten(*texte):
    """Modell-Attrappe: gibt die Texte der Reihe nach zurueck."""
    rest = list(texte)
    gefragt = []

    async def frage(system, frage_):
        gefragt.append((system, frage_))
        return rest.pop(0)
    frage.gefragt = gefragt
    return frage


def test_bewerten():
    L = auftrag_lauf
    stuecke = [L.Stueck("S1", "beispielrepo: Commits a1..b2", "Grenzwert auf 42 gesetzt"),
               L.Stueck("S2", "beispielrepo: spezifikation.md — Grenzwerte",
                        "Der Grenzwert der  Schwelle\nbetraegt 40.")]

    def punkt(beleg, stueck="S2", aussage="Widerspruch zum Grenzwert."):
        return {"aussage": aussage, "beleg": beleg, "stueck": stueck}

    def antwort(*punkte, kurz="Der neue Stand widerspricht der Spezifikation."):
        return json.dumps({"auffaellig": True, "kurz": kurz, "punkte": list(punkte)},
                          ensure_ascii=False)

    # Woertlich; mit anderem Leerraum und anderer Grossschreibung; erfunden;
    # richtiges Zitat, falsches Stueck; unbekanntes Stueck; zu kurz.
    a = L.auswerten(antwort(
        punkt("Grenzwert auf 42 gesetzt", "S1"),
        punkt("der grenzwert der schwelle betraegt 40."),
        punkt("Der Grenzwert betraegt 50."),
        punkt("Grenzwert auf 42 gesetzt", "S2"),
        punkt("Grenzwert auf 42 gesetzt", "S9"),
        punkt("40")), stuecke)
    assert [p["stueck"] for p in a["belegt"]] == ["S1", "S2"]
    assert a["belegt"][1]["herkunft"] == "beispielrepo: spezifikation.md — Grenzwerte"
    assert len(a["unbelegt"]) == 4
    assert a["kurz"].startswith("Der neue Stand")

    # Code-Zaun und Vortext stoeren nicht.
    zaun = "`" * 3
    roh = (f"Hier ist das Ergebnis:\n{zaun}json\n" + antwort(punkt("betraegt 40."))
           + f"\n{zaun}\nFertig.")
    assert len(L.auswerten(roh, stuecke)["belegt"]) == 1

    # Unbrauchbar: ValueError.
    for kaputt in ["", "kein json", "{", '{"punkte": "x"}', '["a"]',
                   '{"auffaellig": true, "kurz": 5, "punkte": []}']:
        try:
            L.auswerten(kaputt, stuecke)
        except ValueError:
            continue
        raise AssertionError(f"haette scheitern muessen: {kaputt!r}")

    a_ = auftraege.Auftrag("stand-beispiel", "stand", "Prüfe den Grenzwert.",
                           quelle="beispielrepo")

    # Fund.
    f = _antworten(antwort(punkt("betraegt 40.")))
    e = asyncio.run(L.lauf(a_, stuecke, ["S2 gekürzt"], f))
    assert e.urteil == "fund" and len(e.belegt) == 1 and e.hinweise == ["S2 gekürzt"]
    system, frage = f.gefragt[0]
    assert "Prüfe den Grenzwert." in frage and "### S1" in frage and "### S2" in frage
    assert "JSON" in system

    # Das Modell meldet etwas, belegt aber nichts: KEIN Fund.
    e = asyncio.run(L.lauf(a_, stuecke, [], _antworten(antwort(punkt("frei erfunden")))))
    assert e.urteil == "nichts" and e.belegt == [] and len(e.unbelegt) == 1

    # Nichts auffaellig.
    e = asyncio.run(L.lauf(a_, stuecke, [], _antworten(
        '{"auffaellig": false, "kurz": "Nichts.", "punkte": []}')))
    assert e.urteil == "nichts"

    # Erst Unsinn, dann brauchbar: ein zweiter Versuch.
    f = _antworten("Entschuldigung, ich", antwort(punkt("betraegt 40.")))
    assert asyncio.run(L.lauf(a_, stuecke, [], f)).urteil == "fund"
    assert len(f.gefragt) == 2
    # Zweimal Unsinn: gescheitert, kein dritter Versuch.
    f = _antworten("x", "y", "z")
    assert asyncio.run(L.lauf(a_, stuecke, [], f)).urteil == "gescheitert"
    assert len(f.gefragt) == 2
    # Kein Stoff: kein Modellaufruf.
    f = _antworten()
    assert asyncio.run(L.lauf(a_, [], [], f)).urteil == "nichts zu prüfen"
    assert f.gefragt == []

    # Ein Abbruch wird durchgereicht, nicht als "gescheitert" verbucht.
    async def abgebrochen(system, frage_):
        raise asyncio.CancelledError
    try:
        asyncio.run(L.lauf(a_, stuecke, [], abgebrochen))
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("CancelledError verschluckt")

    # Befund.
    e = L.Ergebnis("fund", "Der neue Stand widerspricht der Spezifikation.",
                   [{"aussage": "Widerspruch.", "beleg": "betraegt 40.", "stueck": "S2",
                     "herkunft": "beispielrepo: spezifikation.md — Grenzwerte"}],
                   [{"aussage": "Vermutung.", "beleg": "frei erfunden", "stueck": "S1",
                     "herkunft": ""}], 12.4, ["S2 gekürzt"])
    with tempfile.TemporaryDirectory() as t:
        p = L.befund_schreiben(a_, {"vorher": "a1", "nachher": "b2"}, e,
                               "probemodell", Path(t), jetzt=1791100000.0)
        assert p.parent == Path(t) / "stand-beispiel" and p.suffix == ".md"
        text = p.read_text(encoding="utf-8")
        assert text.startswith("# Befund: stand beispiel — Der neue Stand")
        for erwartet in ("Abgeleitet", "Commits a1..b2", "probemodell", "12 s",
                         "S2 gekürzt", "## Belegte Punkte", "> betraegt 40.",
                         "spezifikation.md — Grenzwerte", "## Unbelegt",
                         "frei erfunden"):
            assert erwartet in text, erwartet
        assert text.index("## Belegte Punkte") < text.index("## Unbelegt")
        # Zwei Befunde in derselben Sekunde ueberschreiben einander nicht.
        q = L.befund_schreiben(a_, {"vorher": "", "nachher": "b2"}, e,
                               "probemodell", Path(t), jetzt=1791100000.0)
        assert q != p and q.exists() and p.exists()
    assert L.ausloeser_text(a_, {"vorher": "", "nachher": "b2"}) == "Commit b2"
    print("bewerten: ok")
```

`alle()` um `test_bewerten()` erweitern.

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `.venv/bin/python -m sprachdienst.auftraege_test`
Expected: nach `stoff: ok` ein `AttributeError: module 'sprachdienst.auftrag_lauf' has no attribute 'auswerten'`

- [ ] **Step 3: Implementieren**

In `sprachdienst/auftrag_lauf.py` die Importe ergänzen (`import json`, `import time`, `from dataclasses import dataclass, field`) und anhängen:

```python
# ------------------------------------------------------------------ Bewerten
SYSTEM = """Du prüfst Unterlagen für ein Forschungslabor. Du bekommst ein Ziel \
und nummerierte Stücke (S1, S2, …). Antworte NUR mit JSON in genau dieser Form:

{"auffaellig": true,
 "kurz": "Ein deutscher Satz zum Vorlesen.",
 "punkte": [{"aussage": "Was auffällt.", "beleg": "wörtliches Zitat", "stueck": "S3"}]}

Regeln:
- Jeder Punkt braucht ein wörtliches Zitat aus genau einem Stück, unverändert \
kopiert, höchstens 200 Zeichen.
- Nichts erfinden und nichts aus eigenem Wissen ergänzen. Zählt nur, was in den \
Stücken steht.
- Gibt es zum Ziel nichts zu melden: "auffaellig": false und "punkte": [].
- "kurz" nennt keine Dateinamen und keine Kennungen wie S3.
- Höchstens sechs Punkte, die wichtigsten zuerst."""

BELEG_MIN = 8            # kuerzer ist kein Beleg, sondern ein Wort


@dataclass
class Ergebnis:
    urteil: str                        # fund | nichts | nichts zu prüfen | gescheitert
    kurz: str = ""
    belegt: list = field(default_factory=list)
    unbelegt: list = field(default_factory=list)
    dauer: float = 0.0
    hinweise: list = field(default_factory=list)


def _gleich(s: str) -> str:
    """Leerraum und Gross-/Kleinschreibung angleichen -- mehr nicht. Ein Zitat,
    das nur sinngemaess stimmt, ist keins."""
    return re.sub(r"\s+", " ", s).strip().lower()


def auswerten(roh: str, stuecke: list[Stueck]) -> dict:
    """Die Antwort des Modells lesen und jeden Beleg nachpruefen.

    Belegt ist ein Punkt nur, wenn sein Zitat woertlich im GENANNTEN Stueck
    steht. Das Modell entscheidet damit nicht allein, was als Fund gilt.
    """
    a, z = roh.find("{"), roh.rfind("}")
    if a < 0 or z <= a:
        raise ValueError("kein JSON in der Antwort")
    d = json.loads(roh[a:z + 1])
    punkte = d.get("punkte") if isinstance(d, dict) else None
    kurz = d.get("kurz", "") if isinstance(d, dict) else None
    if not isinstance(punkte, list) or not isinstance(kurz, str):
        raise ValueError("JSON hat nicht die verlangte Form")
    nach_kennung = {s.kennung: s for s in stuecke}
    belegt, unbelegt = [], []
    for p in punkte:
        if not isinstance(p, dict):
            continue
        aussage, beleg = str(p.get("aussage", "")).strip(), str(p.get("beleg", "")).strip()
        kennung = str(p.get("stueck", "")).strip()
        s = nach_kennung.get(kennung)
        eintrag = {"aussage": aussage, "beleg": beleg, "stueck": kennung,
                   "herkunft": s.herkunft if s else ""}
        if (s and aussage and len(_gleich(beleg)) >= BELEG_MIN
                and _gleich(beleg) in _gleich(s.text)):
            belegt.append(eintrag)
        else:
            unbelegt.append(eintrag)
    return {"kurz": kurz.strip(), "belegt": belegt, "unbelegt": unbelegt}


def _frage(auftrag, stuecke: list[Stueck]) -> str:
    teile = [f"Ziel: {auftrag.ziel}", ""]
    for s in stuecke:
        teile += [f"### {s.kennung} — {s.herkunft}", s.text, ""]
    return "\n".join(teile)


async def lauf(auftrag, stuecke: list[Stueck], hinweise: list[str],
               frage_modell) -> Ergebnis:
    """Ein Modellaufruf, bei unbrauchbarer Antwort ein zweiter.

    Ein Abbruch (CancelledError) geht durch: der Aufrufer reiht den Lauf
    wieder ein, das ist kein Scheitern.
    """
    if not stuecke:
        return Ergebnis("nichts zu prüfen", hinweise=list(hinweise))
    t0 = time.time()
    frage = _frage(auftrag, stuecke)
    a = None
    for _ in range(2):
        roh = await frage_modell(SYSTEM, frage)
        try:
            a = auswerten(roh, stuecke)
            break
        except ValueError as e:
            log.warning("Auftrag %s: Antwort unbrauchbar (%s)", auftrag.name, e)
    dauer = time.time() - t0
    if a is None:
        return Ergebnis("gescheitert", dauer=dauer, hinweise=list(hinweise))
    return Ergebnis("fund" if a["belegt"] else "nichts", a["kurz"], a["belegt"],
                    a["unbelegt"], dauer, list(hinweise))


# ------------------------------------------------------------------ Befund
def ausloeser_text(auftrag, ausloeser: dict) -> str:
    if auftrag.art == "stand":
        vorher, nachher = ausloeser.get("vorher") or "", ausloeser.get("nachher", "")
        return f"Commits {vorher}..{nachher}" if vorher else f"Commit {nachher}"
    if auftrag.art == "protokoll":
        return f"Protokoll {Path(ausloeser.get('datei', '')).parent.name}"
    return "Suche: " + "; ".join(auftrag.suchen)


def befund_schreiben(auftrag, ausloeser: dict, ergebnis: Ergebnis, modell: str,
                     ordner: Path, jetzt: float | None = None) -> Path:
    jetzt = time.time() if jetzt is None else jetzt
    ziel = ordner / auftrag.name
    ziel.mkdir(parents=True, exist_ok=True)
    stempel = time.strftime("%Y%m%dT%H%M%S", time.localtime(jetzt))
    datei, n = ziel / f"{stempel}.md", 1
    while datei.exists():
        n += 1
        datei = ziel / f"{stempel}-{n}.md"

    def punkte(liste: list) -> list[str]:
        zeilen = []
        for i, p in enumerate(liste, 1):
            zeilen += [f"{i}. {p['aussage']}", "", f"   > {p['beleg']}", "",
                       f"   Quelle: {p['herkunft'] or 'unbekannt'} ({p['stueck']})", ""]
        return zeilen

    z = [f"# Befund: {auftrag.name.replace('-', ' ')} — {ergebnis.kurz}", "",
         "> **Abgeleitet.** Vom Modell aus den genannten Quellen gezogen.",
         "> Vor Verwendung gegen die Quellen prüfen.", "",
         f"- Auftrag: `{auftrag.name}` ({auftrag.art})",
         f"- Ziel: {auftrag.ziel}",
         f"- Auslöser: {ausloeser_text(auftrag, ausloeser)}",
         f"- Modell: {modell}",
         f"- Dauer: {ergebnis.dauer:.0f} s",
         f"- Zeit: {time.strftime('%Y-%m-%d %H:%M', time.localtime(jetzt))}"]
    z += [f"- Hinweis: {h}" for h in ergebnis.hinweise]
    z += ["", "## Belegte Punkte", ""] + punkte(ergebnis.belegt)
    if ergebnis.unbelegt:
        z += ["## Unbelegt — nicht als Fund gewertet", "",
              "Das Zitat steht so nicht im genannten Stück.", ""] + punkte(ergebnis.unbelegt)
    datei.write_text("\n".join(z).rstrip() + "\n", encoding="utf-8")
    return datei
```

- [ ] **Step 4: Test laufen lassen**

Run: `.venv/bin/python -m sprachdienst.auftraege_test`
Expected: fünf Zeilen, zuletzt `bewerten: ok`

- [ ] **Step 5: Commit**

```bash
git add sprachdienst/auftrag_lauf.py sprachdienst/auftraege_test.py
git commit -m "Daueraufträge: Bewerten mit Belegprüfung, Befund schreiben

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Commit-Bereich im Abgleichsbericht

**Files:**
- Modify: `wissen/einlesen.py` (bei `NOTIZ`, in `git()`, in `alles()`)
- Modify: `sprachdienst/auftraege.py` (anhängen)
- Modify: `sprachdienst/auftraege_test.py`

**Interfaces:**
- Consumes: `wissen.einlesen.git(q: dict, c) -> int`, `wissen.einlesen.alles(nur: str | None = None) -> list[dict]`, `wissen.einlesen.REPOS`, `wissen.index.DB`, `wissen.index.verbinden()`; `auftraege.Auftrag`
- Produces:
  - `wissen.einlesen.BEREICH: dict[str, tuple[str, str]]` — je Git-Quelle `(vorher, nachher)` des letzten Laufs; `vorher` ist leer bei frisch geklonter Quelle
  - Der Bericht von `alles()` trägt je Quelle zusätzlich `"vorher": str` und `"nachher": str` (beide leer bei Nicht-Git-Quellen und bei gescheitertem Abruf)
  - `auftraege.aus_bericht(bericht: list[dict], auftraege: list[Auftrag]) -> list[tuple[str, dict]]` — `(name, {"vorher", "nachher"})` für jeden aktiven `stand`-Auftrag, dessen Quelle einen neuen Commit bekam

- [ ] **Step 1: Test schreiben**

In `sprachdienst/auftraege_test.py` oben ergänzen: `from wissen import einlesen, index`. Vor `def alle():` einfügen:

```python
def test_bereich():
    with tempfile.TemporaryDirectory() as t:
        ursprung = Path(t) / "ursprung"
        eins, zwei, drei = _proberepo(ursprung)
        _git(ursprung, "reset", "-q", "--hard", eins)        # zurueck auf den Anfang
        alt_repos, alt_db = einlesen.REPOS, index.DB
        einlesen.REPOS, index.DB = Path(t) / "repos", Path(t) / "index.db"
        try:
            q = {"name": "probe", "art": "git", "url": f"file://{ursprung}",
                 "muster": ["*.md"]}
            c = index.verbinden()
            einlesen.BEREICH.clear()
            einlesen.git(q, c)                                # frisch geklont
            assert einlesen.BEREICH["probe"] == ("", eins), einlesen.BEREICH
            einlesen.git(q, c)                                # nichts Neues
            assert einlesen.BEREICH["probe"] == (eins, eins)
            _git(ursprung, "reset", "-q", "--hard", zwei)     # ein neuer Commit
            einlesen.git(q, c)
            assert einlesen.BEREICH["probe"] == (eins, zwei)
            # Der Bereich ist im Arbeitsklon auswertbar -- trotz --depth 1.
            assert "Grenzwert auf 42" in _git(einlesen.REPOS / "probe", "log",
                                              "--format=%s", f"{eins}..{zwei}")
            c.close()
        finally:
            einlesen.REPOS, index.DB = alt_repos, alt_db

    stand = auftraege.Auftrag("stand-probe", "stand", "z", quelle="probe")
    aus = auftraege.Auftrag("stand-aus", "stand", "z", aktiv=False, quelle="probe")
    anderes = auftraege.Auftrag("stand-anderes", "stand", "z", quelle="anderes")
    prot = auftraege.Auftrag("protokoll-x", "protokoll", "z")
    alle_ = [stand, aus, anderes, prot]
    b = auftraege.aus_bericht
    neu = {"name": "probe", "art": "git", "neu": 1, "entfernt": 0, "notiz": "",
           "vorher": "aaa", "nachher": "bbb"}
    assert b([neu], alle_) == [("stand-probe", {"vorher": "aaa", "nachher": "bbb"})]
    assert b([{**neu, "nachher": "aaa"}], alle_) == []          # unveraendert
    assert b([{**neu, "vorher": ""}], alle_) == []              # frisch geklont
    assert b([{**neu, "vorher": "", "nachher": ""}], alle_) == []   # Abruf gescheitert
    assert b([{"name": "protokolle", "art": "lokal", "neu": 1}], alle_) == []
    assert b([], alle_) == []
    print("bereich: ok")
```

`alle()` um `test_bereich()` erweitern.

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `.venv/bin/python -m sprachdienst.auftraege_test 2>&1 | tail -3`
Expected: `AttributeError: module 'wissen.einlesen' has no attribute 'BEREICH'`

- [ ] **Step 3: Implementieren**

In `wissen/einlesen.py` direkt unter der Zeile `NOTIZ: dict[str, str] = {}`:

```python
# Commit vor und nach dem letzten Abruf, je Git-Quelle. Die Dauerauftraege
# brauchen den Bereich, um zu pruefen, was neu ist; bisher stand er nur in der
# Ausgabe. "vorher" ist leer, wenn die Quelle frisch geklont wurde -- dann ist
# alles neu, und das ist kein Anlass fuer einen Lauf.
BEREICH: dict[str, tuple[str, str]] = {}
```

In `git()` den `else`-Zweig des Klonens um eine Zeile ergänzen und nach `stand = _head(ziel)` den Bereich festhalten:

```python
    else:
        vorher = ""
        NOTIZ[q["name"]] = "frisch geklont"
        r = subprocess.run(["git", "clone", "--depth", "1", "-q", q["url"], str(ziel)],
                           capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        NOTIZ[q["name"]] = "Abruf fehlgeschlagen"
        print(f"    git fehlgeschlagen: {r.stderr.strip()[:120]}"); return 0
    stand = _head(ziel)
    BEREICH[q["name"]] = (vorher, stand)
```

In `alles()` neben `NOTIZ.pop(q["name"], None)`:

```python
            BEREICH.pop(q["name"], None)
```

und beim Anhängen an den Bericht:

```python
            vorher, nachher = BEREICH.get(q["name"], ("", ""))
            bericht.append({"name": q["name"], "art": q["art"], "neu": n,
                            "entfernt": entfernt, "notiz": notiz,
                            "vorher": vorher, "nachher": nachher})
```

An `sprachdienst/auftraege.py` anhängen:

```python
# ------------------------------------------------------------------ Ausloeser
def aus_bericht(bericht: list[dict], auftraege: list[Auftrag]) -> list[tuple[str, dict]]:
    """Welche stand-Auftraege loest dieser Abgleich aus?

    Nur bei einem echten Schritt von einem Commit zum naechsten. Eine frisch
    geklonte Quelle hat kein "vorher" -- alles daran waere neu, und ein Lauf
    ueber das ganze Repo ist nicht gemeint.
    """
    aus = []
    for b in bericht:
        vorher, nachher = b.get("vorher") or "", b.get("nachher") or ""
        if not vorher or not nachher or vorher == nachher:
            continue
        for a in auftraege:
            if a.art == "stand" and a.aktiv and a.quelle == b.get("name"):
                aus.append((a.name, {"vorher": vorher, "nachher": nachher}))
    return aus
```

- [ ] **Step 4: Test laufen lassen**

Run: `.venv/bin/python -m sprachdienst.auftraege_test 2>&1 | tail -3`
Expected: zuletzt `bereich: ok`

Scheitert der Test am ersten `einlesen.git(q, c)` mit einem Schemafehler, liegt es an `index.DB`: `verbinden()` muss die Datei im Temp-Ordner anlegen, nicht den echten Index anfassen. Prüfen mit `ls -la wissen/index.db` vor und nach dem Lauf — die Änderungszeit darf sich nicht bewegen.

- [ ] **Step 5: Bestehendes nicht gebrochen**

Run: `./dienste.sh wissen status`
Expected: dieselben Zahlen wie vorher, keine Ausnahme.

- [ ] **Step 6: Commit**

```bash
git add wissen/einlesen.py sprachdienst/auftraege.py sprachdienst/auftraege_test.py
git commit -m "Abgleich nennt den Commit-Bereich je Git-Quelle

Die Daueraufträge brauchen ihn als Auslöser.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Abbrechbarer Modellaufruf

**Files:**
- Modify: `sprachdienst/llm.py` (Importe; neue Funktion nach `antwort_text`)
- Modify: `sprachdienst/auftraege_test.py`

**Interfaces:**
- Consumes: in `llm.py` vorhanden: `_modellname() -> str`, `_modell_nachschlagen() -> str | None`, `_denkschalter(rumpf: dict) -> None`, `konfig.LLM_URL` (z. B. `http://127.0.0.1:8889/v1`), `konfig.LLM_ZUSATZ` (dict)
- Produces: `async llm.frage_abbrechbar(system: str, frage: str, max_tokens: int = 1200, timeout: int = 120) -> str` — gibt den Antworttext zurück; wirft `RuntimeError` bei einer HTTP-Antwort ungleich 200; wird die wartende Aufgabe abgebrochen, schließt sie die Verbindung und reicht `asyncio.CancelledError` durch

Warum eine eigene Funktion: die vorhandenen Wege (`_einmal`, `antwort_text`) laufen über `urllib` in einem Thread. Bricht man die wartende Aufgabe ab, rechnet das Modell trotzdem zu Ende — und belegt den einen Platz, den die Stimme gerade braucht. Hier wird die Verbindung geschlossen; der Server bricht dann die Aufgabe ab.

- [ ] **Step 1: Test schreiben**

Vor `def alle():` einfügen:

```python
def test_abbrechbar():
    import http.server
    import select
    import threading
    from . import konfig, llm
    gesehen = {"abbruch": False}

    class Motor(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            rumpf = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.path == "/v1/chat/completions"
            assert rumpf["messages"][0] == {"role": "system", "content": "s"}
            frage = rumpf["messages"][1]["content"]
            if frage == "langsam":
                # Rechnet "lange". Wird die Verbindung geschlossen, ist der
                # Socket lesbar -- genau das merkt auch ein echter Server.
                lesbar, _, _ = select.select([self.connection], [], [], 5)
                gesehen["abbruch"] = bool(lesbar)
                return
            if frage == "kaputt":
                self.send_response(500); self.end_headers(); self.wfile.write(b"nein")
                return
            leib = json.dumps({"choices": [{"message": {
                "content": f"  Antwort auf {frage} "}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(leib)))
            self.end_headers(); self.wfile.write(leib)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Motor)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    alt = konfig.LLM_URL
    konfig.LLM_URL = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        assert asyncio.run(llm.frage_abbrechbar("s", "hallo")) == "Antwort auf hallo"
        try:
            asyncio.run(llm.frage_abbrechbar("s", "kaputt"))
        except RuntimeError as e:
            assert "500" in str(e)
        else:
            raise AssertionError("500 haette scheitern muessen")

        async def abbrechen():
            aufgabe = asyncio.create_task(llm.frage_abbrechbar("s", "langsam"))
            await asyncio.sleep(0.3)
            t0 = time.time()
            aufgabe.cancel()
            try:
                await aufgabe
            except asyncio.CancelledError:
                return time.time() - t0
            raise AssertionError("nicht abgebrochen")

        assert asyncio.run(abbrechen()) < 1.0
        time.sleep(0.3)
        assert gesehen["abbruch"] is True, "der Server hat den Abbruch nicht bemerkt"
    finally:
        konfig.LLM_URL = alt
        server.shutdown()
    print("abbrechbar: ok")
```

`alle()` um `test_abbrechbar()` erweitern.

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `.venv/bin/python -m sprachdienst.auftraege_test 2>&1 | tail -3`
Expected: `AttributeError: module 'sprachdienst.llm' has no attribute 'frage_abbrechbar'`

- [ ] **Step 3: Implementieren**

In `sprachdienst/llm.py` die Importzeile erweitern:

```python
import asyncio, http.client, json, logging, re, socket, threading, urllib.error, urllib.parse, urllib.request
```

und nach der Funktion `antwort_text` einfügen:

```python
async def frage_abbrechbar(system: str, frage: str, max_tokens: int = 1200,
                           timeout: int = 120) -> str:
    """Eine Frage, die sich WIRKLICH abbrechen laesst.

    Die uebrigen Wege laufen ueber urllib in einem Thread. Bricht man dort die
    wartende Aufgabe ab, rechnet der Motor trotzdem zu Ende -- und der hat je
    nach Profil genau einen Platz. Fuer Dauerauftraege ist das der Unterschied
    zwischen "die Stimme hat Vorrang" und "die Stimme wartet 40 Sekunden".

    Hier wird beim Abbruch die Verbindung geschlossen; llama-server und vLLM
    brechen die Aufgabe dann ab ("stop: cancel task" im Protokoll).
    """
    u = urllib.parse.urlsplit(konfig.LLM_URL)
    klasse = (http.client.HTTPSConnection if u.scheme == "https"
              else http.client.HTTPConnection)
    verb = klasse(u.hostname, u.port, timeout=timeout)

    def _lauf() -> str:
        for letzter in (False, True):
            rumpf = {**konfig.LLM_ZUSATZ, "model": _modellname(),
                     "max_tokens": max_tokens, "temperature": 0,
                     "messages": [{"role": "system", "content": system},
                                  {"role": "user", "content": frage}]}
            _denkschalter(rumpf)
            verb.request("POST", u.path.rstrip("/") + "/chat/completions",
                         body=json.dumps(rumpf).encode(),
                         headers={"Content-Type": "application/json"})
            a = verb.getresponse()
            leib = a.read()
            # Derselbe Heilungsweg wie in _anfragen: der Modellwechsel ist der
            # eine Fehler, der sich von hier aus beheben laesst.
            if a.status == 404 and not letzter and _modell_nachschlagen():
                continue
            if a.status != 200:
                raise RuntimeError(f"Modell antwortet {a.status}: {leib[:200]!r}")
            return (json.loads(leib)["choices"][0]["message"].get("content") or "").strip()

    try:
        return await asyncio.to_thread(_lauf)
    except asyncio.CancelledError:
        # Erst shutdown, dann close: close allein laesst einen im recv()
        # haengenden Thread auf manchen Systemen weiter warten.
        if verb.sock is not None:
            try:
                verb.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        raise
    finally:
        verb.close()
```

- [ ] **Step 4: Test laufen lassen**

Run: `.venv/bin/python -m sprachdienst.auftraege_test 2>&1 | tail -3`
Expected: zuletzt `abbrechbar: ok`

- [ ] **Step 5: Gegen das echte Modell (nur wenn es läuft)**

Run:
```bash
.venv/bin/python -c "
import asyncio
from sprachdienst import llm
print(repr(asyncio.run(llm.frage_abbrechbar('Antworte nur mit JSON.', 'Gib {\"ok\": true} zurueck.', 60))))"
```
Expected: ein Text, der `{"ok": true}` enthält. Antwortet nichts unter `konfig.LLM_URL`, kommt `ConnectionRefusedError` — dann diesen Schritt überspringen und im Abschlussbericht vermerken.

- [ ] **Step 6: Commit**

```bash
git add sprachdienst/llm.py sprachdienst/auftraege_test.py
git commit -m "llm: Modellaufruf, der sich wirklich abbrechen lässt

Schließt beim Abbruch die Verbindung, damit der Motor den Platz freigibt.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Anbindung an den Dienst — Schleife, Anstöße, Vorrang der Stimme, Ablage

**Files:**
- Modify: `sprachdienst/gateway.py`
  - Importe (bei `from . import absicht as absicht_modul`)
  - Globale Schlösser (nach `BILDER = asyncio.Lock()`)
  - neue Funktionen vor `async def wecker_lauf():`
  - `Sitzung.antworten()` (erste Zeile)
  - `Sitzung.per_ausloeser()`, Zweig `abgleich`
  - `nachbereiten()`
  - `_liste()`, `_sammlung()`, `_loeschen()`
  - `haupt()`
- Modify: `sprachdienst/auftraege_test.py`

**Interfaces:**
- Consumes:
  - Task 1/2/5: `auftraege.laden()`, `auftraege.Bestand`, `auftraege.protokollieren()`, `auftraege.aus_bericht()`, `auftraege.BEFUNDE`
  - Task 3/4: `auftrag_lauf.stoff_stand/stoff_protokoll/stoff_thema`, `begrenzen()`, `lauf()`, `befund_schreiben()`, `ausloeser_text()`
  - Task 6: `llm.frage_abbrechbar(system, frage)`
  - vorhanden in `gateway.py`: `HALTER.z` (`llm_da`, `recherche`, `transkription`, `phase`), `Phase`, `SITZUNGEN`, `Sitzung.im_gespraech()`, `ABGLEICH`, `NACHZIEHEN`, `BILDER`, `wissen_einlesen`, `wissen_index`, `wissen_web`, `wissen_erschliessen`, `wissen_bilder`, `konfig.WEB_SUCHE`, `konfig.AUFNAHMEN`
- Produces (in `gateway.py`):
  - `BESTAND: auftraege_modul.Bestand`
  - `auftraege_laden() -> auftraege_modul.Geladen`
  - `auftrag_unterbrechen() -> None`
  - `async auftraege_lauf()` — Hintergrundschleife
  - Nachricht an die Klienten: `{"typ": "befund_neu", "titel": str, "kennung": str, "zeit": float}`
  - Ablage-Einträge der Art `befund` mit `kennung = "<auftrag>-<dateistamm>"`

Diese Aufgabe hat keinen eigenen Test im Skript: sie verdrahtet geprüfte Teile mit dem laufenden Dienst. Geprüft wird durch Import, `./dienste.sh namen` und in Task 9 am laufenden Dienst.

- [ ] **Step 1: Importe und Zustand**

In `sprachdienst/gateway.py` unter `from . import absicht as absicht_modul` einfügen:

```python
from . import auftraege as auftraege_modul
from . import auftrag_lauf
```

Und nach dem Block mit `BILDER = asyncio.Lock()`:

```python
# Dauerauftraege. Kein eigenes Schloss: es laeuft hoechstens EINE Aufgabe, und
# die Variable ist zugleich der Griff, an dem die Stimme sie abbricht.
BESTAND = auftraege_modul.Bestand()
AUFTRAG_AUFGABE: asyncio.Task | None = None
BELEGUNG = (Path.home() / "Developer" / "github.com" / "modellbelegung"
            / "belegung-klient.sh")
_AUFTRAG_FEHLER: tuple = ()
```

- [ ] **Step 2: Laden, Leerlauf, Abbruch, Belegung**

Vor `async def wecker_lauf():` einfügen:

```python
# ------------------------------------------------------------ Dauerauftraege
def _git_quellen() -> set[str]:
    try:
        konf = json.loads(wissen_einlesen.QUELLEN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {q["name"] for q in konf.get("quellen", [])
            if q.get("art") == "git" and q.get("aktiv", True)}


def auftraege_laden() -> auftraege_modul.Geladen:
    """Bei jedem Gebrauch frisch lesen: die Datei ist klein, und wer einen
    Auftrag aendert, soll den Dienst nicht neu starten muessen. Fehler nur
    einmal ins Protokoll, nicht alle fuenf Sekunden."""
    global _AUFTRAG_FEHLER
    g = auftraege_modul.laden(git_quellen=_git_quellen())
    if tuple(g.fehler) != _AUFTRAG_FEHLER:
        _AUFTRAG_FEHLER = tuple(g.fehler)
        for f in g.fehler:
            log.warning("Auftragsdatei: %s", f)
    return g


def _leerlauf() -> bool:
    """Darf ein Dauerauftrag das Modell benutzen? Nur wenn niemand sonst es
    braucht -- der Motor hat je nach Profil einen einzigen Platz."""
    z = HALTER.z
    return (z.llm_da and not z.recherche and not z.transkription
            and z.phase in (Phase.LEERLAUF, Phase.BEREIT)
            and not NACHZIEHEN.locked()
            and not any(s.im_gespraech() for s in SITZUNGEN))


def auftrag_unterbrechen():
    """Die Stimme hat Vorrang. Der abgebrochene Lauf bleibt in der Schlange."""
    if AUFTRAG_AUFGABE is not None and not AUFTRAG_AUFGABE.done():
        AUFTRAG_AUFGABE.cancel()


async def _belegung(*args: str) -> int:
    """Belegungsstelle fragen. 0/10 Zuschlag, 20 belegt, 30 nicht erreichbar.
    Nicht erreichbar heisst nicht verboten -- dann laeuft der Auftrag trotzdem."""
    if not BELEGUNG.is_file():
        return 30
    try:
        p = await asyncio.create_subprocess_exec(
            str(BELEGUNG), *args, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        return await asyncio.wait_for(p.wait(), 10)
    except (OSError, asyncio.TimeoutError):
        return 30
```

- [ ] **Step 3: Stiller Abgleich und stilles Nachziehen**

Direkt darunter:

```python
async def _nachziehen_still():
    """Wie Sitzung.nachziehen_lauf(), aber ohne Ansage: nach einem
    automatischen Abgleich ist niemand da, dem man etwas sagen muesste."""
    if NACHZIEHEN.locked():
        return
    try:
        async with NACHZIEHEN:
            await wissen_erschliessen.nachziehen()
        async with BILDER:
            await asyncio.to_thread(wissen_bilder.aufbauen)
    except Exception as e:
        log.warning("Nachziehen nach automatischem Abgleich gescheitert: %r", e)


async def _abgleich_still():
    """Der Abgleich ohne Zuruf. Derselbe Weg und dasselbe Schloss wie das Wort
    "Wissensabgleich" -- nur spricht er nichts."""
    if ABGLEICH.locked():
        return
    try:
        async with ABGLEICH:
            bericht = await asyncio.to_thread(wissen_einlesen.alles)
    except Exception as e:
        log.warning("Automatischer Abgleich gescheitert: %r", e)
        return
    for name, ausloeser in auftraege_modul.aus_bericht(
            bericht, auftraege_laden().auftraege):
        if BESTAND.einreihen(name, ausloeser):
            log.info("  Auftrag %s eingereiht (%s..%s)", name,
                     ausloeser["vorher"], ausloeser["nachher"])
    if any(b.get("neu") or b.get("entfernt") for b in bericht):
        asyncio.create_task(_nachziehen_still())
```

- [ ] **Step 4: Ein Lauf**

Direkt darunter:

```python
async def _auftrag_stoff(a, ausloeser: dict):
    """(Stuecke, Hinweise). Wirft, wenn eine Quelle nicht erreichbar ist."""
    if a.art == "stand":
        return await asyncio.to_thread(
            auftrag_lauf.stoff_stand, a, ausloeser,
            wissen_einlesen.REPOS / a.quelle, wissen_index.suchen), []
    if a.art == "protokoll":
        return await asyncio.to_thread(
            auftrag_lauf.stoff_protokoll, a, ausloeser, wissen_index.suchen), []
    if not konfig.WEB_SUCHE:
        return [], ["Websuche ist abgeschaltet (KIHIWI_WEB)"]
    treffer = []
    for frage in a.suchen:
        # _ruf und nicht suchen(): suchen() verschluckt jeden Fehler und gibt
        # eine leere Liste zurueck. Ein ausgefallenes SearXNG saehe dann aus
        # wie "nichts Neues" -- und das Thema gaelte als beobachtet.
        treffer += await asyncio.to_thread(wissen_web._ruf, frage, 6, 12.0)
    return auftrag_lauf.stoff_thema(a, treffer, BESTAND.gesehen_fuer(a.name)), []


async def _auftrag_ausfuehren(a, eintrag: dict):
    ausloeser = eintrag["ausloeser"]
    if await _belegung("belegen", "kihiwi-auftrag", "", "mit", "300",
                       "Dauerauftrag") == 20:
        BESTAND.vertagen(a.name, 60)
        return
    t0, e, stuecke, urteil = time.time(), None, [], "gescheitert"
    grund = {"auftrag": a.name, "art": a.art,
             "ausloeser": auftrag_lauf.ausloeser_text(a, ausloeser)}
    try:
        roh, hinweise = await _auftrag_stoff(a, ausloeser)
        stuecke, gekuerzt = auftrag_lauf.begrenzen(roh)
        e = await auftrag_lauf.lauf(a, stuecke, hinweise + gekuerzt,
                                    llm.frage_abbrechbar)
        urteil = e.urteil
    except asyncio.CancelledError:
        BESTAND.abgebrochen(a.name)
        auftraege_modul.protokollieren({**grund, "urteil": "abgebrochen",
                                        "dauer": round(time.time() - t0, 1)})
        log.info("  Auftrag %s abgebrochen — die Stimme hat Vorrang", a.name)
        return
    except Exception as exc:
        log.warning("Auftrag %s gescheitert: %r", a.name, exc)
        grund["fehler"] = repr(exc)[:200]
    finally:
        await _belegung("freigeben", "kihiwi-auftrag")

    # Ab hier OHNE await bis alles verbucht ist: ein Abbruch zwischen
    # "Befund geschrieben" und "aus der Schlange genommen" liesse denselben
    # Lauf noch einmal laufen und den Befund doppelt entstehen.
    datei, kurz = None, e.kurz if e else ""
    if urteil == "fund":
        datei = auftrag_lauf.befund_schreiben(a, ausloeser, e, llm._modellname(),
                                              auftraege_modul.BEFUNDE)
        BESTAND.befund_merken(a.name, str(datei), kurz)
    if a.art == "thema" and urteil in ("fund", "nichts"):
        BESTAND.gesehen_dazu(a.name, [s.herkunft for s in stuecke])
    BESTAND.lauf_merken(a.name, urteil, kurz)
    BESTAND.fertig(a.name)
    auftraege_modul.protokollieren({
        **grund, "urteil": urteil, "modell": llm._modellname(),
        "dauer": round(time.time() - t0, 1),
        "stuecke": [{"kennung": s.kennung, "herkunft": s.herkunft} for s in stuecke],
        "belegt": len(e.belegt) if e else 0,
        "unbelegt": len(e.unbelegt) if e else 0})
    log.info("  Auftrag %s: %s nach %.0f s", a.name, urteil, time.time() - t0)

    if datei is not None:
        nachricht = json.dumps({"typ": "befund_neu", "zeit": time.time(),
                                "titel": f"{auftraege_modul.sprechname(a.name)}: {kurz}",
                                "kennung": f"{a.name}-{datei.stem}"})
        for s in list(SITZUNGEN):
            try:
                await s.ws.send(nachricht)
            except Exception:
                pass


async def auftraege_lauf():
    """Die Hintergrundschleife: Abgleich nach Zeitplan, faellige Themen,
    und -- wenn das Modell frei ist -- der naechste Lauf aus der Schlange."""
    global AUFTRAG_AUFGABE
    while True:
        await asyncio.sleep(5)
        try:
            g = auftraege_laden()
            aktive = {a.name: a for a in g.auftraege if a.aktiv}
            jetzt = time.time()
            if (g.abgleich_minuten
                    and any(a.art == "stand" for a in aktive.values())
                    and jetzt - BESTAND.abgleich >= g.abgleich_minuten * 60
                    and not ABGLEICH.locked()):
                # Vor dem Lauf vermerken: scheitert er (kein Netz), kommt der
                # naechste Versuch im Takt und nicht alle fuenf Sekunden.
                BESTAND.abgleich_merken(jetzt)
                asyncio.create_task(_abgleich_still())
            for a in aktive.values():
                if a.art == "thema" and BESTAND.thema_faellig(a, jetzt):
                    BESTAND.einreihen(a.name, {"zeit": jetzt})
            if AUFTRAG_AUFGABE is not None and not AUFTRAG_AUFGABE.done():
                continue
            if not _leerlauf():
                continue
            eintrag = BESTAND.naechster(set(aktive), jetzt)
            if eintrag:
                AUFTRAG_AUFGABE = asyncio.create_task(
                    _auftrag_ausfuehren(aktive[eintrag["name"]], eintrag))
        except Exception:
            # Die Schleife darf nie sterben: eine halb geschriebene
            # Auftragsdatei oder ein voller Datentraeger sind kein Grund,
            # bis zum naechsten Neustart keinen Auftrag mehr laufen zu lassen.
            log.exception("Auftragsschleife")
```

- [ ] **Step 5: Anstöße und Vorrang**

**a)** In `Sitzung.antworten()` als erste Anweisung nach dem Docstring:

```python
        auftrag_unterbrechen()
```

**b)** In `Sitzung.per_ausloeser()`, Zweig `if art == "abgleich":`, direkt nach dem Block

```python
                bericht = await asyncio.to_thread(wissen_einlesen.alles)
```

(noch innerhalb von `async with ABGLEICH:`) einfügen:

```python
                for name, ausloeser in auftraege_modul.aus_bericht(
                        bericht, auftraege_laden().auftraege):
                    BESTAND.einreihen(name, ausloeser)
                BESTAND.abgleich_merken(time.time())
```

**c)** In `nachbereiten()` nach der Zeile `await asyncio.to_thread(wissen_einlesen.alles, "protokolle")`:

```python
            neu = Path(verz) / "protokoll.md"
            if neu.exists():
                for a in auftraege_laden().auftraege:
                    if a.art == "protokoll" and a.aktiv:
                        BESTAND.einreihen(a.name, {"datei": str(neu)})
```

**d)** In `haupt()` nach `asyncio.create_task(wecker_lauf())`:

```python
    asyncio.create_task(auftraege_lauf())
```

- [ ] **Step 6: Befunde in die Ablage**

In `_liste()` die Zeile

```python
        name = pfad.parent.name if pfad.name == "protokoll.md" else pfad.stem
```

ersetzen durch:

```python
        if pfad.name == "protokoll.md":
            name = pfad.parent.name
        elif art == "befund":
            # Der Zeitstempel allein ist nicht eindeutig: zwei Auftraege
            # koennen in derselben Sekunde fertig werden.
            name = f"{pfad.parent.name}-{pfad.stem}"
        else:
            name = pfad.stem
```

`_sammlung()` ersetzen durch:

```python
def _sammlung():
    return (_liste(konfig.AUFNAHMEN, "*/protokoll.md", "protokoll")
            + _mitschnitte()
            + _liste(wissen_recherche.ORDNER, "*.md", "recherche")
            + _liste(auftraege_modul.BEFUNDE, "*/*.md", "befund"))
```

In `_loeschen()` die Zeilen

```python
        wurzel = (wissen_recherche.ORDNER if art == "recherche"
                  else konfig.AUFNAHMEN)
```

ersetzen durch:

```python
        if art == "recherche":
            wurzel = wissen_recherche.ORDNER
        elif art == "befund":
            # Eine Ebene tiefer als die Recherchen: befunde/<auftrag>/<datei>.
            wurzel = pfad.parent
            if wurzel.parent.resolve() != auftraege_modul.BEFUNDE.resolve():
                log.error("Loeschen abgelehnt, liegt nicht in befunde/: %s", pfad)
                return "liegt nicht in der Ablage"
        else:
            wurzel = konfig.AUFNAHMEN
```

- [ ] **Step 7: Prüfen**

Run:
```bash
.venv/bin/python -c "
from sprachdienst import gateway as g
print(g._leerlauf(), g.auftraege_laden().fehler, [e['art'] for e in g._sammlung()][:3])"
./dienste.sh namen
.venv/bin/python -m sprachdienst.auftraege_test | tail -1
```
Expected: erste Zeile `False [] [...]` (ohne laufenden Dienst ist das Modell nicht als erreichbar vermerkt, also kein Leerlauf); dann `… 0 Fund(e)`; dann `abbrechbar: ok`.

- [ ] **Step 8: Commit**

```bash
git add sprachdienst/gateway.py
git commit -m "Daueraufträge: Hintergrundschleife, Anstöße, Vorrang der Stimme

Der Abgleich läuft jetzt auch von selbst (abgleich_minuten). Befunde
erscheinen in der Ablage.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Stimme und Bühne

**Files:**
- Modify: `sprachdienst/auftraege.py` (anhängen)
- Modify: `sprachdienst/absicht.py` (`AUSLOESER`, `BESCHREIBUNG`, Reihenfolge in `ausloeser()`)
- Modify: `sprachdienst/gateway.py` (`Sitzung.per_ausloeser()`)
- Modify: `sprachdienst/klient.html`
- Modify: `sprachdienst/auftraege_test.py`

**Interfaces:**
- Consumes: `auftraege.Geladen`, `Bestand`, `zuordnen()`, `sprechname()`; in `gateway.py`: `BESTAND`, `auftraege_laden()`, `Sitzung.melden(text)`, `Sitzung.zur_buehne(nachricht: dict)`, `wissen_einlesen._head(pfad) -> str`, `wissen_einlesen.REPOS`, `konfig.AUFNAHMEN`
- Produces:
  - `auftraege.lage_satz(g: Geladen, b: Bestand, jetzt: float | None = None) -> str` — das Gesprochene
  - `auftraege.lage_text(g: Geladen, b: Bestand, jetzt: float | None = None) -> str` — Markdown für die Bühne
  - Auslöser `auftragslage`, `auftragslauf`, `auftragspause` in `absicht.ausloeser()`
  - Bühnennachricht `{"typ": "auftragslage", "text": str}`

- [ ] **Step 1: Test schreiben**

In `sprachdienst/auftraege_test.py` vor `def alle():` einfügen:

```python
def test_lage():
    from . import absicht
    # Die drei Woerter, auch auseinandergezogen; und nichts davon im Alltagssatz.
    for satz, art, thema in [
            ("Kiwi, Auftragslage", "auftragslage", "Kiwi"),
            ("Auftrags Lage bitte", "auftragslage", ""),
            ("Auftragslauf stand beispiel", "auftragslauf", "stand beispiel"),
            ("Auftragspause Protokoll nachhalten", "auftragspause", "Protokoll nachhalten")]:
        a, t = absicht.ausloeser(satz)
        assert a == art, (satz, a)
        assert thema.lower() in t.lower() or thema == "Kiwi", (satz, t)
    assert absicht.ausloeser("Der Auftrag ist erledigt, die Lage ist gut.")[0] is None
    assert {"auftragslage", "auftragslauf", "auftragspause"} <= set(absicht.BESCHREIBUNG)

    jetzt = time.mktime((2026, 10, 4, 15, 0, 0, 0, 0, -1))
    with tempfile.TemporaryDirectory() as t:
        g = auftraege.laden(_datei(t, GUT))
        b = auftraege.Bestand(Path(t) / "z.json")

        assert auftraege.lage_satz(g, b, jetzt) == (
            "Nichts Neues. Es ist noch kein Auftrag gelaufen.")
        b.lauf_merken("stand-beispiel", "nichts", "", jetzt - 3600)
        assert auftraege.lage_satz(g, b, jetzt) == (
            "Nichts Neues. Zuletzt lief stand beispiel um 14 Uhr 00, ohne Fund.")

        for i, kurz in enumerate(["Erstens", "Zweitens.", "Drittens", "Viertens"]):
            f = Path(t) / f"b{i}.md"; f.write_text("x")
            b.befund_merken("stand-beispiel", str(f), kurz, jetzt - 60 * i)
        satz = auftraege.lage_satz(g, b, jetzt)
        assert satz == ("4 neue Befunde. Erstens. Zweitens. Drittens. "
                        "Ein weiterer steht auf dem Monitor."), satz
        b.alle_gelesen()
        b.befund_merken("stand-beispiel", str(Path(t) / "b0.md"), "Nur einer", jetzt)
        assert auftraege.lage_satz(g, b, jetzt) == "Ein neuer Befund. Nur einer."

        b.pause("protokoll-nachhalten", True)
        b.einreihen("stand-beispiel", {"vorher": "a", "nachher": "b"})
        text = auftraege.lage_text(g, b, jetzt)
        zeilen = {z.split("|")[1].strip(): [s.strip() for s in z.split("|")[2:-1]]
                  for z in text.splitlines() if z.startswith("| ") and "---" not in z}
        assert zeilen["stand-beispiel"] == ["stand", "wartet", "heute 14:00", "ohne Fund"]
        assert zeilen["protokoll-nachhalten"] == ["protokoll", "pausiert", "–", "–"]
        assert zeilen["thema-beispiel"][1] == "abgeschaltet"
        assert "## Ungelesene Befunde" in text and "Nur einer" in text

        # Fehlerhafte Datei: das sagen, und auf der Buehne steht warum.
        kaputt = auftraege.laden(_datei(t, "{"))
        assert "fehlerhaft" in auftraege.lage_satz(kaputt, b, jetzt)
        assert "> " in auftraege.lage_text(kaputt, b, jetzt)
    print("lage: ok")
```

`alle()` um `test_lage()` erweitern.

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `.venv/bin/python -m sprachdienst.auftraege_test 2>&1 | tail -3`
Expected: `AssertionError` in der ersten Schleife (`('Kiwi, Auftragslage', None)`)

- [ ] **Step 3: Auslösewörter**

In `sprachdienst/absicht.py` im Wörterbuch `AUSLOESER` vor dem Eintrag `"bild":` einfügen:

```python
    # Dauerauftraege: Befunde abrufen, einen Lauf anstossen, einen Auftrag
    # anhalten. Zusammengesetzt wie die uebrigen -- "Auftrag" und "Lage"
    # allein fallen im Labor staendig.
    "auftragslage":  rf"auftrags{_}lage|auftrags{_}stand",
    "auftragslauf":  rf"auftrags{_}lauf|auftrags{_}start",
    "auftragspause": rf"auftrags{_}pause",
```

In `BESCHREIBUNG` vor `"nachziehen":`:

```python
    "auftragslage":  ("Auftragslage", "neue Befunde der Daueraufträge hören und sehen"),
    "auftragslauf":  ("Auftragslauf", "einen Dauerauftrag sofort laufen lassen"),
    "auftragspause": ("Auftragspause", "einen Dauerauftrag anhalten oder fortsetzen"),
```

In `ausloeser()` die Reihenfolge erweitern — die drei neuen direkt nach `"abbruch"`:

```python
    for art in ("hilfe", "abbruch", "auftragslage", "auftragslauf",
                "auftragspause", "bild", "anzeige", "nachziehen", "abgleich",
                "hermes", "dokumente", "websuche", "recherche"):
```

- [ ] **Step 4: Lagebericht**

An `sprachdienst/auftraege.py` anhängen:

```python
# ------------------------------------------------------------------ Lage
URTEIL_WORT = {"fund": "mit Fund", "nichts": "ohne Fund",
               "nichts zu prüfen": "ohne neuen Stoff", "gescheitert": "gescheitert"}
ANSAGE_MAX = 3           # so viele Befunde werden vorgelesen


def _wann(zeit: float, jetzt: float) -> str:
    t, h = time.localtime(zeit), time.localtime(jetzt)
    if t[:3] == h[:3]:
        return time.strftime("heute %H:%M", t)
    return time.strftime("%d.%m. %H:%M", t)


def lage_satz(g: Geladen, b: Bestand, jetzt: float | None = None) -> str:
    """Was Kiwi auf "Auftragslage" sagt. Kurz -- das Ausfuehrliche steht auf
    der Buehne. Stille wird ausgesprochen: "nichts Neues, zuletzt lief ..."
    sieht nicht aus wie ein Ausfall."""
    jetzt = time.time() if jetzt is None else jetzt
    if g.fehler and not g.auftraege:
        return ("Die Auftragsdatei ist fehlerhaft, es läuft kein Auftrag. "
                "Näheres steht auf dem Monitor.")
    neu = b.ungelesene()
    if neu:
        kopf = "Ein neuer Befund." if len(neu) == 1 else f"{len(neu)} neue Befunde."
        saetze = [u["kurz"].rstrip(". ") + "." for u in neu[:ANSAGE_MAX] if u["kurz"]]
        rest = len(neu) - ANSAGE_MAX
        ende = ([] if rest <= 0 else
                ["Ein weiterer steht auf dem Monitor."] if rest == 1 else
                [f"{rest} weitere stehen auf dem Monitor."])
        return " ".join([kopf] + saetze + ende)
    if not b.laeufe:
        return "Nichts Neues. Es ist noch kein Auftrag gelaufen."
    name, lauf = max(b.laeufe.items(), key=lambda x: x[1]["zeit"])
    t = time.localtime(lauf["zeit"])
    return (f"Nichts Neues. Zuletzt lief {sprechname(name)} um "
            f"{t.tm_hour} Uhr {t.tm_min:02d}, "
            f"{URTEIL_WORT.get(lauf['urteil'], lauf['urteil'])}.")


def lage_text(g: Geladen, b: Bestand, jetzt: float | None = None) -> str:
    """Die Uebersicht fuer die Buehne: alle Auftraege, dann die Ungelesenen."""
    jetzt = time.time() if jetzt is None else jetzt
    wartend = {e["name"] for e in b.schlange}
    z = [f"> Fehler: {f}" for f in g.fehler]
    if z:
        z.append("")
    z += ["| Auftrag | Art | Zustand | Letzter Lauf | Urteil |", "|---|---|---|---|---|"]
    for a in g.auftraege:
        zustand = ("abgeschaltet" if not a.aktiv else
                   "pausiert" if a.name in b.pausiert else
                   "wartet" if a.name in wartend else "aktiv")
        lauf = b.laeufe.get(a.name)
        z.append(f"| {a.name} | {a.art} | {zustand} | "
                 f"{_wann(lauf['zeit'], jetzt) if lauf else '–'} | "
                 f"{URTEIL_WORT.get(lauf['urteil'], lauf['urteil']) if lauf else '–'} |")
    neu = b.ungelesene()
    if neu:
        z += ["", "## Ungelesene Befunde", ""]
        z += [f"- **{u['auftrag']}** ({_wann(u['zeit'], jetzt)}): {u['kurz']}" for u in neu]
    return "\n".join(z)
```

- [ ] **Step 5: Test laufen lassen**

Run: `.venv/bin/python -m sprachdienst.auftraege_test 2>&1 | tail -3`
Expected: zuletzt `lage: ok`

- [ ] **Step 6: Sprachbefehle im Dienst**

In `sprachdienst/gateway.py`, in `Sitzung.per_ausloeser()`, vor `if art == "bild":` einfügen:

```python
        if art == "auftragslage":
            g = auftraege_laden()
            await self.zur_buehne({"typ": "auftragslage",
                                   "text": auftraege_modul.lage_text(g, BESTAND)})
            await self.melden(auftraege_modul.lage_satz(g, BESTAND))
            # Erst nach dem Sagen: bricht die Ansage ab, bleiben sie ungelesen.
            BESTAND.alle_gelesen()
            return True

        if art in ("auftragslauf", "auftragspause"):
            g = auftraege_laden()
            namen = [a.name for a in g.auftraege]
            treffer = auftraege_modul.zuordnen(thema, namen)
            if len(treffer) != 1:
                if not namen:
                    await self.melden("Es sind keine Daueraufträge eingerichtet.")
                else:
                    await self.melden(
                        "Welchen Auftrag meinst du? Es gibt "
                        + ", ".join(auftraege_modul.sprechname(n) for n in namen) + ".")
                return True
            a = next(x for x in g.auftraege if x.name == treffer[0])
            name = auftraege_modul.sprechname(a.name)

            if art == "auftragspause":
                an = not re.search(r"fortsetz|weiter|wieder", ganzer_text, re.I)
                BESTAND.pause(a.name, an)
                await self.melden(f"{name} ist angehalten." if an
                                  else f"{name} läuft wieder.")
                return True

            if not a.aktiv:
                await self.melden(f"{name} ist in der Auftragsdatei abgeschaltet.")
                return True
            if a.name in BESTAND.pausiert:
                await self.melden(f"{name} ist angehalten. Sag Auftragspause "
                                  f"{name} fortsetzen.")
                return True
            if a.art == "stand":
                repo = wissen_einlesen.REPOS / a.quelle
                # Der Arbeitsklon ist flach (--depth 1). Ohne den Vorgaenger
                # zeigt "git show" den ganzen Baum als neu.
                await asyncio.to_thread(
                    subprocess.run, ["git", "-C", str(repo), "fetch", "-q", "--deepen=1"],
                    capture_output=True, timeout=60)
                kopf = await asyncio.to_thread(wissen_einlesen._head, repo)
                if not kopf:
                    await self.melden("Diese Quelle ist noch nicht abgeholt. "
                                      "Sag erst Wissensabgleich.")
                    return True
                ausloeser = {"vorher": "", "nachher": kopf}
            elif a.art == "protokoll":
                neueste = max(konfig.AUFNAHMEN.glob("*/protokoll.md"),
                              key=lambda p: p.stat().st_mtime, default=None)
                if neueste is None:
                    await self.melden("Es gibt noch kein Protokoll.")
                    return True
                ausloeser = {"datei": str(neueste)}
            else:
                ausloeser = {"zeit": time.time()}
            BESTAND.einreihen(a.name, ausloeser)
            await self.melden(f"{name} ist eingereiht. Das Ergebnis liegt dann "
                              "in der Ablage.")
            return True
```

Am Kopf von `gateway.py` die Importzeile um `subprocess` erweitern:

```python
import asyncio, http, json, logging, re, shutil, signal, subprocess, time
```

- [ ] **Step 7: Darstellung im Browser**

In `sprachdienst/klient.html`:

**a)** Bei den `.art-…`-Regeln (nach `.art-bild`) einfügen:

```css
  .art-auftragslage { background:#1f2a3a; color:#9cc0ee; }
  .art-befund       { background:#2f2412; color:#e8c069; }
```

und nach `.art.recherche { … }`:

```css
  .art.befund { background:#2f2412; color:#e8c069; }
```

**b)** In `function buehne(…)` die Liste erweitern:

```js
  $("b-art").className = "art-" + (["protokoll","recherche","hilfe","erinnerungen","anzeige","bild","auftragslage","befund"]
```

**c)** `ABLAGE_WORT` erweitern:

```js
const ABLAGE_WORT = {protokoll: "Protokolle", mitschnitt: "Aufzeichnungen",
                     recherche: "Recherchen", befund: "Befunde"};
```

**d)** In `async function oeffnen(e)` die Zeile mit `buehne(e.art === "protokoll" ? "Protokoll" : "Recherche", …` ersetzen durch:

```js
  const ART_WORT = {protokoll: "Protokoll", befund: "Befund"};
  buehne(ART_WORT[e.art] || "Recherche", ablageTitel(e),
         alsHtml(await r.text()), wannText(e.geaendert));
```

**e)** In `ws.onmessage` vor `} else if (d.typ === "bild") {` einfügen:

```js
    } else if (d.typ === "auftragslage") {
      const zeigen = () => buehne("Auftragslage", "Daueraufträge",
                                  alsHtml(d.text), wannZusatz(d));
      zeigen();
      if (!d.wieder) merker("Auftragslage", zeigen);
    } else if (d.typ === "befund_neu") {
      // Nur ein Merker -- Kiwi spricht nicht von selbst ueber Befunde, und
      // die Buehne gehoert dem, was der Nutzer gerade offen hat.
      merker(`Befund: ${d.titel}`,
             () => oeffnen({art: "befund", kennung: d.kennung, titel: d.titel,
                            geaendert: d.zeit}));
      ablageLaden(ablageSichtbar());
```

- [ ] **Step 8: Prüfen**

Run:
```bash
./dienste.sh namen
.venv/bin/python -m sprachdienst.auftraege_test
.venv/bin/python -c "import ast; ast.parse(open('sprachdienst/gateway.py').read()); print('gateway ok')"
```
Expected: `… 0 Fund(e)`; acht Zeilen von `laden: ok` bis `lage: ok`; `gateway ok`.

- [ ] **Step 9: Commit**

```bash
git add sprachdienst/auftraege.py sprachdienst/absicht.py sprachdienst/gateway.py sprachdienst/klient.html sprachdienst/auftraege_test.py
git commit -m "Daueraufträge: Auftragslage, Auftragslauf, Auftragspause

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Am echten Modell prüfen und dokumentieren

**Files:**
- Create (nicht versioniert): `auftraege.json`
- Modify: `fachlich.md`, `technisch.md`, `entwicklung.md`

**Interfaces:**
- Consumes: alles aus Task 1–8; laufendes Sprachmodell auf `konfig.LLM_URL`; `./dienste.sh`
- Produces: nichts für spätere Tasks

**Vorher lesen:** die Global Constraints. Was in diesem Task an Befunden, Auftragsnamen oder Zielen entsteht, stammt aus privaten Quellen und gehört **nicht** in die Doku und nicht in Commit-Texte. In die Doku kommen Zeiten, Größen und Verhalten, keine Inhalte.

- [ ] **Step 1: Eine echte Auftragsdatei anlegen (bleibt lokal)**

Die Namen der Git-Quellen ansehen:

```bash
.venv/bin/python -c "
import json; from wissen import einlesen
print([q['name'] for q in json.load(open(einlesen.QUELLEN))['quellen'] if q.get('art')=='git' and q.get('aktiv', True)])"
```

`auftraege.json` im Projektwurzelverzeichnis schreiben, mit `abgleich_minuten: 0` (für den Test kein automatischer Abgleich), einem `stand`-Auftrag auf die erste Quelle, einem `protokoll`-Auftrag und einem `thema`-Auftrag mit einer unverfänglichen Suchanfrage. Prüfen, dass Git sie nicht sieht:

```bash
git status --short auftraege.json befunde/    # darf nichts ausgeben
.venv/bin/python -c "
from sprachdienst import gateway as g
x = g.auftraege_laden(); print(x.fehler, [(a.name, a.art, a.aktiv) for a in x.auftraege])"
```
Expected: leere Fehlerliste, drei Aufträge.

- [ ] **Step 2: Dienst neu starten — erst das Modell, dann der Dienst**

```bash
./dienste.sh status          # Sprachmodell muss "bereit" sein
./dienste.sh neustart
```

Steht das Sprachmodell nicht auf „bereit": **nicht** selbst starten oder wechseln, sondern anhalten und den Nutzer fragen. Der Sprachdienst übernimmt den Modellnamen nur, wenn das Modell beim Start schon läuft.

- [ ] **Step 3: Je Art ein Lauf**

Dieses Skript als `/tmp/auftrag_probe.py` ablegen (nicht ins Repo):

```python
import asyncio, json, sys, websockets

async def sag(text, warte=20):
    async with websockets.connect("ws://127.0.0.1:8920/audio") as ws:
        await ws.send(json.dumps({"befehl": "vorlesen", "an": False}))
        await ws.send(json.dumps({"befehl": "text", "text": text}))
        ende = asyncio.get_event_loop().time() + warte
        while asyncio.get_event_loop().time() < ende:
            try:
                m = await asyncio.wait_for(ws.recv(), 2)
            except asyncio.TimeoutError:
                continue
            if isinstance(m, bytes):
                continue
            d = json.loads(m)
            if d["typ"] in ("text", "befund_neu", "auftragslage") and not d.get("wieder"):
                print(d["typ"], "|", str(d.get("text") or d.get("titel"))[:300])
            if d["typ"] == "text" and d.get("rolle") == "assistent":
                return

asyncio.run(sag(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 20))
```

Für jeden der drei Aufträge (Namen aus Step 1 einsetzen):

```bash
.venv/bin/python /tmp/auftrag_probe.py "Auftragslauf <name>"
# 60 bis 90 Sekunden warten, dann:
tail -1 zustand/auftraege.log.jsonl
ls -la befunde/*/ 2>/dev/null
```

Expected je Lauf: Kiwi antwortet „… ist eingereiht."; nach spätestens zwei Minuten steht eine neue Zeile im Prüfprotokoll mit `urteil` gleich `fund`, `nichts` oder `nichts zu prüfen` und `dauer` unter 120. Bei `fund` liegt eine Datei unter `befunde/<name>/`.

Jeden entstandenen Befund öffnen und **gegen die Quelle prüfen**: steht jedes Zitat unter „Belegte Punkte" wörtlich in der genannten Quelle, und trägt die Aussage, was das Zitat hergibt? Das Ergebnis dieser Prüfung (wie viele Punkte stimmen, wie viele nicht) notieren.

Steht `urteil: gescheitert` im Protokoll, den Grund in `logs/sprach.log` suchen (`grep "Auftrag" logs/sprach.log | tail`) und beheben, bevor es weitergeht.

- [ ] **Step 4: Vorrang der Stimme**

```bash
.venv/bin/python /tmp/auftrag_probe.py "Auftragslauf <name des stand-auftrags>"
sleep 8        # die Schleife prueft alle 5 s; jetzt rechnet der Lauf
.venv/bin/python /tmp/auftrag_probe.py "Wie spät ist es?"
tail -3 zustand/auftraege.log.jsonl
grep -c "stop: cancel" ~/llama-server.log     # nur bei llama-server
```

Expected: die Frage wird in wenigen Sekunden beantwortet; im Prüfprotokoll steht eine Zeile `"urteil": "abgebrochen"` und danach, nach ein bis zwei Minuten Ruhe, eine weitere mit dem regulären Urteil desselben Auftrags. Kommt die Antwort erst nach dem Ende des Laufs, greift der Abbruch nicht — dann Task 6 prüfen, nicht hier nachbessern.

- [ ] **Step 5: Auftragslage und Pause**

```bash
.venv/bin/python /tmp/auftrag_probe.py "Auftragslage"
.venv/bin/python /tmp/auftrag_probe.py "Auftragspause <name>"
.venv/bin/python /tmp/auftrag_probe.py "Auftragslage"
.venv/bin/python /tmp/auftrag_probe.py "Auftragspause <name> fortsetzen"
.venv/bin/python /tmp/auftrag_probe.py "Auftragslauf Kaffeemaschine"
```

Expected: die erste Auftragslage nennt die neuen Befunde oder „Nichts Neues. Zuletzt lief …"; die zweite zeigt den Auftrag als „pausiert"; der letzte Befehl zählt die vorhandenen Namen auf.

Im Browser (`http://127.0.0.1:8920/klient`) nachsehen: die Tabelle der Auftragslage ist als Tabelle gesetzt, ein Befund lässt sich aus der Ablage öffnen und löschen.

- [ ] **Step 6: Dokumentieren**

**`fachlich.md`** — in der Tabelle unter „## Auslösewörter" vor der Zeile `Kiwihilfe` ergänzen:

```markdown
| `Auftragslage` | nennt neue Befunde der Daueraufträge, zeigt alle Aufträge |
| `Auftragslauf` | lässt einen Dauerauftrag sofort laufen |
| `Auftragspause` | hält einen Dauerauftrag an; mit „fortsetzen" läuft er wieder |
```

und nach dem Absatz zur Bildanzeige einen Abschnitt einfügen:

```markdown
## Daueraufträge

Ein Dauerauftrag bleibt bestehen. Er wird von einem Ereignis oder einem
Zeitplan ausgelöst, prüft etwas und legt einen **Befund** ab. Drei Arten:

- **Neue Stände prüfen** — bei einem neuen Commit in einer Git-Quelle.
- **Protokolle nachhalten** — nach jeder Aufzeichnung.
- **Thema beobachten** — nach Zeitplan im Netz; gemeldet wird nur Neues.

**Kiwi spricht nicht von selbst darüber.** Befunde liegen in der Ablage; auf
„Auftragslage" fasst er zusammen. Ein Assistent, der ungefragt in ein
Laborgespräch redet, wird abgeschaltet.

**Ein Befund ist abgeleitet.** Jeder Punkt trägt ein wörtliches Zitat, und der
Dienst prüft, ob es in der genannten Quelle steht. Was sich nicht belegen
lässt, zählt nicht als Fund und steht abgesetzt darunter. Trotzdem gilt: vor
Verwendung gegen die Quelle prüfen.

**Ein Dauerauftrag liest und berichtet.** Er ändert nichts, und seine Befunde
fließen nicht in den Wissensbestand zurück.

**Die Aufträge stehen in `auftraege.json`,** nicht im Repo — die Ziele
verraten, woran gearbeitet wird. Angelegt und geändert werden sie dort, nicht
per Stimme.

**Zwei Dinge laufen dadurch von selbst, die vorher nur auf Zuruf liefen:** der
Wissensabgleich (alle 30 Minuten, einstellbar) und — bei Themenaufträgen —
Suchanfragen ins Netz. Der Schalter für die Websuche gilt auch hier.
```

**`technisch.md`** — vor `### Timer und Erinnerungen` einen Abschnitt `### Daueraufträge (`sprachdienst/auftraege.py`, `auftrag_lauf.py`)` einfügen. Er beschreibt, mit den **in Step 3 und 4 gemessenen** Zahlen:

- die beiden Module und warum `auftrag_lauf.py` den Dienst nicht kennt
- den Ablauf Stoff → ein Modellaufruf → Belegprüfung, mit der Obergrenze von 24.000 Zeichen und der gemessenen Dauer je Lauf
- die Auslöser und das Zusammenlegen in der Warteschlange
- Leerlaufbedingung, Vorrang der Stimme und `llm.frage_abbrechbar()` mit der gemessenen Zeit bis zur Antwort im Abbruchtest
- die Dateien: `auftraege.json`, `zustand/auftraege.json`, `zustand/auftraege.log.jsonl`, `befunde/`
- die Befehle:

```
    .venv/bin/python -m sprachdienst.auftraege_test    alles ohne Dienst und Modell
    tail zustand/auftraege.log.jsonl                   was lief und mit welchem Urteil
```

- die bekannte Grenze: ein von Hand ausgelöster `stand`-Lauf prüft nur den letzten Commit

**`entwicklung.md`** — einen Eintrag `## Daueraufträge (<Datum>)` anhängen: Ausgangspunkt (Kiwi reagierte nur auf Zuruf), der Versuch mit dem fertigen Agenten-Laufwerk und was daraus folgte (kleiner Stoff, ein Aufruf), was beim Bauen und in Step 3–5 tatsächlich schiefging und wie es behoben wurde, und das Ergebnis der Belegprüfung von Hand aus Step 3 als Zahl. Keine Inhalte aus den Befunden.

- [ ] **Step 7: Nichts Privates im Diff**

```bash
git status --short
git diff | grep -iE "$(.venv/bin/python -c "
import json; from wissen import einlesen
print('|'.join(q['name'] for q in json.load(open(einlesen.QUELLEN))['quellen']))")" || echo "sauber"
```
Expected: `auftraege.json` und `befunde/` erscheinen nicht in `git status`; die zweite Zeile gibt `sauber` aus. Zusätzlich den Diff selbst durchlesen: keine Dateinamen, Zitate oder Ziele aus den Quellen.

- [ ] **Step 8: Commit**

```bash
git add fachlich.md technisch.md entwicklung.md
git commit -m "Daueraufträge dokumentiert

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

Nicht pushen. Dem Nutzer berichten: welche Läufe mit welchem Urteil endeten, das Ergebnis der Belegprüfung von Hand, die gemessene Zeit im Abbruchtest, und dass `abgleich_minuten` in seiner `auftraege.json` für den Test auf 0 steht.
