"""Sprecher im Mitschnitt unterscheiden (Diarisierung).

Laeuft NICHT im Sprachpfad, sondern in der Nachbereitung auf dem gespeicherten
Rohaudio. Faellt der Schritt aus, entsteht das Protokoll wie bisher -- nur ohne
Sprecherangabe. Abschaltbar ueber KIHIWI_SPRECHER=0.

Warum ueberhaupt vorsichtig: ein Mikrofon, Leute in unterschiedlichem Abstand,
ein brummendes Geraet und Menschen, die einander ins Wort fallen. Ueberlappende
Rede ist die eigentliche Schwierigkeit, nicht das Modell. Eine falsche
Zuordnung in einem Laborprotokoll ist schlechter als gar keine -- deshalb wird
nur zugeordnet, was deutlich genug ist, und der Rest bleibt offen.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import konfig

log = logging.getLogger("kihiwi.sprecher")

MODELLE = konfig.WURZEL / "modelle"
SEGMENTIERUNG = MODELLE / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMBEDDING = MODELLE / "campplus.onnx"

AN = os.environ.get("KIHIWI_SPRECHER", "1") not in ("0", "aus", "nein")

# Wie aehnlich zwei Stimmen sein duerfen, um als dieselbe zu gelten. Kleiner =
# strenger = mehr Sprecher. 0,5 ist die Voreinstellung von sherpa-onnx.
SCHWELLE = float(os.environ.get("KIHIWI_SPRECHER_SCHWELLE", "0.7"))
# Kuerzere Redebeitraege werden nicht zugeordnet: fuer ein "mhm" reicht das
# Stimmprofil nicht, und eine geratene Zuordnung ist schlimmer als keine.
MIN_SEGMENT_S = 0.7


@dataclass
class Redebeitrag:
    start_s: float
    ende_s: float
    sprecher: int          # 0, 1, 2 ... innerhalb dieser Aufnahme

    @property
    def name(self) -> str:
        return f"Sprecher {chr(ord('A') + self.sprecher)}"


_dia = None


def verfuegbar() -> bool:
    return AN and SEGMENTIERUNG.exists() and EMBEDDING.exists()


def laden():
    """Laedt die Modelle einmal. Gibt None, wenn etwas fehlt."""
    global _dia
    if _dia is not None:
        return _dia
    if not verfuegbar():
        return None
    try:
        import sherpa_onnx
    except ImportError:
        log.info("sherpa-onnx nicht installiert — keine Sprechertrennung")
        return None
    k = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(SEGMENTIERUNG)),
            num_threads=4),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(EMBEDDING), num_threads=4),
        # num_clusters=-1: die Anzahl der Sprecher ist nicht vorgegeben, sie
        # ergibt sich aus der Schwelle. Im Labor weiss vorher niemand, wie
        # viele Leute mitreden.
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1,
                                                    threshold=SCHWELLE),
        min_duration_on=0.3,
        min_duration_off=0.5)
    if not k.validate():
        log.warning("Diarisierungskonfiguration ungueltig")
        return None
    _dia = sherpa_onnx.OfflineSpeakerDiarization(k)
    log.info("Sprechertrennung bereit (Schwelle %.2f)", SCHWELLE)
    return _dia


def zerlegen(samples: np.ndarray, rate: int) -> list[Redebeitrag]:
    """Audio in Redebeitraege je Sprecher zerlegen. Leer, wenn es nicht geht."""
    d = laden()
    if d is None:
        return []
    if rate != d.sample_rate:
        n = int(len(samples) * d.sample_rate / rate)
        samples = np.interp(np.linspace(0, len(samples) - 1, n),
                            np.arange(len(samples)), samples).astype(np.float32)
    try:
        roh = d.process(samples).sort_by_start_time()
    except Exception as e:
        log.warning("Diarisierung gescheitert: %s", e)
        return []
    return [Redebeitrag(s.start, s.end, s.speaker) for s in roh]


def zuordnen(beitraege: list[Redebeitrag], start_ms: int, ende_ms: int) -> str:
    """Welcher Sprecher gehoert zu einem Abschnitt des Transkripts?

    Entschieden wird ueber die groesste zeitliche Ueberlappung. Reicht sie
    nicht deutlich (unter 60 % des Abschnitts, oder zwei Sprecher fast
    gleichauf), bleibt es offen -- lieber keine Angabe als eine falsche.
    """
    if not beitraege:
        return ""
    a, b = start_ms / 1000, ende_ms / 1000
    dauer = b - a
    if dauer < MIN_SEGMENT_S:
        return ""
    je: dict[int, float] = {}
    for r in beitraege:
        ueber = min(b, r.ende_s) - max(a, r.start_s)
        if ueber > 0:
            je[r.sprecher] = je.get(r.sprecher, 0.0) + ueber
    if not je:
        return ""
    rang = sorted(je.items(), key=lambda x: -x[1])
    bester, anteil = rang[0][0], rang[0][1] / dauer
    if anteil < 0.6:
        return ""
    # Zwei Sprecher fast gleichauf: da hat jemand dazwischengeredet.
    if len(rang) > 1 and rang[1][1] > 0.5 * rang[0][1]:
        return ""
    return Redebeitrag(0, 0, bester).name
