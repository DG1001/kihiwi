# Glossar Deutsch–Englisch

Der Code ist deutsch — siehe [README.en.md](README.en.md) für das Warum. Diese
Tabelle übersetzt, was man zum Lesen braucht. Die Struktur selbst ist gewöhnlich.

*The code is German. This table maps the terms; the structure itself is
conventional.*

## Module — *modules*

| Deutsch | English | Was darin steckt |
|---|---|---|
| `sprachdienst/` | speech service | audio, turn-taking, STT/TTS, WebSocket server |
| `absicht.py` | intent | intent detection, trigger words, direct commands |
| `aktivierung.py` | activation | wake word, fuzzy matching |
| `doku.py` | documentation | the recorder |
| `gateway.py` | gateway | the service itself: sessions, HTTP, WebSocket |
| `konfig.py` | config | every setting, all env-overridable |
| `llm.py` | — | language model, tool calls, sentence splitting |
| `protokoll.py` | protocol | transcription, correction, protocol generation |
| `sprecher.py` | speaker | speaker diarization |
| `stt.py` / `tts.py` | — | speech to text / text to speech |
| `turn.py` | — | turn detection, endpointing |
| `wecker.py` | alarm clock | timers, reminders, German time parsing |
| `zahlwort.py` | number word | numbers to spoken German |
| `zustand.py` | state | service state and its observers |
| `wissen/` | knowledge | index, sources, web and agent search |
| `einlesen.py` | ingest | reading sources into the index |
| `vad/` | — | voice activity detection, endpointing |

## Begriffe — *terms*

| Deutsch | English |
|---|---|
| Abschnitt | section, transcript segment |
| Ablage | archive (protocols and research results) |
| Absicht | intent |
| Aktivierungswort | wake word |
| Anlass | occasion, what a reminder is about |
| Aufzeichnung | recording |
| Auslösewort | trigger word |
| Beimischung | the assistant's own speech mixed into the recording |
| Bühne | stage (the large panel the assistant fills) |
| Erinnerung | reminder |
| Gespräch(smodus) | conversation (mode) |
| Merker | marker, a pointer in the chat to stage content |
| Nachbereitung | post-processing (after a recording stops) |
| Protokoll | protocol, minutes |
| Quelle | source |
| Recherche | research (the long, multi-step kind) |
| Redebeitrag | speech turn by one speaker |
| Sprecher | speaker |
| Verlauf | history, the conversation log |
| Werkzeug | tool (as in tool call) |
| Wissensabgleich | knowledge sync |
| Zustand | state |

## Häufige Funktionen — *common functions*

| Deutsch | English |
|---|---|
| `erkennen` | recognise, detect |
| `deuten` | interpret, parse |
| `zerlegen` | split, decompose |
| `zuordnen` | assign, attribute |
| `melden` | report — speak **and** record in the history |
| `stellen` | set (a timer) |
| `laden` | load |
| `sichern` | persist |
| `verbinden` / `lesen` | connect (write) / connect read-only |
| `verfügbar` | available |
| `erreichbar` | reachable |
| `sprechbar` | speakable (markdown stripped, numbers spelled out) |
| `will_ändern` | wants to change (vs. merely asking about state) |
| `soll_anschalten` | should turn on |

## Was die Kommentare gern sagen — *recurring comment phrases*

| Deutsch | English |
|---|---|
| „bewusst …" | deliberately … |
| „sonst …" | otherwise … (what went wrong before) |
| „gemessen: …" | measured: … |
| „faellt sonst durch" | would otherwise be rejected |
| „lieber X als Y" | prefer X over Y |
