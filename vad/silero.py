"""Silero-VAD v5 ueber onnxruntime, ohne torch.

Arbeitet blockweise mit 512 Samples (32 ms bei 16 kHz) -- das ist die
Fenstergroesse, auf die das Modell trainiert ist. Andere Groessen liefern
stillschweigend schlechtere Werte.
"""
import numpy as np, onnxruntime as ort

FENSTER  = 512     # Samples je Block, 32 ms bei 16 kHz
KONTEXT  = 64      # Silero v5 will die letzten 64 Samples des Vorblocks davor
RATE     = 16000

class Vad:
    def __init__(self, pfad="vad/silero_vad.onnx", threads=1):
        opt = ort.SessionOptions()
        opt.intra_op_num_threads = threads
        opt.inter_op_num_threads = threads
        self.s = ort.InferenceSession(pfad, opt, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        # Ohne diesen Vorlauf sieht das Modell nur Bruchstuecke und meldet
        # durchgehend "keine Sprache" -- still, ohne Fehlermeldung.
        self.kontext = np.zeros(KONTEXT, dtype=np.float32)

    def block(self, samples):
        """samples: float32 [-1,1], genau FENSTER lang. Gibt P(Sprache) zurueck."""
        x = np.concatenate([self.kontext, samples.astype(np.float32)])
        self.kontext = x[-KONTEXT:].copy()
        p, self.state = self.s.run(None, {
            "input": x.reshape(1, -1),
            "state": self.state,
            "sr": np.array(RATE, dtype=np.int64)})
        return float(p[0][0])

    def ueber_datei(self, wav):
        """Gibt eine Liste von P(Sprache) je 32-ms-Block zurueck."""
        import wave
        with wave.open(wav) as w:
            assert w.getframerate() == RATE and w.getnchannels() == 1
            roh = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        x = roh.astype(np.float32) / 32768.0
        self.reset()
        return [self.block(x[i:i+FENSTER])
                for i in range(0, len(x) - FENSTER + 1, FENSTER)]
