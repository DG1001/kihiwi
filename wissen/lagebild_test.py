"""Prueft das Lagebild: Ablage, Korrekturen, Kern fuer den Prompt, Lagefragen.

    .venv/bin/python -m wissen.lagebild_test

Braucht weder Modell noch Dienst noch den echten Index: alles laeuft in einem
Temp-Ordner.
"""
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from .lagebild import (abschnitt_fuer, aktuell, ganzes_lagebild, korrektur_hinzu,
                       korrektur_zuruecknehmen, korrekturen_abloesen, lage_frage,
                       offene_korrekturen, prompt_block)

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


def unit():
    with tempfile.TemporaryDirectory() as t:
        ablage_und_korrekturen(Path(t))


if __name__ == "__main__":
    unit()
    print("ok")
