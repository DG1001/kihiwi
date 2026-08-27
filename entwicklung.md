# aihiwi — Entwicklungsprotokoll

Neuestes zuerst. Jeder Eintrag hält fest, was entschieden wurde und **warum**,
was schiefging und woran es lag. Fachliches steht in [fachlich.md](fachlich.md),
Technisches in [technisch.md](technisch.md).

---

## 2026-08-27 — Erster Tag: vom Brainstorming zum laufenden Gerüst

### Ausgangslage

Auf dem GX10 lief bereits vLLM mit Ornith-1.5-35B-A3B, SearXNG und ein
eingerichteter Hermes-Agent. Kein Audio-Stack, kein Projektcode.

### Entscheidung: zwei getrennte Pipelines

Dokumentation und Dialog haben gegensätzliche Anforderungen — Qualität ohne
Zeitdruck gegen Latenz mit wegwerfbarem Ergebnis. Sie teilen sich nur den
Audio-Stream. **Warum:** Eine gemeinsame Pipeline macht zwangsläufig beides
mittelmäßig.

### Entscheidung: Sprach-Layer getrennt vom Agenten

Wake-Word, VAD, Endpointing, STT, TTS und Turn-Taking sind ein eigener Dienst
hinter einer schmalen Schnittstelle. **Warum:** Der Agent (heute Ornith direkt,
vielleicht später Hermes) soll austauschbar bleiben, ohne dass der mühsamste
Teil — das Turn-Taking — neu gebaut werden muss.

### Entscheidung: LLM gilt als möglicherweise abwesend

Der GX10 ist auch Modell-Prüfstand. Statt das zu bekämpfen, verträgt der Dienst
es: Aufzeichnung und Transkription hängen nicht am Sprachmodell, der Monitor
zeigt den Zustand an.

### Problem: vLLM belegte praktisch den ganzen Speicher

110 von 121 GiB belegt, 6 GiB Swap in Benutzung — für STT und TTS kein Platz.
**Ursache:** nicht `GPU_UTIL`, sondern `CTX`. Die Prüfstandskonfiguration fuhr
131072 Kontext bei 32 Sequenzen, das sind ~80 GB KV-Cache.
**Fix:** Profil `ornith-voice` in `model-switch` mit `CTX=32768 GPU_UTIL=0.55`.
Ergebnis: 41,9 GiB KV-Cache, 110-fache Nebenläufigkeit, Swap von 6,0 auf 1,4 GiB.
`ornith` behält die Prüfstands-Vorgaben, beide Profile stehen nebeneinander.

*Nebenbei mitkorrigiert:* Der Hilfetext von `model-switch` gab `sed -n '2,18p'`
aus und verschwieg dadurch `qwenvl`, `qwenvl30`, `stop` und `status`. Jetzt
`2,23p`. Sicherung unter `model-switch.bak.20260827_150413`.

### whisper.cpp mit CUDA auf aarch64

Das war der Schritt mit dem höchsten Risiko — das Python-Audio-Ökosystem ist auf
aarch64 + Blackwell dünn, deshalb bewusst C++ und ONNX statt Torch-Stack.
Baute in 2 min 10 s durch. Erfolgskontrolle: `ARCHS = 1210`,
`BLACKWELL_NATIVE_FP4 = 1`. **Damit war das Hauptrisiko des Projekts erledigt.**

### Der STT-Test, der in Wahrheit ein TTS-Test war

Mangels Mikrofon wurde das Testaudio mit Piper erzeugt. Die Transkripte sahen
katastrophal aus: „Layer-Norm" → „Lion-On", „Dataloader" → „Deadloader",
„Bottleneck" → „Buddenick". Auch reines Deutsch litt.

**Kontrollversuch:** dieselben Fachbegriffe mit der *englischen* Piper-Stimme und
`-l en` — alles korrekt. **Befund: Whisper kann die Begriffe, Pipers deutsche
Stimme spricht sie unverständlich aus.** Der Test hat also nicht das STT
gemessen, sondern das TTS, und dabei genau das Problem belegt, das vorher nur
vermutet war.

**Folge:** Die echte STT-Genauigkeit ist weiterhin ungemessen und braucht eine
Aufnahme über das Jabra. Für die Sprachausgabe werden Aussprache-Overrides
gebraucht.

### Belegte Whisper-Fallstricke

- **Auto-Spracherkennung kippt.** Ohne `-l de` wurde „Bitte starte die
  Aufzeichnung" zu „Please start the notification" — komplette Übersetzung.
- **Halluzination bei Stille ist real.** 5 s Stille ergaben „Vielen Dank.", mit
  Vokabular-Prompt „Untertitelung des ZDF, 2020"; rosa Rauschen ergab „Amen."
  **VAD-Gating vor dem STT ist zwingend.**
- **Der `--prompt` mit Vokabular hilft messbar:** „Vaseline" → „Baseline",
  „KV Kachel" → „KV-Cache", „Buddenick" → „Bottleneck".

### Problem: Silero-VAD meldete durchgehend „keine Sprache"

Sah aus wie kaputtes Audio, war ein Einbaufehler: **Silero v5 erwartet 64 Samples
Kontext vor dem 512er-Block**, der Eingang ist also 576 lang. Ohne den Vorlauf
liefert das Modell stillschweigend Unsinn — keine Warnung, kein Fehler.
**Fix** in `vad/silero.py`, mit Kommentar.

### Endpointing vermessen

Naiv kostet Fehlerfreiheit **1183 ms** — viermal so viel wie die gesamte
Maschinenzeit. Lexikalische Prüfung (transkribieren und das Modell fragen, ob der
Satz fertig ist) drückt das auf **~510 ms bei 0/12 Fehlschnitten**.

Drei Befunde auf dem Weg:

1. **Whisper hängt IMMER ein Satzzeichen an**, auch mitten im Satz. Dieser
   erfundene Punkt hob „Schreib ins Protokoll" von P(fertig)=0,010 auf 0,731 —
   Faktor 75. **Fix:** Punkt und Komma vor der Prüfung entfernen. Das
   **Fragezeichen behalten** — es stammt aus der Intonation und trägt echtes
   Signal (ohne es fiel „Kannst du den Sweep nochmal laufen lassen" von 0,914
   auf 0,182).
2. **Verworfen: Prompt als Regler.** „Antworte im Zweifel WEITER" kippte das
   Modell vollständig — 12 von 12 Turns ohne Endpoint. Sprachliche Zurede ist ein
   Schalter, kein Poti.
3. **vLLM ist bei `temperature: 0` nicht deterministisch.** Derselbe Satz ergab
   über sechs Läufe P zwischen 0,164 und 0,999; eindeutige Fälle blieben stabil
   (0,978–0,9997). Ursache ist die wechselnde Batch-Zusammensetzung.
   **Folge für den Entwurf:** die Prüfung taugt als *Veto gegen offensichtlich
   Unfertiges* (Schwelle ~0,1), nicht als feiner Regler. Die Decke `decke_ms`
   bleibt als Rückfallebene nötig — sie ist kein Schönheitsfehler.

**Vorbehalt:** 12 synthetische Fälle, Pausen aus reiner Stille. Echte Denkpausen
enthalten Atem und Raumgeräusch. Die Größenordnung ist belastbar, die Schwellen
nicht.

### Korrektur einer eigenen Rechnung

Zwischendurch stand ein Maschinenbudget von 280 ms im Raum, mit „LLM TTFT 70 ms"
als Posten. **Das war um etwa Faktor fünf zu optimistisch.** Die 70 ms sind das
erste *Token*; Piper braucht einen sprechbaren Teilsatz, also 10–25 Token, und
das kostet 350–670 ms. Realistisch sind **600–800 ms** Maschinenseite.
**Gegenmaßnahme:** `llm.antwort_saetze` trennt den *ersten* Brocken schon am
Komma (25–60 Zeichen), erst die folgenden am Satzende. Drückte den TTS-Anteil
von 268 auf 119 ms.

### Sprachdienst gebaut

Läuft durch, mit simuliertem Client gemessen: **1085 / 1690 / 2251 ms** Ende-zu-
Ende. Der Unterschied ist fast vollständig das Endpointing.

### Problem: der System-Prompt landet im Lautsprecher

Der Assistent antwortete „Ich kann das nicht **fuer** dich **ausfuehren**".
**Ursache:** Der System-Prompt in `konfig.py` war ASCII-fiziert
(„Aufzaehlungen", „hoechstens"), und das Modell übernahm den Stil. Piper hätte
das als „Fu-er" ausgesprochen. **Fix:** Umlaute im Prompt sind Pflicht, auch wenn
der übrige Code ASCII bleibt. Der Prompt ist jetzt zusätzlich auf Sprachausgabe
getrimmt (keine Sonderzeichen, kleine Zahlen als Wort).

### Problem: `pkill -f` erschoss zweimal die eigene Shell

`pkill -f sprachdienst.gateway` bringt die aufrufende Shell um, weil das
Suchmuster in deren eigener Kommandozeile steht — auch der `[w]`-Trick hilft
nicht, wenn der Startbefehl im selben Skript steht. **Fix:** Dienste über den
Port finden: `ss -tlnpH "sport = :8920" | grep -oP 'pid=\K[0-9]+'`. So macht es
`dienste.sh`.

### `dienste.sh` und Repository

Ein Skript für alle drei Dienste, idempotent, mit Statusanzeige. **Entscheidung:**
`start` schaltet vLLM *nicht* ungefragt um und `stop` entlädt es nicht — auf
dieser Maschine wird auch gemessen, und ein Wechsel kostet zwei Minuten.

Das Projektverzeichnis war **kein Git-Repository** — trotz des Pfads unter
`github.com/`. Nachgeholt. `aufnahmen/` ist ausgeschlossen: Labormitschnitte
gehören nicht in eine Versionsverwaltung, aus der sie sich praktisch nicht mehr
entfernen lassen.

### Offen

- Autostart (systemd-Units) — bewusst zurückgestellt.
- Wake-Word; der Client meldet bis dahin den Befehl `ansprechen`.
- Aussprache-Overrides für Piper.
- Echte STT-Genauigkeit über eine Jabra-Aufnahme.
- Batch-Transkription des Dokumentationspfads (`doku.offene_segmente()` liefert
  den Arbeitsvorrat).
- Barge-In.
- Parakeet als Alternative im Dialogpfad — whisper.cpp bringt inzwischen
  `libparakeet.so` mit; halluziniert bei Stille konstruktionsbedingt weniger.
