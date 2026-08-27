"""Dokumentationspfad: Aufnahmen transkribieren und Protokolle daraus bauen.

Getrennt vom Dialogpfad, weil hier die umgekehrten Anforderungen gelten:
Latenz ist gleichgueltig, Qualitaet entscheidet. Deshalb laeuft alles im
Nachgang, mit Vokabular-Prompt und einer Korrekturstufe durch das Modell.

Drei Stufen, in dieser Reihenfolge:

  1. VAD-Zerlegung   Nur Sprachbereiche gehen ins STT. Das ist PFLICHT, nicht
                     Optimierung: large-v3-turbo halluziniert in Stille
                     deutsche Phantomsaetze ("Vielen Dank.", "Untertitelung
                     des ZDF, 2020"). Nebeneffekt: jeder Abschnitt bekommt
                     seinen Zeitstempel.
  2. Transkription   je Bereich, mit Vokabular als initial_prompt.
  3. Korrektur       Das Modell repariert NUR Fachbegriffe. Roh und korrigiert
                     werden beide gespeichert.

Das Protokoll ist ABGELEITET und wird als solches gekennzeichnet. Transkript
bleibt Transkript; jede Aussage im Protokoll traegt einen Zeitstempel, unter
dem sich die Stelle im Roh-Audio nachhoeren laesst. Ein Protokoll, das
plausibel klingt und erfunden ist, waere schlechter als gar keins.
"""
from __future__ import annotations

import asyncio, difflib, json, sys, wave
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import konfig, llm, stt

# --- VAD-Zerlegung -----------------------------------------------------------
LUECKE_MS   = 600     # kuerzere Pausen trennen nicht -- sonst zerfaellt jeder Satz
MIN_DAUER_MS = 400    # kuerzeres gilt als Stoergeraeusch
RAND_MS     = 200     # Vorlauf/Nachlauf, damit keine Silbe abgeschnitten wird
MAX_STUECK_S = 60     # laengere Bereiche werden geteilt: Whisper wird sonst ungenau


@dataclass
class Abschnitt:
    start_ms: int
    ende_ms:  int
    text_roh: str = ""
    text:     str = ""      # nach der Korrektur; leer bis dahin

    @property
    def zeit(self) -> str:
        s = self.start_ms // 1000
        return f"{s // 60:02d}:{s % 60:02d}"


def lies_wav(pfad: Path) -> np.ndarray:
    with wave.open(str(pfad)) as w:
        if w.getframerate() != konfig.RATE or w.getnchannels() != 1:
            raise ValueError(f"{pfad}: erwartet {konfig.RATE} Hz mono")
        roh = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return roh.astype(np.float32) / 32768.0


def sprachbereiche(x: np.ndarray) -> list[tuple[int, int]]:
    """Gibt (start_ms, ende_ms) der Sprachbereiche zurueck."""
    from vad.silero import Vad
    v = Vad(str(konfig.VAD_MODELL))
    b = konfig.BLOCK
    aktiv: list[bool] = []
    for i in range(0, len(x) - b + 1, b):
        p = v.block(x[i:i + b])
        # Hysterese wie im Dialogpfad: einmal in Sprache, bleibt man laenger drin.
        drin = aktiv[-1] if aktiv else False
        aktiv.append(p > (konfig.VAD_AUS if drin else konfig.VAD_EIN))

    bereiche: list[list[int]] = []
    for n, a in enumerate(aktiv):
        if not a:
            continue
        t0, t1 = n * konfig.BLOCK_MS, (n + 1) * konfig.BLOCK_MS
        if bereiche and t0 - bereiche[-1][1] <= LUECKE_MS:
            bereiche[-1][1] = t1
        else:
            bereiche.append([t0, t1])

    dauer_ms = len(x) * 1000 // konfig.RATE
    ergebnis = []
    for t0, t1 in bereiche:
        if t1 - t0 < MIN_DAUER_MS:
            continue
        t0 = max(0, t0 - RAND_MS)
        t1 = min(dauer_ms, t1 + RAND_MS)
        # Zu lange Stuecke teilen -- Whisper verliert sonst den Faden und
        # neigt bei sehr langen Eingaben zu Wiederholungen.
        while t1 - t0 > MAX_STUECK_S * 1000:
            ergebnis.append((t0, t0 + MAX_STUECK_S * 1000))
            t0 += MAX_STUECK_S * 1000
        ergebnis.append((t0, t1))
    return ergebnis


# --- Transkription -----------------------------------------------------------
async def transkribiere_datei(wav: Path) -> list[Abschnitt]:
    x = lies_wav(wav)
    abschnitte = []
    for t0, t1 in sprachbereiche(x):
        stueck = x[t0 * konfig.RATE // 1000: t1 * konfig.RATE // 1000]
        text = await stt.transkribiere(stueck, mit_vokabular=True, timeout=120)
        text = text.strip()
        if text:
            abschnitte.append(Abschnitt(t0, t1, text_roh=text))
    return abschnitte


# --- Korrektur ---------------------------------------------------------------
KORREKTUR = (
    "Du korrigierst ein Transkript aus einem KI-Labor. Erlaubt ist AUSSCHLIESSLICH: "
    "falsch geschriebene englische Fachbegriffe berichtigen und Verhörer bei "
    "Fachvokabular reparieren. Ersetze ein Wort NUR, wenn genau ein Begriff aus "
    "der Vokabelliste eindeutig gemeint sein kann. Kämen mehrere in Frage oder "
    "bist du unsicher, lass das Wort unverändert stehen — ein falsch geratener "
    "Fachbegriff verfälscht das Protokoll, ein stehengebliebener ist bloß "
    "sichtbar falsch. Verboten ist alles andere: nichts umformulieren, nichts "
    "kürzen, nichts ergänzen, keine Füllwörter entfernen, die Wortstellung nicht "
    "ändern. Gib nur den Text zurück, ohne Vorrede und ohne Anmerkungen."
)


AEHNLICH_MIN = 0.65   # gemessen: echte Korrekturen 0,73-0,88, Erfindung 0,54


def begriffe() -> list[str]:
    """Die Vokabelliste, ohne Kommentare."""
    try:
        zeilen = konfig.VOKABULAR.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [z.strip() for z in zeilen if z.strip() and not z.startswith("#")]


def sichere_korrektur(roh: str, neu: str, liste: list[str] | None = None) -> tuple[str, int]:
    """Uebernimmt nur Ersetzungen, die dem Original aehnlich genug sind.

    Der Prompt allein reicht nicht. Auf einer echten Aufnahme machte das Modell
    trotz vorsichtiger Anweisung aus "Fenstertechnologie" ein
    "Fenster-Attention-Heads" -- ein Begriff aus der Vokabelliste, der mit dem
    Gesagten nichts zu tun hatte. Ein Protokoll darf so etwas nicht enthalten,
    also wird die Zurueckhaltung hier erzwungen statt erbeten.

    Einfuegungen und Loeschungen werden grundsaetzlich verworfen: die Korrektur
    darf Woerter ersetzen, aber nichts hinzufuegen und nichts weglassen.
    """
    vok = [x.lower() for x in (liste if liste is not None else begriffe())]

    def in_liste(wort: str) -> bool:
        """Steht der Ersatz in der Vokabelliste? Zusammensetzungen zaehlen:
        'Silizium-Nitrid-Fenster' passt zu 'Siliziumnitrid'."""
        w = wort.lower().strip(".,;:!?\"')(")
        teile = [w] + w.replace("-", " ").split()
        return any(any(t and (t in v or v in t) for v in vok) for t in teile)

    a, b = roh.split(), neu.split()
    sm = difflib.SequenceMatcher(None, [w.lower() for w in a], [w.lower() for w in b])
    aus, verworfen = [], 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            aus.extend(a[i1:i2])
        elif tag == "replace":
            va, vb = " ".join(a[i1:i2]).lower(), " ".join(b[j1:j2]).lower()
            # Zwei Bedingungen: aehnlich genug UND der Ersatz steht in der
            # Vokabelliste. Ohne die zweite machte das Modell aus einem
            # korrekten "Hochvakuum" ein falsches "Hochvacuum" -- aehnlich
            # genug, aber nirgends belegt.
            if (difflib.SequenceMatcher(None, va, vb).ratio() >= AEHNLICH_MIN
                    and all(in_liste(w) for w in b[j1:j2])):
                aus.extend(b[j1:j2])
            else:
                aus.extend(a[i1:i2]); verworfen += 1
        elif tag == "delete":
            aus.extend(a[i1:i2]); verworfen += 1
        elif tag == "insert":
            verworfen += 1
    return " ".join(aus), verworfen


async def korrigiere(abschnitte: list[Abschnitt]) -> None:
    """Setzt .text je Abschnitt. Bei Zweifel bleibt der Rohtext stehen."""
    try:
        vok = konfig.VOKABULAR.read_text(encoding="utf-8").strip()
    except OSError:
        vok = ""
    for a in abschnitte:
        a.text = a.text_roh
        if not await llm.erreichbar():
            continue
        frage = (f"Fachvokabular des Labors: {vok}\n\n"
                 f"Transkript:\n{a.text_roh}") if vok else a.text_roh
        neu = await llm.antwort_text(KORREKTUR, frage,
                                     max_tokens=len(a.text_roh) + 200)
        # Zwei Stufen: grober Laengenwaechter gegen komplettes Umformulieren,
        # danach wortweise Pruefung jeder einzelnen Ersetzung.
        if neu and 0.6 * len(a.text_roh) <= len(neu) <= 1.6 * len(a.text_roh):
            gepruft, verworfen = sichere_korrektur(a.text_roh, neu)
            a.text = gepruft
            if verworfen:
                print(f"    [{a.zeit}] {verworfen} Änderung(en) verworfen", flush=True)


# --- Zusammenfassung ---------------------------------------------------------
BERICHT = (
    "Du fasst ein Laborprotokoll zusammen. Halte dich strikt an das Transkript: "
    "keine Vermutungen, keine Ergänzungen aus Weltwissen, nichts erfinden. "
    "Wenn etwas unklar bleibt, schreibe das hin. Gliedere in: 'Worum es ging', "
    "'Beobachtungen und Messwerte', 'Entscheidungen', 'Offene Punkte'. "
    "Setze hinter jede Aussage den Zeitstempel der Stelle, auf die sie sich "
    "stützt, in eckigen Klammern, zum Beispiel [03:12]. Lass einen Abschnitt "
    "weg, wenn das Transkript nichts dazu hergibt. Benutze für die Gliederung "
    "Überschriften der dritten Ebene (###), keine höheren. Wirkt ein Wort im "
    "Transkript verhört und du kannst es nicht sicher auflösen, übernimm es "
    "wörtlich und nenne es am Ende unter '### Unklare Stellen' — rate nicht."
)


async def zusammenfassung(abschnitte: list[Abschnitt]) -> str:
    if not abschnitte or not await llm.erreichbar():
        return ""
    roh = "\n".join(f"[{a.zeit}] {a.text}" for a in abschnitte)
    text = await llm.antwort_text(BERICHT, roh, max_tokens=1200)
    # Sicherheitsnetz: das Modell setzt trotz Anweisung gelegentlich '#' oder
    # '##' und bricht damit die Gliederung der Protokolldatei auf.
    import re as _re
    return _re.sub(r'^(#{1,2})(?!#)\s', '### ', text, flags=_re.M)


# --- Ablauf ------------------------------------------------------------------
async def verarbeite(wav: Path, neu: bool = False) -> list[Abschnitt] | None:
    meta_pfad = wav.with_suffix(".json")
    try:
        meta = json.loads(meta_pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        meta = {}
    if meta.get("transkribiert") and not neu:
        return None

    print(f"  {wav.name} ... ", end="", flush=True)
    abschnitte = await transkribiere_datei(wav)
    if not abschnitte:
        print("keine Sprache gefunden")
        meta.update(transkribiert=True, abschnitte=0,
                    verarbeitet_utc=datetime.now(timezone.utc).isoformat())
        meta_pfad.write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
        return []
    await korrigiere(abschnitte)
    print(f"{len(abschnitte)} Abschnitte")

    wav.with_suffix(".transkript.json").write_text(
        json.dumps([asdict(a) for a in abschnitte], indent=1, ensure_ascii=False),
        encoding="utf-8")
    meta.update(transkribiert=True, abschnitte=len(abschnitte),
                verarbeitet_utc=datetime.now(timezone.utc).isoformat())
    meta_pfad.write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return abschnitte


async def verarbeite_sitzung(sitzung: Path, neu: bool = False) -> Path | None:
    alle: list[Abschnitt] = []
    versatz = 0
    for wav in sorted(sitzung.glob("*.wav")):
        vorhanden = wav.with_suffix(".transkript.json")
        a = await verarbeite(wav, neu)
        if a is None and vorhanden.exists():
            a = [Abschnitt(**d) for d in json.loads(vorhanden.read_text(encoding="utf-8"))]
            print(f"  {wav.name} ... schon transkribiert ({len(a)} Abschnitte)")
        for x in (a or []):
            alle.append(Abschnitt(x.start_ms + versatz, x.ende_ms + versatz,
                                  x.text_roh, x.text))
        with wave.open(str(wav)) as w:
            versatz += w.getnframes() * 1000 // konfig.RATE

    if not alle:
        print("  nichts zu protokollieren")
        return None

    print("  Zusammenfassung ... ", end="", flush=True)
    bericht = await zusammenfassung(alle)
    print("fertig" if bericht else "übersprungen (kein Modell)")

    ziel = sitzung / "protokoll.md"
    zeilen = [
        f"# Laborprotokoll {sitzung.name}",
        "",
        f"Erzeugt {datetime.now(timezone.utc).astimezone().strftime('%d.%m.%Y %H:%M')} "
        f"aus {len(alle)} Sprachabschnitten.",
        "",
    ]
    if bericht:
        zeilen += [
            "## Zusammenfassung",
            "",
            "> **Abgeleitet, nicht wörtlich.** Vom Sprachmodell aus dem Transkript",
            "> erzeugt. Die Zeitstempel verweisen auf das Transkript unten und",
            "> damit auf das Roh-Audio. Im Zweifel gilt die Aufnahme.",
            "",
            bericht, "",
        ]
    zeilen += ["## Transkript", ""]
    zeilen += [f"**[{a.zeit}]** {a.text}" + ("" if a.text == a.text_roh
               else f"  \n<sub>roh: {a.text_roh}</sub>") for a in alle]
    zeilen += ["", "---", "",
               "Roh-Audio liegt neben dieser Datei und wird nicht gelöscht.",
               "Korrigierte Stellen zeigen den Rohtext darunter."]
    ziel.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    print(f"  -> {ziel}")
    return ziel


async def haupt(argv):
    neu = "--neu" in argv
    namen = [a for a in argv if not a.startswith("--")]
    wurzel = konfig.AUFNAHMEN
    sitzungen = ([wurzel / n for n in namen] if namen
                 else sorted(p for p in wurzel.glob("*") if p.is_dir()))
    if not sitzungen:
        print(f"Keine Aufnahmen in {wurzel}")
        return
    for s in sitzungen:
        if not s.is_dir():
            print(f"{s} gibt es nicht"); continue
        print(f"Sitzung {s.name}")
        await verarbeite_sitzung(s, neu)


if __name__ == "__main__":
    asyncio.run(haupt(sys.argv[1:]))
