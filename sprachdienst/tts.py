"""Piper als dauerhaft geladene Stimme.

Die Stimme wird EINMAL geladen (595 ms) und bleibt im Speicher. Als
CLI-Prozess je Satz kostet dasselbe 800 ms statt 110 ms -- fast alles davon
Python- und ONNX-Start.

Piper laeuft bewusst auf der CPU: der GX10 hat 20 Kerne, die weitgehend
leerlaufen, und der Speicher ist die knappe Ressource.
"""
import asyncio, queue, threading
import numpy as np
from . import konfig

_stimme = None
_sperre = threading.Lock()


def laden():
    global _stimme
    with _sperre:
        if _stimme is None:
            from piper import PiperVoice
            _stimme = PiperVoice.load(str(konfig.STIMME))
    return _stimme


def _synth(text: str, q: queue.Queue):
    try:
        for stueck in laden().synthesize(text):
            q.put((stueck.audio_int16_bytes, stueck.sample_rate))
    except Exception as e:                     # pragma: no cover
        q.put(("fehler", repr(e)))
    finally:
        q.put(None)


async def sprich(text: str):
    """Liefert (pcm_bytes, rate) stueckweise, sobald sie fertig sind.

    Stueckweise ist der Punkt: der erste Teil geht raus, waehrend der Rest
    noch rechnet. Der Nutzer hoert nach ~110 ms etwas, nicht nach dem ganzen
    Satz.
    """
    q: queue.Queue = queue.Queue()
    threading.Thread(target=_synth, args=(text, q), daemon=True).start()
    while True:
        stueck = await asyncio.to_thread(q.get)
        if stueck is None:
            return
        if stueck[0] == "fehler":
            return
        yield stueck
