"""Absichtserkennung vor dem Sprachmodell.

**Warum das nötig ist.** Ornith hat 3 Milliarden aktive Parameter. Gemessen am
27.08.2026: dieselbe Frage, dasselbe Modell -- mit vier Werkzeugen und langem
System-Prompt ruft es KEIN Werkzeug auf, mit einem Werkzeug und kurzem Prompt
ruft es zuverlässig auf. Die Zuverlässigkeit hängt an der Menge, die es
gleichzeitig im Blick behalten muss.

Also wird vorher entschieden, worum es geht, und dem Modell nur das eine
passende Werkzeug mit einem kurzen Prompt gegeben.

**Regeln statt Modell.** Kein Embedding-Modell, kein zusätzlicher LLM-Aufruf:
die Absichten sind wenige und sprachlich deutlich getrennt, und der Router muss
in Mikrosekunden entscheiden, sonst frisst er die Latenz, die er sparen soll.
Erkennt keine Regel etwas, wird nicht geraten -- dann gilt WISSEN, weil
Nachschlagen der häufigste Fall ist und am wenigsten Schaden anrichtet.
"""
from __future__ import annotations

import re
import unicodedata
from enum import Enum


class Absicht(str, Enum):
    AUFZEICHNUNG = "aufzeichnung"   # Mitschnitt an/aus
    RECHERCHE    = "recherche"      # mehrschrittig, dauert Minuten
    WEB          = "web"            # eine Sache schnell im Netz nachsehen
    WISSEN       = "wissen"         # in den Unterlagen nachschlagen
    PLAUDEREI    = "plauderei"      # kein Werkzeug nötig


# Umlaute AUSSCHREIBEN, nicht nur die Punkte entfernen: NFKD macht aus "ü" ein
# "u", damit passte "ausführlich" nicht auf das Muster "ausfuehrlich".
_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                         "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def _normal(s: str) -> str:
    s = s.lower().translate(_UMLAUT)
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


# Aufzeichnung: braucht ein Objekt UND eine Handlung -- "die Aufzeichnung läuft
# ja noch" ist eine Feststellung, kein Befehl.
_AUF_OBJEKT = re.compile(r"aufzeichnung|aufnahme|mitschnitt|mitschneid|protokollier")
_AUF_TUN    = re.compile(r"\b(start|starte|starten|beginn|beginne|an|ein|einschalt|"
                         r"anschalt|aktivier|stopp|stoppe|stoppen|beend|beende|aus|"
                         r"ausschalt|abschalt|halt|anhalt|pausier)\w*")

# Recherche: ausdrücklich verlangt oder erkennbar mehrschrittig.
#
# "im internet" gehoert hier NICHT hinein. "Suche im Internet nach X" ist eine
# schlichte Netzsuche; als Recherche eingestuft bekam das Modell nur
# 'rechercheauftrag', fand nichts Passendes und erfand ein "researchauftrag".
_RECH = re.compile(r"recherchier|recherche|vergleich|gegenueberstell|"
                   r"literatur|stand der (technik|forschung)|"
                   r"(schau|sieh|guck)\w*\s+(mal\s+)?(ausfuehrlich|genauer|"
                   r"gruendlich)")

# Netzsuche: ausdruecklich nach draussen, aber einschrittig.
_WEB = re.compile(r"im internet|im netz|im web|online|google|"
                  r"(such|schau|sieh|guck)\w*\s+.{0,20}(internet|netz|web)")

# Wissen: Frage nach Sachverhalten. Fragewörter oder ein Fragezeichen genügen.
_FRAGE = re.compile(r"^(wie|was|wo|wer|wann|warum|weshalb|welche\w*|wieviel\w*|"
                    r"wie viel\w*|gibt es|haben wir|hatten wir|kannst du .*sagen)\b|\?")

# Plauderei: kurze Höflichkeiten ohne Sachfrage.
_PLAUDER = re.compile(r"^(danke|dankeschoen|hallo|hi|guten (morgen|tag|abend)|"
                      r"alles klar|ok|okay|passt|gut|super|prima|"
                      r"wie geht|was machst du)\b")


def erkennen(text: str) -> Absicht:
    t = _normal(text).strip()
    if not t:
        return Absicht.PLAUDEREI

    # Reihenfolge ist Absicht: ein Aufzeichnungsbefehl bleibt einer, auch wenn
    # er als Frage formuliert ist ("kannst du die Aufzeichnung starten?").
    if _AUF_OBJEKT.search(t) and _AUF_TUN.search(t):
        return Absicht.AUFZEICHNUNG
    if _RECH.search(t):
        return Absicht.RECHERCHE
    if _WEB.search(t):
        return Absicht.WEB
    if _PLAUDER.match(t) and len(t.split()) <= 6:
        return Absicht.PLAUDEREI
    if _FRAGE.search(t):
        return Absicht.WISSEN
    return Absicht.WISSEN


# Werkzeuge je Absicht. Bewusst knapp: jedes zusaetzliche Werkzeug kostet
# Zuverlaessigkeit.
WERKZEUGE_JE_ABSICHT = {
    Absicht.AUFZEICHNUNG: ["aufzeichnung"],
    Absicht.RECHERCHE:    ["rechercheauftrag"],
    Absicht.WEB:          ["web_suchen"],
    Absicht.WISSEN:       ["dokumente_suchen", "web_suchen"],
    Absicht.PLAUDEREI:    [],
}

# Kurzer Zusatz je Absicht statt eines langen Alleskoenner-Prompts.
ZUSATZ_JE_ABSICHT = {
    Absicht.AUFZEICHNUNG:
        " Der Nutzer will die Aufzeichnung ändern. Rufe dafür das Werkzeug auf —"
        " behaupte niemals eine Änderung, die du nicht ausgeführt hast.",
    Absicht.RECHERCHE:
        " Der Nutzer will eine Recherche. Rufe 'rechercheauftrag' auf und sage"
        " danach nur zu, dich zu melden. Antworte NICHT inhaltlich.",
    Absicht.WEB:
        " Der Nutzer will ausdrücklich im Internet nachgesehen haben. Rufe"
        " 'web_suchen' auf und sage dazu, dass das Ergebnis aus dem Internet"
        " stammt, nicht aus den Laborunterlagen.",
    Absicht.WISSEN:
        " Rufe zuerst 'dokumente_suchen' auf. Antworte nur mit dem, was die"
        " Unterlagen hergeben, und nenne die Quelle. Findest du nichts, sag das"
        " offen — erfinde keine Zahlen. 'web_suchen' nur, wenn die Unterlagen"
        " nichts hergeben; sag dann, dass es aus dem Internet stammt.",
    Absicht.PLAUDEREI: "",
}
