# kihiwi

Sprachgesteuerter Forschungsassistent für ein Labor. Läuft **vollständig
lokal** — kein Cloud-Dienst im Pfad, auch nicht zum Testen.

Ein Laborrechner mit Freisprecher und Monitor steht im Labor, die Rechenarbeit
läuft auf einer separaten Maschine. Der Assistent schreibt das Laborgespräch mit
(nur wenn ausdrücklich aktiviert), beantwortet Fragen aus den eigenen
Unterlagen, stellt Timer und erzeugt aus jeder Aufzeichnung ein Protokoll.

*[English overview](README.en.md) · [Glossar Deutsch–Englisch](GLOSSAR.md)*

> **Der Code ist deutsch** — Bezeichner, Kommentare, Dokumentation. Das ist
> Absicht: Auslösewörter, Zahlwörter, Umlautbehandlung und die unscharfe
> Erkennung deutscher Spracherkennungsausgabe sind keine Übersetzung, sondern
> die Sache selbst. Das [Glossar](GLOSSAR.md) übersetzt die Begriffe.

Entwickelt auf einem ASUS Ascent GX10 (GB10, 121 GiB Unified Memory, aarch64).
Gebunden ist daran nichts: Alle Endpunkte hängen an Umgebungsvariablen, und das
Sprachmodell wird über die OpenAI-kompatible Schnittstelle angesprochen.

## Was er kann

**Ansprechen.** „Kiwi" sagen, auf „Ja?" warten, dann sprechen — oder alles in
einem Zug. Danach bleibt das Gespräch offen, bis „Danke, Kiwi" fällt oder 45
Sekunden nichts kommt.

**Auslösewörter** wählen den Weg, statt einen Router raten zu lassen:

| Wort | Wirkung |
|---|---|
| **Internetsuche** | eine Sache schnell nachsehen (2–3 s) |
| **Internetrecherche** | gründlich, Ergebnis kommt nach (30–40 s) |
| **Dokumentenrecherche** | in den eigenen Unterlagen suchen |
| **Wissensabgleich** | neue Stände aus Repo und Cloud holen |
| **Hermesaufgabe** | Anweisung unverändert an den Rechercheagenten |
| **Kiwihilfe** | die Liste, gesprochen und auf dem Monitor |

**Ohne Aktivierungswort:** „Sprachaufzeichnung starten", „Audioaufnahme
starten", „Tonaufzeichnung stoppen" — und dieselben mit „… stoppen" — wirken
direkt, ohne erst „Kiwi" zu rufen.

**Timer und Erinnerungen.** „Timer zehn Minuten", „erinner mich in einer halben
Stunde daran, die Probe zu wechseln", „erinner mich um 15 Uhr an die
Besprechung". Überdauert einen Neustart des Dienstes.

**Protokolle.** Beim Stoppen der Aufzeichnung entsteht automatisch eines: mit
Transkript, absoluten Zeitstempeln, Zusammenfassung und — soweit sicher —
Sprecherangabe. Danach sofort abrufbar, denn es geht in den Wissensindex.

**Browseroberfläche.** Links der Gesprächsverlauf, rechts eine Fläche, die der
Assistent auf Zuruf befüllt: Recherchen, Protokolle, die Ablage, die Hilfe.

## Voraussetzungen

| Was | Wofür | Anmerkung |
|---|---|---|
| Python 3.12 | alles | `pip install -r requirements.txt` |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) als Server | Spracherkennung | mit `ggml-large-v3-turbo` |
| OpenAI-kompatibler LLM-Server | Antworten | vLLM, llama.cpp, Ollama — beliebig |
| [Piper](https://github.com/OHF-Voice/piper1-gpl) + eine Stimme | Sprachausgabe | `de_DE-thorsten-medium` |
| [Silero VAD](https://github.com/snakers4/silero-vad) als ONNX | Sprachaktivität | nach `vad/silero_vad.onnx` |
| SearXNG *(optional)* | Internetsuche | sonst `KIHIWI_WEB=0` |
| sherpa-onnx-Modelle *(optional)* | Sprechertrennung | `./dienste.sh sprechermodelle` |

Alle Adressen über Umgebungsvariablen: `KIHIWI_LLM`, `KIHIWI_STT`,
`KIHIWI_MODEL`, `KIHIWI_SEARXNG`, `KIHIWI_BIND`, `KIHIWI_PORT`.

## Schnellstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp vokabular.beispiel.txt vokabular.txt          # eigenes Fachvokabular
cp wissen/quellen.beispiel.json wissen/quellen.json
./dienste.sh start                               # LLM, whisper-server, Sprachdienst
./dienste.sh status
```

Bedienung im Browser unter <http://127.0.0.1:8920/klient>, Anzeige für den
Laborbildschirm unter <http://127.0.0.1:8920/>.

Ausführlich in [technisch.md](technisch.md#einrichten-nach-frischem-klon).
Modelle und venv liegen nicht im Repository.

## Aufbau

```
sprachdienst/     Sprach-Layer: VAD, Endpointing, STT, TTS, WebSocket-Dienst
  absicht.py      Absichtserkennung und Auslösewörter
  wecker.py       Timer und Erinnerungen, deutsche Zeitangaben
  sprecher.py     Sprechertrennung (optional)
  klient.html     Bedienoberfläche; monitor.html für den Laborbildschirm
wissen/           Wissensindex (SQLite FTS5), Quellen, Web- und Agentensuche
vad/              Silero-VAD und die beiden Endpointing-Verfahren
dienste.sh        alle Dienste starten, stoppen, anzeigen
```

Der Sprach-Layer ist bewusst **vom Agenten getrennt**. Turn-Taking und Audio
wissen nichts vom Sprachmodell.

## Zwei Grundsätze

**Wo eine Handlung deterministisch ist, entscheidet der Dienst — nicht das
Modell.** Aufzeichnung, Timer, Auslösewörter, Zeitangaben werden im Code
ausgewertet. Der Grund ist gemessen: derselbe Prompt, der für gutes Sprechen
sorgt, unterdrückte Werkzeugaufrufe (0 von 3 Versuchen mit, 3 von 3 ohne). Ein
Timer, den das Modell zu stellen vergisst, fällt erst auf, wenn er nicht
klingelt.

**Lieber keine Angabe als eine falsche.** Findet die Suche nichts, sagt der
Assistent das, statt eine Zahl zu erfinden. Ist eine Sprecherzuordnung nicht
eindeutig, bleibt sie leer. Das Protokoll trennt Transkript von Zusammenfassung
und markiert letztere als abgeleitet.

## Aufzeichnung ist rechtlich heikel

Kontinuierlicher Mitschnitt fällt in Deutschland unter § 201 StGB und braucht
die Einwilligung aller Beteiligten. Deshalb: harte Mute-Taste, großflächiger
Aufnahmeindikator auf dem Monitor, Roh-Audio bleibt als Beleg erhalten,
`aufnahmen/` ist vom Repository ausgeschlossen. Das ist keine Rechtsberatung —
wer das einsetzt, klärt es für sich.

## Der Engpass ist nicht das Sprachmodell

Auf der Entwicklungsmaschine 600–800 ms von der Spracherkennung bis zum ersten
Ton. Das Endpointing — die Entscheidung „der Mensch hat aufgehört zu sprechen"
— kostet noch einmal 490–1450 ms. Dort lohnt Optimierung, nicht bei der
Modellgröße. Bandbreite schlägt Kapazität: ein dichtes 27B-Modell kam auf
4,4 tok/s, ein MoE mit 35B gesamt und 3B aktiv auf 78,4.

## Wo steht was

| Datei | Inhalt |
|---|---|
| [fachlich.md](fachlich.md) | Was der Assistent leisten soll, Sprache, Recht, Betrieb |
| [technisch.md](technisch.md) | Maschine, Modelle, Dienste, Architektur, Messwerte |
| [entwicklung.md](entwicklung.md) | Protokoll: Entscheidungen, Probleme, Fehlschläge |
| [DRITTANBIETER.md](DRITTANBIETER.md) | Fremde Bestandteile und ihre Lizenzen |
| [CLAUDE.md](CLAUDE.md) | Einstieg für Claude Code |

`entwicklung.md` schreibt auch die Irrwege mit — den synthetischen Test, der in
Wahrheit die Sprachausgabe prüfte; die zwei Lookbehinds, die dieselbe Stelle
prüfen; die Bilanz, die „alles aktuell" meldete, während zwei Commits
hereinkamen. Das ist der nützlichste Teil.

## Lizenz

MIT, siehe [LICENSE](LICENSE). Fremde Bestandteile mit eigenen Lizenzen sind in
[DRITTANBIETER.md](DRITTANBIETER.md) aufgeführt — **Piper steht unter GPL-3.0**
und wird nicht mitgeliefert.
