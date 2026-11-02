"""Prueft das Lagebild: Ablage, Korrekturen, Kern fuer den Prompt, Lagefragen.

    .venv/bin/python -m wissen.lagebild_test

Braucht weder Modell noch Dienst noch den echten Index: alles laeuft in einem
Temp-Ordner.
"""
import asyncio
import inspect
import json
import os
import sqlite3
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from sprachdienst import llm
from . import index
from . import lagebild
from .lagebild import (ablegen, abschnitt_fuer, aktuell, budget_einhalten,
                       eingaben_sammeln, erzeugen, ganzes_lagebild, korrektur_hinzu,
                       korrektur_zuruecknehmen, korrekturen_abloesen, lage_frage,
                       offene_korrekturen, prompt_block, pruefen)

LB = ("# Lagebild vom 01.11.2026\n\n> Automatisch abgeleitet, nicht geprüft.\n\n"
      "## Worum es geht\n- Ein kompaktes Mikroskop. [Projektbeschreibung, 12.10.2026]\n\n"
      "## Stand\n- Teststand läuft. [Statusbericht, 31.10.2026]\n\n"
      "## Als Nächstes\n- Messreihe. [Messplan, 30.10.2026]\n\n"
      "## Probleme und Herausforderungen\nKeine Angaben in den Quellen.\n\n"
      "## Vorzeigbar\n- Erste Bilder. [Abbildung, 29.10.2026]\n")


def ablage_und_korrekturen(v: Path):
    jetzt = datetime(2026, 11, 2, 9, 0).astimezone()

    # ohne Dateien
    assert aktuell(v) is None and prompt_block(jetzt, v) == ""
    assert abschnitt_fuer("stand", v) is None and lage_frage("stand", "x", jetzt, v) is None
    assert ganzes_lagebild(v) is None

    # Ablage: der juengere Lauf desselben Tages gilt
    (v / "2026-11-01T0330.md").write_text(LB.replace("Teststand läuft", "alt"))
    (v / "2026-11-01T1415.md").write_text(LB)
    a = aktuell(v)
    assert a["datum"] == date(2026, 11, 1) and "Teststand läuft" in a["abschnitte"]["stand"]

    # Abschnitt mit Quellen
    u, t, q, d = abschnitt_fuer("stand", v)
    assert u == "Stand" and "Teststand läuft" in t and q == ["Statusbericht"]
    assert d == date(2026, 11, 1)

    # Kern im Prompt, gekennzeichnet, ohne die uebrigen Abschnitte
    b = prompt_block(jetzt, v)
    assert "Lagebild vom 01.11.2026" in b and "automatisch abgeleitet" in b
    assert "Ein kompaktes Mikroskop" in b and "Teststand" not in b

    # Korrekturen
    k = korrektur_hinzu("Der Teststand ist umgebaut.", jetzt, v)
    assert k["id"] == "K1" and k["status"] == "offen"
    assert "Der Teststand ist umgebaut." in prompt_block(jetzt, v)
    assert korrektur_zuruecknehmen(v)["id"] == "K1" and offene_korrekturen(jetzt, v) == []
    assert korrektur_zuruecknehmen(v) is None

    # Lagefrage: Buehne, Modellfrage, Altershinweis erst ab mehr als zwei Tagen
    b, f, q = lage_frage("stand", "Wie ist der Stand?", jetzt, v)
    assert b == {"typ": "lagebild", "titel": "Stand", "text": t, "stand": "01.11.2026"}
    assert "Abschnitt „Stand“" in f and f.rstrip().endswith("Wie ist der Stand?")
    assert "Nenne dabei" not in f and q == ["Statusbericht"]
    f2 = lage_frage("stand", "Wie ist der Stand?", jetzt + timedelta(days=2, hours=1), v)[1]
    assert "Nenne dabei, dass das Lagebild vom 01.11.2026 ist." in f2
    assert ganzes_lagebild(v)["text"].startswith("# Lagebild vom 01.11.2026")

    # Ablauf nach 14 Tagen, Abloesung durch eine neuere Quelle
    korrektur_hinzu("Alt.", jetzt - timedelta(days=14, minutes=1), v)
    korrektur_hinzu("Grenze.", jetzt - timedelta(days=13, hours=23), v)
    assert [x["text"] for x in offene_korrekturen(jetzt, v)] == ["Grenze."]
    korrekturen_abloesen({"K3": "Statusbericht, 03.11.2026"}, v)
    assert offene_korrekturen(jetzt, v) == []


def _git(repo: Path, *args, datum: str | None = None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}
    if datum:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = datum
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                   capture_output=True)


def _protokoll(aufnahmen: Path, name: str, text: str, wann: datetime):
    d = aufnahmen / name
    d.mkdir(parents=True)
    (d / "protokoll.md").write_text(text)
    os.utime(d / "protokoll.md", (wann.timestamp(), wann.timestamp()))


def eingaben(t: Path):
    jetzt = datetime(2026, 11, 2, 9, 0).astimezone()
    v, repos, aufnahmen = t / "lb", t / "repos", t / "aufnahmen"
    repo = repos / "probe"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "notiz.md").write_text("eins")
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "Erster", datum="2026-10-20T10:00:00")
    (repo / "STAND.md").write_text("Der Teststand läuft.")
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "Zweiter", datum="2026-10-31T10:00:00")
    (repo / "notiz.md").write_text("zwei")
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "Dritter", datum="2026-11-01T10:00:00")

    c = sqlite3.connect(t / "i.db")
    c.executescript(index.SCHEMA)
    index.dokument_setzen(c, "probe", "probe:STAND.md", "STAND.md", "", "", "f1",
                          [("Stand", "Der Teststand läuft. " * 30)])
    index.dokument_setzen(c, "probe", "probe:notiz.md", "notiz.md", "", "", "f2",
                          [("", "zwei")])
    c.commit()

    _protokoll(aufnahmen, "20261101T100000Z",
               "# Protokoll\n\n## Zusammenfassung\n\nPumpe besprochen.\n\n## Wortlaut\n\nx",
               datetime(2026, 11, 1, 11, 0))
    _protokoll(aufnahmen, "20261030T100000Z", "# Protokoll\n\n## Wortlaut\n\nnur Text",
               datetime(2026, 10, 30, 11, 0))
    _protokoll(aufnahmen, "20261015T100000Z",
               "# Protokoll\n\n## Zusammenfassung\n\nzu alt\n", datetime(2026, 10, 15, 11, 0))
    korrektur_hinzu("Die Pumpe ist getauscht.", jetzt - timedelta(days=1), v)

    konf = {"status": ["*STAND*"], "vorzeigbar": ["*vortrag*"], "tage": 7,
            "commits_max": 80, "dokument_zeichen": 12000}
    e = eingaben_sammeln(konf, c, repos, aufnahmen, jetzt, v)
    arten = [x["art"] for x in e]
    assert arten[0] == "korrektur" and e[0]["id"].startswith("K"), e[0]
    assert e[0]["text"] == "Die Pumpe ist getauscht."
    st = [x for x in e if x["art"] == "status"]
    assert [x["titel"] for x in st] == ["STAND.md"] and st[0]["datum"] == date(2026, 10, 31)
    assert [x["titel"] for x in e if x["art"] == "commit"] == ["Dritter", "Zweiter"]
    pr = [x for x in e if x["art"] == "protokoll"]
    assert len(pr) == 1 and pr[0]["text"].strip() == "Pumpe besprochen.", pr
    assert all(isinstance(x["datum"], date) for x in e)
    assert len({x["id"] for x in e}) == len(e)
    assert all(x["id"].startswith("E") for x in e if x["art"] != "korrektur")

    k = budget_einhalten(e, 300)
    assert any(x["art"] == "korrektur" for x in k)
    assert sum(len(x["text"]) for x in k if x["art"] != "korrektur") <= 300
    assert not any(x["art"] == "commit" for x in k)       # Commits fallen zuerst
    assert budget_einhalten(e, 10**6) == e


def erzeugung(v: Path):
    jetzt = datetime(2026, 11, 2, 9, 0).astimezone()
    assert inspect.signature(llm.antwort_text).parameters["timeout"].default == 60
    eing = [{"id": "K1", "art": "korrektur", "titel": "Korrektur",
             "datum": date(2026, 10, 30), "text": "x"},
            {"id": "E1", "art": "status", "titel": "STAND.md",
             "datum": date(2026, 10, 31), "text": "y"},
            {"id": "E2", "art": "commit", "titel": "Umbau",
             "datum": date(2026, 10, 29), "text": "z"},
            {"id": "E3", "art": "status", "titel": "Notiz", "datum": None, "text": "w"}]
    roh = ("**Worum es geht**\n- Mikroskop. [E1]\n\n## Stand\n- Läuft. [E1, K1]\n"
           "- Erfunden. [E99]\n- Ohne Verweis.\n\n## Probleme und Herausforderungen\n"
           "- Pumpe. [E2]\n- Unklar. [E3]\n\n## Korrekturen\nK1: abgelöst durch E1\n")
    text, abl, entfernt = pruefen(roh, eing, jetzt)
    assert "[STAND.md, 31.10.2026]" in text and "[E1" not in text, text
    assert "[STAND.md, 31.10.2026; Korrektur, 30.10.2026]" in text
    assert "[Notiz, Datum unbekannt]" in text
    assert "Erfunden" not in text and "Ohne Verweis" not in text and entfernt == 2
    assert "## Als Nächstes\nKeine Angaben in den Quellen." in text
    assert "## Korrekturen" not in text
    assert text.startswith("# Lagebild vom 02.11.2026\n\n> Automatisch abgeleitet, "
                           "nicht geprüft. Erzeugt um 09:00 aus 4 Eingaben.")
    assert abl == {"K1": "STAND.md, 31.10.2026"}
    assert pruefen("## Stand\n- Nur Erfundenes. [E7]\n", eing, jetzt)[0] is None
    # E2 ist aelter als die Korrektur, E3 ohne Datum: beide loesen nichts ab
    abl2 = pruefen("## Stand\n- a [E2]\n## Korrekturen\nK1: abgelöst durch E2\n"
                   "K1: abgelöst durch E3\n", eing, jetzt)[1]
    assert abl2 == {}
    # Zu lang: Punkte vom Ende des laengsten Abschnitts fallen
    lang = "## Stand\n" + "".join(f"- Punkt {i} {'x' * 300}. [E1]\n" for i in range(60))
    gekuerzt = pruefen(lang, eing, jetzt)[0]
    assert len(gekuerzt) <= 10_000 and "Punkt 0 " in gekuerzt and "Punkt 59 " not in gekuerzt

    gefragt = {}
    async def fake(system, nutzer):
        gefragt["s"], gefragt["n"] = system, nutzer
        return roh
    aus = asyncio.run(erzeugen(eing, "# Lagebild vom 01.11.2026 alt", fake))
    assert "Mikroskop" in aus
    assert "[K1]" in gefragt["n"] and "[E3]" in gefragt["n"] and "alt" in gefragt["n"]
    assert "## Worum es geht" in gefragt["s"] and "## Korrekturen" in gefragt["s"]

    p = ablegen(text, eing, entfernt, jetzt, v)
    assert p.name == "2026-11-02T0900.md" and aktuell(v)["pfad"] == p
    log = json.loads((v / "2026-11-02T0900.eingaben.json").read_text())
    assert log["entfernt"] == 2 and [x["id"] for x in log["eingaben"]] == ["K1", "E1", "E2", "E3"]
    assert "text" not in log["eingaben"][0]


def lauf():
    st = {"belegt_von": [{"wer": "andere-anwendung", "art": "exklusiv"}]}
    assert lagebild.belegung_frei(st, "kihiwi-lagebild") is False
    assert lagebild.belegung_frei({"belegt_von": [{"wer": "kihiwi-waechter", "art": "mit"}]},
                                  "kihiwi-lagebild")
    assert lagebild.belegung_frei({"belegt_von": [{"wer": "kihiwi-lagebild",
                                                   "art": "exklusiv"}]}, "kihiwi-lagebild")
    assert lagebild.belegung_frei(None, "kihiwi-lagebild") is True   # nicht erreichbar

    # Fruehe Ausstiege von erneuern(), ohne Netz: die Anbindungen sind ersetzt.
    alt = (lagebild._modell_auf_server, lagebild._status, lagebild.belegen,
           lagebild.freigeben, lagebild._protokollieren)
    gerufen = []
    try:
        lagebild._protokollieren = lambda *a: None
        lagebild.freigeben = lambda wer: gerufen.append("frei")
        lagebild._modell_auf_server = lambda: "ein-anderes-modell"
        assert asyncio.run(lagebild.erneuern(abgleich=False, nacht=False)) == \
            lagebild.FALSCHES_MODELL
        lagebild._modell_auf_server = lambda: "qwen3.8-flash-next"
        lagebild._status = lambda: st
        lagebild.belegen = lambda *a: gerufen.append("belegt") or True
        assert asyncio.run(lagebild.erneuern(abgleich=False, nacht=False)) == lagebild.BELEGT
        assert "belegt" not in gerufen          # bei fremder Belegung nicht anmelden
    finally:
        (lagebild._modell_auf_server, lagebild._status, lagebild.belegen,
         lagebild.freigeben, lagebild._protokollieren) = alt


def unit():
    with tempfile.TemporaryDirectory() as t:
        ablage_und_korrekturen(Path(t))
    with tempfile.TemporaryDirectory() as t:
        eingaben(Path(t))
    with tempfile.TemporaryDirectory() as t:
        erzeugung(Path(t))
    lauf()


if __name__ == "__main__":
    unit()
    print("ok")
