"""Anbindung an Ornith (vLLM).

Das Modell gilt als MOEGLICHERWEISE ABWESEND. Diese Maschine ist auch ein
Modell-Pruefstand; jedes `model-switch` nimmt dem Assistenten fuer ein bis zwei
Minuten das Gehirn. Der Dienst muss das aushalten, ohne stumm zu werden --
Aufzeichnung und Transkription laufen weiter, der Monitor zeigt es an.
"""
import asyncio, json, re, threading, urllib.error, urllib.request
from . import konfig

# Satzende: Punkt/Frage/Ausruf gefolgt von Leerraum oder Textende. Die
# Abkuerzungen davor abzufangen lohnt nicht -- ein zu frueh geschnittener Satz
# klingt in der Sprachausgabe nur nach einer Atempause.
_SATZENDE = re.compile(r'(?<=[.!?])\s+')
# Nur fuer den ERSTEN Brocken: auch an Komma, Semikolon oder Gedankenstrich
# trennen. Der erste Brocken bestimmt die gefuehlte Latenz -- er soll kurz
# sein, die folgenden duerfen ganze Saetze bleiben. Gemessen kostete ein
# 96-Zeichen-Satz 346 ms im LLM plus 268 ms im TTS, bevor der erste Ton kam.
_TEILSATZ = re.compile(r'(?<=[,;:—–])\s+')
ERSTER_MIN = 25          # kuerzer klingt abgehackt
ERSTER_MAX = 60          # laenger kostet unnoetig Zeit


def _strom(nachrichten, max_tokens, schieb):
    """schieb(x) legt x threadsicher in die asyncio.Queue des Aufrufers."""
    try:
        req = urllib.request.Request(
            f"{konfig.LLM_URL}/chat/completions",
            data=json.dumps({"model": konfig.LLM_MODEL, "stream": True,
                             "max_tokens": max_tokens, "temperature": 0.3,
                             "messages": nachrichten}).encode(),
            headers={"Content-Type": "application/json"})
        for roh in urllib.request.urlopen(req, timeout=60):
            zeile = roh.decode().strip()
            if not zeile.startswith("data: ") or zeile.endswith("[DONE]"):
                continue
            d = json.loads(zeile[6:])["choices"][0]["delta"].get("content")
            if d:
                schieb(d)
    except (urllib.error.URLError, OSError, TimeoutError, KeyError, json.JSONDecodeError):
        pass
    finally:
        schieb(None)


async def antwort_saetze(frage: str, verlauf=None, max_tokens: int = 160):
    """Liefert die Antwort SATZWEISE, sobald ein Satz vollstaendig ist.

    Das ist der Grund, warum die gefuehlte Latenz nur bis zum ERSTEN Satz
    zaehlt: waehrend das Modell weiterschreibt, spricht Piper schon.
    """
    nachrichten = [{"role": "system", "content": konfig.SYSTEM_PROMPT}]
    nachrichten += list(verlauf or [])
    nachrichten.append({"role": "user", "content": frage})

    # Der Thread SCHIEBT, die Schleife fragt nicht ab -- sonst haengt er auf
    # einer leeren Queue, wenn die Antwort abgebrochen wird, und blockiert das
    # Beenden des Dienstes um 120 Sekunden.
    schleife = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    schieb = lambda x: schleife.call_soon_threadsafe(q.put_nowait, x)
    threading.Thread(target=_strom, args=(nachrichten, max_tokens, schieb),
                     daemon=True).start()

    puffer = ""
    erster = True
    while True:
        stueck = await q.get()
        if stueck is None:
            break
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
