# kihiwi — Einstieg für Claude Code

Sprachgesteuerter Forschungsassistent für ein KI-Labor, vollständig lokal auf dem
GX10. Überblick in [README.md](README.md).

**Antworten auf Deutsch.**

## Wo steht was

| Frage | Datei |
|---|---|
| Was soll der Assistent leisten? Sprache, Recht, Betrieb | [fachlich.md](fachlich.md) |
| Maschine, Modelle, Dienste, Architektur, Messwerte | [technisch.md](technisch.md) |
| Was wurde wann entschieden, was ging schief | [entwicklung.md](entwicklung.md) |

**Bei technischen Fragen zuerst [technisch.md](technisch.md) lesen** — dort
stehen die gemessenen Zahlen und die Fallen, die schon einmal Zeit gekostet
haben. Neue Erkenntnisse gehören in die passende der drei Dateien, neue
Entscheidungen und Fehlschläge zusätzlich als Eintrag in `entwicklung.md`.

## Regeln, die immer gelten

- **Modell starten mit `model-switch ornith-voice`**, nicht `ornith`. Letzteres
  ist das Prüfstandsprofil und belegt den ganzen Speicher.
- **Dienste über `./dienste.sh`** starten, stoppen und prüfen.
- **Niemals an `0.0.0.0` oder `[::]` binden.** Die Maschine hat eine weltweit
  geroutete IPv6 ohne NAT davor.
- **Nichts verlässt das Netz.** Keine Cloud-Dienste im Pfad, auch nicht zum
  Testen.
- **`aufnahmen/` niemals committen.** Labormitschnitte, § 201 StGB.
- **Prozesse über den Port finden, nicht über den Namen.** `pkill -f` erwischt
  die eigene Shell.
- **Immer warm messen.** Kalte Läufe sind um ein Vielfaches langsamer und
  verfälschen jede Latenzaussage.

## Maschinen-Notizen außerhalb des Repos

`~/.claude/projects/-home-nutzer/memory/` (Projekt-Scope `~`, hier nicht
automatisch geladen) enthält zweierlei:

- **Notizen zum GX10 als Maschine** — `model-switch`, Benchmark, Netz,
  Hermes-Fallen. Bei Widersprüchen zu `technisch.md` sind jene die Quelle.
- **Arbeitsweise von Fred**, projektübergreifend: die Dreiteilung der
  Dokumentation und Commits an sinnvollen Schnitten ohne Nachfrage.

Sie liegen dort und nicht im kihiwi-Scope, weil sie über dieses Projekt
hinaus gelten.
