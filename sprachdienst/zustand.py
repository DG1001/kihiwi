"""Zustand des Dienstes und die Ereignisse, die ihn veraendern.

Aufzeichnung und Dialog sind bewusst ORTHOGONAL: der Assistent kann waehrend
einer laufenden Dokumentation angesprochen werden und ausserhalb davon ebenso.
Deshalb ein Schalter `aufnahme` neben der Phase, kein gemeinsamer Automat.
"""
import asyncio, time
from dataclasses import dataclass, field, asdict
from enum import Enum


class Phase(str, Enum):
    LEERLAUF   = "leerlauf"     # Mikrofon aus
    BEREIT     = "bereit"       # Mikrofon an, wartet auf Ansprache
    HOEREN     = "hoeren"       # nimmt gerade eine Aeusserung auf
    DENKEN     = "denken"       # STT und LLM laufen
    ANTWORTEN  = "antworten"    # Sprachausgabe laeuft


@dataclass
class Zustand:
    phase: Phase = Phase.LEERLAUF
    aufnahme: bool = False          # Dokumentation laeuft
    mikro: bool = False             # Mikrofon freigegeben
    gespraech: bool = False         # Rueckfragen ohne Aktivierungswort moeglich
    llm_da: bool = False            # Ornith erreichbar
    stt_da: bool = False            # whisper-server erreichbar
    letzter_text: str = ""          # was zuletzt verstanden wurde
    letzte_antwort: str = ""
    hinweis: str = ""               # fuer den Monitor, z.B. "Modell wird gewechselt"
    seit: float = field(default_factory=time.time)

    def als_dict(self):
        d = asdict(self); d["phase"] = self.phase.value; return d


class Zustandshalter:
    """Haelt den Zustand und benachrichtigt Beobachter (Monitor, Client)."""

    def __init__(self):
        self.z = Zustand()
        self._beobachter: set[asyncio.Queue] = set()

    def abonnieren(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=32)
        self._beobachter.add(q)
        q.put_nowait(self.z.als_dict())
        return q

    def abbestellen(self, q):
        self._beobachter.discard(q)

    def setzen(self, **felder):
        geaendert = False
        for k, v in felder.items():
            if getattr(self.z, k) != v:
                setattr(self.z, k, v); geaendert = True
        if not geaendert:
            return
        self.z.seit = time.time()
        schnappschuss = self.z.als_dict()
        for q in list(self._beobachter):
            try:
                q.put_nowait(schnappschuss)
            except asyncio.QueueFull:
                # Ein langsamer Monitor darf den Dienst nicht bremsen.
                pass
