"""Eine einzelne Audiodatei abschreiben — auch von ausserhalb des Labors.

    ./dienste.sh abschrift <datei> [--fach] [--zeiten] [--nach <ziel>]

Gedacht fuer das, was einem zugeschickt wird: eine Sprachnachricht aus
WhatsApp (`.ogg`/`.opus`), ein Diktat, ein Mitschnitt. Der Weg ist derselbe
wie im Dokumentationspfad -- ffmpeg wandelt, die Sprachbereiche werden
einzeln an den whisper-server geschickt -- nur ohne Sitzungsordner, ohne
Protokoll und ohne Sprechertrennung.

**Ohne Fachvokabular, wenn nichts anderes gesagt wird.** Der Laborpfad reicht
`vokabular.txt` als initial_prompt mit, damit aus "BSE" nicht "Bese" wird. Auf
eine private Sprachnachricht angewandt zieht dieselbe Liste die Erkennung in
Richtung Elektronenmikroskopie -- gute Gruende, sie hier wegzulassen. Mit
`--fach` kommt sie zurueck, fuer Diktate aus dem Labor.

Nichts verlaesst dabei das Netz: ffmpeg und whisper-server laufen lokal.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from . import konfig, stt
from . import protokoll


def _zeit(ms: int) -> str:
    return f"{ms // 60000:d}:{ms // 1000 % 60:02d}"


async def abschrift(pfad: Path, fach: bool = False) -> list[protokoll.Abschnitt]:
    """Die Datei in Sprachbereiche zerlegen und Stueck fuer Stueck abschreiben."""
    x = protokoll.lies_audio(pfad)
    if not len(x):
        raise ValueError(f"{pfad}: keine Audiodaten gelesen — Format geprüft?")
    aus = []
    bereiche = protokoll.sprachbereiche(x)
    for i, (t0, t1) in enumerate(bereiche, 1):
        stueck = x[t0 * konfig.RATE // 1000: t1 * konfig.RATE // 1000]
        text = (await stt.transkribiere(stueck, mit_vokabular=fach,
                                        timeout=120)).strip()
        print(f"  {i}/{len(bereiche)} [{_zeit(t0)}]", file=sys.stderr, flush=True)
        if text:
            aus.append(protokoll.Abschnitt(t0, t1, text_roh=text))
    return aus


async def haupt(argv: list[str]) -> int:
    namen = [a for a in argv if not a.startswith("--")]
    # --nach <ziel>: der Wert steht dahinter und ist kein Dateiname.
    ziel = None
    if "--nach" in argv:
        i = argv.index("--nach")
        if i + 1 >= len(argv):
            print("--nach braucht einen Dateinamen", file=sys.stderr)
            return 2
        ziel = Path(argv[i + 1])
        namen = [n for n in namen if n != argv[i + 1]]
    if not namen:
        print(__doc__.strip().split("\n\n")[1].strip(), file=sys.stderr)
        return 2
    pfad = Path(namen[0]).expanduser()
    if not pfad.is_file():
        print(f"{pfad} gibt es nicht", file=sys.stderr)
        return 1
    if not await stt.erreichbar():
        print("whisper-server antwortet nicht — erst ./dienste.sh start",
              file=sys.stderr)
        return 1

    abschnitte = await abschrift(pfad, fach="--fach" in argv)
    if not abschnitte:
        print("Keine Sprache gefunden.", file=sys.stderr)
        return 1
    if "--zeiten" in argv:
        text = "\n".join(f"[{_zeit(a.start_ms)}] {a.text_roh}" for a in abschnitte)
    else:
        text = " ".join(a.text_roh for a in abschnitte)
    print(text)
    if ziel:
        ziel.write_text(text + "\n", encoding="utf-8")
        print(f"  geschrieben: {ziel}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(haupt(sys.argv[1:])))
