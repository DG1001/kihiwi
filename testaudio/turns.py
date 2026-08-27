"""Baut Sprech-Turns mit kontrollierter Pause in der Mitte.

Grundwahrheit: der Turn endet am Ende von Teil B. Eine Pause der Laenge P
in der Mitte ist die Falle -- ein naiver Endpointer mit Schwelle T < P
schneidet dort ab, mitten im Satz.
"""
import subprocess, sys, os
sys.path.insert(0, '.')
from piper import PiperVoice
import numpy as np, wave

STIMME = "voices/de_DE-thorsten-medium.onnx"
PAAR = [
    ("Der Durchsatz lag bei",                    "achtundsiebzig Token pro Sekunde."),
    ("Kannst du den Sweep über die Lernrate",    "nochmal laufen lassen?"),
    ("Schreib ins Protokoll:",                   "Baseline reproduziert."),
]
PAUSEN = [200, 400, 600, 800]     # ms
NACHLAUF = 2000                   # ms Stille am Ende

v = PiperVoice.load(STIMME)

def sprich(text):
    teile = [c.audio_int16_bytes for c in v.synthesize(text)]
    roh = np.frombuffer(b"".join(teile), dtype=np.int16)
    return roh, 22050

def resample_16k(x, sr):
    import math
    n = int(len(x) * 16000 / sr)
    return np.interp(np.linspace(0, len(x)-1, n), np.arange(len(x)), x).astype(np.int16)

os.makedirs("testaudio/turns", exist_ok=True)
meta = []
for i, (a, b) in enumerate(PAAR, 1):
    xa, sr = sprich(a); xb, _ = sprich(b)
    xa, xb = resample_16k(xa, sr), resample_16k(xb, sr)
    for p in PAUSEN:
        pause = np.zeros(int(16000*p/1000), dtype=np.int16)
        nach  = np.zeros(int(16000*NACHLAUF/1000), dtype=np.int16)
        vor   = np.zeros(int(16000*0.3), dtype=np.int16)
        y = np.concatenate([vor, xa, pause, xb, nach])
        name = f"testaudio/turns/t{i}_p{p}.wav"
        with wave.open(name, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(y.tobytes())
        # Grundwahrheit in ms ab Dateianfang
        ende_a = (len(vor)+len(xa))/16.0
        start_b = (len(vor)+len(xa)+len(pause))/16.0
        ende_b = (len(vor)+len(xa)+len(pause)+len(xb))/16.0
        meta.append(dict(datei=name, pause=p, ende_a=ende_a, start_b=start_b, ende_b=ende_b,
                         text_a=a, text_b=b))
import json; json.dump(meta, open("testaudio/turns/meta.json","w"), indent=1, ensure_ascii=False)
print(f"{len(meta)} Turns gebaut")
for m in meta[:2]:
    print(f"  {m['datei']}: A endet {m['ende_a']:.0f} ms, B startet {m['start_b']:.0f} ms, B endet {m['ende_b']:.0f} ms")
