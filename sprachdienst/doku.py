"""Dokumentationspfad: kontinuierlich mitschneiden, spaeter transkribieren.

Bewusst getrennt vom Dialogpfad. Hier zaehlt Qualitaet, nicht Latenz -- das
Roh-Audio wird BEHALTEN, nicht nur das Transkript. Damit bleibt die
Entscheidung gegen Diarisierung umkehrbar, falls doch mehrere Personen
sprechen, und ein abgeleitetes Protokoll bleibt gegen die Quelle pruefbar.
"""
import json, time, wave
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from . import konfig

SEGMENT_S = 300           # neue Datei alle fuenf Minuten


class Rekorder:
    def __init__(self, verzeichnis: Path | None = None):
        self.verz = Path(verzeichnis or konfig.AUFNAHMEN)
        self.w: wave.Wave_write | None = None
        self.pfad: Path | None = None
        self.begonnen = 0.0
        self.sitzung: str | None = None

    def start(self) -> str:
        self.stop()
        self.sitzung = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (self.verz / self.sitzung).mkdir(parents=True, exist_ok=True)
        self._neue_datei()
        return self.sitzung

    def _neue_datei(self):
        self._schliessen()
        stempel = datetime.now(timezone.utc).strftime("%H%M%S")
        self.pfad = self.verz / self.sitzung / f"{stempel}.wav"
        self.w = wave.open(str(self.pfad), "wb")
        self.w.setnchannels(1); self.w.setsampwidth(2); self.w.setframerate(konfig.RATE)
        self.begonnen = time.time()
        # Begleitdatei: ohne sie ist spaeter nicht rekonstruierbar, wann was war.
        (self.pfad.with_suffix(".json")).write_text(json.dumps({
            "begonnen_utc": datetime.now(timezone.utc).isoformat(),
            "rate": konfig.RATE, "kanaele": 1,
            "transkribiert": False}, indent=1), encoding="utf-8")

    def _schliessen(self):
        if self.w:
            self.w.close(); self.w = None

    def block(self, samples: np.ndarray):
        if not self.w:
            return
        if time.time() - self.begonnen >= SEGMENT_S:
            self._neue_datei()
        self.w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())

    def stop(self):
        self._schliessen(); self.pfad = None

    @property
    def laeuft(self) -> bool:
        return self.w is not None


def offene_segmente(verz: Path | None = None):
    """Alle noch nicht transkribierten Aufnahmen -- Arbeitsvorrat fuer den
    Batch-Lauf, der spaeter dazukommt."""
    verz = Path(verz or konfig.AUFNAHMEN)
    for j in sorted(verz.glob("*/*.json")):
        try:
            m = json.loads(j.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not m.get("transkribiert") and j.with_suffix(".wav").exists():
            yield j.with_suffix(".wav")
