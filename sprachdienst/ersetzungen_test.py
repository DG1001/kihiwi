"""Prueft die feste Ersetzungsliste fuer wiederkehrende Verhoerer.

    .venv/bin/python -m sprachdienst.ersetzungen_test
"""
import tempfile
from pathlib import Path

from . import stt


def unit():
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "ersetzungen.txt"
        p.write_text("# Kommentar\n"
                     "Rückstrahlelektroden => Rückstreuelektronen\n"
                     "Petra Schale => Petrischale\n"
                     "\n"
                     "Kamskan => KamScan\n"
                     "KamScan 4 4 => KamScan 44\n"
                     "kaputte Zeile ohne Pfeil\n", encoding="utf-8")
        regeln = stt.ersetzungen(p)
        assert len(regeln) == 4, regeln
        e = lambda s: stt.ersetzen(s, regeln)
        # Gross-/Kleinschreibung egal, Ersatz wortgetreu
        assert e("Die rückstrahlelektroden zeigen") == "Die Rückstreuelektronen zeigen"
        # Leerzeichen und Bindestrich zwischen den Teilen gleichwertig
        assert e("die Petra-Schale") == "die Petrischale"
        assert e("die Petra Schale und") == "die Petrischale und"
        # Nur ganze Woerter: der Vorname allein bleibt
        assert e("Petra Meier kommt") == "Petra Meier kommt"
        assert e("Kamskans") == "Kamskans"
        # Regeln der Reihe nach: erst der Name, dann die Zahl dahinter
        assert e("am Kamskan 4-4 durch") == "am KamScan 44 durch"
        assert e("") == ""
        # Ohne Datei: keine Regeln, Text unveraendert
        assert stt.ersetzungen(Path(t) / "fehlt.txt") == []


if __name__ == "__main__":
    unit()
    print("ok")
