"""Sprachmessung: Wie gut erkennt die Spracherkennung die Fachbegriffe?

    .venv/bin/python -m sprachdienst.sprachmessung <sprecher>

Liest die Aufnahmen der Vorleseseite (zustand/sprachmessung/aufnahmen/<sprecher>)
und schickt jede dreimal an den whisper-server, wie der Dienst es tut:
  ohne    -- ohne Vokabelliste
  kurz    -- erster Durchgang im Dialog (wenige Begriffe plus Aktivierungswort)
  voll    -- zweiter Durchgang nach dem Ansprechen (nachschaerfen), das ist
             der Text, mit dem Kiwi tatsaechlich arbeitet
Gezaehlt wird, ob jeder erwartete Fachbegriff im Transkript steht, getrennt
nach Begriffen aus der Vokabelliste und anderen. Die Ergebnisse je Satz gehen
nach zustand/sprachmessung/ergebnis-<sprecher>.json (nicht im Repo).
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import unicodedata
import wave
from pathlib import Path

import numpy as np

from . import stt, vorlesen


def _normal(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[^0-9a-zäöüß]+", "", s)


def getroffen(begriff: str, transkript: str) -> bool:
    """Steht der Begriff im Transkript? Bindestriche, Leerzeichen und
    Gross-/Kleinschreibung zaehlen nicht ("Siliziumnitrid Fenster" trifft
    "Siliziumnitrid-Fenster"), jeder Buchstabe schon."""
    return _normal(begriff) in _normal(transkript)


def _woerter(s: str) -> list[str]:
    return [w for w in (_normal(x) for x in s.split()) if w]


def wortfehler(soll: str, ist: str) -> float:
    """Wortfehlerrate (Levenshtein ueber Woerter) bezogen auf den Sollsatz."""
    a, b = _woerter(soll), _woerter(ist)
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        vorher, d[0] = d[0], i
        for j in range(1, len(b) + 1):
            vorher, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, vorher + (a[i - 1] != b[j - 1]))
    return d[len(b)] / max(1, len(a))


def _laden(p: Path) -> np.ndarray:
    with wave.open(str(p)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


async def messen(sprecher: str, verz: Path = vorlesen.VERZ) -> dict:
    saetze = {s["nr"]: s for s in json.loads((verz / "saetze.json").read_text(encoding="utf-8"))["saetze"]}
    vokabular = {_normal(b) for b in stt.begriffe()}
    zeilen = []
    for nr in vorlesen.fertig(verz, sprecher):
        s, samples = saetze[nr], _laden(verz / "aufnahmen" / sprecher / f"{nr:02d}.wav")
        ergebnis = {"nr": nr, "satz": s["satz"], "begriffe": s["begriffe"]}
        for art, kwargs in (("ohne", {"mit_vokabular": False}), ("kurz", {"kurz": True}),
                            ("voll", {"kurz": False})):
            t = await stt.transkribiere(samples, **kwargs)
            ergebnis[art] = {"text": t, "wortfehler": round(wortfehler(s["satz"], t), 3),
                             "treffer": {b: getroffen(b, t) for b in s["begriffe"]}}
        ergebnis["im_vokabular"] = {b: _normal(b) in vokabular for b in s["begriffe"]}
        zeilen.append(ergebnis)
    (verz / f"ergebnis-{sprecher}.json").write_text(
        json.dumps(zeilen, ensure_ascii=False, indent=1), encoding="utf-8")
    return _zusammenfassen(zeilen)


def _zusammenfassen(zeilen: list[dict]) -> dict:
    aus = {}
    for art in ("ohne", "kurz", "voll"):
        z = {"vokabular": [0, 0], "andere": [0, 0]}
        for e in zeilen:
            for b, ok in e[art]["treffer"].items():
                k = "vokabular" if e["im_vokabular"][b] else "andere"
                z[k][0] += ok; z[k][1] += 1
        wf = sorted(e[art]["wortfehler"] for e in zeilen)
        aus[art] = {**{k: f"{v[0]}/{v[1]}" for k, v in z.items()},
                    "wortfehler_median": wf[len(wf) // 2] if wf else None,
                    "saetze": len(zeilen)}
    return aus


if __name__ == "__main__":
    print(json.dumps(asyncio.run(messen(sys.argv[1])), ensure_ascii=False, indent=1))
