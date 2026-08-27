"""Kommandozeile: ./dienste.sh wissen [einlesen|status|suchen ...]"""
import sys

from . import einlesen, index, web


def main(argv):
    befehl = argv[0] if argv else "status"
    if befehl == "einlesen":
        print("Lese Quellen ein ...")
        einlesen.alles(nur=argv[1] if len(argv) > 1 else None)
        s = index.stand()
        print(f"Index: {s['dokumente']} Dokumente, {s['abschnitte']} Abschnitte")
    elif befehl == "status":
        s = index.stand()
        print(f"Index: {s['dokumente']} Dokumente, {s['abschnitte']} Abschnitte")
        for q, n in sorted(s["quellen"].items()):
            print(f"  {q:<16} {n}")
        if not s["quellen"]:
            print("  (leer — './dienste.sh wissen einlesen')")
    elif befehl == "suchen":
        frage = " ".join(argv[1:])
        for t in index.suchen(frage, anzahl=5):
            print(f"\n  [{t.quelle}] {t.titel} — {t.ueberschrift}")
            print(f"  {t.text[:280].strip()}")
            print(f"  ({t.herkunft})")
    elif befehl == "web":
        import asyncio
        for t in asyncio.run(web.suchen(" ".join(argv[1:]))):
            print(f"\n  {t['titel']}\n  {t['text'][:200]}\n  {t['url']}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
