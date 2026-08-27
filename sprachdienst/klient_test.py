"""Simuliert den Laborclient: spielt eine WAV-Datei ein, nimmt die Antwort auf.

Ersetzt Futro und Jabra, solange es die Hardware nicht gibt. Sendet in
Echtzeit (32-ms-Bloecke mit Pause), weil der VAD sonst eine ganz andere
Zeitachse sieht als spaeter im Betrieb.

    .venv/bin/python -m sprachdienst.klient_test testaudio/s1.wav
"""
import asyncio, json, sys, time, wave
import numpy as np
import websockets

from . import konfig

ZIEL = f"ws://{konfig.BIND}:{konfig.PORT}/audio"
NACHLAUF_S = 2.5          # Stille anhaengen, damit das Endpointing ausloest


def lies(pfad):
    with wave.open(pfad) as w:
        assert w.getframerate() == konfig.RATE and w.getnchannels() == 1, \
            "erwartet 16 kHz mono"
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


async def lauf(pfad, aufnahme=False, ansprechen=True):
    x = lies(pfad)
    x = np.concatenate([x, np.zeros(int(konfig.RATE * NACHLAUF_S), dtype=np.int16)])

    async with websockets.connect(ZIEL, max_size=2**22) as ws:
        marken = {}
        antwort = []
        rate_aus = konfig.RATE
        fertig = asyncio.Event()

        async def empfangen():
            nonlocal rate_aus
            async for n in ws:
                if isinstance(n, bytes):
                    if "erster_ton" not in marken:
                        marken["erster_ton"] = time.time()
                    antwort.append(n); continue
                d = json.loads(n)
                if d["typ"] == "zustand":
                    print(f"    [zustand] {d['phase']:<10} aufnahme={d['aufnahme']} "
                          f"llm={d['llm_da']} stt={d['stt_da']} {d['hinweis']}")
                elif d["typ"] == "text":
                    marken.setdefault(f"text_{d['rolle']}", time.time())
                    print(f"    [{d['rolle']}] {d['text']}")
                elif d["typ"] == "ton":
                    rate_aus = d["rate"]
                elif d["typ"] == "ton_ende":
                    # NICHT sofort aufhoeren: seit der Dienst den gewaehlten Weg
                    # ansagt, folgt nach dem ersten ton_ende noch die eigentliche
                    # Antwort. Stattdessen auf Ruhe warten.
                    marken["letzter_ton"] = time.time()

        aufgabe = asyncio.create_task(empfangen())
        await ws.send(json.dumps({"befehl": "mikro", "an": True}))
        if aufnahme:
            await ws.send(json.dumps({"befehl": "aufnahme", "an": True}))
        if ansprechen:
            await ws.send(json.dumps({"befehl": "ansprechen"}))
        else:
            print("  (kein Knopf — es zaehlt nur das Aktivierungswort)")

        print(f"  sende {len(x)/konfig.RATE:.1f}s Audio in Echtzeit ...")
        t0 = time.time()
        ende_sprache = t0 + (len(x) - konfig.RATE * NACHLAUF_S) / konfig.RATE
        for i in range(0, len(x) - konfig.BLOCK + 1, konfig.BLOCK):
            await ws.send(x[i:i + konfig.BLOCK].tobytes())
            ziel = t0 + (i + konfig.BLOCK) / konfig.RATE
            rest = ziel - time.time()
            if rest > 0:
                await asyncio.sleep(rest)
            if fertig.is_set():
                break

        # Fertig ist, wenn 4 s lang nichts mehr kam.
        ende = time.time() + 40
        while time.time() < ende:
            await asyncio.sleep(0.5)
            letzter = marken.get("letzter_ton")
            # 10 s: nach der Wegansage kann die eigentliche Antwort mehrere
            # Sekunden brauchen (Werkzeugrunde plus Schlussantwort). Bei 4 s
            # brach der Test ab, bevor sie kam -- und sah aus wie ein Fehler
            # im Dienst.
            if letzter and time.time() - letzter > 10:
                break
        else:
            print("  ! keine vollstaendige Antwort innerhalb von 40 s")
        aufgabe.cancel()

        if antwort:
            ziel = "/tmp/kihiwi-antwort.wav"
            with wave.open(ziel, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate_aus)
                w.writeframes(b"".join(antwort))
            print(f"  Antwortaudio: {ziel} "
                  f"({len(b''.join(antwort))/2/rate_aus:.1f}s)")
        if "erster_ton" in marken:
            print(f"  ==> vom Sprechende bis zum ersten Ton: "
                  f"{(marken['erster_ton']-ende_sprache)*1000:.0f} ms")
        if "text_nutzer" in marken:
            print(f"      davon bis zum Transkript:            "
                  f"{(marken['text_nutzer']-ende_sprache)*1000:.0f} ms")


if __name__ == "__main__":
    datei = sys.argv[1] if len(sys.argv) > 1 else "testaudio/s1.wav"
    asyncio.run(lauf(datei, aufnahme="--aufnahme" in sys.argv,
                     ansprechen="--wach" not in sys.argv))
