"""Anbindung an whisper-server.

Zwei Wege mit verschiedenen Anforderungen (siehe CLAUDE.md):
  transkribiere()  -- Dialogpfad, schnell, Ergebnis wird verworfen
  transkribiere_datei() -- Dokumentationspfad, Qualitaet zaehlt
Beide setzen die Sprache HART auf Deutsch. Ohne das uebersetzt Whisper deutsche
Saetze mit englischen Fachbegriffen komplett ins Englische -- gemessen.
"""
import asyncio, io, json, urllib.error, urllib.request, wave
import numpy as np
from . import konfig


def _vokabular() -> str:
    """Fachbegriffe fuer den initial_prompt. Groesster Qualitaetshebel bei
    deutschem Fachvokabular, kostet nichts."""
    try:
        return konfig.VOKABULAR.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _mehrteilig(felder: dict, wav: bytes) -> tuple[bytes, str]:
    grenze = "----aihiwi7f3a"
    teile = []
    for k, v in felder.items():
        teile.append(f'--{grenze}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    teile.append(f'--{grenze}\r\nContent-Disposition: form-data; name="file"; '
                 f'filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode() + wav + b"\r\n")
    teile.append(f"--{grenze}--\r\n".encode())
    return b"".join(teile), grenze


def _wav(samples: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(konfig.RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def _ruf(wav: bytes, prompt: str, timeout: float) -> str:
    felder = {"language": "de", "response_format": "text", "temperature": "0"}
    if prompt:
        felder["prompt"] = prompt
    koerper, grenze = _mehrteilig(felder, wav)
    req = urllib.request.Request(
        konfig.STT_URL, data=koerper,
        headers={"Content-Type": f"multipart/form-data; boundary={grenze}"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode().strip()


async def transkribiere(samples: np.ndarray, mit_vokabular: bool = True,
                        timeout: float = 15.0) -> str:
    """Dialogpfad. Gibt bei Fehlern "" zurueck statt zu werfen -- ein toter
    STT darf den Dienst nicht mitreissen."""
    try:
        return await asyncio.to_thread(
            _ruf, _wav(samples), _vokabular() if mit_vokabular else "", timeout)
    except (urllib.error.URLError, OSError, TimeoutError):
        return ""


async def erreichbar(timeout: float = 2.0) -> bool:
    """Kurzer Ton statt Leerlauf: whisper-server hat keinen Gesundheitspfad,
    also wird eine winzige Anfrage geschickt."""
    try:
        stille = np.zeros(konfig.RATE // 10, dtype=np.float32)
        await asyncio.to_thread(_ruf, _wav(stille), "", timeout)
        return True
    except Exception:
        return False
