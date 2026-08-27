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
import asyncio, http, json, logging, signal
import numpy as np
import websockets
from websockets.asyncio.server import serve

from . import doku, konfig, llm, stt, tts
from .turn import Turnerkenner
from .zustand import Phase, Zustandshalter

log = logging.getLogger("aihiwi")
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


# ---------------------------------------------------------------- Sitzung
class Sitzung:
    """Eine Client-Verbindung: Audio rein, Sprache raus."""

    def __init__(self, ws):
        self.ws = ws
        self.turn = Turnerkenner()
        self.rek = doku.Rekorder()
        self.verlauf: list[dict] = []
        self.antwort_task: asyncio.Task | None = None

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
                self.rek.stop(); HALTER.setzen(aufnahme=False)
                self.turn.reset()
        elif art == "aufnahme":
            an = bool(b.get("an"))
            if an and HALTER.z.mikro:
                self.rek.start()
            else:
                self.rek.stop()
            HALTER.setzen(aufnahme=self.rek.laeuft)
        elif art == "ansprechen":
            # Ersatz fuer das Aktivierungswort, solange es keins gibt: der
            # Client meldet Taste oder Wake-Word, der Dienst hoert ab jetzt zu.
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
            if HALTER.z.phase is not Phase.HOEREN:
                continue
            ep = await self.turn.block(block)
            if ep is not None:
                log.info("Endpoint: %s nach %.0f ms Aeusserung, Text %s",
                         ep.grund, ep.dauer_ms, "vorhanden" if ep.text else "fehlt")
                HALTER.setzen(phase=Phase.DENKEN)
                self.antwort_task = asyncio.create_task(self.antworten(ep))

    async def antworten(self, ep):
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
            async for satz in llm.antwort_saetze(text, self.verlauf):
                if erster:
                    log.info("  LLM erster Satz nach %.0f ms (%d Zeichen)",
                             (_t.time()-t1)*1000, len(satz)); erster = False
                ganze.append(satz)
                HALTER.setzen(letzte_antwort=" ".join(ganze))
                await self.ws.send(json.dumps({"typ": "text", "rolle": "assistent",
                                               "text": satz}))
                await self.sag(satz)
            self.verlauf += [{"role": "user", "content": text},
                             {"role": "assistant", "content": " ".join(ganze)}]
            del self.verlauf[:-8]
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # pragma: no cover
            log.exception("Antwort fehlgeschlagen: %s", e)
        finally:
            if HALTER.z.phase in (Phase.DENKEN, Phase.ANTWORTEN):
                HALTER.setzen(phase=Phase.BEREIT if HALTER.z.mikro else Phase.LEERLAUF)

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
        HALTER.setzen(phase=Phase.LEERLAUF, mikro=False, aufnahme=False)


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
