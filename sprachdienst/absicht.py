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
    ZEIT         = "zeit"           # Datum oder Uhrzeit
    WECKER       = "wecker"         # Timer oder Erinnerung stellen/abfragen
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

# Datum und Uhrzeit. Eng gefasst: "wann haben wir das gemessen" ist eine Frage
# an die Unterlagen, keine an die Uhr.
_ZEIT = re.compile(
    r"wie (spaet|spat) ist|wieviel uhr|wie viel uhr|"
    r"welche(s|n)? (datum|tag|wochentag)|"
    r"(datum|wochentag|uhrzeit)\b.{0,12}\b(heute|jetzt|gerade|aktuell)|"
    r"(heute|jetzt|gerade|aktuell)\b.{0,12}\b(datum|wochentag|uhrzeit)|"
    r"was fuer ein tag ist|welcher tag ist|den wievielten")

# Verweist die Frage auf eine Quelle, ist nicht die Uhr gemeint.
_ZEIT_NICHT = re.compile(r"protokoll|unterlage|dokument|datei|aufzeichnung|"
                         r"steht|stand|damals|letzte[nrs]?\b|vorige")

# Wissen: Frage nach Sachverhalten. Fragewörter oder ein Fragezeichen genügen.
_FRAGE = re.compile(r"^(wie|was|wo|wer|wann|warum|weshalb|welche\w*|wieviel\w*|"
                    r"wie viel\w*|gibt es|haben wir|hatten wir|kannst du .*sagen)\b|\?")

# Plauderei: kurze Höflichkeiten ohne Sachfrage.
_PLAUDER = re.compile(r"^(danke|dankeschoen|hallo|hi|guten (morgen|tag|abend)|"
                      r"alles klar|ok|okay|passt|gut|super|prima|"
                      r"wie geht|was machst du)\b")


# --- Timer und Erinnerungen --------------------------------------------------
# Ein Zeitwort allein genuegt nicht: "in zehn Minuten ist die Probe fertig" ist
# eine Feststellung, keine Bestellung. Verlangt wird zusaetzlich ein Wort, das
# den Auftrag traegt.
# "time" und "taimer" stehen mit drin: Whisper schrieb "starte ein Time fuer
# 30 Sekunden". Das Vokabular hilft dagegen, faengt es aber nicht immer.
_WECK_WORT = re.compile(r"\b(timer|time|taimer|teimer|wecker|"
                        r"erinnerung(?:en)?|alarm|"
                        r"erinner\w*|weck\w*|sag mir bescheid|"
                        r"gib mir bescheid|melde dich)\b")
_WECK_ZEIGEN = re.compile(r"\b(welche|welcher|wieviel\w*|wie viel\w*|wie lange|"
                          r"zeig\w*|liste|nenne|laeuft|laufen|gibt es|hab ich|"
                          r"habe ich|steht noch|noch)\b")
_WECK_WEG = re.compile(r"\b(loesch\w*|entfern\w*|streich\w*|abbrech\w*|"
                       r"brich\w*|stopp\w*|stoppe?n?|beend\w*|weg|"
                       r"vergiss|absag\w*|abbestell\w*)\b")
_WECK_ALLE = re.compile(r"\b(alle|alles|saemtliche|jede[nr]?)\b")

# Sonderfaelle als GANZE Aeusserung. Im Satz waeren sie mehrdeutig ("loesch
# alle Protokolle"), allein stehend nicht -- und beide sind die natuerliche
# Antwort auf das, was Kiwi selbst vorschlaegt beziehungsweise gerade gestellt
# hat. Ohne sie lief "Sag alle loeschen" ins Leere.
_NUR_ALLE = re.compile(r"^\s*(?:und\s+)?(?:alle|alles)\s+"
                       r"(?:loesch\w*|weg|abbrech\w*|stopp\w*|entfern\w*)"
                       r"\s*[.!?]*\s*$", re.I)
_NUR_RESTZEIT = re.compile(r"^\s*wie\s*(?:lange|viel\s*zeit)"
                           r"(?:\s*(?:noch|ist\s*noch|bleibt|dauert\s*(?:es|das)?))?"
                           r"\s*[.!?]*\s*$", re.I)


def wecker_absicht(text: str) -> str | None:
    """'stellen', 'zeigen', 'loeschen' oder None.

    Getrennt von erkennen(), weil der Dienst das selbst entscheidet und das
    Modell hier gar nicht gefragt wird -- ein Timer, den es zu stellen
    vergisst, faellt erst auf, wenn er nicht klingelt.
    """
    t = _normal(text)
    if _NUR_ALLE.match(t):
        return "loeschen"
    if _NUR_RESTZEIT.match(t):
        return "zeigen"
    if not _WECK_WORT.search(t):
        return None
    if _WECK_WEG.search(t):
        return "loeschen"
    # Erst stellen, dann zeigen: "wie lange laeuft der Timer noch" fragt ab,
    # "erinner mich in 10 Minuten" enthaelt kein Fragewort und stellt.
    from . import wecker as _w
    if _w.deuten(text) and not _WECK_ZEIGEN.search(t):
        return "stellen"
    if _WECK_ZEIGEN.search(t):
        return "zeigen"
    # Kein Zeitpunkt und kein Frage- oder Zeigewort: dann ist das Weckwort nur
    # ein Wort im Satz. Hier stand ein "zeigen" als Auffangfall, und auf
    # "wenn ich es richtig mich in Erinnerung habe, Backscattered und
    # Secondary" antwortete der Dienst mitten im Fachgespraech "Es laeuft
    # gerade kein Timer.". "Erinnerung", "erinnere" und "melde dich" sind
    # alltaegliche Woerter; das Weckwort allein traegt die Entscheidung nicht.
    # Lieber nichts erkennen als das Falsche -- erkennen() faellt dann auf die
    # normale Unterhaltung zurueck.
    return None


def alle_loeschen(text: str) -> bool:
    t = _normal(text)
    return bool(_NUR_ALLE.match(t) or _WECK_ALLE.search(t))


# --- Direktbefehle ohne Aktivierungswort -------------------------------------
# "Sprachaufzeichnung starten/stoppen" wirkt OHNE vorheriges "Kiwi" und ohne
# den Gespraechsmodus zu oeffnen. Zusammengesetztes Wort wie bei den anderen
# Ausloesern: es faellt nicht versehentlich, und im Labor wird ueber die
# Aufzeichnung durchaus geredet ("die Aufzeichnung laeuft ja").
# Nicht auf die genaue Schreibung festnageln: die Erkennung schrieb
# "Sprachaufzeichen und starten". Ein langes Kompositum wird verstuemmelt, das
# Aktivierungswort loest dasselbe Problem seit jeher unscharf.
#
# Der Anker bleibt "sprachauf": damit passt weder "Aufzeichnung" allein noch
# irgendein anderes Wort des Labors. Getrennte Schreibung wird vorher
# zusammengezogen, die Erkennung zerlegt Komposita gern.
# Auch "Audioaufnahme", "Audioaufzeichnung", "Tonaufnahme" -- gemeldet, weil
# genau daran vier Versuche hintereinander scheiterten: das Protokoll zeigt
# "nicht angesprochen: 'Audioaufzeichnung starten.'". Der Sprecher sagt nicht
# das Wort, das im Code steht, und merkt nur, dass nichts passiert.
#
# BEWUSST weiter nur Komposita, kein blosses "Aufzeichnung": im Labor wird
# ueber die Aufzeichnung geredet ("die Aufzeichnung muessen wir noch
# starten"), und ein Direktbefehl wirkt OHNE Aktivierungswort. Ein
# zusammengesetztes Wort faellt nicht versehentlich.
_DIREKT_WORT = re.compile(r"\b(?:sprach|audio|ton)[- ]?(?:auf|aus)\w*")
_DIREKT_AN  = re.compile(r"\b(start\w*|beginn\w*|los|an|anschalt\w*|"
                         r"einschalt\w*|aufnehmen|mitschneiden)\b")
_DIREKT_AUS = re.compile(r"\b(stopp\w*|stop|beend\w*|aus|ausschalt\w*|"
                         r"abschalt\w*|halt|anhalt\w*|schluss|ende)\b")


def direktbefehl(text: str) -> bool | None:
    """True = starten, False = stoppen, None = kein Direktbefehl.

    Bewusst eng: das zusammengesetzte Wort MUSS fallen, dazu ein Tuwort, und
    der Satz muss kurz sein. Ohne die Kuerze wuerde "wir sollten die
    Sprachaufzeichnung nachher mal starten, wenn alle da sind" mitten im
    Gespraech den Mitschnitt anwerfen -- und das ohne jede Ansprache.
    """
    t = _normal(text)
    if not _DIREKT_WORT.search(t):
        return None
    if len(t.split()) > 6:
        return None
    if _DIREKT_AUS.search(t):
        return False
    if _DIREKT_AN.search(t):
        return True
    return None


def erkennen(text: str) -> Absicht:
    t = _normal(text).strip()
    if not t:
        return Absicht.PLAUDEREI

    # Nur wenn nach der JETZIGEN Zeit gefragt wird. "Welches Datum steht im
    # Protokoll?" ist eine Frage an die Unterlagen.
    if _ZEIT.search(t) and not _ZEIT_NICHT.search(t):
        return Absicht.ZEIT

    # Vor der Aufzeichnung: "stopp den Timer" darf nicht als "stopp die
    # Aufzeichnung" durchgehen.
    if wecker_absicht(text):
        return Absicht.WECKER

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
    Absicht.ZEIT:         [],
    Absicht.WECKER:       [],
    # unterlagen_ueberblick nur hier: die Landkarte beantwortet "was gibt es
    # ueberhaupt", nicht "wie hoch ist die Spannung". Bei den anderen Absichten
    # waere sie nur ein teurer Umweg.
    Absicht.WISSEN:       ["dokumente_suchen", "unterlagen_ueberblick", "web_suchen"],
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
    Absicht.ZEIT: "",
    Absicht.WECKER: "",
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
# `_` steht fuer "zusammen, getrennt oder mit Bindestrich". Die Erkennung
# schreibt Komposita variabel -- "Internetsuche", "Internet-Suche" und
# "Internet Suche" kamen alle vor, und nur die erste Form traf.
_ = r"[- ]?"

AUSLOESER = {
    "recherche": rf"internet{_}recherche|netz{_}recherche|web{_}recherche|"
                 rf"recherche{_}auftrag|recherchier(?:e|st|en)?",
    "dokumente": rf"dokumenten{_}recherche|dokumenten{_}suche|akten{_}recherche|"
                 rf"unterlagen{_}recherche",
    # Suche vs. Recherche: der schnelle Weg holt eine Tatsache direkt ueber
    # SearXNG (2-3 s, sofortige Antwort), der lange laesst Hermes suchen,
    # Seiten lesen und verketten (30-40 s, Ergebnis kommt nach). Wer das Wort
    # waehlt, waehlt den Weg -- besser als ein Router, der raet.
    "websuche":  rf"internet{_}suche|netz{_}suche|web{_}suche",
    # Nicht bloss "hermes": ueber den Agenten wird im Labor geredet, und die
    # Durchreichung gibt einer gesprochenen Anweisung Zugriff auf Dateien,
    # Browser und Codeausfuehrung. Ein zusammengesetztes Wort faellt nicht
    # versehentlich. Getrennte Schreibung mit, weil die Erkennung
    # Zusammensetzungen gern auseinanderzieht.
    "hermes":    rf"hermes{_}aufgabe|hermes{_}auftrag",
    # Holt neue Staende aus Git und Nextcloud und indiziert nach. Ein eigenes
    # Wort, weil es ein paar Sekunden dauert und Netzverkehr ausloest -- das
    # soll niemand nebenbei anstossen.
    "abgleich":  rf"wissens{_}abgleich|unterlagen{_}abgleich|quellen{_}abgleich|"
                 rf"wissens{_}auffrischung",
    # Bricht einen laufenden Rechercheauftrag ab. Muss VOR "recherche" geprueft
    # werden, sonst startet "Recherche abbrechen" eine neue.
    #
    # Es gab keinen Weg dafuer: ein Auftrag mit leerem Thema lief drei Minuten,
    # blockierte den naechsten -- und auf "kannst du die Recherche abbrechen?"
    # antwortete das Modell aus der Vorstellung ("ich habe keine laufenden
    # Prozesse"). Ohne Werkzeug erfindet es eines.
    "abbruch":   rf"(?:recherche|auftrag|hermes{_}aufgabe|hermes{_}auftrag)"
                 rf"{_}?(?:abbrechen|abbruch|stoppen|stopp|beenden|abblasen)|"
                 rf"(?:brich|stoppe|beende)\s+(?:die\s+|den\s+)?"
                 rf"(?:recherche|auftrag)(?:\s+ab)?",
}
# "Hilfe" allein faellt im Labor staendig ("ich brauche Hilfe beim Mikroskop").
# Das liess sich zwar mit Stellungsregeln abfangen, aber solche Waechter sind
# Naeherungen -- ein zusammengesetztes Wort braucht keine. Gleiche Ueberlegung
# wie bei "Hermesaufgabe".
AUSLOESER["hilfe"] = rf"kiwi{_}hilfe|befehls{_}liste|was kannst du"

# Sonderfall: "Kiwi, Hilfe" -- nach dem Abschneiden des Aktivierungsworts
# bleibt nur "Hilfe" uebrig. Als GANZE Aeusserung ist das eindeutig; in einem
# Satz wie "ich brauche Hilfe beim Mikroskop" nicht, und der faellt hier durch.
_NUR_HILFE = re.compile(r"^\s*(hilfe|hilfe bitte|bitte hilfe)\s*[.!?]?\s*$", re.I)

_AUSLOESER_RE = {art: re.compile(rf"\b(?:{muster})\b", re.I)
                 for art, muster in AUSLOESER.items()}

# Beschreibung je Ausloeser -- die Hilfe wird daraus erzeugt, damit sie nicht
# veraltet, sobald jemand die Tabelle oben erweitert.
BESCHREIBUNG = {
    "websuche":  ("Internetsuche", "eine Sache schnell im Netz nachsehen"),
    "recherche": ("Internetrecherche", "gründliche Recherche, Ergebnis kommt nach"),
    "dokumente": ("Dokumentenrecherche", "in unseren eigenen Unterlagen suchen"),
    "hermes":    ("Hermesaufgabe", "Anweisung unverändert an den Rechercheagenten"),
    "abgleich":  ("Wissensabgleich", "neue Stände aus Repo und Cloud holen"),
    "abbruch":   ("Recherche abbrechen", "einen laufenden Auftrag beenden"),
    "hilfe":     ("Kiwihilfe", "diese Liste"),
}


def hilfe_zeilen() -> list[tuple[str, str]]:
    """(Wort, Erklaerung) fuer alle Ausloeser, in der Reihenfolge von
    BESCHREIBUNG -- "Kiwihilfe" zuletzt.

    Bewusst keine eigene Liste: eine zweite Aufzaehlung veraltet, sobald jemand
    die Tabelle oben erweitert. Genau das war passiert, "Wissensabgleich" fehlte
    in der Hilfe."""
    zeilen = [(a, BESCHREIBUNG[a]) for a in BESCHREIBUNG]
    zeilen.sort(key=lambda z: z[0] == "hilfe")
    return [e for _, e in zeilen]


def ausloeser(text: str):
    """Gibt (art, thema) zurueck, wenn ein Ausloesewort fiel, sonst (None, "").

    Reihenfolge: der spezifischste zuerst. "Dokumentenrecherche" enthaelt
    "recherche" -- ohne feste Reihenfolge wuerde daraus ein Internetauftrag.
    """
    if _NUR_HILFE.match(text):
        return "hilfe", ""

    # abbruch vor recherche: "Recherche abbrechen" darf keine neue starten.
    for art in ("hilfe", "abbruch", "abgleich", "hermes", "dokumente",
                "websuche", "recherche"):
        m = _AUSLOESER_RE[art].search(text)
        if not m:
            continue
        return art, _thema(text, m)
    return None, ""


def _thema(text: str, m) -> str:
    # Thema ist der Rest ohne Ausloeser und Fuellwoerter davor.
    rest = (text[:m.start()] + " " + text[m.end():])
    rest = re.sub(r"\s+", " ", rest).strip()
    # Satzzeichen, die am Ausloeser klebten ("Hermesaufgabe: oeffne ...").
    rest = rest.strip(" ,.:;–—-")
    # Fuellwoerter vorne wiederholt abraeumen: "Bitte mach eine ... zum X"
    # laesst sonst "eine zum X" stehen.
    fueller = (r"^(bitte|mal|doch|kiwi|mach|mache|starte|beginne|gib mir|"
               r"eine|einen|ein|die|der|das|zu|zum|zur|ueber|über|nach|"
               r"fuer|für|mir|uns|thema|thematik|sache|frage)\b[\s,]*")
    vorher = None
    while rest != vorher:
        vorher = rest
        rest = re.sub(fueller, "", rest, flags=re.I).strip(" ,.:;–—-")
    # LEER, wenn nichts uebrig bleibt -- nicht der ganze Text.
    #
    # Vorher stand hier `rest or text`. Auf "Hermes Aufgabe:" (ohne Auftrag)
    # wurde damit das Ausloesewort selbst zur Forschungsfrage: der Agent lief
    # drei Minuten durch Dateien, blockierte die naechste Anfrage und war nicht
    # abzubrechen. Wer entscheiden will, was ein leeres Thema bedeutet, braucht
    # die Information, DASS es leer ist -- der ganze Text steht dem Aufrufer
    # ohnehin zur Verfuegung.
    return rest


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
