"""Prueft das Lagebild im Gespraech -- gegen den LAUFENDEN Sprachdienst.

    .venv/bin/python -m sprachdienst.lagebild_gespraech_test

Tippt Fragen ueber /audio ({"befehl": "text"}) und sammelt, was zurueckkommt.
Braucht Sprachdienst und Modell; legt eine Probekorrektur an und nimmt sie
wieder zurueck.
"""
import asyncio
import json
import time

import websockets

from . import konfig
from wissen.lagebild import aktuell

ADRESSE = f"ws://{konfig.BIND}:{konfig.PORT}/audio"


async def _frage(text: str, ende_s: float = 90.0, ruhe_s: float = 4.0) -> dict:
    """Schickt text und sammelt bis der Dienst wieder bereit ist oder ruhe_s lang
    nichts mehr kommt, nachdem er etwas gesagt hat."""
    nachrichten, gesagt, erster_ton = [], [], None
    async with websockets.connect(ADRESSE, max_size=2**22) as ws:
        # Beim Verbinden spielt der Dienst die letzte Buehne erneut ab
        # ("wieder": true) -- das ist nicht die Antwort auf diese Frage.
        t0 = time.time()
        await ws.send(json.dumps({"befehl": "text", "text": text}))
        gefragt = False
        while time.time() - t0 < ende_s:
            try:
                roh = await asyncio.wait_for(ws.recv(), timeout=ruhe_s)
            except asyncio.TimeoutError:
                if gesagt:
                    break
                continue
            if isinstance(roh, bytes):
                continue
            m = json.loads(roh)
            if m.get("wieder"):
                continue
            nachrichten.append(m)
            if m.get("typ") == "ton" and erster_ton is None:
                erster_ton = time.time() - t0
            if m.get("typ") == "text" and m.get("rolle") == "assistent":
                gesagt.append(m.get("text", ""))
            if m.get("typ") == "zustand":
                phase = m.get("phase")
                if phase in ("denken", "antworten"):
                    gefragt = True
                elif gefragt and phase in ("bereit", "leerlauf") and gesagt:
                    break
    return {"nachrichten": nachrichten, "gesagt": " ".join(gesagt),
            "erster_ton_s": erster_ton}


def frage(text: str) -> dict:
    return asyncio.run(_frage(text))


def gespraech():
    r = frage("Wie ist der Stand des Projekts?")
    if aktuell() is None:
        assert "Es gibt noch kein Lagebild" in r["gesagt"], r["gesagt"]
    else:
        assert any(m.get("typ") == "lagebild" for m in r["nachrichten"]), r["gesagt"]
    assert r["erster_ton_s"] is not None, r["gesagt"]

    r = frage("Lagebildkorrektur")
    assert "Was soll ich am Lagebild korrigieren?" in r["gesagt"], r["gesagt"]
    r = frage("Lagebildkorrektur: Testeintrag bitte gleich zurücknehmen")
    assert "Notiert" in r["gesagt"], r["gesagt"]
    r = frage("Lagebildkorrektur zurücknehmen")
    assert "Zurückgenommen" in r["gesagt"], r["gesagt"]


if __name__ == "__main__":
    gespraech()
    print("ok")
