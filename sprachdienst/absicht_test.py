"""Prueft die Absicht LAGE, die Lagebild-Ausloeser, Nachfragen und Zustimmung.

    .venv/bin/python -m sprachdienst.absicht_test

Braucht weder Modell noch Dienst: nur die Regeln in absicht.py.
"""
from .absicht import (Absicht, ausloeser, erkennen, ist_nachfrage,
                      lage_abschnitt, zustimmung)

LAGE = {
    "stand": ["Wie ist der Stand des Projekts?", "Wo stehen wir gerade?",
              "Wie weit sind wir insgesamt?"],
    "naechstes": ["Was steht als Nächstes an?", "Was sind die nächsten Schritte?",
                  "Was kommt als Nächstes?"],
    "probleme": ["Welche Probleme gibt es gerade?", "Wo hakt es im Moment?",
                 "Was sind aktuell die größten Herausforderungen?"],
    "vorzeigbar": ["Was kann ich vorzeigen?", "Was können wir herzeigen?",
                   "Was lässt sich vorführen?"],
}

# Wissensfragen, die einzelne Woerter mit den Lagefragen teilen -- "Stand",
# "Probleme", "steht", "zeigt" -- aber nach einer bestimmten Sache fragen.
NICHT_LAGE = ["Wie ist der Stand der Technik bei Glühkathoden?",
              "Welche Probleme gibt es mit der Hochspannung?",
              "Was steht im Messplan zur Kalibrierung?",
              "Wie hoch ist die Beschleunigungsspannung?",
              "Was ist ein Wehneltzylinder?",
              "Wo liegt die Anleitung für den Teststand?",
              "Welche Pumpe nutzen wir am Vakuumteststand?",
              "Was zeigt die Abbildung zur Strahlführung?",
              "Wann war die letzte Messung am Teststand?",
              "Welche Werte hat der Leuchtschirm gezeigt?"]


def unit():
    for art, saetze in LAGE.items():
        for s in saetze:
            assert erkennen(s) is Absicht.LAGE, s
            assert lage_abschnitt(s) == art, (s, lage_abschnitt(s))
    for s in NICHT_LAGE:
        assert erkennen(s) is not Absicht.LAGE, s
        assert lage_abschnitt(s) is None, (s, lage_abschnitt(s))

    # Bestehende Absichten gewinnen weiter
    assert erkennen("Wie spät ist es?") is Absicht.ZEIT
    assert erkennen("Zeig mir das Protokoll") is Absicht.PROTOKOLL

    # Nachfrage nach einer Lagebild-Antwort: Rueckbezug statt neuer Lagefrage
    assert ist_nachfrage("Was ist da genau das Problem?")
    assert ist_nachfrage("Erzähl mehr dazu")
    assert not ist_nachfrage("Und was steht als Nächstes an?")

    # Zustimmung zu einer Rueckfrage
    for s in ("Ja", "ja bitte", "mach das", "okay", "gerne"):
        assert zustimmung(s) is True, s
    for s in ("nein", "lieber nicht", "später"):
        assert zustimmung(s) is False, s
    assert zustimmung("Wie spät ist es?") is None

    # Ausloeser
    assert ausloeser("Lagebildkorrektur: der Teststand läuft wieder") == \
        ("lagebildkorrektur", "der Teststand läuft wieder")
    assert ausloeser("Lagebildkorrektur") == ("lagebildkorrektur", "")
    assert ausloeser("Lagebild-Korrektur zurücknehmen")[0] == "lagebild_zurueck"
    assert ausloeser("Lagebild erneuern")[0] == "lagebild_neu"
    assert ausloeser("Zeig mir das Lagebild")[0] == "lagebild"
    assert ausloeser("Recherche abbrechen")[0] == "abbruch"


if __name__ == "__main__":
    unit()
    print("ok")
