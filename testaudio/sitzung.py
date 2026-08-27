"""Erzeugt eine synthetische Laborsitzung als Aufnahme.

Ersatz fuer eine echte Aufzeichnung, solange die Client-Hardware fehlt --
mehrere Aeusserungen mit realistischen Pausen dazwischen, damit die
VAD-Zerlegung des Dokumentationspfads etwas zu tun bekommt.
"""
import sys, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, '.')
from piper import PiperVoice

SAETZE = [
    ("So, wir fangen an. Heute testen wir den Layer-Norm-Patch im Attention-Head.", 1.2),
    ("Baseline vom Montag lag bei achtundsiebzig Token pro Sekunde.", 2.0),
    ("Ich setze die Batch-Size auf zweiunddreißig und den Kontext auf zweiunddreißig K.", 1.5),
    ("Erster Durchlauf: vierundachtzig Komma zwei Token pro Sekunde.", 2.5),
    ("Das sind knapp acht Prozent über der Baseline.", 1.0),
    ("Der Dataloader hat aber immer noch einen Bottleneck, die GPU wartet zwischendurch.", 2.2),
    ("Wir sollten das Preprocessing in einen eigenen Worker ziehen.", 1.8),
    ("Zweiter Durchlauf mit Early-Stopping: bricht nach sechs Epochen ab.", 2.4),
    ("Validation Loss geht ab Epoche vier nicht mehr runter.", 1.6),
    ("Entscheidung: wir behalten den Patch und kümmern uns nächste Woche um den Dataloader.", 2.0),
    ("Offen bleibt, ob das auch bei größerem Kontext hält. Das muss noch jemand prüfen.", 1.5),
]

STIMME = "voices/de_DE-thorsten-medium.onnx"
ZIEL = Path(sys.argv[1] if len(sys.argv) > 1 else "aufnahmen/TEST-sitzung/090000.wav")

v = PiperVoice.load(STIMME)

def sprich(text):
    teile = [c.audio_int16_bytes for c in v.synthesize(text)]
    x = np.frombuffer(b"".join(teile), dtype=np.int16)
    n = int(len(x) * 16000 / 22050)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16)

stuecke = [np.zeros(16000, dtype=np.int16)]          # eine Sekunde Vorlauf
for text, pause in SAETZE:
    stuecke.append(sprich(text))
    stuecke.append(np.zeros(int(16000 * pause), dtype=np.int16))
stuecke.append(np.zeros(16000 * 2, dtype=np.int16))  # Nachlauf

y = np.concatenate(stuecke)
ZIEL.parent.mkdir(parents=True, exist_ok=True)
with wave.open(str(ZIEL), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(y.tobytes())
ZIEL.with_suffix(".json").write_text(
    '{\n "begonnen_utc": "2026-08-27T09:00:00+00:00",\n "rate": 16000,\n'
    ' "kanaele": 1,\n "transkribiert": false\n}\n', encoding="utf-8")
print(f"{ZIEL}  {len(y)/16000:.1f}s  {len(SAETZE)} Äußerungen")
