"""Anbindung an Ornith (vLLM).

Das Modell gilt als MOEGLICHERWEISE ABWESEND. Diese Maschine ist auch ein
Modell-Pruefstand; jedes `model-switch` nimmt dem Assistenten fuer ein bis zwei
Minuten das Gehirn. Der Dienst muss das aushalten, ohne stumm zu werden --
Aufzeichnung und Transkription laufen weiter, der Monitor zeigt es an.
"""
import asyncio, json, logging, re, threading, urllib.error, urllib.request
from . import konfig

log = logging.getLogger("kihiwi.llm")

# Der Name, unter dem der Server das Modell gerade anbietet. Wird bei einem
# 404 einmal nachgeschlagen.
#
# **Warum das noetig ist.** Auf dieser Maschine teilen sich mehrere
# Anwendungen ein Modell, und wer umschaltet, aendert damit den
# `served-model-name`. Der Sprachdienst laeuft dabei durch -- er merkte den
# Wechsel bisher NICHT und lief in einen 404 auf jede Anfrage, bis jemand ihn
# neu startete. Am 14.09.2026 stand er so sechs Tage lang, ohne dass es
# auffiel; gemeldet wurde es erst, als jemand mit ihm sprach.
#
# Die Pruefung beim Start (dienste.sh) reicht nicht: der Wechsel passiert
# waehrend des Betriebs.
_MODELL_JETZT: str | None = None


def _modellname() -> str:
    return _MODELL_JETZT or konfig.LLM_MODEL


def _modell_nachschlagen() -> str | None:
    """Den angebotenen Namen holen. None, wenn nichts zu holen ist."""
    global _MODELL_JETZT
    try:
        with urllib.request.urlopen(f"{konfig.LLM_URL}/models", timeout=5) as a:
            neu = json.load(a)["data"][0]["id"]
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as e:
        log.warning("Modellname nicht abrufbar: %r", e)
        return None
    if neu != _modellname():
        log.warning("Modell hat gewechselt: %r -> %r", _modellname(), neu)
        _MODELL_JETZT = neu
        return neu
    return None


def _anfragen(rumpf: dict, timeout: int):
    """Eine Anfrage an den Motor. Bei 404 einmal mit frisch geholtem Namen.

    Der Modellwechsel ist der einzige Fehler, der sich von hier aus heilen
    laesst -- und der einzige, der sonst tagelang unbemerkt bleibt. Alle
    Anfragen laufen durch diese Stelle, damit es nur eine gibt, die heilt.
    """
    for letzter in (False, True):
        rumpf["model"] = _modellname()
        req = urllib.request.Request(
            f"{konfig.LLM_URL}/chat/completions",
            data=json.dumps(rumpf).encode(),
            headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if letzter or e.code != 404 or not _modell_nachschlagen():
                raise
            log.info("Modellname aktualisiert, wiederhole die Anfrage")


# Satzende: Punkt/Frage/Ausruf gefolgt von Leerraum oder Textende. Die
# Abkuerzungen davor abzufangen lohnt nicht -- ein zu frueh geschnittener Satz
# klingt in der Sprachausgabe nur nach einer Atempause.
# Nicht nach einer Ziffer trennen: "der 28. August" und "die 3. Messung" sind
# Ordnungszahlen, keine Satzenden. Ungetrennt klingt hoechstens ein Satz zu
# lang; falsch getrennt hoert man die Luecke mitten im Datum.
_SATZENDE = re.compile(r'(?<![0-9]\.)(?<=[.!?])\s+')
# Nur fuer den ERSTEN Brocken: auch an Komma, Semikolon oder Gedankenstrich
# trennen. Der erste Brocken bestimmt die gefuehlte Latenz -- er soll kurz
# sein, die folgenden duerfen ganze Saetze bleiben. Gemessen kostete ein
# 96-Zeichen-Satz 346 ms im LLM plus 268 ms im TTS, bevor der erste Ton kam.
_TEILSATZ = re.compile(r'(?<=[,;:—–])\s+')
_WERKZEUG_ROH = re.compile(r'<tool_call>\s*(.*?)\s*</tool_call>', re.S)
ERSTER_MIN = 25          # kuerzer klingt abgehackt
ERSTER_MAX = 60          # laenger kostet unnoetig Zeit


def _strom(nachrichten, max_tokens, temperatur, schieb, werkzeuge=None):
    """schieb(x) legt x threadsicher in die asyncio.Queue des Aufrufers.

    Geschoben wird ("text", stueck) oder ("werkzeug", teilstueck). Werkzeug-
    aufrufe kommen bei vLLM stueckweise: erst Name und Kennung, dann die
    Argumente als JSON-Fragmente ueber mehrere Deltas.
    """
    try:
        rumpf = {**konfig.LLM_ZUSATZ, "stream": True,
                 "max_tokens": max_tokens, "temperature": temperatur,
                 "messages": nachrichten}
        if werkzeuge:
            rumpf["tools"] = werkzeuge
            rumpf["tool_choice"] = "auto"
        for roh in _anfragen(rumpf, 60):
            zeile = roh.decode().strip()
            if not zeile.startswith("data: ") or zeile.endswith("[DONE]"):
                continue
            delta = json.loads(zeile[6:])["choices"][0]["delta"]
            for w in (delta.get("tool_calls") or []):
                schieb(("werkzeug", w))
            d = delta.get("content")
            if d:
                schieb(("text", d))
    except urllib.error.HTTPError as e:
        # Stillschweigend zu scheitern hat schon einmal eine halbe Stunde
        # gekostet: der Dienst antwortete einfach nicht mehr.
        try:
            log.error("vLLM %s: %s", e.code, e.read().decode()[:400])
        except Exception:
            log.error("vLLM %s", e.code)
    except (urllib.error.URLError, OSError, TimeoutError, KeyError,
            json.JSONDecodeError) as e:
        log.error("LLM-Strom abgebrochen: %r", e)
    except BaseException as e:
        log.exception("LLM-Strom unerwartet: %r", e)
    finally:
        schieb(None)


def _einmal(nachrichten, max_tokens, temperatur, werkzeuge, timeout=60):
    """Eine Runde OHNE Streaming. Gibt (text, rufe) zurueck.

    Der Streaming-Pfad von vLLM stellt sich mit mehreren Werkzeugen tot: mit
    einem Werkzeug lief er, mit dreien blieb die Antwort komplett aus, ohne
    Fehler und ohne dass die Anfrage im vLLM-Protokoll auftauchte. Halbierung
    am 27.08.2026 belegt. Der ungestreamte Pfad ist davon nicht betroffen.
    """
    rumpf = {**konfig.LLM_ZUSATZ,
             "model": _modellname(), "max_tokens": max_tokens,
             "temperature": temperatur, "messages": nachrichten}
    if werkzeuge:
        rumpf["tools"] = werkzeuge
        rumpf["tool_choice"] = "auto"
    a = json.load(_anfragen(rumpf, timeout))
    m = a["choices"][0]["message"]
    text = m.get("content") or ""
    rufe = m.get("tool_calls") or []
    # Der Parser laesst gelegentlich seine Rohmarkierung im Text stehen. Ohne
    # das Herausschneiden spricht der Assistent woertlich "tool call" aus.
    # Der Parser laesst seine Rohmarkierung gelegentlich im Text stehen; ohne
    # Herausschneiden spricht der Assistent woertlich "tool call" aus. Nur
    # entfernen, nicht daraus Aufrufe rekonstruieren -- ein Versuch, das zu
    # bergen, hat die Werkzeugschleife zum Haengen gebracht.
    text = _WERKZEUG_ROH.sub("", text).strip()
    return text, rufe


async def antwort_mit_werkzeugen(frage: str, verlauf, werkzeuge, ausfuehren,
                                 system: str | None = None,
                                 system_antwort: str | None = None,
                                 max_tokens: int = 200, runden: int = 3):
    """Wie antwort_saetze, aber das Modell darf Werkzeuge aufrufen.

    Liefert ("satz", text) fuer Sprachausgabe und ("werkzeug", name, args,
    ergebnis) nach jeder Ausfuehrung. `ausfuehren(name, args)` ist eine
    Korutine des Aufrufers -- der Sprachdienst weiss, wie man aufzeichnet,
    dieses Modul nicht.

    `runden` bremst die Schleife: ohne Deckel koennte das Modell endlos
    Werkzeuge aufrufen.

    **Zwei Prompts, nicht einer.** `system` gilt fuer die Werkzeugrunden,
    `system_antwort` fuer die Schlussantwort. Gemessen am 27.08.2026: mit dem
    Sprechstil-Prompt ("hoechstens zwei Saetze") im Werkzeugaufruf rief das
    Modell in 0 von 3 Faellen ein Werkzeug, ohne ihn in 3 von 3. Es folgt der
    Anweisung, kurz zu ANTWORTEN -- und antwortet eben, statt zu handeln. Der
    Sprechstil gehoert deshalb nur an die Antwort.
    """
    # Fuer die Werkzeugentscheidung ohne Absagen: sie sind der haeufigste Grund,
    # warum das Modell ein vorhandenes Werkzeug nicht mehr benutzt.
    nachrichten = [{"role": "system", "content": system or konfig.SYSTEM_PROMPT}]
    nachrichten += ohne_absagen(verlauf)
    nachrichten.append({"role": "user", "content": frage})

    # Ergebnisse der Werkzeuge, als reiner Text gesammelt.
    befunde: list[str] = []

    def _antwortlauf():
        """Saubere Nachrichtenfolge fuer die Schlussantwort.

        Die Werkzeug-Strukturen (tool_calls, role=tool) werden NICHT
        mitgeschleppt: ein Aufruf ohne `tools`, aber mit solchen Eintraegen in
        der Vorgeschichte, lieferte eine leere Antwort -- ohne Fehler, ohne
        Protokollzeile. Stattdessen stehen die Befunde als Text in der Frage.
        """
        frage_mit_befunden = frage
        if befunde:
            frage_mit_befunden = (
                "Das haben deine Werkzeuge geliefert:\n\n"
                + "\n\n".join(befunde)
                + f"\n\nBeantworte damit: {frage}")
        return ([{"role": "system", "content": system_antwort or system
                  or konfig.SYSTEM_PROMPT}]
                + list(verlauf or [])
                + [{"role": "user", "content": frage_mit_befunden}])

    for runde in range(runden):
        try:
            text, rufe = await asyncio.to_thread(
                _einmal, nachrichten, max_tokens, 0.3, werkzeuge)
        except Exception as e:
            log.error("Werkzeugrunde gescheitert: %r", e)
            return

        if not rufe:
            # Schlussantwort IMMER neu und gestreamt, mit dem Antwort-Prompt.
            # Den Text aus der Werkzeugrunde zu uebernehmen sparte zwar einen
            # Durchlauf, brachte aber Aufzaehlungen und Fettschrift in die
            # Sprachausgabe -- die Werkzeugrunde traegt bewusst keinen
            # Sprechstil, sonst ruft das Modell keine Werkzeuge auf.
            async for satz in _saetze(_antwortlauf(), max_tokens, 0.3):
                yield ("satz", satz)
            return

        nachrichten.append({"role": "assistant", "content": text or "",
                            "tool_calls": rufe})
        for i, r in enumerate(rufe):
            f = r.get("function") or {}
            try:
                args = json.loads(f.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            # Erst ankuendigen, dann ausfuehren: eine Dokumentensuche dauert
            # ueber eine Sekunde, ein Rechercheauftrag Minuten. Wer nicht weiss,
            # welchen Weg der Assistent nimmt, wartet ins Leere.
            yield ("werkzeug_beginnt", f.get("name", ""), args)
            ergebnis = await ausfuehren(f.get("name", ""), args)
            befunde.append(str(ergebnis))
            yield ("werkzeug", f.get("name", ""), args, ergebnis)
            nachrichten.append({"role": "tool",
                                "tool_call_id": r.get("id") or f"ruf{i}",
                                "content": str(ergebnis)})

    # Runden aufgebraucht, ohne dass eine Antwort kam: das Modell hat sich in
    # Suchen verrannt. Statt zu schweigen einmal ohne Werkzeuge antworten
    # lassen -- es hat inzwischen genug Material gesehen.
    try:
        async for satz in _saetze(_antwortlauf(), max_tokens, 0.3):
            yield ("satz", satz)
    except Exception as e:
        log.error("Schlussantwort gescheitert: %r", e)


async def erzwinge_werkzeug(frage: str, verlauf, werkzeuge, name: str,
                            system: str | None = None, timeout: float = 20.0):
    """Zwingt das Modell, genau dieses Werkzeug aufzurufen, und gibt die
    Argumente zurueck (oder None).

    Gebraucht als Nachfassen: das Modell behauptet gelegentlich eine Aenderung,
    ohne das Werkzeug aufgerufen zu haben. Statt nur zu widersprechen, wird der
    Aufruf hier nachgeholt -- der Nutzer bekommt, worum er gebeten hat.
    """
    nachrichten = [{"role": "system", "content": system or konfig.SYSTEM_PROMPT}]
    nachrichten += list(verlauf or [])
    nachrichten.append({"role": "user", "content": frage})

    def _p():
        a = json.load(_anfragen({
            **konfig.LLM_ZUSATZ, "max_tokens": 80, "temperature": 0,
            "messages": nachrichten, "tools": werkzeuge,
            "tool_choice": {"type": "function", "function": {"name": name}},
        }, timeout))
        rufe = a["choices"][0]["message"].get("tool_calls") or []
        for r in rufe:
            if r["function"]["name"] == name:
                return json.loads(r["function"]["arguments"] or "{}")
        return None

    try:
        return await asyncio.to_thread(_p)
    except Exception:
        return None


async def erreichbar(timeout: float = 2.0) -> bool:
    def _p():
        with urllib.request.urlopen(f"{konfig.LLM_URL}/models", timeout=timeout) as r:
            return konfig.LLM_MODEL in r.read().decode()
    try:
        return await asyncio.to_thread(_p)
    except Exception:
        return False


async def p_fertig(text: str, timeout: float = 5.0) -> float:
    """P(Aeusserung abgeschlossen) fuer das lexikalische Endpointing.

    Punkt und Komma werden entfernt, das Fragezeichen NICHT: Whisper haengt
    immer ein Satzzeichen an, auch mitten im Satz (der erfundene Punkt hob
    "Schreib ins Protokoll" von P=0,010 auf 0,731), das Fragezeichen dagegen
    stammt aus der Intonation und traegt echtes Signal.
    """
    import math
    text = text.rstrip().rstrip(".,;:…").rstrip()
    if not text:
        return 0.0
    sys_p = ("Du bekommst ein laufendes Transkript eines Sprechers. Entscheide, ob "
             "er seinen Satz abgeschlossen hat oder ob er mitten im Satz Luft holt. "
             "Antworte ausschliesslich mit FERTIG oder WEITER.")

    def _p():
        a = json.load(_anfragen(
            {**konfig.LLM_ZUSATZ, "max_tokens": 1,
             "temperature": 0, "logprobs": True, "top_logprobs": 20,
             "messages": [{"role": "system", "content": sys_p},
                          {"role": "user", "content": text}]}, timeout))
        top = a["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        pf = pw = 0.0
        for t in top:
            # Erstes Token ist "WE" bzw. "FER", nicht das ganze Wort.
            tok = t["token"].strip().upper(); p = math.exp(t["logprob"])
            if tok and "FERTIG".startswith(tok): pf += p
            elif tok and "WEITER".startswith(tok): pw += p
        return pf / (pf + pw) if (pf + pw) > 0 else 0.0

    try:
        return await asyncio.to_thread(_p)
    except Exception:
        # Kein LLM erreichbar -> keine Aussage. Der Aufrufer faellt auf die
        # Decke zurueck, statt vorschnell abzuschneiden.
        return 0.0
