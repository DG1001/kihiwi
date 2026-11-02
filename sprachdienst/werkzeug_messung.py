"""Misst, wie oft das Modell in der Werkzeugrunde ein Werkzeug aufruft --
ohne und mit dem Kern des Lagebilds im Prompt.

    .venv/bin/python -m sprachdienst.werkzeug_messung [--runden 3]

Fragt das Modell direkt, mit genau dem Prompt und den Werkzeugen, die der
Sprachdienst je Absicht verwendet. Ueber den Dienst liefe ein Rechercheauftrag
wirklich los und belegte das Modell fuer Minuten; hier wird nur gezaehlt, ob
ein Aufruf kommt. Grundlage fuer konfig.LAGEBILD_IN_WERKZEUGRUNDE: ein langer
Prompt hat Werkzeugaufrufe schon einmal unterdrueckt (0 von 3 gegen 3 von 3).
"""
import json
import sys
from pathlib import Path

from . import absicht as absicht_modul
from . import gateway, konfig, llm

FRAGEN = Path(__file__).with_name("werkzeug_fragen.json")


def messen(runden: int, mit_kern: bool) -> dict[str, list[int]]:
    konfig.LAGEBILD_IN_WERKZEUGRUNDE = mit_kern
    ergebnis: dict[str, list[int]] = {}
    for f in json.loads(FRAGEN.read_text(encoding="utf-8"))["fragen"]:
        wofuer = absicht_modul.Absicht(f["absicht"])
        erkannt = absicht_modul.erkennen(f["frage"])
        if erkannt is not wofuer:
            print(f"  Hinweis: {f['frage']!r} wird als {erkannt.value} erkannt, "
                  f"nicht als {wofuer.value}")
        # system_prompt braucht keine Sitzung, nur den Zustand des Dienstes.
        system = gateway.Sitzung.system_prompt(None, wofuer)
        namen = absicht_modul.WERKZEUGE_JE_ABSICHT.get(wofuer, [])
        werkzeuge = [w for w in gateway.WERKZEUGE if w["function"]["name"] in namen]
        nachrichten = [{"role": "system", "content": system},
                       {"role": "user", "content": f["frage"]}]
        treffer = 0
        for _ in range(runden):
            _, rufe = llm._einmal(nachrichten, 80, 0.3, werkzeuge)
            treffer += bool(rufe)
        ergebnis.setdefault(f["absicht"], [0, 0])
        ergebnis[f["absicht"]][0] += treffer
        ergebnis[f["absicht"]][1] += runden
    return ergebnis


def main(argv):
    runden = int(argv[argv.index("--runden") + 1]) if "--runden" in argv else 3
    for mit in (False, True):
        e = messen(runden, mit)
        gesamt = [sum(v[0] for v in e.values()), sum(v[1] for v in e.values())]
        teile = ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in e.items())
        print(f"{'mit Kern ' if mit else 'ohne Kern'}: {gesamt[0]}/{gesamt[1]}  ({teile})")


if __name__ == "__main__":
    main(sys.argv[1:])
