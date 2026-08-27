# aihiwi

Sprachgesteuerter Forschungsassistent für ein KI-Labor. Läuft **vollständig
lokal** auf einem ASUS Ascent GX10 (GB10, 121 GiB Unified Memory) — kein
Cloud-Dienst im Pfad.

Ein Laborrechner mit Freisprecher und Monitor steht im Labor, die Rechenarbeit
läuft auf dem GX10 im Serverraum. Der Assistent kann das Laborgespräch
mitschreiben (nur wenn ausdrücklich aktiviert) und wird per Zuruf angesprochen.

> **Status:** Gerüst. Der Durchstich vom Audio bis zur Sprachantwort läuft und
> ist vermessen; Aktivierungswort und die echte Client-Hardware fehlen noch.

## Schnellstart

```bash
./dienste.sh start                                        # vLLM, whisper-server, Sprachdienst
.venv/bin/python -m sprachdienst.klient_test testaudio/s3.wav
```

Der zweite Befehl ersetzt den Laborrechner: Er spielt eine WAV-Datei in Echtzeit
ein, empfängt die gesprochene Antwort und misst die Latenz. Den Monitor für den
Laborbildschirm gibt es unter <http://127.0.0.1:8920/>.

```
./dienste.sh status
Dienste:
  ✓ vLLM             :8889  bereit    ornith-1.5-35b-a3b, ctx 32768
  ✓ whisper-server   :8910  bereit    large-v3-turbo, -l de
  ✓ Sprachdienst     :8920  bereit    Monitor: http://127.0.0.1:8920/
```

Einrichten nach frischem Klon: siehe [technisch.md](technisch.md#einrichten-nach-frischem-klon).
Modelle und venv liegen nicht im Repo.

## Aufbau

```
sprachdienst/     Sprach-Layer: VAD, Endpointing, STT, TTS, WebSocket-Dienst
  monitor.html    Anzeige für den Laborbildschirm
  klient_test.py  simulierter Client, solange die Hardware fehlt
vad/              Silero-VAD und die beiden Endpointing-Verfahren
testaudio/        Testfälle und die Vokabelliste
dienste.sh        alle Dienste starten, stoppen, anzeigen
```

Der Sprach-Layer ist bewusst **vom Agenten getrennt**. Turn-Taking und Audio
wissen nichts vom Sprachmodell; dahinter lässt sich Ornith durch etwas anderes
ersetzen, ohne den mühsamen Teil neu zu bauen.

## Wo steht was

| Datei | Inhalt |
|---|---|
| [fachlich.md](fachlich.md) | Was der Assistent leisten soll, Sprache, Recht und Betrieb |
| [technisch.md](technisch.md) | Maschine, Modelle, Dienste, Architektur, Messwerte |
| [entwicklung.md](entwicklung.md) | Protokoll: Entscheidungen, Probleme, Fixes |
| [CLAUDE.md](CLAUDE.md) | Einstieg für Claude Code |

## Zwei Dinge vorab

**Aufzeichnung ist rechtlich heikel.** Kontinuierlicher Mitschnitt fällt unter
§ 201 StGB und braucht die Einwilligung aller Beteiligten. Deshalb: harte
Mute-Taste, großflächiger Aufnahmeindikator auf dem Monitor, festes
Löschkonzept. `aufnahmen/` ist aus dem Repository ausgeschlossen.

**Der Engpass ist nicht das Sprachmodell.** Die Maschine braucht 600–800 ms von
der Spracherkennung bis zum ersten Ton. Das Endpointing — die Entscheidung „der
Mensch hat aufgehört zu sprechen" — kostet noch einmal 490–1450 ms. Dort lohnt
Optimierung, nicht bei der Modellgröße.
