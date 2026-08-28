"""Rechercheaufträge: langlaufende Fragen an den Hermes-Agenten.

**Asynchron, weil eine Sprachschnittstelle nicht warten kann.** Ein gemessener
Auftrag brauchte 18,7 Sekunden, ein aufwendiger dauert Minuten. Der Assistent
nimmt den Auftrag an, sagt Bescheid und arbeitet im Hintergrund weiter; das
Ergebnis kommt, wenn es fertig ist.

**Höchstens einer gleichzeitig.** Hermes und der Sprachpfad teilen sich ein
Modell auf einer GPU; zwei parallele Aufträge würden die Sprachantworten
unbrauchbar träge machen. Der Assistent sagt stattdessen, dass er beschäftigt
ist.

Was zurückkommt, ist **abgeleitet** und wird als solches behandelt: mit
Zeitstempel und Quellenpflicht gespeichert, nie als Tatsache ins Protokoll.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sprachdienst import konfig

HERMES = Path.home() / ".local" / "bin" / "hermes"
ORDNER = konfig.WURZEL / "recherchen"
# Eigenes Arbeitsverzeichnis fuer den Agenten. Ohne --in erbt Hermes das
# Verzeichnis des Sprachdienstes -- also das Repo -- und legt seine
# Zwischenergebnisse dort ab; ein Auftrag hinterliess eine 20-KB-HTML-Seite
# neben der Quelle. --no-restore-cwd hilft dagegen nicht, das betrifft nur
# fortgesetzte Sitzungen. Unter zustand/, weil das ohnehin nicht ins Repo
# gehoert und man trotzdem nachsehen kann, was der Agent gebaut hat.
ARBEIT = konfig.WURZEL / "zustand" / "hermes"
MAX_S = 420          # danach gilt der Auftrag als gescheitert

AUFTRAG_ZUSATZ = (
    " Antworte auf Deutsch, höchstens acht Sätze. Nenne zu jeder Zahl und jeder "
    "Sachaussage die Quelle. Was du nicht belegen kannst, lässt du weg oder "
    "kennzeichnest es ausdrücklich als unsicher."
)


@dataclass
class Auftrag:
    frage: str
    gestellt: float = field(default_factory=time.time)
    fertig: float | None = None
    ergebnis: str = ""
    fehler: str = ""
    hermes_sitzung: str = ""

    @property
    def dauer(self) -> float:
        return (self.fertig or time.time()) - self.gestellt


class Recherche:
    def __init__(self):
        self.laufend: Auftrag | None = None
        self._task: asyncio.Task | None = None

    @property
    def beschaeftigt(self) -> bool:
        return self.laufend is not None

    async def starten(self, frage: str, wenn_fertig, roh: bool = False) -> Auftrag | None:
        """Nimmt einen Auftrag an. Gibt None zurueck, wenn schon einer laeuft."""
        if self.beschaeftigt:
            return None
        a = Auftrag(frage=frage)
        self.laufend = a
        self._task = asyncio.create_task(self._lauf(a, wenn_fertig, roh))
        return a

    async def _lauf(self, a: Auftrag, wenn_fertig, roh: bool = False):
        try:
            # roh=True reicht die Anweisung unveraendert durch -- fuer den
            # Fall, dass jemand Hermes direkt ansprechen will.
            auftrag = a.frage if roh else a.frage + AUFTRAG_ZUSATZ
            a.ergebnis, a.hermes_sitzung = await self._hermes(auftrag)
        except asyncio.TimeoutError:
            a.fehler = f"Zeitüberschreitung nach {MAX_S} s"
        except Exception as e:                        # pragma: no cover
            a.fehler = repr(e)
        finally:
            a.fertig = time.time()
            self.laufend = None
            try:
                self._ablegen(a)
            except OSError:
                pass
            try:
                await wenn_fertig(a)
            except Exception:
                pass

    @staticmethod
    async def _hermes(frage: str) -> tuple[str, str]:
        # -m ausdruecklich: ohne den Schalter nimmt Hermes das Modell aus
        # ~/.hermes/config.yaml, und dort stand Ornith fest. Beim Umschalten
        # auf ein anderes Modell scheiterte die Recherche mit
        # "HTTP 404: The model ornith-1.5-35b-a3b does not exist", waehrend
        # der Sprachpfad einwandfrei lief -- eine Abhaengigkeit auf eine
        # Datei ausserhalb des Repos, die nichts sichtbar machte.
        ARBEIT.mkdir(parents=True, exist_ok=True)
        # cwd= UND --in: der Schalter allein reicht nicht. Hermes stellt ein
        # gemerktes Arbeitsverzeichnis wieder her ("Shell cwd was reset to
        # ..."), und ein Auftrag legte seine HTML-Seite trotz --in wieder im
        # Repo ab. Das cwd des Kindprozesses kann er nicht ueberschreiben.
        p = await asyncio.create_subprocess_exec(
            str(HERMES), "chat", "-Q", "--no-restore-cwd",
            "--in", str(ARBEIT),
            "-m", konfig.LLM_MODEL, "-q", frage,
            cwd=str(ARBEIT),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            aus, fehler = await asyncio.wait_for(p.communicate(), timeout=MAX_S)
        except asyncio.TimeoutError:
            p.kill()
            raise
        text = aus.decode(errors="replace").strip()
        if p.returncode != 0 and not text:
            raise RuntimeError(fehler.decode(errors="replace")[:200])
        # -Q stellt eine Zeile "session_id: ..." voran.
        # -Q stellt die Kennung voran, aber nicht immer als erste Zeile.
        sitzung = ""
        zeilen = text.splitlines()
        behalten = []
        for z in zeilen:
            m = re.match(r"\s*session_id:\s*(\S+)\s*$", z)
            if m and not sitzung:
                sitzung = m.group(1)
            else:
                behalten.append(z)
        return "\n".join(behalten).strip(), sitzung

    @staticmethod
    def _ablegen(a: Auftrag) -> Path:
        ORDNER.mkdir(parents=True, exist_ok=True)
        stempel = datetime.fromtimestamp(a.gestellt, timezone.utc)
        ziel = ORDNER / f"{stempel.strftime('%Y%m%dT%H%M%SZ')}.md"
        ziel.write_text("\n".join([
            f"# Recherche: {a.frage}", "",
            f"Beauftragt {stempel.astimezone().strftime('%d.%m.%Y %H:%M')}, "
            f"gedauert {a.dauer:.0f} s"
            + (f", Hermes-Sitzung `{a.hermes_sitzung}`" if a.hermes_sitzung else ""),
            "",
            "> **Abgeleitet, nicht geprüft.** Von einem Agenten aus Internet- und",
            "> Dokumentenquellen erstellt. Vor Verwendung im Protokoll gegen die",
            "> genannten Quellen prüfen.", "",
            a.ergebnis or f"**Gescheitert:** {a.fehler}", "",
        ]) + "\n", encoding="utf-8")
        return ziel


def offene_ergebnisse(seit: float = 0.0) -> list[Path]:
    """Abgelegte Rechercheergebnisse, neueste zuerst."""
    if not ORDNER.is_dir():
        return []
    return sorted(ORDNER.glob("*.md"), reverse=True)
