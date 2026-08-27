# aihiwi — KI-Labor-Forschungsassistent

Sprachgesteuerter Forschungsassistent für ein Labor, vollständig lokal auf dem
GX10. Laborrechner (Client) mit Jabra-Freisprecher und Monitor, Rechenarbeit auf
dem GX10 über Netz. Zwei Aufgaben: kontinuierliche Dokumentation (nur wenn
ausdrücklich aktiviert) und Frage/Antwort per Zuruf.

**Antworten auf Deutsch.**

## Die Maschine

`gx10` — ASUS Ascent GX10, GB10, aarch64, Ubuntu (Kernel 6.17-nvidia),
20 Kerne (Cortex-X925 + A725), **121 GiB Unified Memory**, 916 GB NVMe.

**Die wichtigste Regel dieser Hardware: Bandbreite ist knapp, nicht Kapazität.**
Rund 273 GB/s. Ein dichtes Modell muss je Token alle Gewichte durchschieben und
verliert deshalb immer gegen MoE — Qwen3.6-27B (dicht, 51 GiB) kam auf 4,4 tok/s,
Ornith (35B gesamt, 3B aktiv) auf 78,4. **Bei Modellwahl zuerst auf die aktiven
Parameter sehen, nicht auf die Gesamtgröße.** Unified Memory heißt außerdem: CPU
und GPU teilen sich diese Bandbreite.

## Modelle

`~/.local/bin/model-switch` — es läuft immer nur **ein** großes Modell.
Argumente: `ds4` | `ornith` | **`ornith-voice`** | `qwen36nvfp4` | `qwen38` |
`nemotron` | `nemotronspec` | `qwenvl30` | `stop` | `status`.

**Für aihiwi `model-switch ornith-voice` benutzen**, nicht `ornith`. Gleiches
Modell und gleicher `served-model-name`, aber `DEF_CTX=32768` und `GPU_UTIL` auf
0.55 statt der Prüfstands-Vorgaben 131072/0.85. Am Kontext in
`model-switch status` sieht man, welches Profil läuft.

Alle vLLM-Modelle teilen sich Port **8889** und den Container `vllm-model`;
`ds4-server` hört auf 8888 und hat **keinen** Autostart.

Je Aufruf übersteuerbar: `CTX=`, `GPU_UTIL=` (Default 0.85), `KV_DTYPE=`
(Default fp8), `ABBILD_UEBER=`, `VERZ_UEBER=`.

Vorhanden: Ornith-1.5-35B-A3B NVFP4 (23,5 GB, Standard), Qwen3.6-35B-A3B NVFP4,
Qwen3.8-27B NVFP4+MTP, Nemotron-3.5-Lightning NVFP4 (+DSpark),
DeepSeek-V4-Flash GGUF, **Qwen3-VL-30B-A3B als Bildmodell**, Qwen3.8-Flash-Next
Q3_K_XL (wartet auf llama.cpp PR #27742).

**Speicher-Falle:** Nicht `GPU_UTIL` ist der Hebel, sondern `CTX`. Ornith läuft
mit `DEF_CTX=131072` und `--max-num-seqs 32` — das sind ~80 GB KV-Cache für eine
Prüfstands-Konfiguration. Für einen Sprachassistenten mit einem Nutzer genügen
`CTX=32768 GPU_UTIL=0.55`, das gibt zweistellige GB frei, ohne etwas zu
verlangsamen. Dieselbe Falle bei ds4: `-c 262144` belegte 114/121 GiB und
lieferte 503er, die Fehlermeldung nannte Speicher statt Kontext.

**vLLM hängt sich reproduzierbar auf, und ein hängender Motor sieht aus wie ein
langsames Modell.** Vor jeder Latenzmessung und im Voice-Dienst als Watchdog:
`~/gx10-blog/bench/bereit.sh`.

## Weitere Dienste

| Dienst | Adresse | Anmerkung |
|---|---|---|
| vLLM | `127.0.0.1:8889` | Container `vllm-model` |
| ds4-server | `127.0.0.1:8888` | manuell zu starten |
| SearXNG | `127.0.0.1:8088` | Container, für Hermes-Suche |
| Hermes | `~/.hermes` | Mantel `~/.local/bin/hermes` |

## Netz und Zugriff

Serverraum, `<lan-praefix>` an einer FRITZ!Box, Ethernet `enP7s7` (Gigabit —
bei Einbruch zuerst `ethtool enP7s7` prüfen, ein zweipaariges Kabel hatte den
Link schon einmal auf 100 Mbit gedrückt). Stabil erreichbar über Tailnet:
`<tailnet-adresse>` / `<rechner>`.

**Direkt an der FRITZ!Box, ohne IPFire davor** — die Maschine hat eine weltweit
geroutete IPv6 ohne NAT, der Schutz hängt allein an der FRITZ!Box-Konfiguration.
Neue Dienste, besonders der Audio-Gateway, **ausdrücklich an die Tailnet- oder
LAN-Adresse binden, nie an `0.0.0.0`/`[::]`**. Stand 27.08.2026 lauscht nur SSH
auf `[::]`.

SSH ist **key-only** (`/etc/ssh/sshd_config.d/99-hardening.conf`), dazu
passwortloses sudo. Passwort-Auth nie beiläufig reaktivieren.

## Hermes

NousResearch Hermes Agent v0.20.5 in `~/.hermes/hermes-agent`, läuft gegen das
lokal geladene Modell. Interaktiv: `hermes chat --no-restore-cwd --in .`

Kandidat als Agent-Basis, mit Vorbehalten:

- **`-z/--oneshot` nimmt die erste Antwort als Ergebnis** — bei langen Aufgaben
  schreibt das Modell einen ausführlichen Bericht über Arbeit, die es nie getan
  hat. Für alles Agentische `chat -q`. Für einen Dokumentationsassistenten ist
  konfabulierte Arbeit die schlimmste Fehlerart.
- Hermes greift über sein Arbeitsverzeichnis hinaus; nach Läufen `git status`.
- `stt` steht auf `whisper-1` (Cloud) bzw. `local.model: base` — beides für
  deutsches Fachvokabular ungeeignet, muss ersetzt werden.
- Fürs Codieren taugt Hermes wenig.

## Entwurfsentscheidungen

**Sprecher:** meist eine Person, Deutsch mit englischen Fachbegriffen. Keine
Diarisierung nötig — aber **Roh-Audio behalten**, damit sie nachrüstbar bleibt.

**Zwei getrennte Pipelines**, die sich nur den Audio-Stream teilen:
*Doku* (Latenz egal, Qualität entscheidend, Batch-Transkription) und
*Dialog* (Latenz entscheidend, Ergebnis wird nach dem Turn verworfen).

**Der Sprach-Layer ist ein eigener Dienst**, nicht Teil des Agenten — Wake-Word,
VAD, Endpointing, STT, TTS, Barge-In, Turn-Taking hinter einer schmalen
Schnittstelle. Nur so bleibt der Agent austauschbar.

**Das LLM gilt als möglicherweise abwesend.** Diese Maschine ist auch ein
Modell-Prüfstand; jedes `model-switch` nimmt dem Assistenten das Gehirn. Bei
Modellwechsel muss Aufzeichnung und Transkription weiterlaufen und der Monitor
das anzeigen.

**Whisper-Fallstricke für Deutsch mit englischen Fachbegriffen:**
Sprache hart auf `de` und `task=transcribe` (Auto-Detect kippt bei gehäuften
englischen Begriffen auf `en`); **VAD-Gating vor dem STT ist Pflicht**, weil
large-v3-turbo in Sprechpausen deutsche Phantomsätze halluziniert („Vielen
Dank.", „Untertitel von …"); der `initial_prompt` mit korrekt geschriebenen
Fachbegriffen ist der größte Qualitätshebel. Eine gepflegte **Vokabelliste** ist
deshalb Projektbestandteil — sie speist Whisper-Prompt, LLM-Nachkorrektur und
Piper-Aussprache-Overrides (deutsche Stimmen sprechen „Layer" sonst „La-yer").

**Latenzziel** 700 ms–1,2 s vom Sprechende bis zum ersten Ton. Größter
Einzelposten ist das Endpointing, nicht das LLM. Satzweises Streaming in den TTS.
Triviale Kommandos über einen Intent-Router am LLM vorbei.

**Recht:** Kontinuierliche Aufzeichnung fällt unter § 201 StGB — Einwilligung
aller Beteiligten nötig, nicht bloß Datenschutz. Harte Mute-Taste, sichtbarer
Aufnahme-Indikator, festes Löschkonzept, Datenschutzbeauftragte und Personalrat
vor Inbetriebnahme. Bestehende Auflage von Fred: **nichts verlässt das Netz.**

## Gemessener Stand (27.08.2026)

**whisper.cpp** in `~/code/whisper.cpp`, gebaut mit
`cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121 -DCMAKE_BUILD_TYPE=Release`
(2 min 10 s, `-j 18`). CUDA laeuft: `ARCHS = 1210`, `BLACKWELL_NATIVE_FP4 = 1`.
Modell `ggml-large-v3-turbo.bin` (1,6 GB) in `models/`.
Server: `./build/bin/whisper-server -m models/ggml-large-v3-turbo.bin --host 127.0.0.1 --port 8910 -l de -t 8`

**Piper** im venv `.venv` des Projekts (`pip install piper-tts`, aarch64-Wheels
vorhanden), Stimmen in `voices/`: `de_DE-thorsten-medium`, `en_US-lessac-medium`.

Latenzbudget, warm gemessen:

| Glied | Zeit | Anmerkung |
|---|---|---|
| STT (whisper-server, large-v3-turbo) | ~100 ms | konstant, weil Whisper immer ein 30-s-Fenster rechnet |
| LLM erstes Token (Ornith, ctx 32768) | ~70 ms | 65 tok/s; erster Aufruf nach Start 226 ms |
| LLM erster **sprechbarer** Brocken | 350-670 ms | 30-90 Zeichen, gemessen in der Pipeline |
| TTS erster Chunk (Piper, CPU) | 120-270 ms | je nach Brockenlaenge; Stimme laedt einmalig in 595 ms |
| **Summe Maschine** | **~600-800 ms** | ohne VAD/Endpointing und ohne Netz |

**Achtung, naheliegender Denkfehler:** Die 70 ms sind das erste *Token*, nicht der
erste *sprechbare* Brocken. Piper braucht einen Teilsatz, also 10-25 Token — das
kostet 350-670 ms und nicht 70. Eine Budgetrechnung, die TTFT einsetzt, ist um
den Faktor fuenf zu optimistisch. Deshalb trennt `llm.antwort_saetze` den ERSTEN
Brocken schon am Komma (25-60 Zeichen) und erst die folgenden am Satzende: das
drueckte den TTS-Anteil von 268 auf 119 ms.

Wichtig bei Messungen: **immer warm messen.** Der erste CUDA-Lauf kostet
Kernel-Autotuning (266 ms statt 100 ms), der erste vLLM-Aufruf 226 ms statt 70 ms,
und Piper als CLI-Prozess 800 ms statt 110 ms (Python- und ONNX-Start).
Prozesse muessen dauerhaft laufen, nicht je Anfrage starten.

**vLLM laeuft seit 27.08.2026 mit `CTX=32768 GPU_UTIL=0.55`** — 41,9 GiB KV-Cache,
3,6 Mio. Token, 110x Nebenlaeufigkeit bei 32k. Swap von 6,0 auf 1,4 GiB gefallen.
Seit dem 27.08.2026 als Eintrag `ornith-voice` in `model-switch` hinterlegt, damit
die Werte einen Wechsel ueberleben; `ornith` behaelt die Pruefstands-Vorgaben.
Beim selben Eingriff mitkorrigiert: der Hilfetext des Skripts gab `sed -n '2,18p'`
aus und verschwieg dadurch `qwenvl`, `qwenvl30`, `stop` und `status` — jetzt `2,23p`.
Sicherung unter `~/.local/bin/model-switch.bak.20260827_150413`.

### Belegte Befunde

- **Auto-Spracherkennung kippt.** Ohne `-l de` uebersetzt Whisper deutsche Saetze
  mit englischen Fachbegriffen komplett ins Englische („Bitte starte die
  Aufzeichnung" wurde zu „Please start the notification"). `-l de` ist Pflicht.
- **Halluzination bei Stille ist real und reproduzierbar.** 5 s Stille ergaben
  „Vielen Dank.", mit Vokabular-Prompt „Untertitelung des ZDF, 2020"; rosa
  Rauschen ergab „Amen." **VAD-Gating vor dem STT ist zwingend.**
- **Der `--prompt` mit Vokabular hilft messbar:** „Vaseline" wurde zu „Baseline",
  „KV Kachel" zu „KV-Cache", „Buddenick" zu „Bottleneck".
- **Pipers deutsche Stimme verstuemmelt englische Fachbegriffe.** Kontrolliert
  nachgewiesen: dieselben Begriffe mit `en_US-lessac` gesprochen versteht Whisper
  korrekt, mit `de_DE-thorsten` wird „Layer-Norm" zu etwas, das als „Lion-On"
  ankommt. Das ist ein **TTS**-Problem, kein STT-Problem — Aussprache-Overrides
  fuer die haeufigsten Fachbegriffe werden gebraucht.
- **Noch offen: die echte STT-Genauigkeit.** Synthetisches Audio taugt dafuer
  nicht, das obige misst Piper mit. Braucht eine Aufnahme ueber das Jabra.
- whisper.cpp bringt inzwischen `libparakeet.so` mit — NVIDIA Parakeet als
  moegliche schnellere Alternative fuer den Dialog-Pfad, ungetestet.

## VAD und Endpointing (27.08.2026)

Code in `vad/`: `silero.py` (Silero-VAD v5 ueber onnxruntime, ohne torch),
`endpoint.py` (naiver Endpointer), `lexikalisch.py` (Pruefung per LLM).
Testfaelle in `testaudio/turns/` — je zwei Sprechteile mit einer gebauten
Denkpause von 200/400/600/800 ms dazwischen, Grundwahrheit in `meta.json`.

**Silero-VAD kostet 0,07 ms je 32-ms-Block** — 0,2 % eines Kerns. Auf dem Client
belanglos, das kann auch ein Futro.

**Falle beim Einbau:** Silero v5 will 64 Samples Kontext VOR dem 512er-Block,
Eingang also 576. Ohne den Vorlauf meldet das Modell durchgehend „keine Sprache",
ohne Fehlermeldung — sah aus wie kaputtes Audio.

**Naiver Endpointer, Kosten fuer Fehlerfreiheit:**

| Schwelle | Fehlschnitte | Zusatzlatenz |
|---|---|---|
| 600 ms | 8/12 | 575 ms |
| 800 ms | 5/12 | 759 ms |
| 1000 ms | 2/12 | 991 ms |
| **1200 ms** | **0/12** | **1183 ms** |

Damit ist das Endpointing viermal so teuer wie die gesamte Maschinenzeit (280 ms).

**Lexikalische Pruefung** (bei 300 ms Stille transkribieren und Ornith fragen, ob
der Satz fertig ist) kostet **228 ms** je Pruefung: 130 ms STT + 97 ms LLM.
Bestes gemessenes Ergebnis: **~510 ms Medianlatenz bei 0/12 Fehlschnitten**,
also gut halb so viel wie naiv.

### Drei Befunde, die beim Nachbauen Zeit sparen

1. **Whisper haengt IMMER ein Satzzeichen an**, auch mitten im Satz. Dieser
   erfundene Punkt hebt „Schreib ins Protokoll" von P(fertig)=0,010 auf 0,731 —
   Faktor 75. **Schlusspunkt und Komma vor der Pruefung entfernen.** Das
   Fragezeichen dagegen BEHALTEN: es stammt aus der Intonation und traegt echtes
   Signal (ohne es faellt „Kannst du den Sweep nochmal laufen lassen" von 0,914
   auf 0,182).
2. **Ein Prompt taugt nicht als Regler.** „Antworte im Zweifel WEITER" kippte das
   Modell vollstaendig — 12 von 12 Turns ohne Endpoint. Entweder Logprobs
   auswerten oder die Entscheidung anders gewichten, aber nicht per Zurede.
3. **vLLM ist bei `temperature: 0` nicht deterministisch.** Derselbe Satz ergab
   ueber sechs Laeufe P zwischen 0,164 und 0,999 (Spanne 0,83); eindeutige Faelle
   blieben stabil (0,978–0,9997). Ursache ist die wechselnde Batch-Zusammensetzung.
   **Folge fuer den Entwurf:** die lexikalische Pruefung taugt als *Veto gegen
   offensichtlich unfertige* Aeusserungen (Schwelle niedrig, ~0,1), nicht als fein
   einstellbarer Regler. Die Decke (`decke_ms`) bleibt als Rueckfallebene noetig.

**Vorbehalt:** 12 synthetische Faelle, Pausen aus reiner Stille. Echte Denkpausen
enthalten Atem, „aeh" und Raumgeraeusch — der VAD sieht hier zu gut aus. Die
Schwellen sind damit NICHT bestimmt, nur die Groessenordnung.

## Einrichten (nach frischem Klon)

Modelle und venv liegen bewusst nicht im Repo. Zum Wiederherstellen:

    python3 -m venv .venv && .venv/bin/pip install piper-tts websockets numpy
    .venv/bin/python -m piper.download_voices de_DE-thorsten-medium --data-dir voices
    .venv/bin/python -m piper.download_voices en_US-lessac-medium  --data-dir voices
    curl -L -o vad/silero_vad.onnx \
      https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx
    .venv/bin/python testaudio/turns.py          # erzeugt das Testmaterial neu

whisper.cpp liegt ausserhalb des Projekts in `~/code/whisper.cpp` — Bau und
Modell siehe „Gemessener Stand".

## Der Sprachdienst (Gerüst, 27.08.2026)

`sprachdienst/`, lauffähig und mit simuliertem Client durchgemessen:

| Datei | Rolle |
|---|---|
| `konfig.py` | alle Einstellungen an einer Stelle |
| `zustand.py` | Zustand + Beobachter; Aufnahme und Dialog sind **orthogonal** |
| `turn.py` | VAD, Endpointing, lexikalische Prüfung nebenher |
| `stt.py` / `tts.py` / `llm.py` | Dienstanbindungen, jede einzeln ersetzbar |
| `doku.py` | Dokumentationspfad, rotiert alle 5 min, behält Roh-Audio |
| `gateway.py` | WebSocket-Dienst, bindet auf `127.0.0.1:8920` |
| `monitor.html` | Laborbildschirm, Aufnahmebalken über ein Drittel der Höhe |
| `klient_test.py` | spielt eine WAV in Echtzeit ein, misst Ende-zu-Ende |

**Alle Dienste laufen über `./dienste.sh`** (im Projekt):

    ./dienste.sh start          alles hochfahren, idempotent
    ./dienste.sh stop           Sprachdienst + whisper-server (vLLM bleibt)
    ./dienste.sh stop --vllm    zusätzlich das Modell entladen
    ./dienste.sh neustart       nur die beiden lokalen Dienste
    ./dienste.sh status         was läuft, auf welchem Port
    ./dienste.sh log sprach     Protokoll folgen (sprach | whisper | vllm)

Protokolle in `logs/`. **`start` schaltet vLLM nicht ungefragt um** — läuft dort
ein anderes Profil, wird nur gewarnt, denn auf dieser Maschine wird auch gemessen
und ein Wechsel kostet zwei Minuten. Dienste werden über den **Port** gefunden,
nicht über den Prozessnamen (siehe Bash-Falle unten).

Einzeln, wenn nötig: `.venv/bin/python -m sprachdienst.gateway`
Testen: `.venv/bin/python -m sprachdienst.klient_test testaudio/s3.wav [--aufnahme]`

**Protokoll** auf `/audio`: Client sendet Binärrahmen (PCM int16 mono 16 kHz) und
JSON-Befehle (`mikro`, `aufnahme`, `ansprechen`, `abbrechen`); der Dienst sendet
JSON (`zustand`, `text`, `ton`, `ton_ende`) und Binäraudio. `/monitor` liefert nur
den Zustand, `GET /` die Monitorseite.

**Gemessen Ende-zu-Ende** (Sprechende bis erster Ton), drei Läufe:
1085 ms / 1690 ms / 2251 ms. Der Unterschied ist fast vollständig das
Endpointing — 489 ms wenn die lexikalische Prüfung greift, 1446 ms wenn sie in
die Decke läuft (dann fehlt auch ihr Transkript und das STT läuft nochmal).
**Greift die Prüfung, wird ihr Transkript weiterverwendet und das STT im
Antwortpfad ganz übersprungen** (0 ms statt 130-257 ms).

**Bewusst noch nicht drin:** Wake-Word (der Client meldet stattdessen den Befehl
`ansprechen`), Barge-In, Batch-Transkription des Dokumentationspfads
(`doku.offene_segmente()` liefert den Arbeitsvorrat), Autostart.

**Der System-Prompt landet im Lautsprecher.** In `konfig.py` stand er zunächst
ASCII-fiziert („Aufzaehlungen", „hoechstens") — das Modell ahmte den Stil nach und
antwortete „Ich kann das nicht fuer dich ausfuehren", was Piper als „Fu-er"
ausspricht. Umlaute im Prompt sind deshalb Pflicht, auch wenn der übrige Code
ASCII bleibt.

**Bash-Falle beim Neustarten:** `pkill -f sprachdienst.gateway` bringt die eigene
Shell um, weil das Suchmuster in ihrer eigenen Kommandozeile steht. Über den Port
gehen: `ss -tlnpH "sport = :8920" | grep -oP 'pid=\K[0-9]+'`.

## Offen / später

- **Autostart fehlt bewusst.** vLLM (`model-switch ornith-voice`) und
  `whisper-server` überstehen keinen Reboot und müssen von Hand gestartet werden.
  systemd-Units sind ein eigener Schritt, absichtlich zurückgestellt (27.08.2026).
- Wake-Word: openWakeWord-Modelle sind englisch, deutsches Wort braucht eigenes
  Training oder ein in beiden Sprachen gleich klingendes Wort.
- Aussprache-Overrides für Piper (englische Fachbegriffe, siehe Befunde).
- Echte STT-Genauigkeit über eine Jabra-Aufnahme messen.
- Parakeet als Alternative im Dialog-Pfad ausprobieren.

## Maschinen-Notizen

Ausführlicher in `~/.claude/projects/-home-nutzer/memory/` (Projekt-Scope `~`,
in aihiwi-Sessions nicht automatisch geladen): `gx10-zwei-modelle`,
`gx10-modell-benchmark`, `gx10-netzwerk-serverraum`, `gx10-serverraum-umzug`,
`gx10-ssh-key-only`, `gx10-ds4-opencode`, `hermes-searxng`,
`qwen38-flash-wartet`. Bei Widersprüchen sind jene die Quelle — diese Datei ist
die Zusammenfassung.
