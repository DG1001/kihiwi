"""Prueft die Ablage der Vorleseseite (Messung der Spracherkennung).

    .venv/bin/python -m sprachdienst.vorlesen_test
"""
import io
import json
import tempfile
import wave
from pathlib import Path

from . import vorlesen


def _wav(sekunden: float = 0.5, rate: int = 16000) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(sekunden * rate))
    return b.getvalue()


def unit():
    with tempfile.TemporaryDirectory() as t:
        v = Path(t)
        # Ohne Satzliste: leer, kein Fehler
        assert vorlesen.saetze(v) == []
        (v / "saetze.json").write_text(json.dumps({"saetze": [
            {"nr": 1, "satz": "Erster Satz.", "begriffe": ["Satz"]},
            {"nr": 2, "satz": "Zweiter Satz.", "begriffe": []}]}), encoding="utf-8")
        # Die Seite bekommt nur Nummer und Satz, nicht die erwarteten Begriffe
        assert vorlesen.saetze(v) == [{"nr": 1, "satz": "Erster Satz."},
                                      {"nr": 2, "satz": "Zweiter Satz."}]
        assert vorlesen.fertig(v, "sprecher1") == []

        p = vorlesen.ablegen(v, "sprecher1", 2, _wav())
        assert p == v / "aufnahmen" / "sprecher1" / "02.wav" and p.read_bytes() == _wav()
        assert vorlesen.fertig(v, "sprecher1") == [2] and vorlesen.fertig(v, "sprecher2") == []
        # Nochmal aufgenommen: ersetzt
        vorlesen.ablegen(v, "sprecher1", 2, _wav(1.0))
        assert p.read_bytes() == _wav(1.0)

        # Abgelehnt: fremde Namen (Pfad!), unbekannte Nummern, kein WAV,
        # falsches Format, zu kurz, zu lang
        for sprecher, nr, daten in [("../x", 1, _wav()), ("", 1, _wav()), ("Max Muster", 1, _wav()),
                                    ("sprecher1", 3, _wav()), ("sprecher1", 0, _wav()),
                                    ("sprecher1", 1, b"kein wav"), ("sprecher1", 1, _wav(rate=44100)),
                                    ("sprecher1", 1, _wav(0.05)), ("sprecher1", 1, _wav(61))]:
            try:
                vorlesen.ablegen(v, sprecher, nr, daten)
                raise AssertionError(f"haette abgelehnt werden muessen: {sprecher!r} {nr}")
            except ValueError:
                pass
        assert sorted(x.name for x in (v / "aufnahmen").iterdir()) == ["sprecher1"]
        assert vorlesen.fertig(v, "sprecher1") == [2]


if __name__ == "__main__":
    unit()
    print("ok")
