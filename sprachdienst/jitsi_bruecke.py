"""Jitsi-Bruecke: Kiwi als Teilnehmer in einer Jitsi-Konferenz.

Ein Chromium ohne Bildschirm tritt dem Raum als "Kiwi" bei und ist fuer den
Sprachdienst ein Klient wie /klient: Was im Raum gesprochen wird, geht als
PCM int16 mono 16 kHz an /audio, Kiwis Sprachausgabe kommt als Mikrofon in
die Konferenz. Kiwi reagiert wie am Freisprecher nur auf das
Aktivierungswort.

Die Bruecke ist ein ausgehender Teilnehmer; der Sprachdienst bleibt an seiner
Adresse (konfig.BIND), nichts wird geoeffnet. Ist im Raum eine Lobby aktiv,
klopft Kiwi an und wartet, bis ein Moderator ihn hereinlaesst.

Aufruf:
    .venv/bin/python -m sprachdienst.jitsi_bruecke https://<jitsi>/<raum>
        [--name Kiwi] [--sichtbar]

Einrichten: pip install playwright && python -m playwright install chromium
"""
import argparse, asyncio, base64, json, logging, signal
from pathlib import Path
from urllib.parse import quote

import websockets
from playwright.async_api import async_playwright

from . import konfig

log = logging.getLogger("kihiwi.jitsi")
SEITENTEIL = Path(__file__).with_name("jitsi_bruecke.js")


def raum_url(url: str, name: str) -> str:
    """Raum-URL mit Jitsi-Einstellungen im Hash: ohne Vorschaltseite, mit
    automatischem Anklopfen an der Lobby, ohne Peer-to-Peer (sonst wechseln
    die Tonspuren bei zwei Teilnehmern zwischen Bridge und direkter
    Verbindung)."""
    einstellungen = [
        "config.prejoinConfig.enabled=false",
        "config.lobby.autoKnock=true",
        "config.p2p.enabled=false",
        "config.disableDeepLinking=true",
        "userInfo.displayName=" + quote(json.dumps(name)),
    ]
    return url.split("#")[0] + "#" + "&".join(einstellungen)


async def lauf(url: str, name: str, sichtbar: bool):
    dienst = f"ws://{konfig.BIND}:{konfig.PORT}/audio"
    nach_kiwi: asyncio.Queue[bytes] = asyncio.Queue(maxsize=300)

    async with websockets.connect(dienst, max_size=2**22) as ws, \
            async_playwright() as pw:
        log.info("Sprachdienst verbunden: %s", dienst)
        browser = await pw.chromium.launch(
            headless=not sichtbar,
            args=["--use-fake-ui-for-media-stream",
                  "--autoplay-policy=no-user-gesture-required"])
        seite = await (await browser.new_context(
            permissions=["microphone", "camera"])).new_page()

        def ton_raus(_quelle, daten: str):
            # Laeuft der Dienst hinterher, lieber Bloecke verwerfen als die
            # Konferenz anhalten: Kiwi verpasst dann eine Silbe, statt immer
            # weiter zurueckzufallen.
            try:
                nach_kiwi.put_nowait(base64.b64decode(daten))
            except asyncio.QueueFull:
                pass

        await seite.expose_binding("kiwiTonRaus", ton_raus)
        await seite.add_init_script(path=str(SEITENTEIL))
        seite.on("pageerror", lambda e: log.warning("Seite: %s", e))

        await ws.send(json.dumps({"befehl": "mikro", "an": True}))
        await seite.goto(raum_url(url, name))
        log.info("Raum betreten (oder Anklopfen): %s", url.split("#")[0])

        async def hoch():
            while True:
                await ws.send(await nach_kiwi.get())

        async def runter():
            async for n in ws:
                if isinstance(n, bytes):
                    await seite.evaluate("d => window.kiwiSpiel(d)",
                                         base64.b64encode(n).decode())
                    continue
                d = json.loads(n)
                if d.get("typ") == "ton":
                    await seite.evaluate("r => window.kiwiRate(r)", d["rate"])
                elif d.get("typ") == "zustand":
                    await seite.evaluate("z => window.kiwiZustand(z)", d)
                    if d.get("aufnahme"):
                        log.warning("Aufnahme laeuft -- alle im Raum werden aufgezeichnet")
                elif d.get("typ") == "text":
                    log.info("%s: %s", d.get("rolle", "?"), d.get("text", ""))

        async def stand():
            # Alle 30 s ins Protokoll, wie viele Tonspuren ankommen -- die
            # einzige Stelle, an der man von aussen sieht, ob Kiwi zuhoert.
            while True:
                await asyncio.sleep(30)
                try:
                    log.info("Stand: %s", await seite.evaluate("window.kiwiStand()"))
                except Exception:
                    pass

        ende = asyncio.Event()
        seite.on("close", lambda: ende.set())
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, ende.set)

        aufgaben = [asyncio.create_task(t) for t in
                    (hoch(), runter(), stand(), ende.wait())]
        fertig, offen = await asyncio.wait(aufgaben,
                                           return_when=asyncio.FIRST_COMPLETED)
        for t in offen:
            t.cancel()
        for t in fertig:
            if t.exception():
                log.error("Bruecke beendet: %r", t.exception())
        try:
            await ws.send(json.dumps({"befehl": "mikro", "an": False}))
        except websockets.ConnectionClosed:
            pass
        await browser.close()
        log.info("Bruecke beendet")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("url", help="Raum-URL, z. B. https://<jitsi>/<raum>")
    ap.add_argument("--name", default="Kiwi", help="Anzeigename im Raum")
    ap.add_argument("--sichtbar", action="store_true",
                    help="Browserfenster zeigen (zur Fehlersuche)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(lauf(a.url, a.name, a.sichtbar))


if __name__ == "__main__":
    main()
