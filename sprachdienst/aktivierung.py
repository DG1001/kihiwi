"""Aktivierungswort erkennen.

Kein eigenes Wake-Word-Modell: die vortrainierten (openWakeWord & Co.) sind
englisch, ein deutsches Wort braeuchte eigenes Training. Stattdessen wird der
Text benutzt, den die Spracherkennung ohnehin liefert -- sie laeuft mit ~100 ms
je Aeusserung, und die lexikalische Endpoint-Pruefung transkribiert schon
mitten im Satz. Damit kostet die Erkennung praktisch nichts extra.

Erkannt wird unscharf: die Spracherkennung schreibt denselben Laut mal so, mal
anders ("Hiwi", "Hi Wi", "Hiwie"). Ein exakter Vergleich wuerde die Haelfte
verpassen.
"""
import difflib
import re
import unicodedata

from . import konfig

_WORT = re.compile(r"[^\W\d_]+", re.UNICODE)


def _normal(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.replace("ß", "ss")


def erkannt(text: str) -> tuple[bool, str]:
    """Prueft, ob der Text mit dem Aktivierungswort beginnt.

    Gibt (ja, resttext) zurueck. Der Rest ist das, was nach dem
    Aktivierungswort gesagt wurde -- meist die eigentliche Anweisung, denn
    "Hiwi, starte die Aufzeichnung" kommt in einem Atemzug.

    Nur der ANFANG wird geprueft: sonst loest jede Erwaehnung mitten im
    Gespraech aus, und im Labor wird ueber den Assistenten auch geredet.
    """
    woerter = _WORT.findall(text)
    if not woerter:
        return False, ""
    kandidaten = [_normal(w) for w in konfig.AKTIVIERUNG]

    def passt(vorn: str, n: int) -> bool:
        eng = vorn.replace(" ", "")
        for k in kandidaten:
            # Wortzahl muss uebereinstimmen. Ohne das trifft "Der Hiwi" auf
            # "hey hiwi" -- eine Erwaehnung wuerde als Ansprache gelten, und im
            # Labor wird ueber den Assistenten auch geredet.
            if len(k.split()) == n and \
                    difflib.SequenceMatcher(None, vorn, k).ratio() >= konfig.AKTIVIERUNG_MIN:
                return True
            # Zusammengezogen nur gegen einwortige Kandidaten: faengt ein
            # zerlegtes "Hi Wi" ab, ohne beliebige Wortpaare zuzulassen.
            if len(k.split()) == 1 and n > 1 and \
                    difflib.SequenceMatcher(None, eng, k).ratio() >= konfig.AKTIVIERUNG_MIN:
                return True
        return False

    # Bis zu zwei Woerter, damit "Hey Hiwi" und zerlegtes "Hi Wi" mitgehen.
    for n in (2, 1):
        if len(woerter) < n:
            continue
        vorn = _normal(" ".join(woerter[:n]))
        if True:
            if passt(vorn, n):
                rest = text
                # Das Aktivierungswort samt folgender Satzzeichen abschneiden.
                for _ in range(n):
                    rest = _WORT.sub("", rest, count=1)
                return True, rest.lstrip(" ,.;:!?-–—").strip()
    return False, ""
