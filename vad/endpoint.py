"""Naiver Endpointer: Sprache erkennen, dann T ms Stille abwarten."""
import sys, wave, numpy as np
sys.path.insert(0, '.')
from vad.silero import Vad, FENSTER, RATE

BLOCK_MS = FENSTER * 1000 // RATE      # 32

def bloecke(wav):
    with wave.open(wav) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    x = x.astype(np.float32) / 32768.0
    for i in range(0, len(x) - FENSTER + 1, FENSTER):
        yield x[i:i+FENSTER]

def endpoint(wav, T_ms, ein=0.5, aus=0.35, min_sprache_ms=160, vad=None):
    """Gibt (ms_des_endpoints, ms_des_sprachendes) zurueck, oder (None, ...)."""
    v = vad or Vad(); v.reset()
    sprach = False; sprach_ms = 0; stille_ms = 0; letzte_sprache = None
    for n, b in enumerate(bloecke(wav)):
        p = v.block(b)
        jetzt = (n+1) * BLOCK_MS
        aktiv = p > (aus if sprach else ein)
        if aktiv:
            sprach_ms += BLOCK_MS; stille_ms = 0
            if sprach_ms >= min_sprache_ms: sprach = True
            letzte_sprache = jetzt
        else:
            sprach_ms = 0
            if sprach:
                stille_ms += BLOCK_MS
                if stille_ms >= T_ms:
                    return jetzt, letzte_sprache
    return None, letzte_sprache
