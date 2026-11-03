"""Prueft den Vergleich von Erwartung und Transkript der Sprachmessung.

    .venv/bin/python -m sprachdienst.sprachmessung_test
"""
from .sprachmessung import getroffen, wortfehler


def unit():
    # Schreibweise eines Kompositums zaehlt nicht, die Buchstaben schon
    assert getroffen("Siliziumnitrid-Fenster", "Prüfe das Siliziumnitrid Fenster bitte.")
    assert getroffen("Siliziumnitrid-Fenster", "das siliziumnitridfenster")
    assert not getroffen("Siliziumnitrid-Fenster", "das Silizium mit Tretfenster")
    assert getroffen("KamScan 44", "am Kamscan 44 durch")
    assert not getroffen("Petrischale", "die Petra-Schale")
    assert getroffen("Rückstreuelektronen", "Die Rückstreuelektronen zeigen")
    # Wortfehlerrate: Satzzeichen und Gross-/Kleinschreibung zaehlen nicht
    assert wortfehler("Kiwi, starte die Messung.", "kiwi starte die Messung") == 0.0
    assert wortfehler("eins zwei drei vier", "eins drei vier") == 0.25
    assert wortfehler("eins zwei", "eins zwei drei") == 0.5


if __name__ == "__main__":
    unit()
    print("ok")
