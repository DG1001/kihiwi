"""Anbindung an Ornith (vLLM).

Das Modell gilt als MOEGLICHERWEISE ABWESEND. Diese Maschine ist auch ein
Modell-Pruefstand; jedes `model-switch` nimmt dem Assistenten fuer ein bis zwei
Minuten das Gehirn. Der Dienst muss das aushalten, ohne stumm zu werden --
Aufzeichnung und Transkription laufen weiter, der Monitor zeigt es an.
"""
import asyncio, json, logging, re, threading, urllib.error, urllib.request
from . import konfig

log = logging.getLogger("kihiwi.llm")

# Satzende: Punkt/Frage/Ausruf gefolgt von Leerraum oder Textende. Die
# Abkuerzungen davor abzufangen lohnt nicht -- ein zu frueh geschnittener Satz
# klingt in der Sprachausgabe nur nach einer Atempause.
_SATZENDE = re.compile(r'(?<=[.!?])\s+')
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
        rumpf = {"model": konfig.LLM_MODEL, "stream": True,
                 "max_tokens": max_tokens, "temperature": temperatur,
                 "messages": nachrichten}
        if werkzeuge:
            rumpf["tools"] = werkzeuge
            rumpf["tool_choice"] = "auto"
        req = urllib.request.Request(
            f"{konfig.LLM_URL}/chat/completions",
            data=json.dumps(rumpf).encode(),
            headers={"Content-Type": "application/json"})
        for roh in urllib.request.urlopen(req, timeout=60):
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


async def antwort_text(system: str, frage: str, max_tokens: int = 400,
                        temperatur: float = 0.2) -> str:
    """Gibt die vollstaendige Antwort UNVERAENDERT zurueck.

    Fuer erzeugte Dokumente. antwort_saetze* zerlegt in Saetze -- das ist im
    Sprachpfad genau richtig und in Markdown falsch: beim Wiederzusammensetzen
    gehen Zeilenumbrueche verloren und Aufzaehlungen laufen auf eine Zeile.
    """
    teile = []
    async for stueck in _roh([{"role": "system", "content": system},
                              {"role": "user", "content": frage}],
                             max_tokens, temperatur):
        teile.append(stueck)
    return "".join(teile).strip()


async def _roh(nachrichten, max_tokens, temperatur):
    """Liefert die Token-Stuecke, wie sie kommen."""
    schleife = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    schieb = lambda x: schleife.call_soon_threadsafe(q.put_nowait, x)
    threading.Thread(target=_strom, args=(nachrichten, max_tokens, temperatur, schieb),
                     daemon=True).start()
    while True:
        stueck = await q.get()
        if stueck is None:
            return
        if stueck[0] == "text":
            yield stueck[1]


async def antwort_saetze_roh(system: str, frage: str, max_tokens: int = 400,
                             temperatur: float = 0.2):
    """Wie antwort_saetze, aber mit eigenem System-Prompt und ohne Verlauf.

    Gebraucht vom Dokumentationspfad: dessen Aufgaben (Korrektur,
    Zusammenfassung) haben nichts mit dem Sprachassistenten zu tun, und
    konfig.SYSTEM_PROMPT ist auf gesprochene Kurzantworten getrimmt --
    "hoechstens zwei Saetze" waere fuer ein Protokoll fatal.
    """
    async for satz in _saetze([{"role": "system", "content": system},
                               {"role": "user", "content": frage}],
                              max_tokens, temperatur):
        yield satz


async def antwort_saetze(frage: str, verlauf=None, max_tokens: int = 160):
    """Liefert die Antwort SATZWEISE, sobald ein Satz vollstaendig ist.

    Das ist der Grund, warum die gefuehlte Latenz nur bis zum ERSTEN Satz
    zaehlt: waehrend das Modell weiterschreibt, spricht Piper schon.
    """
    nachrichten = [{"role": "system", "content": konfig.SYSTEM_PROMPT}]
    nachrichten += list(verlauf or [])
    nachrichten.append({"role": "user", "content": frage})
    async for satz in _saetze(nachrichten, max_tokens, 0.3):
        yield satz


async def _saetze(nachrichten, max_tokens, temperatur):
    # Der Thread SCHIEBT, die Schleife fragt nicht ab -- sonst haengt er auf
    # einer leeren Queue, wenn die Antwort abgebrochen wird, und blockiert das
    # Beenden des Dienstes um 120 Sekunden.
    schleife = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    schieb = lambda x: schleife.call_soon_threadsafe(q.put_nowait, x)
    threading.Thread(target=_strom, args=(nachrichten, max_tokens, temperatur, schieb),
                     daemon=True).start()

    puffer = ""
    erster = True
    while True:
        posten = await q.get()
        if posten is None:
            break
        if posten[0] != "text":
            continue
        stueck = posten[1]
        puffer += stueck

        if erster and len(puffer) >= ERSTER_MIN:
            teile = _TEILSATZ.split(puffer, maxsplit=1)
            if len(teile) == 2 and ERSTER_MIN <= len(teile[0]) <= ERSTER_MAX:
                kopf, puffer = teile[0].strip(), teile[1]
                erster = False
                if kopf:
                    yield kopf
                    continue

        while True:
            teile = _SATZENDE.split(puffer, maxsplit=1)
            if len(teile) < 2:
                break
            satz, puffer = teile[0].strip(), teile[1]
            if satz:
                erster = False
                yield satz
    if puffer.strip():
        yield puffer.strip()


class _Teiler:
    """Zerlegt einen Token-Strom satzweise, ersten Brocken frueher.

    Ausgelagert, weil Dialog und Werkzeugschleife dieselbe Logik brauchen:
    der erste Brocken bestimmt die gefuehlte Latenz und wird schon am Komma
    getrennt, die folgenden erst am Satzende.
    """

    def __init__(self):
        self.puffer = ""
        self.erster = True

    def dazu(self, stueck: str):
        self.puffer += stueck
        if self.erster and len(self.puffer) >= ERSTER_MIN:
            teile = _TEILSATZ.split(self.puffer, maxsplit=1)
            if len(teile) == 2 and ERSTER_MIN <= len(teile[0]) <= ERSTER_MAX:
                kopf, self.puffer = teile[0].strip(), teile[1]
                self.erster = False
                if kopf:
                    yield kopf
                    return
        while True:
            teile = _SATZENDE.split(self.puffer, maxsplit=1)
            if len(teile) < 2:
                break
            satz, self.puffer = teile[0].strip(), teile[1]
            if satz:
                self.erster = False
                yield satz

    def rest(self):
        r, self.puffer = self.puffer.strip(), ""
        return r


def _einmal(nachrichten, max_tokens, temperatur, werkzeuge, timeout=60):
    """Eine Runde OHNE Streaming. Gibt (text, rufe) zurueck.

    Der Streaming-Pfad von vLLM stellt sich mit mehreren Werkzeugen tot: mit
    einem Werkzeug lief er, mit dreien blieb die Antwort komplett aus, ohne
    Fehler und ohne dass die Anfrage im vLLM-Protokoll auftauchte. Halbierung
    am 27.08.2026 belegt. Der ungestreamte Pfad ist davon nicht betroffen.
    """
    rumpf = {"model": konfig.LLM_MODEL, "max_tokens": max_tokens,
             "temperature": temperatur, "messages": nachrichten}
    if werkzeuge:
        rumpf["tools"] = werkzeuge
        rumpf["tool_choice"] = "auto"
    req = urllib.request.Request(
        f"{konfig.LLM_URL}/chat/completions", data=json.dumps(rumpf).encode(),
        headers={"Content-Type": "application/json"})
    a = json.load(urllib.request.urlopen(req, timeout=timeout))
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
    nachrichten = [{"role": "system", "content": system or konfig.SYSTEM_PROMPT}]
    nachrichten += list(verlauf or [])
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
        req = urllib.request.Request(
            f"{konfig.LLM_URL}/chat/completions",
            data=json.dumps({
                "model": konfig.LLM_MODEL, "max_tokens": 80, "temperature": 0,
                "messages": nachrichten, "tools": werkzeuge,
                "tool_choice": {"type": "function", "function": {"name": name}},
            }).encode(),
            headers={"Content-Type": "application/json"})
        a = json.load(urllib.request.urlopen(req, timeout=timeout))
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
        req = urllib.request.Request(
            f"{konfig.LLM_URL}/chat/completions",
            data=json.dumps({"model": konfig.LLM_MODEL, "max_tokens": 1,
                             "temperature": 0, "logprobs": True, "top_logprobs": 20,
                             "messages": [{"role": "system", "content": sys_p},
                                          {"role": "user", "content": text}]}).encode(),
            headers={"Content-Type": "application/json"})
        a = json.load(urllib.request.urlopen(req, timeout=timeout))
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
