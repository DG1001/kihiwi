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
    PROTOKOLL    = "protokoll"      # das eigene Protokoll zeigen/vorlesen
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
# Auch verbale Formen: "zeichnest du auf", "nimmst du auf", "schneidest du mit".
_AUF_OBJEKT = re.compile(r"aufzeichnung|aufnahme|mitschnitt|mitschneid|protokollier|"
                         r"zeichnest du|nimmst du auf|schneidest du mit|"
                         r"zeichne .{0,12}auf|nimm .{0,12}auf")
_AUF_TUN    = re.compile(r"\b(start|starte|starten|beginn|beginne|an|ein|einschalt|"
                         r"anschalt|aktivier|stopp|stoppe|stoppen|beend|beende|aus|"
                         r"ausschalt|abschalt|halt|anhalt|pausier)\w*")
# Auch die reine Statusfrage gehoert hierher. Sonst landete "Laeuft die
# Aufzeichnung?" bei WISSEN, wo es kein Aufzeichnungswerkzeug gibt -- das Modell
# antwortete "kann ich nicht pruefen", und diese Absage vergiftete den Verlauf:
# ab da wiederholte es sie auch bei echten Befehlen.
_AUF_STATUS = re.compile(r"\b(laeuft|lauft|luft|an|aus|aktiv|zeichnest du auf|"
                         r"nimmst du auf|wird aufgezeichnet)\b")

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

# Protokoll: das zuletzt erstellte anzeigen oder vorlesen. Bewusst VOR der
# Aufzeichnungspruefung ausgewertet, sonst faengt "Protokoll" dort haengen.
_PROT = re.compile(r"\bprotokoll\w*\b(?!ier)")
_PROT_TUN = re.compile(r"zeig|lies|vorles|vorlesen|anzeig|was steht|zusammenfass|"
                       r"gib mir|schick|oeffne|sehen|sieh")

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

    # Vor der Aufzeichnung pruefen: "zeig mir das Protokoll" ist keine
    # Steuerung des Mitschnitts, sondern ein Abruf des Ergebnisses.
    if _PROT.search(t) and _PROT_TUN.search(t):
        return Absicht.PROTOKOLL

    # Reihenfolge ist Absicht: ein Aufzeichnungsbefehl bleibt einer, auch wenn
    # er als Frage formuliert ist ("kannst du die Aufzeichnung starten?").
    # Jede Aeusserung, die die Aufzeichnung erwaehnt, betrifft die Aufzeichnung.
    # Feiner zu unterscheiden brachte nur Luecken ("Zeichnest du gerade auf?").
    if _AUF_OBJEKT.search(t):
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
    Absicht.PROTOKOLL:    [],
    Absicht.WISSEN:       ["dokumente_suchen", "web_suchen"],
    Absicht.PLAUDEREI:    [],
}

# Kurzer Zusatz je Absicht statt eines langen Alleskoenner-Prompts.
ZUSATZ_JE_ABSICHT = {
    Absicht.AUFZEICHNUNG:
        " Es geht um die Aufzeichnung. Soll sie geändert werden, rufe das"
        " Werkzeug auf — behaupte niemals eine Änderung, die du nicht ausgeführt"
        " hast. Wird nur gefragt, ob sie läuft, antworte aus dem oben genannten"
        " Zustand, ohne Werkzeug. Du KANNST die Aufzeichnung steuern.",
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
    Absicht.PROTOKOLL: "",
    Absicht.PLAUDEREI: "",
}


# "auf" darf nicht als Tuwort in _AUF_TUN stehen -- es steckt in
# "Aufzeichnung" und wuerde jede Erwaehnung zum Befehl machen. Verbale
# Aufforderungen deshalb einzeln.
_AUF_BEFEHL = re.compile(r"\b(nimm|zeichne|schneide?|starte?|mach)\b.{0,15}\bauf\b")


# Feste Ausloesewoerter -- so wie "Kiwi" die Ansprache ausloest. Wer das Wort
# sagt, will die Handlung; das Modell wird gar nicht erst gefragt. Es hat sich
# zu oft geweigert ("Ich kann leider keine Internetrecherche durchfuehren"),
# obwohl das Werkzeug bereitstand.
#
# Das Muster ist dasselbe wie bei den Aufzeichnungsbefehlen: Wo die Handlung
# eindeutig ist, entscheidet der Dienst, nicht das Modell. Weitere Ausloeser
# kosten hier eine Zeile.
AUSLOESER = {
    "recherche": r"internetrecherche|netzrecherche|webrecherche|rechercheauftrag|"
                 r"recherchier(?:e|st|en)?",
    "dokumente": r"dokumentenrecherche|dokumentensuche|aktenrecherche|"
                 r"unterlagenrecherche",
    # Nicht bloss "hermes": ueber den Agenten wird im Labor geredet, und die
    # Durchreichung gibt einer gesprochenen Anweisung Zugriff auf Dateien,
    # Browser und Codeausfuehrung. Ein zusammengesetztes Wort faellt nicht
    # versehentlich. Getrennte Schreibung mit, weil die Erkennung
    # Zusammensetzungen gern auseinanderzieht.
    "hermes":    r"hermes[- ]?aufgabe|hermes[- ]?auftrag",
}
_AUSLOESER_RE = {art: re.compile(rf"\b(?:{muster})\b", re.I)
                 for art, muster in AUSLOESER.items()}


def ausloeser(text: str):
    """Gibt (art, thema) zurueck, wenn ein Ausloesewort fiel, sonst (None, "").

    Reihenfolge: der spezifischste zuerst. "Dokumentenrecherche" enthaelt
    "recherche" -- ohne feste Reihenfolge wuerde daraus ein Internetauftrag.
    """
    for art in ("hermes", "dokumente", "recherche"):
        m = _AUSLOESER_RE[art].search(text)
        if m:
            return art, _thema(text, m)
    return None, ""


def _thema(text: str, m) -> str:
    # Thema ist der Rest ohne Ausloeser und Fuellwoerter davor.
    rest = (text[:m.start()] + " " + text[m.end():])
    rest = re.sub(r"\s+", " ", rest).strip()
    # Fuellwoerter vorne wiederholt abraeumen: "Bitte mach eine ... zum X"
    # laesst sonst "eine zum X" stehen.
    fueller = (r"^(bitte|mal|doch|kiwi|mach|mache|starte|beginne|gib mir|"
               r"eine|einen|ein|die|der|das|zu|zum|zur|ueber|über|nach|"
               r"fuer|für|mir|uns)\b[\s,]*")
    vorher = None
    while rest != vorher:
        vorher = rest
        rest = re.sub(fueller, "", rest, flags=re.I).strip(" ,.")
    return rest or text


def rechercheauftrag(text: str) -> str | None:
    """Nur der Internetauftrag -- Ruecksicht auf bestehende Aufrufe."""
    art, thema = ausloeser(text)
    return thema if art == "recherche" else None


def will_aendern(text: str) -> bool:
    """Verlangt die Aeusserung eine Aenderung (statt nur nach dem Zustand zu
    fragen)? Nur dann wird gehandelt."""
    t = _normal(text)
    if not _AUF_OBJEKT.search(t) and not _AUF_BEFEHL.search(t):
        return False
    return bool(_AUF_TUN.search(t) or _AUF_BEFEHL.search(t))


_AUS = re.compile(r"\b(stopp|stoppe|stoppen|beend|beende|beenden|aus|ausschalt|"
                  r"abschalt|halt|anhalt|pausier|schluss)\w*")


def soll_anschalten(text: str) -> bool:
    """An oder aus? Ausschalten wird ausdruecklich verlangt, sonst einschalten.

    Bewusst so herum: "Aufzeichnung" ohne Zusatz meint eher starten, und ein
    faelschlich gestarteter Mitschnitt ist harmloser als ein faelschlich
    gestoppter -- er ist sichtbar, das Fehlen nicht.
    """
    return not bool(_AUS.search(_normal(text)))
