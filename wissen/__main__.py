"""Kommandozeile: ./dienste.sh wissen [einlesen|erschliessen|vektoren|katalog|ueberblick|status|suchen ...]"""
import sys

from . import einlesen, erschliessen, index, vektor, web


def main(argv):
    befehl = argv[0] if argv else "status"
    if befehl == "einlesen":
        import asyncio
        print("Lese Quellen ein ...")
        nur = next((a for a in argv[1:] if not a.startswith("--")), None)
        einlesen.alles(nur=nur)
        s = index.stand()
        print(f"Index: {s['dokumente']} Dokumente, {s['abschnitte']} Abschnitte")
        if "--nur-lesen" in argv:
            print("  (Nachziehen übersprungen — './dienste.sh wissen erschliessen'"
                  " und '... vektoren' laufen lassen)")
            return 0
        # Ohne Obergrenze: auf der Kommandozeile darf es dauern.
        print("Ziehe Schlagwörter, Kurzfassungen und Vektoren nach ...")
        e = asyncio.run(erschliessen.nachziehen(grenze=None))
        for name in ("schlagwoerter", "kurzfassungen"):
            t = e.get(name) or {}
            if t.get("offen"):
                print(f"  {name}: {t['erledigt']} von {t['offen']}, {t['sekunden']:.0f} s")
        v = e.get("vektoren") or {}
        if v.get("fehler"):
            print(f"  vektoren: übersprungen ({v['fehler'][:80]})")
        elif v.get("offen"):
            print(f"  vektoren: {v['erledigt']} von {v['offen']}, {v['sekunden']:.0f} s")
    elif befehl == "erschliessen":
        # Braucht das Sprachmodell. Laut scheitern, nicht still weniger tun.
        import asyncio
        from sprachdienst import konfig
        alle = "--alle" in argv
        grenze = next((int(a) for a in argv[1:] if a.isdigit()), None)
        print(f"Erschliesse Abschnitte mit {konfig.LLM_MODEL} "
              f"({'alle' if alle else 'nur ohne Schlagwoerter'}) ...")
        e = asyncio.run(erschliessen.alles(nur_fehlende=not alle, grenze=grenze))
        if not e["offen"]:
            print("  Nichts zu tun — alle Abschnitte haben Schlagwoerter.")
        else:
            print(f"  {e['erledigt']} erschlossen, {e['gescheitert']} gescheitert, "
                  f"{e['sekunden']:.0f} s")
        if e["gescheitert"]:
            return 1
    elif befehl == "vektoren":
        print(f"Bette fehlende Abschnitte ein ({vektor.MODELL}) ...")
        e = vektor.nachtragen()
        if not e["offen"]:
            print("  Nichts zu tun — alle Abschnitte haben einen Vektor.")
        else:
            print(f"  {e['erledigt']} eingebettet, {e['sekunden']:.0f} s")
    elif befehl == "katalog":
        import asyncio
        from sprachdienst import konfig
        print("Baue Kurzfassungen je Dokument ...")
        e = asyncio.run(erschliessen.kurzfassungen(neu_bauen="--alle" in argv))
        if not e["offen"]:
            print("  Nichts zu tun — alle Dokumente haben eine Kurzfassung.")
        else:
            print(f"  {e['erledigt']} gebaut, {e['gescheitert']} gescheitert, "
                  f"{e['sekunden']:.0f} s")
        if e["gescheitert"]:
            return 1
    elif befehl == "ueberblick":
        t = erschliessen.ueberblick()
        print(t)
        print(f"\n({len(t)} Zeichen, rund {len(t)/3.7/1000:.0f}k Token)")
    elif befehl == "status":
        s = index.stand()
        print(f"Index: {s['dokumente']} Dokumente, {s['abschnitte']} Abschnitte")
        c = index.lesen()
        mit = c.execute("SELECT COUNT(*) FROM abschnitte WHERE schlagwoerter != ''").fetchone()[0]
        # Nur die AKTUELLEN zaehlen: eine Kurzfassung zu einem veralteten
        # Fingerabdruck beschreibt eine Fassung, die es nicht mehr gibt.
        # Vorher meldete status "351 von 351", waehrend 350 davon veraltet
        # waren und gerade neu gebaut wurden.
        kurz = c.execute("""SELECT COUNT(*) FROM dokumente d
             JOIN kurzfassung k ON k.pfad = d.pfad
             WHERE k.fingerab IS d.fingerab""").fetchone()[0]
        vek, _ = vektor.bestand(c)
        c.close()
        print(f"  erschlossen: {mit} von {s['abschnitte']} Abschnitten")
        print(f"  Kurzfassungen: {kurz} von {s['dokumente']} Dokumenten")
        print(f"  Vektoren: {vek} von {s['abschnitte']} Abschnitten"
              + ("" if vek else "  (Suche laeuft nur ueber den Volltext)"))
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
