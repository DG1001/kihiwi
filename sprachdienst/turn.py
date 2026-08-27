"""Turn-Erkennung: VAD, Endpointing, und die lexikalische Pruefung nebenher.

Der entscheidende Punkt: die Pruefung (STT + LLM, ~230 ms) laeuft als eigene
Aufgabe, WAEHREND weiter Audio hereinkommt. Faellt sie auf WEITER, hat sie
nichts gekostet -- es wurde ja durchgehend mitgeschnitten. Nur die letzte,
richtige Entscheidung schlaegt mit ihrer Laufzeit zu Buche.
"""
import asyncio, time
from dataclasses import dataclass
import numpy as np
from . import konfig, llm, stt
from vad.silero import Vad


@dataclass
class Endpoint:
    samples: np.ndarray      # die ganze Aeusserung
    text: str                # bereits transkribiert, falls die Pruefung ihn lieferte
    grund: str               # "lexikalisch" | "decke"
    dauer_ms: float


class Turnerkenner:
    """Blockweise gefuettert (32 ms). Gibt einen Endpoint zurueck, wenn der
    Sprecher fertig ist, sonst None."""

    def __init__(self, mit_pruefung: bool = True):
        self.vad = Vad(str(konfig.VAD_MODELL))
        self.mit_pruefung = mit_pruefung
        self.reset()

    def reset(self):
        self.vad.reset()
        self.puffer: list[np.ndarray] = []
        self.sprach = False
        self.sprach_ms = 0
        self.stille_ms = 0
        self.beginn = None
        self._pruefung: asyncio.Task | None = None
        self._pruefung_ab = 0

    @property
    def aktiv(self) -> bool:
        return self.sprach

    async def _pruefe(self, samples):
        text = await stt.transkribiere(samples)
        if not text:
            return 0.0, ""
        return await llm.p_fertig(text), text

    async def block(self, samples: np.ndarray) -> Endpoint | None:
        self.puffer.append(samples)
        p = self.vad.block(samples)
        aktiv = p > (konfig.VAD_AUS if self.sprach else konfig.VAD_EIN)

        if aktiv:
            self.sprach_ms += konfig.BLOCK_MS
            self.stille_ms = 0
            if not self.sprach and self.sprach_ms >= konfig.MIN_SPRACHE_MS:
                self.sprach = True
                self.beginn = time.time()
            # Es geht weiter -- eine laufende Pruefung ist damit hinfaellig.
            if self._pruefung and not self._pruefung.done():
                self._pruefung.cancel()
            self._pruefung = None
        else:
            self.sprach_ms = 0
            if not self.sprach:
                # Vorlauf vor der ersten Sprache nicht endlos mitschleppen.
                if len(self.puffer) > 40:
                    self.puffer.pop(0)
                return None
            self.stille_ms += konfig.BLOCK_MS

            if self.stille_ms >= konfig.DECKE_MS:
                return self._fertig("decke", "")

            if (self.mit_pruefung and self._pruefung is None
                    and self.stille_ms >= konfig.T_KURZ_MS):
                schnitt = np.concatenate(self.puffer)
                self._pruefung = asyncio.create_task(self._pruefe(schnitt))
                self._pruefung_ab = self.stille_ms

        if self._pruefung is not None and self._pruefung.done():
            try:
                pf, text = self._pruefung.result()
            except asyncio.CancelledError:
                pf, text = 0.0, ""
            self._pruefung = None
            if pf >= konfig.P_FERTIG_SCHWELLE and not aktiv:
                return self._fertig("lexikalisch", text)
            # WEITER: naechste Pruefung erst nach einer weiteren Stillestrecke,
            # sonst fragt der Dienst im Sekundentakt dasselbe.
            self._pruefung_ab = self.stille_ms

        if (self.mit_pruefung and self._pruefung is None and self.sprach
                and not aktiv and self.stille_ms - self._pruefung_ab >= konfig.T_KURZ_MS):
            schnitt = np.concatenate(self.puffer)
            self._pruefung = asyncio.create_task(self._pruefe(schnitt))
            self._pruefung_ab = self.stille_ms

        return None

    def _fertig(self, grund: str, text: str) -> Endpoint:
        samples = np.concatenate(self.puffer)
        dauer = (time.time() - self.beginn) * 1000 if self.beginn else 0.0
        ep = Endpoint(samples=samples, text=text, grund=grund, dauer_ms=dauer)
        self.reset()
        return ep
