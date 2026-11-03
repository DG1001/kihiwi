"""Prueft die Modellwahl der Einbettung und dass sich Vektoren zweier Modelle
nie mischen.

    .venv/bin/python -m wissen.vektor_test

Braucht kein Einbettungsmodell: einbetten() ist durch Zufallsvektoren ersetzt.
"""
import json
import sqlite3
import tempfile
from pathlib import Path

import numpy as np

from . import bilder, index, vektor


GERUFEN = []


def _falsch_einbetten(texte, als_frage, modell=None):
    GERUFEN.append(modell)
    rng = np.random.default_rng(len(texte))
    V = rng.normal(size=(len(texte), vektor.DIM)).astype(np.float32)
    return V / np.linalg.norm(V, axis=1, keepdims=True)


def einstellung(t: Path):
    assert vektor.einstellung(t / "fehlt.json") == {}
    (t / "e.json").write_text(json.dumps({"_hinweis": "x", "modell": "probe/fein",
                                          "pfad": "~/modelle/fein", "bilder_sicher_ab": 0.9}))
    e = vektor.einstellung(t / "e.json")
    assert e["modell"] == "probe/fein" and "_hinweis" not in e
    assert e["pfad"].endswith("/modelle/fein") and not e["pfad"].startswith("~")
    assert vektor.bilder_modell({}) == vektor.VORGABE
    assert vektor.bilder_modell({"modell": "probe/fein"}) == vektor.VORGABE
    assert vektor.bilder_modell({"modell": "probe/fein", "bilder_modell": "probe/fein"}) == "probe/fein"
    # Kaputt ist laut: still auf das Vorgabemodell zurueckzufallen hiesse,
    # Fragen mit dem einen und Abschnitte mit dem anderen Modell zu vergleichen.
    (t / "kaputt.json").write_text("{modell:")
    try:
        vektor.einstellung(t / "kaputt.json")
        raise AssertionError("kaputte Einstellung muss auffallen")
    except ValueError:
        pass


def getrennt(t: Path):
    db = t / "index.db"
    c = sqlite3.connect(db)
    c.executescript(index.SCHEMA)
    c.executescript(bilder.SCHEMA)
    for i in range(3):
        index.dokument_setzen(c, "probe", f"probe:d{i}.md", f"D{i}", "", "", f"f{i}",
                              [("", f"Abschnitt Nummer {i}")])
    texte = [r[0] for r in c.execute("SELECT text FROM abschnitte ORDER BY rowid")]
    v = _falsch_einbetten(["x"], False)[0].tobytes()
    for i, (m) in enumerate(["alt", "alt", "neu"]):
        c.execute("INSERT INTO vektoren (hash, v, modell, stand) VALUES (?,?,?,?)",
                  (index.abschnitt_hash(texte[i]), v, m, "s"))
    c.commit(); c.close()

    alt = (index.lesen, index.verbinden, vektor.MODELL, vektor.einbetten, vektor._matrix,
           vektor.EINSTELLUNG, vektor.BILDER_MODELL)
    try:
        index.lesen = index.verbinden = lambda: sqlite3.connect(db)
        vektor.MODELL, vektor.einbetten, vektor._matrix = "neu", _falsch_einbetten, None
        # Bilder mit eigenem Modell: die Feinabstimmung auf Fliesstext machte
        # die Stichwort-Eintraege der Bilder schlechter (5 statt 7 von 7)
        vektor.BILDER_MODELL = "bildmodell"
        # Nur Vektoren des eingestellten Modells zaehlen und werden durchsucht
        assert vektor.bestand() == (1, 3), vektor.bestand()
        V, ids = vektor._matrix_laden()
        assert len(ids) == 1 and V.shape == (1, vektor.DIM)
        # Die des anderen Modells gelten als fehlend und werden neu eingebettet
        e = vektor.nachtragen()
        assert e["erledigt"] == 2, e
        assert vektor.bestand() == (3, 3)
        c = sqlite3.connect(db)
        assert {r[0] for r in c.execute("SELECT modell FROM vektoren")} == {"neu"}

        # Bilder: Eintraege eines anderen Modells sucht man nicht ...
        for pfad, m in (("probe:a.png", "neu"), ("probe:b.png", "bildmodell")):
            c.execute("INSERT INTO bilder (pfad,quelle,rel,unterschrift,text,herkunft,stand,v,modell) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (pfad, "probe", pfad[6:], "", f"Bild: {pfad}",
                                                      "", "s", v, m))
        c.commit()
        GERUFEN.clear()
        assert [x["rel"] for x in bilder.suchen("Bild", c=c)] == ["b.png"]
        assert GERUFEN == ["bildmodell"], GERUFEN          # Frage mit dem Bildmodell
        # ... und die Schwelle fuer "sicher" gehoert zum Modell
        vektor.EINSTELLUNG = {"bilder_sicher_ab": 0.95}
        assert bilder.sicher_ab() == 0.95
        vektor.EINSTELLUNG = {}
        assert bilder.sicher_ab() == bilder.SICHER_AB
        c.close()

        # ... und ein Katalogaufbau bettet sie neu ein, auch bei gleichem Text
        repos = t / "repos"
        (repos / "probe").mkdir(parents=True)
        (repos / "probe" / "a.png").write_bytes(b"\x89PNG")
        c = sqlite3.connect(db)
        c.execute("DELETE FROM bilder"); c.commit()
        e1 = bilder.aufbauen(repos=repos, quellen=[{"name": "probe"}], c=c)
        assert e1["neu"] == 1
        assert bilder.aufbauen(repos=repos, quellen=[{"name": "probe"}], c=c)["neu"] == 0
        c.execute("UPDATE bilder SET modell='neu'"); c.commit()
        assert bilder.aufbauen(repos=repos, quellen=[{"name": "probe"}], c=c)["neu"] == 1
        assert {r[0] for r in c.execute("SELECT modell FROM bilder")} == {"bildmodell"}
        c.close()
    finally:
        (index.lesen, index.verbinden, vektor.MODELL, vektor.einbetten, vektor._matrix,
         vektor.EINSTELLUNG, vektor.BILDER_MODELL) = alt


def unit():
    with tempfile.TemporaryDirectory() as t:
        einstellung(Path(t))
    with tempfile.TemporaryDirectory() as t:
        getrennt(Path(t))


if __name__ == "__main__":
    unit()
    print("ok")
