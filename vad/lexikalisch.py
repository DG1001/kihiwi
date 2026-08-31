"""Lexikalisches Endpointing: kurze Schwelle, dann fragen statt warten.

Bei Stille von T_kurz wird das bisher Gehoerte transkribiert und das LLM
entscheidet, ob der Sprecher fertig ist. Lautet die Antwort WEITER, laeuft
die Aufnahme weiter -- gekostet hat das nichts, weil waehrenddessen
weiter mitgeschnitten wird. Nur die letzte, richtige Entscheidung kostet
Zeit.
"""
import io, json, sys, time, wave, urllib.request
import numpy as np
sys.path.insert(0, '.')
from vad.silero import Vad, FENSTER, RATE
from vad.endpoint import bloecke, BLOCK_MS
# Nicht fest verdrahten: der Name muss der sein, den der Server anbietet.
from sprachdienst.konfig import LLM_MODEL

STT = "http://127.0.0.1:8910/inference"
LLM = "http://127.0.0.1:8889/v1/chat/completions"
SYS = ("Du bekommst ein laufendes Transkript eines Sprechers. Entscheide, ob er "
       "seinen Satz abgeschlossen hat oder ob er mitten im Satz Luft holt. "
       "Antworte ausschliesslich mit FERTIG oder WEITER.")

def transkribiere(samples):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1, 1)*32767).astype(np.int16).tobytes())
    daten = buf.getvalue()
    grenze = "----x"
    teile = []
    for name, wert in [("language","de"), ("response_format","text"), ("temperature","0")]:
        teile.append(f"--{grenze}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{wert}\r\n".encode())
    teile.append(f"--{grenze}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
                 f"Content-Type: audio/wav\r\n\r\n".encode() + daten + b"\r\n")
    teile.append(f"--{grenze}--\r\n".encode())
    koerper = b"".join(teile)
    req = urllib.request.Request(STT, data=koerper,
        headers={"Content-Type": f"multipart/form-data; boundary={grenze}"})
    return urllib.request.urlopen(req, timeout=30).read().decode().strip()

def fertig(text):
    req = urllib.request.Request(LLM, data=json.dumps({
        "model": LLM_MODEL, "max_tokens": 3, "temperature": 0,
        "messages": [{"role":"system","content":SYS},
                     {"role":"user","content":text}]}).encode(),
        headers={"Content-Type":"application/json"})
    a = json.load(urllib.request.urlopen(req, timeout=30))
    return "FERTIG" in a["choices"][0]["message"]["content"].upper()

def endpoint_lex(wav, T_kurz=300, ein=0.5, aus=0.35, min_sprache_ms=160, vad=None):
    """Gibt (entscheidungs_ms, anzahl_pruefungen, pruefkosten_ms, text) zurueck."""
    v = vad or Vad(); v.reset()
    alle = []
    sprach=False; sprach_ms=0; stille_ms=0; pruef=0; kosten=0.0
    for n, b in enumerate(bloecke(wav)):
        alle.append(b)
        p = v.block(b); jetzt = (n+1)*BLOCK_MS
        aktiv = p > (aus if sprach else ein)
        if aktiv:
            sprach_ms += BLOCK_MS; stille_ms = 0
            if sprach_ms >= min_sprache_ms: sprach = True
        else:
            sprach_ms = 0
            if sprach:
                stille_ms += BLOCK_MS
                if stille_ms >= T_kurz:
                    t0=time.time()
                    txt = transkribiere(np.concatenate(alle))
                    ende = fertig(txt) if txt else False
                    dt=(time.time()-t0)*1000; pruef+=1
                    if ende:
                        return jetzt+dt, pruef, dt, txt
                    kosten += dt
                    stille_ms = 0     # weiterhoeren
    return None, pruef, kosten, ""


def p_fertig(text):
    """P(FERTIG) aus den Logprobs des ersten Tokens -- ein stufenloser Regler
    statt eines Ja/Nein, das sich per Prompt nur ganz oder gar nicht kippen
    laesst.

    Der abschliessende Punkt wird vorher entfernt: Whisper setzt IMMER ein
    Satzzeichen ans Ende, unabhaengig davon, ob der Sprecher fertig war. Das
    ist ein erfundenes Vollstaendigkeitssignal -- gemessen hebt es
    "Schreib ins Protokoll" von P=0,010 auf P=0,731.
    """
    import math
    # Nur Punkt und Komma weg. Das Fragezeichen bleibt: Whisper leitet es aus
    # der Intonation ab, es traegt also echtes Signal. Gemessen ohne "?" faellt
    # "Kannst du den Sweep nochmal laufen lassen" von P=0,914 auf P=0,182.
    text = text.rstrip().rstrip(".,;:…").rstrip()
    req = urllib.request.Request(LLM, data=json.dumps({
        "model": LLM_MODEL, "max_tokens": 1, "temperature": 0,
        "logprobs": True, "top_logprobs": 20,
        "messages": [{"role":"system","content":SYS},
                     {"role":"user","content":text}]}).encode(),
        headers={"Content-Type":"application/json"})
    a = json.load(urllib.request.urlopen(req, timeout=30))
    top = a["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    pf = pw = 0.0
    for t in top:
        tok = t["token"].strip().upper()
        p = math.exp(t["logprob"])
        # Das Modell gibt "WE" bzw. "FER" als ERSTES Token aus, nicht das ganze
        # Wort -- deshalb praefixweise gegen das Zielwort pruefen, nicht umgekehrt.
        if tok and "FERTIG".startswith(tok): pf += p
        elif tok and "WEITER".startswith(tok): pw += p
    return pf / (pf + pw) if (pf + pw) > 0 else 0.5


def endpoint_lex_p(wav, T_kurz=300, schwelle=0.5, ein=0.5, aus=0.35,
                   min_sprache_ms=160, vad=None, decke_ms=1200):
    """Wie endpoint_lex, aber mit Wahrscheinlichkeitsschwelle und einer
    Obergrenze: nach decke_ms Stille wird ohnehin abgeschlossen, damit ein
    dauerndes WEITER den Turn nicht ewig offen haelt."""
    v = vad or Vad(); v.reset()
    alle=[]; sprach=False; sprach_ms=0; stille_ms=0; pruef=0
    for n, b in enumerate(bloecke(wav)):
        alle.append(b); p = v.block(b); jetzt=(n+1)*BLOCK_MS
        aktiv = p > (aus if sprach else ein)
        if aktiv:
            sprach_ms += BLOCK_MS; stille_ms = 0
            if sprach_ms >= min_sprache_ms: sprach = True
        else:
            sprach_ms = 0
            if sprach:
                stille_ms += BLOCK_MS
                if stille_ms >= decke_ms:
                    return jetzt, pruef, "decke"
                if stille_ms >= T_kurz and stille_ms % T_kurz < BLOCK_MS:
                    t0=time.time(); txt = transkribiere(np.concatenate(alle))
                    pf = p_fertig(txt) if txt else 0.0
                    dt=(time.time()-t0)*1000; pruef+=1
                    if pf >= schwelle:
                        return jetzt+dt, pruef, "lexikalisch"
    return None, pruef, "keiner"
