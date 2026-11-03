"""Vorleseseite: bekannte Saetze einsprechen, um die Spracherkennung zu messen.

Die Saetze (mit den erwarteten Fachbegriffen) liegen in
zustand/sprachmessung/saetze.json, die Aufnahmen daneben unter
aufnahmen/<sprecher>/<nr>.wav. Beides nicht im Repo: die Saetze stammen aus den
Unterlagen, die Aufnahmen sind Stimmen.

Aufgenommen wird im Browser wie in klient.html (gleiche Mikrofoneinstellungen,
16 kHz mono) -- gemessen werden soll der Weg, den Kiwi wirklich hoert.
"""
from __future__ import annotations

import io
import json
import re
import wave
from pathlib import Path

from . import konfig

VERZ = konfig.WURZEL / "zustand" / "sprachmessung"
_SPRECHER = re.compile(r"^[a-z0-9_-]{1,32}$")
MIN_S, MAX_S = 0.3, 60.0


def _alle(verz: Path) -> list[dict]:
    try:
        return json.loads((verz / "saetze.json").read_text(encoding="utf-8"))["saetze"]
    except FileNotFoundError:
        return []


def saetze(verz: Path = VERZ) -> list[dict]:
    """Nummer und Satz fuer die Seite. Die erwarteten Begriffe bleiben auf dem
    Server: wer sie sieht, betont sie anders."""
    return [{"nr": s["nr"], "satz": s["satz"]} for s in _alle(verz)]


def _ordner(verz: Path, sprecher: str) -> Path:
    if not _SPRECHER.match(sprecher or ""):
        raise ValueError(f"Sprechername ungueltig: {sprecher!r} (nur a-z, 0-9, _ und -)")
    return verz / "aufnahmen" / sprecher


def fertig(verz: Path, sprecher: str) -> list[int]:
    o = _ordner(verz, sprecher)
    if not o.is_dir():
        return []
    return sorted(int(p.stem) for p in o.glob("[0-9][0-9].wav"))


def ablegen(verz: Path, sprecher: str, nr: int, daten: bytes) -> Path:
    """Prueft und speichert eine Aufnahme; ersetzt eine fruehere zum selben Satz."""
    o = _ordner(verz, sprecher)
    if nr not in {s["nr"] for s in _alle(verz)}:
        raise ValueError(f"Satz {nr} gibt es nicht")
    try:
        with wave.open(io.BytesIO(daten)) as w:
            kanaele, breite, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError) as e:
        raise ValueError(f"kein WAV: {e}") from None
    if (kanaele, breite, rate) != (1, 2, 16000):
        raise ValueError(f"erwartet 16 kHz mono 16 bit, bekommen {rate} Hz, {kanaele} Kanal, {8*breite} bit")
    if not MIN_S <= n / rate <= MAX_S:
        raise ValueError(f"Laenge {n / rate:.1f} s ausserhalb {MIN_S}-{MAX_S} s")
    o.mkdir(parents=True, exist_ok=True)
    p = o / f"{nr:02d}.wav"
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(daten)
    tmp.replace(p)
    return p
