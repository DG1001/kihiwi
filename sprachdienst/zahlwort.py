"""Deutsche Zahlwörter für die Sprachausgabe.

Piper spricht über eSpeak, und dessen Zahlbehandlung ist bei deutschen Datums-
und Zeitangaben unzuverlässig: „28.08.2026" wird Ziffer für Ziffer samt Punkten
gelesen, „12:38 Uhr" wird zu „zwölf Uhr achtunddreißig Uhr".

Deshalb wird ausgeschrieben, bevor der Text zur Ausgabe geht. Nur der Bereich,
der wirklich vorkommt: Zahlen bis 9999, Ordnungszahlen bis 31, Monatsnamen.
"""
import re

_EINER = ["null", "eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben",
          "acht", "neun", "zehn", "elf", "zwölf", "dreizehn", "vierzehn",
          "fünfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn"]
_ZEHNER = {2: "zwanzig", 3: "dreißig", 4: "vierzig", 5: "fünfzig", 6: "sechzig",
           7: "siebzig", 8: "achtzig", 9: "neunzig"}
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]


def wort(n: int) -> str:
    """Kardinalzahl 0..9999 als Wort."""
    if n < 20:
        return _EINER[n]
    if n < 100:
        z, e = divmod(n, 10)
        return _ZEHNER[z] if e == 0 else f"{'ein' if e == 1 else _EINER[e]}und{_ZEHNER[z]}"
    if n < 1000:
        h, r = divmod(n, 100)
        kopf = ("hundert" if h == 1 else f"{_EINER[h]}hundert")
        return kopf + (wort(r) if r else "")
    t, r = divmod(n, 1000)
    kopf = ("tausend" if t == 1 else f"{wort(t)}tausend")
    return kopf + (wort(r) if r else "")


def ordnungszahl(n: int) -> str:
    """1..31 als Ordnungszahl im Nominativ ("der achtundzwanzigste")."""
    sonder = {1: "erste", 3: "dritte", 7: "siebte", 8: "achte"}
    if n in sonder:
        return sonder[n]
    return wort(n) + ("te" if n < 20 else "ste")


def jahr(n: int) -> str:
    """Jahreszahlen folgen im Deutschen einer eigenen Regel: 1937 ist
    „neunzehnhundertsiebenunddreißig", nicht „eintausendneunhundert...".
    Ab 2000 gilt wieder die normale Form."""
    if 1100 <= n < 2000:
        h, r = divmod(n, 100)
        return f"{wort(h)}hundert" + (wort(r) if r else "")
    return wort(n)


def uhrzeit(stunde: int, minute: int) -> str:
    if minute == 0:
        return f"{wort(stunde)} Uhr"
    return f"{wort(stunde)} Uhr {wort(minute)}"


def datum(tag: int, monat: int, jahr_zahl: int) -> str:
    return f"{ordnungszahl(tag)} {MONATE[monat - 1]} {jahr(jahr_zahl)}"


# --- Ersetzung in beliebigem Text -------------------------------------------
_DATUM = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_UHR   = re.compile(r"\b(\d{1,2}):(\d{2})\s*(Uhr)?", re.I)


def ausschreiben(text: str) -> str:
    """Datums- und Zeitangaben in Wörter überführen.

    Absichtlich nur diese beiden Muster: einzelne Zahlen wie „50 nm" spricht
    eSpeak korrekt, und alles auszuschreiben machte Messwerte unleserlich.
    """
    text = _DATUM.sub(lambda m: datum(int(m[1]), int(m[2]), int(m[3])), text)
    text = _UHR.sub(lambda m: uhrzeit(int(m[1]), int(m[2])), text)
    return text
