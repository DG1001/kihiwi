"""Der Sprachdienst: nimmt Audio vom Client, gibt Sprache und Zustand zurueck.

Bewusst EIN Dienst mit schmaler Schnittstelle, getrennt vom Agenten. Der
Agent (heute Ornith direkt, spaeter vielleicht Hermes) haengt hinten dran und
ist austauschbar, ohne dass Turn-Taking und Audio neu gebaut werden muessen.

Protokoll auf /audio
  Client -> Dienst : Binaerrahmen = PCM int16 mono 16 kHz
                     Textrahmen   = {"befehl": ...}
  Dienst -> Client : Textrahmen   = {"typ": "zustand"|"text"|"ton_ende", ...}
                     Binaerrahmen = PCM int16 mono (Sprachausgabe, Rate in
                                    der vorangehenden "ton"-Nachricht)
Auf /monitor liegt nur der Zustand -- fuer den Bildschirm im Labor.
"""
import asyncio, http, json, logging, re, signal, time
import numpy as np
import websockets
from websockets.asyncio.server import serve

from wissen import index as wissen_index, web as wissen_web

from . import aktivierung, doku, konfig, llm, stt, tts
from .turn import Turnerkenner
from .zustand import Phase, Zustandshalter

log = logging.getLogger("kihiwi")
HALTER = Zustandshalter()
SEITEN = {"/": "monitor.html", "/index.html": "monitor.html",
          "/klient": "klient.html", "/klient.html": "klient.html"}


# ---------------------------------------------------------------- Gesundheit
async def gesundheit():
    """Ornith kann jederzeit verschwinden -- diese Maschine ist auch ein
    Pruefstand. Der Dienst bleibt trotzdem oben: Aufzeichnung und
    Transkription haengen nicht am LLM."""
    while True:
        llm_da, stt_da = await asyncio.gather(llm.erreichbar(), stt.erreichbar())
        hinweis = ""
        if not stt_da:
            hinweis = "Spracherkennung nicht erreichbar"
        elif not llm_da:
            hinweis = "Modell nicht erreichbar — es wird weiter aufgezeichnet"
        HALTER.setzen(llm_da=llm_da, stt_da=stt_da, hinweis=hinweis)
        await asyncio.sleep(5)


# Behauptet die Antwort eine Aenderung an der Aufzeichnung? Grob, absichtlich:
# lieber einmal zu viel den wahren Zustand nachschieben als eine unbemerkte
# Falschaussage stehen lassen.
# Kuendigt die Antwort ein Nachschlagen an, ohne dass gesucht wurde? Dasselbe
# Muster wie bei der Aufzeichnung: das Modell sagt, was es tun wird, statt es
# zu tun. Der kurze Sprech-Prompt ("hoechstens zwei Saetze") verstaerkt das.
_ANGEKUENDIGT = re.compile(
    r"(schaue?|sehe?|gucke?|pruefe?|prüfe?)[^.]{0,30}"
    r"(nach|unterlagen|dokument)|(unterlagen|dokumenten)[^.]{0,20}(nachsehen|nachschauen)",
    re.I)

_BEHAUPTUNG = re.compile(
    r"(aufzeichnung|aufnahme|mitschnitt)[^.]{0,40}"
    r"(gestartet|gestoppt|angelaufen|beendet|läuft|aus|an)", re.I)

WERKZEUGE = [{
    "type": "function",
    "function": {
        "name": "dokumente_suchen",
        "description": ("Durchsucht die Unterlagen des Labors: Protokolle, Notizen, "
                        "Handbücher, Projektdateien. IMMER zuerst benutzen, wenn nach "
                        "Geräten, Messwerten, Verfahren oder früheren Arbeiten gefragt "
                        "wird."),
        "parameters": {
            "type": "object",
            "properties": {"frage": {"type": "string",
                                     "description": "Suchbegriffe oder die Frage"}},
            "required": ["frage"]},
    }}, {
    "type": "function",
    "function": {
        "name": "web_suchen",
        "description": ("Sucht im Internet. Nur benutzen, wenn die Unterlagen des "
                        "Labors nichts hergeben und es um allgemeines Fachwissen geht."),
        "parameters": {
            "type": "object",
            "properties": {"frage": {"type": "string", "description": "Suchbegriffe"}},
            "required": ["frage"]},
    }}, {
    "type": "function",
    "function": {
        "name": "aufzeichnung",
        "description": ("Schaltet die Audioaufzeichnung des Laborgesprächs an oder "
                        "aus. Nur benutzen, wenn ausdrücklich darum gebeten wird."),
        "parameters": {
            "type": "object",
            "properties": {"an": {"type": "boolean",
                                  "description": "true startet, false stoppt"}},
            "required": ["an"]},
    }}]


# ---------------------------------------------------------------- Sitzung
class Sitzung:
    """Eine Client-Verbindung: Audio rein, Sprache raus."""

    def __init__(self, ws):
        self.ws = ws
        self.turn = Turnerkenner()
        self.rek = doku.Rekorder()
        self.verlauf: list[dict] = []
        self.antwort_task: asyncio.Task | None = None
        self.letzte_ansprache = 0.0
        self.web_benutzt = False

    async def schliessen(self):
        self.rek.stop()
        if self.antwort_task and not self.antwort_task.done():
            self.antwort_task.cancel()

    async def befehl(self, b: dict):
        art = b.get("befehl")
        if art == "mikro":
            an = bool(b.get("an"))
            HALTER.setzen(mikro=an, phase=Phase.BEREIT if an else Phase.LEERLAUF)
            if not an:
                self.rek.stop(); HALTER.setzen(aufnahme=False, gespraech=False)
                self.turn.reset()
        elif art == "aufnahme":
            an = bool(b.get("an"))
            if an and HALTER.z.mikro:
                self.rek.start()
            else:
                self.rek.stop()
            HALTER.setzen(aufnahme=self.rek.laeuft)
        elif art == "ansprechen":
            # Taste am Client -- gleichwertig zum Aktivierungswort, aber ohne
            # dass es gesagt werden muss. Bleibt nuetzlich, wenn es laut ist.
            if HALTER.z.mikro:
                self.turn.reset()
                HALTER.setzen(phase=Phase.HOEREN)
        elif art == "abbrechen":
            if self.antwort_task and not self.antwort_task.done():
                self.antwort_task.cancel()
            HALTER.setzen(phase=Phase.BEREIT if HALTER.z.mikro else Phase.LEERLAUF)

    async def audio(self, roh: bytes):
        x = np.frombuffer(roh, dtype=np.int16).astype(np.float32) / 32768.0
        for i in range(0, len(x) - konfig.BLOCK + 1, konfig.BLOCK):
            block = x[i:i + konfig.BLOCK]
            # Dokumentationspfad laeuft unabhaengig vom Dialog weiter.
            if self.rek.laeuft:
                self.rek.block(block)
            # Waehrend der Assistent spricht, nicht auf sich selbst reagieren.
            if HALTER.z.phase in (Phase.DENKEN, Phase.ANTWORTEN):
                continue
            ep = await self.turn.block(block)
            if ep is None:
                continue

            gerufen = HALTER.z.phase is Phase.HOEREN     # Knopf gedrueckt
            text = ep.text or await stt.transkribiere(ep.samples)
            if not text.strip():
                continue

            if not gerufen:
                # Mikrofon offen: jede Aeusserung wird transkribiert, aber nur
                # eine mit Aktivierungswort gilt als Ansprache -- ausser das
                # Gespraech laeuft noch, dann genuegt Weitersprechen.
                if self.im_gespraech():
                    if aktivierung.ende(text):
                        log.info("Gespräch beendet: %r", text[:40])
                        await self.gespraech_beenden()
                        continue
                    # Ein vorangestelltes "Kiwi," stoert nicht, muss aber weg.
                    ja, rest = aktivierung.erkannt(text)
                    if ja:
                        text = rest
                else:
                    ja, rest = aktivierung.erkannt(text)
                    if not ja:
                        continue
                    log.info("Aktivierungswort erkannt: %r", text[:60])
                    text = rest
                    HALTER.setzen(gespraech=True)

            self.letzte_ansprache = time.time()
            if not text.strip():
                # Nur gerufen, ohne Anweisung -- kurz quittieren statt das
                # Modell mit einem leeren Satz zu behelligen.
                HALTER.setzen(gespraech=True, phase=Phase.ANTWORTEN)
                await self.sag("Ja?")
                HALTER.setzen(phase=Phase.BEREIT)
                continue
            ep.text = text
            log.info("Endpoint: %s nach %.0f ms Aeusserung", ep.grund, ep.dauer_ms)
            HALTER.setzen(phase=Phase.DENKEN)
            self.antwort_task = asyncio.create_task(self.antworten(ep))

    def im_gespraech(self) -> bool:
        """Laeuft das Gespraech noch? Die Stillegrenze ist keine Bequemlichkeit,
        sondern noetig: ohne sie reagierte der Assistent im Labor auf jedes
        Gespraech, sobald jemand vergisst, sich zu verabschieden."""
        return (HALTER.z.gespraech
                and time.time() - self.letzte_ansprache < konfig.GESPRAECH_STILLE_S)

    async def gespraech_beenden(self, sagen: str | None = "Bis dann."):
        HALTER.setzen(gespraech=False, phase=Phase.ANTWORTEN if sagen else Phase.BEREIT)
        if sagen:
            await self.ws.send(json.dumps({"typ": "text", "rolle": "assistent",
                                           "text": sagen}))
            await self.sag(sagen)
            HALTER.setzen(phase=Phase.BEREIT if HALTER.z.mikro else Phase.LEERLAUF)

    async def antworten(self, ep):
        """Mit hartem Zeitlimit. vLLM haengt sich gelegentlich auf -- ohne
        Grenze blieb der Assistent dann stumm stehen, ohne Fehler, ohne
        Rueckfallebene. Ein haengender Motor sieht aus wie ein langsames
        Modell; hier soll er wie ein Fehler aussehen."""
        try:
            await asyncio.wait_for(self._antworten(ep), timeout=konfig.ANTWORT_MAX_S)
        except asyncio.TimeoutError:
            log.warning("Antwort abgebrochen: laenger als %ds", konfig.ANTWORT_MAX_S)
            try:
                await self.ws.send(json.dumps(
                    {"typ": "text", "rolle": "assistent",
                     "text": "Das dauert mir zu lange, ich breche ab."}))
                await self.sag("Das dauert mir zu lange, ich breche ab.")
            except Exception:
                pass
        finally:
            self.letzte_ansprache = time.time()
            if HALTER.z.phase in (Phase.DENKEN, Phase.ANTWORTEN):
                HALTER.setzen(phase=Phase.BEREIT if HALTER.z.mikro else Phase.LEERLAUF)

    async def _antworten(self, ep):
        import time as _t
        t0 = _t.time()
        try:
            text = ep.text or await stt.transkribiere(ep.samples)
            log.info("  STT %.0f ms%s", (_t.time()-t0)*1000,
                     " (uebersprungen)" if ep.text else "")
            if not text:
                HALTER.setzen(phase=Phase.BEREIT, hinweis="nichts verstanden")
                return
            HALTER.setzen(letzter_text=text)
            await self.ws.send(json.dumps({"typ": "text", "rolle": "nutzer", "text": text}))

            if not HALTER.z.llm_da:
                await self.sag("Das Modell ist gerade nicht erreichbar. "
                               "Ich zeichne weiter auf.")
                HALTER.setzen(phase=Phase.BEREIT)
                return

            HALTER.setzen(phase=Phase.ANTWORTEN, letzte_antwort="")
            ganze = []
            # Satzweise: der erste Satz geht raus, waehrend der Rest noch
            # geschrieben wird. Nur bis dahin zaehlt die gefuehlte Latenz.
            t1 = _t.time(); erster = True
            werkzeug_gerufen = False
            self.web_benutzt = False
            async for e in llm.antwort_mit_werkzeugen(
                    text, self.verlauf, WERKZEUGE, self.werkzeug,
                    system=self.system_prompt()):
                if e[0] == "werkzeug":
                    werkzeug_gerufen = True
                    log.info("  Werkzeug %s%r -> %s", e[1], e[2], e[3])
                    await self.ws.send(json.dumps({"typ": "werkzeug", "name": e[1],
                                                   "args": e[2], "ergebnis": e[3]}))
                    continue
                satz = e[1]
                if erster:
                    log.info("  LLM erster Satz nach %.0f ms (%d Zeichen)",
                             (_t.time()-t1)*1000, len(satz)); erster = False
                ganze.append(satz)
                HALTER.setzen(letzte_antwort=" ".join(ganze))
                await self.ws.send(json.dumps({"typ": "text", "rolle": "assistent",
                                               "text": satz}))
                await self.sag(satz)
            # Sicherheitsnetz: das Modell hat schon behauptet, die Aufzeichnung
            # gestoppt zu haben, ohne das Werkzeug aufzurufen. Ein verschaerfter
            # Prompt half, aber darauf allein darf sich das nicht stuetzen --
            # wer glaubt, es werde nicht mehr aufgezeichnet, muss recht haben.
            antwort = " ".join(ganze)

            # Angekuendigt statt getan: Suche nachholen und erneut antworten.
            if not werkzeug_gerufen and _ANGEKUENDIGT.search(antwort):
                log.info("  Nachschlagen angekündigt — hole die Suche nach")
                ergebnis = await self.werkzeug("dokumente_suchen", {"frage": text})
                verlauf2 = list(self.verlauf) + [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": antwort},
                    {"role": "user", "content":
                     f"Das steht in den Unterlagen:\n{ergebnis}\n\n"
                     f"Beantworte damit jetzt: {text}"}]
                nach = []
                async for satz in llm.antwort_saetze("", verlauf2[:-1] + [verlauf2[-1]],
                                                     max_tokens=200):
                    nach.append(satz)
                    await self.ws.send(json.dumps({"typ": "text",
                                                   "rolle": "assistent", "text": satz}))
                    await self.sag(satz)
                ganze += nach
                antwort = " ".join(ganze)
                werkzeug_gerufen = True

            if not werkzeug_gerufen and _BEHAUPTUNG.search(antwort):
                log.warning("Behauptung ohne Werkzeugaufruf — hole ihn nach")
                args = await llm.erzwinge_werkzeug(text, self.verlauf, WERKZEUGE,
                                                   "aufzeichnung",
                                                   system=self.system_prompt())
                if args is not None:
                    ergebnis = await self.werkzeug("aufzeichnung", args)
                    log.info("  nachgeholt: aufzeichnung%r -> %s", args, ergebnis)
                    await self.ws.send(json.dumps({"typ": "werkzeug",
                                                   "name": "aufzeichnung",
                                                   "args": args, "ergebnis": ergebnis,
                                                   "nachgeholt": True}))
                else:
                    # Auch das Nachfassen kann scheitern. Dann wenigstens nicht
                    # die Falschaussage stehen lassen.
                    wahr = ("Zur Sicherheit: die Aufzeichnung läuft weiterhin."
                            if HALTER.z.aufnahme else
                            "Zur Sicherheit: es wird gerade nicht aufgezeichnet.")
                    await self.ws.send(json.dumps({"typ": "text", "rolle": "assistent",
                                                   "text": wahr}))
                    await self.sag(wahr)
                    ganze.append(wahr)

            self.verlauf += [{"role": "user", "content": text},
                             {"role": "assistant", "content": " ".join(ganze)}]
            del self.verlauf[:-8]
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # pragma: no cover
            log.exception("Antwort fehlgeschlagen: %s", e)
        finally:
            self.letzte_ansprache = time.time()
            if HALTER.z.phase in (Phase.DENKEN, Phase.ANTWORTEN):
                HALTER.setzen(phase=Phase.BEREIT if HALTER.z.mikro else Phase.LEERLAUF)

    def system_prompt(self) -> str:
        """Der Zustand gehoert in den Prompt, nicht in ein Werkzeug: sonst
        fragt das Modell erst nach, ob aufgezeichnet wird, bevor es handelt."""
        lauft = "läuft gerade" if HALTER.z.aufnahme else "läuft gerade nicht"
        return (konfig.SYSTEM_PROMPT + " " + konfig.WISSEN_PROMPT +
                f" Die Audioaufzeichnung des Laborgesprächs {lauft}. "
                "Willst du daran etwas ändern, MUSST du das Werkzeug 'aufzeichnung' "
                "aufrufen. Behaupte niemals eine Änderung, die du nicht über das "
                "Werkzeug ausgeführt hast — das Protokoll und die Anzeige im Labor "
                "hängen daran. Sag danach in einem kurzen Satz, was geschehen ist.")

    async def werkzeug(self, name: str, args: dict) -> str:
        """Fuehrt einen Werkzeugaufruf aus. Rueckgabe geht ans Modell zurueck."""
        if name == "dokumente_suchen":
            frage = str(args.get("frage", ""))
            treffer = await asyncio.to_thread(wissen_index.suchen, frage, 4)
            if not treffer:
                return ("Nichts in den Unterlagen gefunden. Sag das offen, statt zu "
                        "raten.")
            teile = []
            for t in treffer:
                # Quelle mitgeben, damit das Modell zitieren kann statt zu behaupten.
                teile.append(f"[{t.quelle} — {t.titel}, Abschnitt: {t.ueberschrift}]\n"
                             f"{t.text[:700]}")
            return "\n\n".join(teile)

        if name == "web_suchen":
            if not konfig.WEB_SUCHE:
                return "Websuche ist abgeschaltet."
            treffer = await wissen_web.suchen(str(args.get("frage", "")))
            if not treffer:
                return "Die Websuche hat nichts geliefert."
            self.web_benutzt = True
            return "\n\n".join(
                f"[Web: {t['titel']}]\n{t['text']}\nQuelle: {t['url']}"
                for t in treffer)

        if name != "aufzeichnung":
            return f"Unbekanntes Werkzeug {name}."
        an = bool(args.get("an"))
        if an and not HALTER.z.mikro:
            return "Fehlgeschlagen: das Mikrofon ist aus."
        if an:
            self.rek.start()
        else:
            self.rek.stop()
        HALTER.setzen(aufnahme=self.rek.laeuft)
        # Die Aufzeichnung ist rechtlich heikel; sie darf nie still anlaufen.
        # Der Monitor zeigt es ohnehin, die Bestaetigung sagt es zusaetzlich.
        return "Aufzeichnung läuft jetzt." if self.rek.laeuft else "Aufzeichnung ist gestoppt."

    async def sag(self, satz: str):
        import time as _t
        _ts = _t.time()
        erster = True
        async for pcm, rate in tts.sprich(satz):
            if erster:
                log.info("  TTS erster Ton nach %.0f ms", (_t.time()-_ts)*1000)
                await self.ws.send(json.dumps({"typ": "ton", "rate": rate}))
                erster = False
            await self.ws.send(pcm)
            # Antworten gehoeren in die Aufzeichnung -- sonst steht im
            # Protokoll nur die Haelfte des Gesprächs.
            if self.rek.laeuft:
                self.rek.mische(pcm, rate)
        await self.ws.send(json.dumps({"typ": "ton_ende"}))


# ---------------------------------------------------------------- Verbindungen
async def zustand_senden(ws):
    q = HALTER.abonnieren()
    try:
        while True:
            z = await q.get()
            await ws.send(json.dumps({"typ": "zustand", **z}))
    except websockets.ConnectionClosed:
        pass
    finally:
        HALTER.abbestellen(q)


async def behandeln(ws):
    pfad = ws.request.path
    if pfad.startswith("/monitor"):
        await zustand_senden(ws)
        return
    if not pfad.startswith("/audio"):
        await ws.close(1008, "unbekannter Pfad")
        return

    s = Sitzung(ws)
    senden = asyncio.create_task(zustand_senden(ws))
    try:
        async for nachricht in ws:
            if isinstance(nachricht, bytes):
                await s.audio(nachricht)
            else:
                await s.befehl(json.loads(nachricht))
    except websockets.ConnectionClosed:
        pass
    finally:
        senden.cancel()
        await s.schliessen()
        HALTER.setzen(phase=Phase.LEERLAUF, mikro=False, aufnahme=False,
                      gespraech=False)


async def http_seite(verbindung, anfrage):
    """GET / liefert den Laborbildschirm, /klient den Sprachclient fuer den
    Browser; alles andere geht an den WebSocket.

    Der Sprachclient braucht einen "secure context", sonst gibt der Browser das
    Mikrofon nicht frei. Ueber SSH weiterleiten und als localhost aufrufen:
        ssh -L 8920:127.0.0.1:8920 <rechner>
        http://localhost:8920/klient
    Damit bleibt der Dienst auf 127.0.0.1 gebunden.
    """
    datei = SEITEN.get(anfrage.path.split("?")[0])
    if datei is None:
        return None
    try:
        leib = (konfig.WURZEL / "sprachdienst" / datei).read_text(encoding="utf-8")
    except OSError:
        leib = f"<h1>{datei} fehlt</h1>"
    antwort = verbindung.respond(http.HTTPStatus.OK, leib)
    # respond() setzt text/plain -- der Browser zeigt die Seite sonst als
    # Quelltext an. Headers.__setitem__ HAENGT AN statt zu ersetzen, deshalb
    # erst loeschen.
    del antwort.headers["Content-Type"]
    antwort.headers["Content-Type"] = "text/html; charset=utf-8"
    return antwort


async def haupt():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    konfig.AUFNAHMEN.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(tts.laden)          # Stimme vorladen: 595 ms einmalig
    log.info("Stimme geladen")
    asyncio.create_task(gesundheit())

    stopp = asyncio.Event()
    schleife = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        schleife.add_signal_handler(s, stopp.set)

    async with serve(behandeln, konfig.BIND, konfig.PORT,
                     process_request=http_seite, max_size=2**22):
        log.info("Sprachdienst auf ws://%s:%d/audio  (Monitor: http://%s:%d/)",
                 konfig.BIND, konfig.PORT, konfig.BIND, konfig.PORT)
        await stopp.wait()
    log.info("beendet")


if __name__ == "__main__":
    asyncio.run(haupt())
