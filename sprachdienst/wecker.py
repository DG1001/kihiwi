"""Timer und Erinnerungen.

Deterministisch wie die Aufzeichnungssteuerung: die Zeitangabe wird hier
geparst, nicht vom Modell. Ein Timer, den das Modell "vergisst" anzulegen,
faellt erst auf, wenn er nicht klingelt -- und dann ist es zu spaet.

Ueberdauert einen Neustart des Dienstes: "erinner mich um 15 Uhr" waere sonst
weg, sobald jemand die Dienste durchstartet, und das passiert hier oft.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta

from . import konfig

log = logging.getLogger("kihiwi.wecker")

DATEI = konfig.WURZEL / "zustand" / "wecker.json"

# --------------------------------------------------------------- Zahlwoerter
# Whisper schreibt Zahlen meist als Ziffern, aber nicht immer -- "in zehn
# Minuten" kommt vor. Beides muss gehen.
_WORT = {
    "null": 0, "ein": 1, "eine": 1, "einer": 1, "eins": 1, "zwei": 2, "drei": 3,
    "vier": 4, "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
    "zehn": 10, "elf": 11, "zwoelf": 12, "dreizehn": 13, "vierzehn": 14,
    "fuenfzehn": 15, "sechzehn": 16, "siebzehn": 17, "achtzehn": 18,
    "neunzehn": 19, "zwanzig": 20, "dreissig": 30, "vierzig": 40,
    "fuenfzig": 50, "sechzig": 60, "neunzig": 90,
    "viertel": 0.25, "halbe": 0.5, "halben": 0.5, "halber": 0.5, "halb": 0.5,
    "anderthalb": 1.5, "eineinhalb": 1.5, "zweieinhalb": 2.5,
}
_ZUS = re.compile(r"\b(ein|zwei|drei|vier|fuenf|sechs|sieben|acht|neun)"
                  r"und(zwanzig|dreissig|vierzig|fuenfzig)\b")

_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                         "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def _normal(s: str) -> str:
    return s.translate(_UMLAUT).lower()


def _zahl(s: str) -> float | None:
    s = s.strip()
    if re.fullmatch(r"\d+([.,]\d+)?", s):
        return float(s.replace(",", "."))
    m = _ZUS.fullmatch(s)
    if m:
        return _WORT[m[1]] + _WORT[m[2]]
    return _WORT.get(s)


_ZAHLWORT = (r"\d+(?:[.,]\d+)?|" + "|".join(sorted(_WORT, key=len, reverse=True))
             + r"|(?:ein|zwei|drei|vier|fuenf|sechs|sieben|acht|neun)"
               r"und(?:zwanzig|dreissig|vierzig|fuenfzig)")

_EINHEIT = {"sekunde": 1, "sekunden": 1, "sek": 1,
            "minute": 60, "minuten": 60, "min": 60,
            "stunde": 3600, "stunden": 3600, "std": 3600}

# "in 10 Minuten", "fuer eine halbe Stunde", "nach 90 Sekunden"
# Der Artikel steht optional VOR der Zahl: "in einer halben Stunde" ist
# 0,5 h, "in einer Stunde" ist 1 h -- dort ist "einer" selbst die Zahl, und
# die Gruppe faellt beim Zurueckspringen leer aus.
_DAUER = re.compile(rf"\b(?:(?:in|fuer|nach|auf)\s+)?(?:(?:einer|eine|einen)\s+)?"
                    rf"({_ZAHLWORT})\s*"
                    rf"({'|'.join(sorted(_EINHEIT, key=len, reverse=True))})\b")
# "um 15 Uhr", "um 15:30", "um 15.30 Uhr", "um viertel nach drei" (nicht)
_UHRZEIT = re.compile(r"\bum\s+(\d{1,2})(?:[:.](\d{2}))?\s*uhr(?:\s*(\d{1,2}))?\b"
                      r"|\bum\s+(\d{1,2}):(\d{2})\b")


def deuten(text: str, jetzt: float | None = None) -> tuple[float, str] | None:
    """(Faelligkeit als Unix-Zeit, Restsatz) oder None, wenn keine Zeit drinsteht."""
    jetzt = jetzt if jetzt is not None else time.time()
    t = _normal(text)

    m = _UHRZEIT.search(t)
    if m:
        if m[1] is not None:
            stunde = int(m[1]); minute = int(m[2] or m[3] or 0)
        else:
            stunde = int(m[4]); minute = int(m[5])
        if not (0 <= stunde <= 23 and 0 <= minute <= 59):
            return None
        n = datetime.fromtimestamp(jetzt).astimezone()
        ziel = n.replace(hour=stunde, minute=minute, second=0, microsecond=0)
        # Eine Uhrzeit, die schon vorbei ist, meint morgen.
        if ziel.timestamp() <= jetzt:
            ziel += timedelta(days=1)
        return ziel.timestamp(), _rest(text, m)

    m = _DAUER.search(t)
    if m:
        n = _zahl(m[1])
        if n is None or n <= 0:
            return None
        sek = n * _EINHEIT[m[2]]
        if sek < 1 or sek > 24 * 3600:
            return None
        return jetzt + sek, _rest(text, m)
    return None


# Was den Auftrag einleitet und nicht zum Erinnerungstext gehoert.
_EINLEITUNG = re.compile(
    r"^\s*(?:kiwi\b[\s,]*)?(?:bitte\s+)?"
    # Hoefliche Umschreibung: "kannst du mich ... erinnern".
    r"(?:(?:kannst|koenntest|wuerdest|willst|magst)\s+)?"
    # Die Verben mit \w*: Whisper schrieb "Kiwi erinnert mich in 40 Sekunden".
    # Mit fester Endung ("erinnere?") blieb das "t" stehen und wanderte in den
    # Erinnerungstext -- "erinnere dich daran, t mich daran, die Pumpe ...".
    r"(?:(?:erinner\w*|weck\w*|sag\w*|meld\w*|stell\w*|setz\w*|start\w*|"
    r"mach\w*|leg\w*)"
    # Mehrere Fuerwoerter hintereinander: "erinnerst DU MICH in fuenf Minuten".
    r"(?:[\s,]*(?:du|sie|mich|uns|mir|dich))*[\s,]*(?:bescheid)?[\s,]*)?"
    # Fuerwoerter auch ohne vorangehendes Verb: "kannst DU MICH ... erinnern".
    r"(?:(?:du|sie|mich|uns|mir|dich)[\s,]*)*"
    # Artikel NUR zusammen mit dem Substantiv -- als getrennte optionale
    # Gruppen frisst er sonst das "die" aus "die Pumpe abzuschalten".
    r"(?:(?:(?:einen?|ein|die|der|das)\s+)?"
    r"(?:timer|time|wecker|erinnerung\w*|alarm)[\s,]*)?"
    r"(?:daran|darauf|dass)?[\s,]*", re.I)

# Hoefliches am Satzende, das nie zum Erinnerungstext gehoert.
_NACHKLAPP = re.compile(r"[\s,]*(?:(?:zu\s+)?erinnern|bitte|danke|kiwi)"
                        r"(?:[\s,]*(?:bitte|danke|kiwi))*\s*[.!?]*\s*$", re.I)

# Das Verb kann auch HINTEN stehen: "Timer eine Minute setzen" -- aus dem
# laufenden Betrieb gemeldet, "setzen" landete als Erinnerungstext und Kiwi
# sagte "erinnere ich dich daran, setzen".
#
# Nur wenn NICHTS ausser dem Verb uebrig bleibt. Es blind am Satzende zu
# streichen war zu grob: aus "erinner mich in 10 Minuten daran, die Messung zu
# starten" wurde "die Messung zu".
_NUR_BEFEHL = re.compile(r"^[\s,]*(?:setzen|stellen|einstellen|starten|"
                         r"laufen\s*lassen|machen|nehmen|an|los)"
                         r"[\s,]*\s*[.!?]*\s*$", re.I)


def _rest(text: str, m) -> str:
    """Der Erinnerungstext: alles ohne Zeitangabe und Einleitung."""
    rest = (text[:m.start()] + " " + text[m.end():])
    rest = re.sub(r"\s{2,}", " ", rest).strip()
    rest = _EINLEITUNG.sub("", rest)
    rest = _NACHKLAPP.sub("", rest)
    rest = re.sub(r"^\s*(?:daran|darauf|dass|und|mich|mir|uns)\b[\s,]*", "",
                  rest, flags=re.I)
    if _NUR_BEFEHL.match(rest):
        return ""
    return rest.strip(" ,.:;–—-")


# ------------------------------------------------------------------- Wecker
@dataclass
class Erinnerung:
    kennung: str
    faellig: float
    text: str            # was erinnert werden soll, leer bei reinem Timer
    angelegt: float

    def als_dict(self):
        return asdict(self)


class Wecker:
    def __init__(self):
        self.laufende: list[Erinnerung] = []
        self._n = 0
        self._laden()

    # --- Bestand -----------------------------------------------------------
    def _laden(self):
        try:
            roh = json.loads(DATEI.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        jetzt = time.time()
        for d in roh:
            try:
                e = Erinnerung(**d)
            except TypeError:
                continue
            # Laengst Abgelaufenes nicht nachtraeglich klingeln lassen: eine
            # Erinnerung von gestern hilft niemandem und erschreckt nur.
            if e.faellig > jetzt - 300:
                self.laufende.append(e)
        self._n = max((int(e.kennung[1:]) for e in self.laufende
                       if e.kennung[1:].isdigit()), default=0)
        if self.laufende:
            log.info("%d Erinnerung(en) aus %s uebernommen", len(self.laufende), DATEI)

    def _sichern(self):
        try:
            DATEI.parent.mkdir(parents=True, exist_ok=True)
            DATEI.write_text(json.dumps([e.als_dict() for e in self.laufende],
                                        ensure_ascii=False, indent=1),
                             encoding="utf-8")
        except OSError as e:
            log.warning("Erinnerungen nicht gesichert: %s", e)

    # --- Verwalten ---------------------------------------------------------
    def stellen(self, faellig: float, text: str = "") -> Erinnerung:
        self._n += 1
        e = Erinnerung(f"w{self._n}", faellig, text, time.time())
        self.laufende.append(e)
        self.laufende.sort(key=lambda x: x.faellig)
        self._sichern()
        return e

    def loeschen(self, kennung: str | None = None) -> list[Erinnerung]:
        """Ohne Kennung: alle. Gibt zurueck, was entfernt wurde."""
        if kennung is None:
            weg, self.laufende = self.laufende, []
        else:
            weg = [e for e in self.laufende if e.kennung == kennung]
            self.laufende = [e for e in self.laufende if e.kennung != kennung]
        self._sichern()
        return weg

    def faellige(self, jetzt: float | None = None) -> list[Erinnerung]:
        jetzt = jetzt if jetzt is not None else time.time()
        faellig = [e for e in self.laufende if e.faellig <= jetzt]
        if faellig:
            self.laufende = [e for e in self.laufende if e.faellig > jetzt]
            self._sichern()
        return faellig

    def als_liste(self) -> list[dict]:
        return [e.als_dict() for e in self.laufende]


# ------------------------------------------------------------------ Ansagen
def dauer_wort(sek: float) -> str:
    """Restdauer sprechbar: "zwei Stunden und zehn Minuten"."""
    sek = max(0, int(round(sek)))
    # Unter zwei Minuten in Sekunden: aus 90 s ein "2 Minuten" zu machen
    # waere schlicht falsch.
    if sek < 120:
        return f"{sek} Sekunden" if sek != 1 else "eine Sekunde"
    minuten, stunden = (sek + 30) // 60, 0
    if minuten >= 60:
        stunden, minuten = divmod(minuten, 60)
    teile = []
    if stunden:
        teile.append("eine Stunde" if stunden == 1 else f"{stunden} Stunden")
    if minuten:
        teile.append("eine Minute" if minuten == 1 else f"{minuten} Minuten")
    return " und ".join(teile) or "weniger als eine Minute"


def anlass(text: str) -> str:
    """Haengt den Erinnerungstext grammatisch passend an "erinnere dich ...".

    Die Deutung liefert zweierlei Formen: Praepositionalphrasen ("an die
    Besprechung") und Infinitive ("die Pumpe abzuschalten"). Ein festes
    "wegen:" davor passt zu keiner von beiden.
    """
    t = text.strip()
    if not t:
        return ""
    if re.match(r"^(an|ans|auf|aufs|wegen|fuer|für|um|zu[rm]?)\b", t, re.I):
        return " " + t
    return " daran, " + t


def uhrzeit_wort(faellig: float) -> str:
    n = datetime.fromtimestamp(faellig).astimezone()
    heute = datetime.now().astimezone().date()
    zeit = n.strftime("%H:%M")
    return zeit if n.date() == heute else f"morgen {zeit}"
