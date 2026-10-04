"""Prueft Katalog, Suche und Auslieferungsgrenze der Bildanzeige.

    .venv/bin/python -m wissen.bilder_test

Der erste Teil baut ein kleines Repo im Temp-Ordner und braucht nur das
Einbettungsmodell. Der zweite laeuft gegen `wissen/repos` mit den Fragen aus
`wissen/bilder_fragen.json` und wird uebersprungen, wenn eines von beiden fehlt.
"""
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from . import bilder, einlesen


def _repo(wurzel: Path):
    (wurzel / "optik").mkdir(parents=True)
    (wurzel / "optik" / "messaufbau.png").write_bytes(b"\x89PNG")
    (wurzel / "optik" / "netzteil_schaltbild.svg").write_text("<svg/>")
    (wurzel / "plan" / "bilder").mkdir(parents=True)
    (wurzel / "plan" / "bilder" / "a1.jpg").write_bytes(b"\xff\xd8")
    (wurzel / "plan" / "Zeitplan.pdf").write_bytes(b"%PDF-1.4")
    (wurzel / "plan" / "notiz.txt").write_text("kein Bild")
    (wurzel / "plan" / "bericht.md").write_text(
        "Text\n\n![kurz](bilder/a1.jpg)\n\n"
        "![**Zeitplan** des Projekts mit drei Varianten](bilder/a1.jpg)\n"
        "![draussen](../../geheim.png)\n![netz](https://example.org/x.png)\n")
    subprocess.run(["git", "init", "-q", str(wurzel)], check=True)


def unit():
    with tempfile.TemporaryDirectory() as t:
        repos = Path(t) / "repos"
        _repo(repos / "probe")
        (Path(t) / "geheim.png").write_bytes(b"\x89PNG")
        (repos / "probe" / "optik" / "link.png").symlink_to(Path(t) / "geheim.png")

        # Woerter aus dem Pfad
        assert bilder._woerter("optik/feld/messung_last-spannung.png") == \
            "messung last spannung, feld, optik"

        # Unterschriften: laengste gewinnt, Markdown raus, nichts von draussen
        u = bilder.unterschriften(repos / "probe")
        assert u == {"plan/bilder/a1.jpg":
                     "Zeitplan des Projekts mit drei Varianten"}, u

        # Katalog
        c = sqlite3.connect(Path(t) / "i.db")
        q = [{"name": "probe", "art": "git", "web": "https://example.org/r"}]
        e = bilder.aufbauen(repos, q, c)
        assert e["bilder"] == 5 and e["neu"] == 5, e      # 3 Bilder, Link, PDF
        assert bilder.anzahl(c) == 5
        assert bilder.aufbauen(repos, q, c)["neu"] == 0   # nichts doppelt
        (repos / "probe" / "optik" / "messaufbau.png").unlink()
        e = bilder.aufbauen(repos, q, c)
        assert e["entfernt"] == 1 and bilder.anzahl(c) == 4, e

        # Suche: Unterschrift, Dateiname, PDF
        def erster(frage):
            return bilder.suchen(frage, c=c)[0]
        assert erster("Zeitplan mit Varianten")["rel"] == "plan/bilder/a1.jpg"
        assert erster("Schaltplan vom Netzteil")["rel"] == \
            "optik/netzteil_schaltbild.svg"
        z = erster("Zeitplan als PDF")
        assert z["rel"] == "plan/Zeitplan.pdf" and z["pdf"], z
        assert z["herkunft"] == "https://example.org/r/blob/HEAD/plan/Zeitplan.pdf"
        assert bilder.suchen("", c=c) == []
        c.close()

        # Auslieferungsgrenze
        d = lambda rel, quelle="probe": bilder.datei(quelle, rel, repos)
        assert d("plan/bilder/a1.jpg") is not None
        assert d("plan/Zeitplan.pdf") is not None
        assert d("plan/notiz.txt") is None                 # keine zeigbare Endung
        assert d("../../geheim.png") is None               # hinaus
        assert d("optik/../../../geheim.png") is None
        assert d("optik/link.png") is None                 # Symlink nach draussen
        assert d(".git/HEAD") is None
        assert d("plan/fehlt.png") is None
        assert d("plan/bilder/a1.jpg", quelle="..") is None
        assert d("plan/bilder/a1.jpg", quelle="probe/..") is None
        assert d("a1.jpg", quelle="fehlt") is None
    print("unit: ok")


# Was am Laborrechner gefragt werden duerfte -> was oben stehen muss.
# NICHT im Repo: die Fragen nennen Dateien aus den eigenen, privaten Quellen,
# und dieses Repo ist oeffentlich. Aufbau der Datei:
#     [["Frage", "Teil des erwarteten Pfads"], ...]
FRAGEN = Path(__file__).with_name("bilder_fragen.json")
UNSINN = ["Rezept für Apfelkuchen", "Fahrplan der Straßenbahn"]


def echt():
    if not any(einlesen.REPOS.glob("*/.git")) or not FRAGEN.exists():
        print(f"echt: übersprungen (keine Repos oder {FRAGEN.name} fehlt)")
        return
    fragen = json.loads(FRAGEN.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as t:
        c = sqlite3.connect(Path(t) / "i.db")
        e = bilder.aufbauen(c=c)
        print(f"echt: {e['bilder']} Einträge, {e['sekunden']:.1f} s")
        fehler = 0
        for frage, soll in fragen:
            tr = bilder.suchen(frage, c=c)
            gut = soll in tr[0]["rel"] and tr[0]["sicher"]
            fehler += not gut
            print(f"  {'ok ' if gut else 'FEHLT'} {tr[0]['punkte']:.3f} "
                  f"{frage!r} -> {tr[0]['rel']}")
        for frage in UNSINN:
            tr = bilder.suchen(frage, c=c)
            gut = not tr[0]["sicher"]
            fehler += not gut
            print(f"  {'ok ' if gut else 'ZU SICHER'} {tr[0]['punkte']:.3f} "
                  f"{frage!r} -> {tr[0]['rel']}")
        c.close()
    assert not fehler, f"{fehler} Anfragen daneben"
    print("echt: ok")


if __name__ == "__main__":
    unit()
    echt()
