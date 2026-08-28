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


# Die Sprachausgabe wird leiser beigemischt als das Mikrofonsignal: sie soll
# hoerbar und transkribierbar sein, aber das Gesprochene im Raum nicht
# uebertoenen und beim Addieren nicht uebersteuern.
MISCH_PEGEL = 0.6
MAX_STAU_S  = 4      # so viel Sprachausgabe darf hoechstens ungeschrieben warten


class Rekorder:
    def __init__(self, verzeichnis: Path | None = None):
        self.verz = Path(verzeichnis or konfig.AUFNAHMEN)
        self.w: wave.Wave_write | None = None
        self.pfad: Path | None = None
        self.begonnen = 0.0
        self.sitzung: str | None = None
        self.beimischung = np.zeros(0, dtype=np.float32)

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

    def mische(self, pcm: bytes, rate: int):
        """Nimmt Sprachausgabe entgegen, die in die Aufnahme gehoert.

        Kiwis Antworten kommen im Labor aus dem Lautsprecher und waeren fuer
        jeden im Raum hoerbar -- nur die Echounterdrueckung des Freisprechers
        haelt sie aus dem Mikrofonsignal heraus. Ohne Beimischung enthielte das
        Protokoll nur die halbe Unterhaltung.

        Gemischt statt angehaengt: Anhaengen wuerde die Datei schneller wachsen
        lassen als die Zeit vergeht und alle Zeitstempel verschieben.
        """
        if not self.w:
            return
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if rate != konfig.RATE:
            n = int(len(x) * konfig.RATE / rate)
            x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
        self.beimischung = np.concatenate([self.beimischung, x.astype(np.float32)])
        # Sicherung gegen einen stockenden Client: die Beimischung wird nur
        # abgebaut, wenn Mikrofonbloecke ankommen. Bleibt der Strom stehen,
        # staut sie sich und wuerde spaeter ueber fremde Stellen gelegt.
        # Lieber den Ueberhang verwerfen als die Aufnahme verfaelschen.
        grenze = konfig.RATE * MAX_STAU_S
        if len(self.beimischung) > grenze:
            self.beimischung = self.beimischung[-grenze:]

    def block(self, samples: np.ndarray):
        if not self.w:
            return
        if time.time() - self.begonnen >= SEGMENT_S:
            self._neue_datei()
        if len(self.beimischung):
            n = min(len(samples), len(self.beimischung))
            samples = samples.copy()
            samples[:n] += self.beimischung[:n] * MISCH_PEGEL
            self.beimischung = self.beimischung[n:]
        self.w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())

    def stop(self):
        self._schliessen(); self.pfad = None

    @property
    def laeuft(self) -> bool:
        return self.w is not None

    @property
    def verzeichnis(self):
        """Verzeichnis der laufenden bzw. zuletzt beendeten Sitzung."""
        return (self.verz / self.sitzung) if self.sitzung else None


def offene_segmente(verz: Path | None = None):
    """Alle noch nicht transkribierten Aufnahmen -- Arbeitsvorrat fuer den
    Batch-Lauf, der spaeter dazukommt."""
    verz = Path(verz or konfig.AUFNAHMEN)
    for j in sorted(verz.glob("*/*.json")):
        try:
            m = json.loads(j.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for endung in (".wav", ".opus"):
            if not m.get("transkribiert") and j.with_suffix(endung).exists():
                yield j.with_suffix(endung)
                break
