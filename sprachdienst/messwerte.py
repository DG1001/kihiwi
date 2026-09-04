"""Messwerte der Maschine für die Anzeigetafel.

**Erweiterbar gedacht.** Jeder Wert ist ein `Wert` mit Name, Zahl, Einheit und
Bereich -- die Anzeige im Browser kennt nur diese Form und nicht, woher sie
kommt. Später sollen Werte von außen dazukommen (MQTT); dafür genügt es, sie
in dieselbe Form zu bringen und in `alle()` einzuhängen.

**Auf dem GB10 gibt es keinen getrennten GPU-Speicher.** `nvidia-smi` meldet
für `memory.used` ein `[N/A]`, weil Prozessor und Grafikeinheit sich denselben
Unified Memory teilen. Deshalb steht hier RAM für beides, und die GPU trägt
nur ihre Auslastung bei.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, asdict

log = logging.getLogger("kihiwi.messwerte")

# Nicht bei jedem Aufruf messen: die Anzeige fragt im Sekundentakt, und
# nvidia-smi kostet ~40 ms.
_ZWISCHEN: dict[str, tuple[float, object]] = {}
FRISCH_S = 0.9


@dataclass
class Wert:
    name: str          # angezeigter Name
    zahl: float
    einheit: str
    min: float = 0.0
    max: float = 100.0
    # Ab hier wird die Anzeige gelb bzw. rot. None = keine Schwelle.
    warn: float | None = None
    kritisch: float | None = None


def _gecacht(schluessel, hol):
    t, w = _ZWISCHEN.get(schluessel, (0.0, None))
    if time.time() - t < FRISCH_S:
        return w
    w = hol()
    _ZWISCHEN[schluessel] = (time.time(), w)
    return w


def _gpu() -> Wert | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        z = float(r.stdout.strip().splitlines()[0])
    except (subprocess.SubprocessError, ValueError, IndexError) as e:
        log.debug("GPU nicht lesbar: %r", e)
        return None
    return Wert("GPU-Auslastung", z, "%", warn=85, kritisch=97)


_CPU_VORHER: tuple[float, float] | None = None


def _cpu() -> Wert | None:
    """Auslastung seit dem VORIGEN Aufruf, nicht seit dem Systemstart.

    /proc/stat zaehlt kumulativ. Die Differenz zweier Messungen ist die
    Auslastung im Zeitraum dazwischen -- ein absoluter Wert waere der
    Durchschnitt seit dem Booten und damit nutzlos.
    """
    global _CPU_VORHER
    try:
        f = open("/proc/stat").readline().split()[1:]
        werte = [float(x) for x in f[:8]]
    except (OSError, ValueError):
        return None
    gesamt, leer = sum(werte), werte[3] + werte[4]
    if _CPU_VORHER is None:
        _CPU_VORHER = (gesamt, leer)
        return Wert("CPU-Auslastung", 0.0, "%", warn=85, kritisch=97)
    dg, dl = gesamt - _CPU_VORHER[0], leer - _CPU_VORHER[1]
    _CPU_VORHER = (gesamt, leer)
    z = 0.0 if dg <= 0 else max(0.0, min(100.0, (1 - dl / dg) * 100))
    return Wert("CPU-Auslastung", round(z, 1), "%", warn=85, kritisch=97)


def _ram() -> Wert | None:
    try:
        m = {}
        for zeile in open("/proc/meminfo"):
            k, _, rest = zeile.partition(":")
            m[k] = float(rest.split()[0]) / 1048576      # kB -> GiB
    except (OSError, ValueError, IndexError):
        return None
    gesamt = m.get("MemTotal", 0)
    frei = m.get("MemAvailable", 0)
    if not gesamt:
        return None
    # Belegt, nicht frei: eine Anzeige, die hochgeht wenn es eng wird, liest
    # sich richtiger als eine, die runtergeht.
    return Wert("Speicher belegt", round(gesamt - frei, 1), "GiB",
                max=round(gesamt, 1), warn=round(gesamt * 0.85, 1),
                kritisch=round(gesamt * 0.95, 1))


def _platte() -> Wert | None:
    try:
        s = shutil.disk_usage("/")
    except OSError:
        return None
    g = 1024 ** 3
    return Wert("Platte belegt", round(s.used / g, 1), "GB",
                max=round(s.total / g, 1), warn=round(s.total / g * 0.85, 1),
                kritisch=round(s.total / g * 0.95, 1))


GEBER = {"gpu": _gpu, "cpu": _cpu, "ram": _ram, "platte": _platte}


def einer(schluessel: str) -> dict | None:
    hol = GEBER.get(schluessel)
    if hol is None:
        return None
    w = _gecacht(schluessel, hol)
    return asdict(w) if w else None


def alle() -> dict[str, dict]:
    aus = {}
    for k in GEBER:
        w = einer(k)
        if w:
            aus[k] = w
    return aus
