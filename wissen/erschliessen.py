"""Schlagwoerter zu jedem Abschnitt, erzeugt vom lokalen Sprachmodell.

**Wozu.** Die Volltextsuche findet nur, was woertlich dasteht. Gemessen ueber
acht Paare aus gesprochener Form und Schreibweise im Text -- "Rueckstreu-
elektronen" gegen "BSE", "Rasterelektronenmikroskop" gegen "REM" -- war die
Ueberschneidung der Trefferlisten **jedes Mal null**. Wer die eine Form fragt,
bekommt die Stellen mit der anderen nicht.

Ein Vektorindex wuerde das auch loesen, aber teurer: ein Embedding-Modell im
Speicher, den das Sprachmodell belegt, und ein zweiter Dienst. Hier genuegt es,
die Bruecke EINMAL zu bauen und in den vorhandenen FTS-Index zu legen.

**Was es NICHT ist.** Keine Zusammenfassung. Der Text der Abschnitte bleibt
unangetastet, und was das Modell ans Sprachmodell liefert, kommt weiterhin aus
`text` -- Schlagwoerter wirken ausschliesslich auf die Rangfolge. Abgeleitetes
bleibt damit ausserhalb dessen, was zitiert wird.

**Kosten.** Gemessen auf dem GX10 mit Qwen3.6-35B-A3B: 0,32 s je Abschnitt bei
32 gleichzeitigen Anfragen (207 tok/s gesamt), also rund 13 Minuten fuer 2.484
Abschnitte. Sequentiell waeren es 1,3 s -- die Nebenlaeufigkeit ist der ganze
Unterschied, `--max-num-seqs 32` steht ohnehin im Startbefehl.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from sprachdienst import konfig
from . import index

GLEICHZEITIG = 32          # entspricht --max-num-seqs des vLLM-Starts
MAX_ZEICHEN = 2500         # laengere Abschnitte werden vorn beschnitten
MAX_TOKEN = 120

SYSTEM = (
    "Du erschliesst deutsche Fachtexte fuer eine Volltextsuche. "
    "Gib NUR eine Zeile mit 8 bis 14 Suchbegriffen, durch Komma getrennt. "
    "Nenne zu jeder Abkuerzung im Text die ausgeschriebene deutsche Form und "
    "zu jedem ausgeschriebenen Fachbegriff die uebliche Abkuerzung. "
    "Nimm Synonyme auf, die jemand beim Suchen benutzen wuerde. "
    "Erfinde nichts, was nicht im Text steht. Keine Erklaerung, keine Zahlen, "
    "keine Aufzaehlungszeichen."
)


ANTWORT_MAX_S = 120        # ein haengender Motor soll auffallen, nicht warten


def motor_bereit(modell: str) -> None:
    """Eine kurze Antwort abfordern, bevor tausende Anfragen rausgehen.

    vLLM haengt sich reproduzierbar auf: `/v1/models` antwortet weiter mit 200,
    `/chat/completions` nie wieder. Genau das ist einem Lauf hier passiert -- 32
    Anfragen standen offen, der Prozess wartete zehn Minuten auf `ep_poll`, und
    von aussen sah es aus wie ein langsames Modell. Diese Probe unterscheidet
    beides in zwei Sekunden.
    """
    rumpf = {"model": modell, "max_tokens": 4, "temperature": 0,
             "messages": [{"role": "user", "content": "ok"}]}
    req = urllib.request.Request(
        f"{konfig.LLM_URL}/chat/completions", data=json.dumps(rumpf).encode(),
        headers={"Content-Type": "application/json"})
    try:
        json.load(urllib.request.urlopen(req, timeout=30))
    except Exception as e:
        raise RuntimeError(
            f"{konfig.LLM_URL} nimmt keine Anfragen an ({e!r}). "
            f"Modell neu starten, z.B. model-switch qwen36nvfp4") from e


def modell_am_endpunkt() -> str:
    """Den Namen nehmen, den der Server ANBIETET.

    Diese Fehlerklasse hat jetzt zweimal zugeschlagen: einmal bei Hermes, der
    seinen eigenen Namen aus der Konfiguration nahm, und einmal hier -- die
    Kommandozeile laeuft ohne KIHIWI_MODEL und fragte nach der Vorgabe
    `ornith-1.5-35b-a3b`, waehrend `qwen3.6-35b-a3b-nvfp4` lief. Alle 100
    Anfragen liefen in einen 404.

    Fuer einen Stapellauf ist Fragen richtig; im Sprachpfad bleibt es beim
    festen Namen, dort ist ein lautes Scheitern besser als ein stiller Wechsel.
    """
    req = urllib.request.Request(f"{konfig.LLM_URL}/models")
    d = json.load(urllib.request.urlopen(req, timeout=10))
    namen = [m["id"] for m in d.get("data", [])]
    if konfig.LLM_MODEL in namen:
        return konfig.LLM_MODEL
    if not namen:
        raise RuntimeError(f"{konfig.LLM_URL} nennt kein Modell")
    print(f"    (Endpunkt bietet {namen[0]!r}, nicht {konfig.LLM_MODEL!r} — "
          f"nehme das angebotene)")
    return namen[0]


def _einmal(text: str, modell: str) -> str:
    rumpf = {**konfig.LLM_ZUSATZ,
             "model": modell, "max_tokens": MAX_TOKEN,
             "temperature": 0,
             "messages": [{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": text[:MAX_ZEICHEN]}]}
    req = urllib.request.Request(
        f"{konfig.LLM_URL}/chat/completions", data=json.dumps(rumpf).encode(),
        headers={"Content-Type": "application/json"})
    a = json.load(urllib.request.urlopen(req, timeout=ANTWORT_MAX_S))
    return (a["choices"][0]["message"].get("content") or "").strip()


def _saeubern(roh: str) -> str:
    """Eine Zeile Komma-Liste. Das Modell haelt sich meist daran, aber nicht
    immer -- Aufzaehlungszeichen und Vorreden werden hier entfernt, statt sich
    auf den Prompt zu verlassen."""
    zeile = " ".join(roh.splitlines()).replace("*", " ").replace("- ", " ")
    teile = [t.strip(" .;:—-") for t in zeile.split(",")]
    teile = [t for t in teile if 2 < len(t) <= 60]
    # Reihenfolge behalten, Doppelte raus.
    return ", ".join(dict.fromkeys(teile))[:600]


async def alles(nur_fehlende: bool = True, grenze: int | None = None) -> dict:
    c = index.verbinden()
    wo = "WHERE schlagwoerter = ''" if nur_fehlende else ""
    zeilen = c.execute(
        f"SELECT rowid, text FROM abschnitte {wo} ORDER BY rowid").fetchall()
    if grenze:
        zeilen = zeilen[:grenze]
    if not zeilen:
        c.close()
        return {"offen": 0, "erledigt": 0, "gescheitert": 0, "sekunden": 0.0}

    modell = modell_am_endpunkt()
    motor_bereit(modell)
    t0 = time.time()
    erledigt = gescheitert = 0
    erster_fehler = None
    sperre = asyncio.Semaphore(GLEICHZEITIG)
    stand = datetime.now(timezone.utc).isoformat(timespec="seconds")

    async def eine(rowid, text):
        nonlocal erledigt, gescheitert
        async with sperre:
            try:
                roh = await asyncio.to_thread(_einmal, text, modell)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                    TimeoutError, KeyError, json.JSONDecodeError) as e:
                nonlocal erster_fehler
                if erster_fehler is None:
                    # Ohne das stand hier "100 gescheitert" und sonst nichts.
                    naeher = ""
                    if isinstance(e, urllib.error.HTTPError):
                        try:
                            naeher = ": " + e.read().decode()[:200]
                        except Exception:
                            pass
                    erster_fehler = f"{e!r}{naeher}"
                    print(f"    erster Fehler: {erster_fehler}")
                gescheitert += 1
                return None
        w = _saeubern(roh)
        if not w:
            gescheitert += 1
            return None
        erledigt += 1
        return (rowid, index.abschnitt_hash(text), w)

    # In Bloecken, damit das Ergebnis zwischendurch auf der Platte landet:
    # ein Abbruch nach zehn Minuten soll nicht alles verwerfen.
    BLOCK = 200
    for i in range(0, len(zeilen), BLOCK):
        teil = zeilen[i:i + BLOCK]
        aus = [x for x in await asyncio.gather(*(eine(r, t) for r, t in teil)) if x]
        for rowid, h, w in aus:
            c.execute("INSERT OR REPLACE INTO erschliessung (hash,woerter,modell,stand) "
                      "VALUES (?,?,?,?)", (h, w, konfig.LLM_MODEL, stand))
            c.execute("UPDATE abschnitte SET schlagwoerter=? WHERE rowid=?", (w, rowid))
        c.commit()
        print(f"    {min(i + BLOCK, len(zeilen))}/{len(zeilen)} "
              f"({erledigt} erschlossen, {gescheitert} gescheitert)")
    c.close()
    return {"offen": len(zeilen), "erledigt": erledigt, "modell": modell,
            "gescheitert": gescheitert, "fehler": erster_fehler,
            "sekunden": time.time() - t0}


# --------------------------------------------------------- Kurzfassungen
KURZ_ZEICHEN = 500          # Ziel je Dokument
KURZ_EINGABE = 6000         # so viel Dokumenttext geht ins Modell

KURZ_SYSTEM = (
    "Du schreibst den Katalogeintrag eines Fachdokuments. Hoechstens drei "
    "Saetze, zusammen unter 500 Zeichen, auf Deutsch. Sage, WORUM es geht und "
    "WELCHE Fragen man damit beantworten kann. Keine Zahlenwerte, keine "
    "Aufzaehlungszeichen, keine Einleitung wie 'Dieses Dokument'."
)


def _dokumenttext(c, pfad: str) -> str:
    """Ueberschriften plus Anfang -- nicht das ganze Dokument.

    Ein Dokument hat hier bis zu 178.000 Zeichen; alles zu schicken kostet
    Vorlaufzeit und bringt fuer einen Katalogeintrag nichts. Die
    Ueberschriften tragen die Gliederung, der Anfang den Gegenstand.
    """
    zeilen = c.execute(
        "SELECT ueberschrift, text FROM abschnitte WHERE pfad=? ORDER BY nr",
        (pfad,)).fetchall()
    ueb = ", ".join(dict.fromkeys(u for u, _ in zeilen if u))[:1500]
    anfang = "\n".join(t for _, t in zeilen)[:KURZ_EINGABE - len(ueb)]
    return f"Gliederung: {ueb}\n\n{anfang}"


def _kurz_einmal(text: str, modell: str) -> str:
    rumpf = {**konfig.LLM_ZUSATZ, "model": modell, "max_tokens": 220,
             "temperature": 0,
             "messages": [{"role": "system", "content": KURZ_SYSTEM},
                          {"role": "user", "content": text}]}
    req = urllib.request.Request(
        f"{konfig.LLM_URL}/chat/completions", data=json.dumps(rumpf).encode(),
        headers={"Content-Type": "application/json"})
    a = json.load(urllib.request.urlopen(req, timeout=ANTWORT_MAX_S))
    roh = (a["choices"][0]["message"].get("content") or "").strip()
    return " ".join(roh.split())[:KURZ_ZEICHEN + 100]


async def kurzfassungen(neu_bauen: bool = False) -> dict:
    """Katalogeintrag je Dokument. Nur fuer neue oder geaenderte Dateien."""
    c = index.verbinden()
    dok = c.execute("SELECT pfad, titel, fingerab FROM dokumente ORDER BY pfad").fetchall()
    offen = []
    for pfad, titel, fingerab in dok:
        r = c.execute("SELECT fingerab FROM kurzfassung WHERE pfad=?", (pfad,)).fetchone()
        if neu_bauen or not r or r[0] != fingerab:
            offen.append((pfad, titel, fingerab))
    if not offen:
        c.close()
        return {"offen": 0, "erledigt": 0, "gescheitert": 0, "sekunden": 0.0}

    modell = modell_am_endpunkt()
    motor_bereit(modell)
    t0 = time.time()
    erledigt = gescheitert = 0
    erster_fehler = None
    sperre = asyncio.Semaphore(GLEICHZEITIG)
    stand = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Texte VORHER im Hauptthread lesen: die SQLite-Verbindung darf nur dort
    # benutzt werden, und asyncio.to_thread schiebt den Aufruf in einen
    # Arbeitsthread ("SQLite objects created in a thread can only be used in
    # that same thread"). 179 Dokumente zu lesen dauert Millisekunden -- die
    # Nebenlaeufigkeit wird fuer die Modellaufrufe gebraucht, nicht dafuer.
    vorrat = {pfad: _dokumenttext(c, pfad) for pfad, _, _ in offen}

    async def eine(pfad, titel, fingerab):
        nonlocal erledigt, gescheitert, erster_fehler
        text = vorrat[pfad]
        async with sperre:
            try:
                k = await asyncio.to_thread(_kurz_einmal, f"Titel: {titel}\n\n{text}", modell)
            except Exception as e:
                if erster_fehler is None:
                    erster_fehler = repr(e)
                    print(f"    erster Fehler: {erster_fehler}")
                gescheitert += 1
                return None
        if not k:
            gescheitert += 1
            return None
        erledigt += 1
        return (pfad, fingerab, k)

    aus = [x for x in await asyncio.gather(*(eine(*d) for d in offen)) if x]
    for pfad, fingerab, k in aus:
        c.execute("INSERT OR REPLACE INTO kurzfassung (pfad,fingerab,text,modell,stand) "
                  "VALUES (?,?,?,?,?)", (pfad, fingerab, k, modell, stand))
    c.commit()
    c.close()
    return {"offen": len(offen), "erledigt": erledigt, "modell": modell,
            "gescheitert": gescheitert, "fehler": erster_fehler,
            "sekunden": time.time() - t0}


def ueberblick(max_zeichen: int = 120_000) -> str:
    """Die ganze Landkarte als Text, nach Quelle gruppiert.

    Fuer ein Werkzeug, nicht fuer jeden Sprachzug: 179 Eintraege sind rund
    27k Token Vorlauf, und der Sprachpfad lebt von Sekundenbruchteilen.
    """
    c = index.lesen()
    zeilen = c.execute("""
        SELECT d.quelle, d.titel, d.pfad, k.text
        FROM dokumente d LEFT JOIN kurzfassung k ON k.pfad = d.pfad
        ORDER BY d.quelle, d.titel""").fetchall()
    c.close()
    aus, quelle, laenge = [], None, 0
    for q, titel, pfad, kurz in zeilen:
        if q != quelle:
            quelle = q
            aus.append(f"\n## {q}")
        z = f"- **{titel}** — {kurz or '(noch keine Kurzfassung)'}"
        laenge += len(z)
        if laenge > max_zeichen:
            aus.append(f"- … gekürzt, {len(zeilen)} Dokumente insgesamt")
            break
        aus.append(z)
    return "\n".join(aus).strip()
