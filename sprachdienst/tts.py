"""Piper als dauerhaft geladene Stimme.

Die Stimme wird EINMAL geladen (595 ms) und bleibt im Speicher. Als
CLI-Prozess je Satz kostet dasselbe 800 ms statt 110 ms -- fast alles davon
Python- und ONNX-Start.

Piper laeuft bewusst auf der CPU: der GX10 hat 20 Kerne, die weitgehend
leerlaufen, und der Speicher ist die knappe Ressource.
"""
import asyncio, threading
import numpy as np
from . import konfig

_stimme = None
_sperre = threading.Lock()

# Feste Saetze einmal rendern und behalten. Sie machen den Anfang fast jeder
# Antwort aus ("Ich schaue in den Unterlagen nach"), und gerendert kosten sie
# null statt 343 ms -- damit traegt die bessere Stimme sich selbst: der erste
# Ton kommt frueher als vorher, nur die variable Antwort dahinter ist langsamer.
_vorrat: dict[str, list] = {}


def vorrendern(saetze) -> int:
    """Rendert feste Saetze in den Vorrat. Gibt die Anzahl zurueck."""
    v = laden()
    for satz in saetze:
        if satz in _vorrat:
            continue
        _vorrat[satz] = [(c.audio_int16_bytes, c.sample_rate)
                         for c in v.synthesize(satz)]
    return len(_vorrat)


def laden():
    global _stimme
    with _sperre:
        if _stimme is None:
            from piper import PiperVoice
            _stimme = PiperVoice.load(str(konfig.STIMME))
    return _stimme


async def sprich(text: str):
    """Liefert (pcm_bytes, rate) stueckweise, sobald sie fertig sind.

    Stueckweise ist der Punkt: der erste Teil geht raus, waehrend der Rest
    noch rechnet. Der Nutzer hoert nach ~110 ms etwas, nicht nach dem ganzen
    Satz.

    Der Arbeitsthread SCHIEBT in eine asyncio.Queue, statt dass die Schleife
    ihn per to_thread(q.get) abfragt. Andernfalls bleibt der Thread auf einer
    leeren Queue haengen, sobald der Verbraucher vorzeitig aufhoert -- und
    asyncio.run wartet beim Beenden 120 Sekunden auf genau solche Threads.
    """
    fertig = _vorrat.get(text)
    if fertig is not None:
        for stueck in fertig:
            yield stueck
        return

    schleife = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def lauf():
        try:
            for stueck in laden().synthesize(text):
                schleife.call_soon_threadsafe(
                    q.put_nowait, (stueck.audio_int16_bytes, stueck.sample_rate))
        except Exception:                       # pragma: no cover
            pass
        finally:
            schleife.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=lauf, daemon=True).start()
    while True:
        stueck = await q.get()
        if stueck is None:
            return
        yield stueck
