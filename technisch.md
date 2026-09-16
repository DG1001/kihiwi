# kihiwi — Technische Beschreibung

## Die Maschine

ASUS Ascent GX10, GB10, aarch64, Ubuntu (Kernel 6.17-nvidia), 20 Kerne
(Cortex-X925 + A725), **121 GiB Unified Memory**, 916 GB NVMe.

**Die wichtigste Regel dieser Hardware: Bandbreite ist knapp, nicht Kapazität.**
Rund 273 GB/s. Ein dichtes Modell muss je Token alle Gewichte durchschieben und
verliert deshalb immer gegen MoE — Qwen3.6-27B (dicht, 51 GiB) kam auf 4,4 tok/s,
Ornith (35B gesamt, 3B aktiv) auf 78,4. **Bei Modellwahl zuerst auf die aktiven
Parameter sehen, nicht auf die Gesamtgröße.** Unified Memory heißt außerdem: CPU
und GPU teilen sich diese Bandbreite, ein großes CPU-Modell hilft nicht.

## Modelle

`~/.local/bin/model-switch` — es läuft immer nur **ein** großes Modell.
Argumente: `ds4` | `ornith` | **`ornith-voice`** | `qwen36nvfp4` | `qwen38` |
`nemotron` | `nemotronspec` | `qwenvl30` | `stop` | `status`.

**Für kihiwi ein `*-voice`-Profil benutzen**, nicht das Prüfstandsprofil.
Es gibt `ornith-voice` und `qwen36nvfp4-voice`; welches `./dienste.sh start`
lädt, steht in `KIHIWI_PROFIL`. **Vorgabe ist seit dem 01.09.2026
`qwen36nvfp4-voice`** — gleiche Bauart und gleicher Durchsatz wie Ornith
(78,3 gegen 78,4 tok/s), aber es sucht in den Unterlagen *und* antwortet
trotzdem aus eigenem Wissen, wenn nichts zu finden ist.

```bash
./dienste.sh start                              # Qwen3.6-35B-A3B NVFP4
KIHIWI_PROFIL=ornith-voice ./dienste.sh start   # Ornith wie bisher
```

**`dienste.sh` hält Profil und Modellnamen zusammen.** Ist `KIHIWI_MODEL`
nicht gesetzt, übernimmt `start_sprach` den Namen, den der Server tatsächlich
anbietet — ein Profilwechsel allein kann damit keinen 404 mehr erzeugen. Wer
`KIHIWI_MODEL` selbst setzt, behält die Kontrolle und wird gewarnt, wenn es
nicht passt.

*Die erste Fassung dieser Prüfung sah nur die Umgebungsvariable an und schwieg
deshalb im gefährlichsten Fall: nichts gesetzt, Vorgabe aus `konfig.py`, Profil
gewechselt. Aufgefallen beim Testlauf von `ornith-voice`.*

**Eine Ausnahme in der Antwortaufgabe verstummte spurlos.** `antworten()` fing
nur `TimeoutError`; alles andere verließ die Aufgabe und wurde von asyncio
verschluckt — Eingabe angenommen, keine Antwort, keine Zeile im Protokoll.
Jetzt wird jede Ausnahme protokolliert und der Nutzer bekommt einen Satz.
**Stumm ist die schlechteste Fehlermeldung, die ein Sprachassistent geben
kann.**

**`dienste.sh` warnt, wenn `KIHIWI_MODEL` nicht zum angebotenen Namen passt.**
Diese Fehlerklasse hat zweimal zugeschlagen — Hermes mit dem Namen aus seiner
eigenen Konfiguration, und ein Stapellauf ohne gesetztes `KIHIWI_MODEL`, der
100 Anfragen in einen 404 schickte und das als „100 gescheitert" meldete.

**Für kihiwi `model-switch ornith-voice` benutzen**, nicht `ornith`. Gleiches
Modell, gleicher `served-model-name`, gleicher Kontext — der **einzige**
Unterschied ist `GPU_UTIL` 0.55 statt 0.85. Das kostet nichts (41,9 GiB
KV-Cache, weit mehr als ein Sprecher braucht) und lässt rund 41 GiB für
whisper.cpp, Piper und sherpa-onnx frei; mit 0.85 waren 110 von 121 GiB belegt
und 6 GiB Swap in Benutzung.

**Am Kontext sind die beiden Profile nicht mehr zu unterscheiden.** Beide stehen
auf 131072, seit Hermes bei 65536 mit „Context length exceeded" abbrach. Wer
wissen will, welches Profil läuft, muss die Speicherreservierung ansehen:
`docker inspect vllm-model` oder einfach die Zeile „Speicher: … frei" aus
`./dienste.sh status`. `dienste.sh` verglich früher den Kontext gegen 32768 und
warnte deshalb bei jedem Start, gerade wenn alles richtig war.

Alle vLLM-Modelle teilen sich Port **8889** und den Container `vllm-model`;
`ds4-server` hört auf 8888 und hat **keinen** Autostart. Je Aufruf übersteuerbar:
`CTX=`, `GPU_UTIL=`, `KV_DTYPE=`, `ABBILD_UEBER=`, `VERZ_UEBER=`.

Vorhanden: Ornith-1.5-35B-A3B NVFP4 (22 GB, Standard), Qwen3.6-35B-A3B NVFP4,
Qwen3.8-27B NVFP4+MTP, Nemotron-3.5-Lightning NVFP4 (+DSpark),
DeepSeek-V4-Flash GGUF, **Qwen3-VL-30B-A3B als Bildmodell**, Qwen3.8-Flash-Next
Q3_K_XL (wartet auf llama.cpp PR #27742).

**Speicher-Falle: nicht `GPU_UTIL` ist der Hebel, sondern `CTX`.** Ornith kostet
**~12 KB KV je Token** (gemessen: 41,9 GiB für 3,62 Mio. Token bei 32k, 41,1 GiB
für 3,85 Mio. bei 64k). *Meine frühere Rechnung von 40 KB aus der Kopfgeometrie
war um Faktor drei zu hoch — die Zahl aus dem vLLM-Protokoll gilt.* Mit den Prüfstands-Vorgaben waren 110 von
121 GiB belegt und 6 GiB Swap in Benutzung; mit `ornith-voice` bleiben 41,9 GiB
KV-Cache, 110-fache Nebenläufigkeit und rund 40 GiB frei. Dieselbe Falle bei
ds4: `-c 262144` belegte 114/121 GiB und lieferte 503er — die Fehlermeldung
nannte Speicher statt Kontext.

**vLLM hängt sich reproduzierbar auf, und ein hängender Motor sieht aus wie ein
langsames Modell.** Vor jeder Latenzmessung: `~/gx10-blog/bench/bereit.sh`.

### Wächter

`./dienste.sh waechter [start|stop|log]` prüft alle 120 s, ob der Motor
antwortet, und startet ihn sonst über `KIHIWI_PROFIL` neu.

**Er fordert eine echte Antwort ab**, nicht nur `/v1/models` — das antwortete
beim Hänger weiter mit 200 und hätte nichts gemerkt. Zwei Ausfallarten sind
belegt:

| | Datum | Laufzeit | Bild |
|---|---|---|---|
| Hänger | 28.08. | — | `/v1/models` 200, `/chat/completions` nie |
| Absturz | 03.09. | ~2 Tage | `CUDA error: illegal memory access`, dann 500 |
| Absturz | 07.09. | ~3,5 Tage | dasselbe |

Beide Abstürze mit `qwen3.6-35b-a3b-nvfp4`. Ob es an Modell, Quantisierung
oder vLLM-Fassung liegt, ist offen — deshalb läuft seit dem 07.09.
`ornith-voice` als Gegenprobe.

**Der Wächter fragt vor jedem Neustart die Belegungsstelle**
(`github.com/DG1001/modellbelegung`, `127.0.0.1:8930`) und hält still, wenn
jemand *exklusiv* angemeldet ist. Am 08.09.2026 hat er zweimal mitten in einen
laufenden Gutachtenlauf hineingeschaltet, weil er von ihm nichts wusste — und
der Motor antwortete nicht, weil dort gerade ein anderes Modell geladen wurde.

Ist die Stelle nicht erreichbar, wird neu gestartet wie zuvor: eine
ausgefallene Buchführung darf den Assistenten nicht dauerhaft stumm lassen.
kihiwi meldet sich als `mit` an, nicht `exklusiv` — es nimmt, was da ist, und
blockiert damit niemanden.

Eine zweite Chance nach 15 s verhindert Fehlalarm unter Last; nach drei
erfolglosen Neustarts in einer Stunde hört der Wächter auf und schreibt es ins
Protokoll, statt die Maschine im Kreis neu zu starten. Geprüft mit einem
angehaltenen Container: Ausfall erkannt nach 150 s, wieder betriebsbereit nach
drei Minuten.

### Ein anderes Modell ausprobieren

Nichts im Code ist an Ornith gebunden — `KIHIWI_LLM` und `KIHIWI_MODEL` reichen.
Der Name muss der `served-model-name` des Profils sein; nur `ornith` und
`ornith-voice` teilen sich absichtlich einen:

```bash
./dienste.sh stop
CTX=131072 GPU_UTIL=0.55 model-switch qwen38     # 0.55 nicht vergessen!
KIHIWI_MODEL=qwen3.8-27b ./dienste.sh start
```

`GPU_UTIL=0.55` ist der Punkt, an dem man es falsch macht: alle anderen Profile
haben 0.85 als Vorgabe, und dann hungert der Sprachstapel. `CTX=131072` ist nur
bei `qwenvl30` nötig (dessen `DEF_CTX` ist 65536 — Hermes' Untergrenze, an der
er schon einmal scheiterte).

**Ein fremder Motor auf 8889: `KIHIWI_LLM_ZUSATZ`.** Was der eine Motor als
Startflagge kennt, verlangt der andere im Anfragerumpf. vLLM schaltet das
Nachdenken mit `--default-chat-template-kwargs` ab, `llama-server` hat dafür
keine Vorgabe — dort muss es der Klient mitschicken. Die Variable wird als JSON
in **jede** Anfrage gemischt:

```bash
KIHIWI_LLM_ZUSATZ='{"chat_template_kwargs":{"enable_thinking":false}}'
```

Bewusst roh durchgereicht statt auf bekannte Schalter beschränkt: welcher Motor
hängt, entscheidet der Betrieb. Verunglückt die Variable, redet der Dienst
trotzdem weiter — aber mit einer Zeile auf stderr, nicht still.

**Hermes bekommt das Modell jetzt mit `-m` übergeben.** Ohne den Schalter nimmt
er `model.default` aus `~/.hermes/config.yaml` — und dort steht Ornith fest.
Nach einem Wechsel lief der Sprachpfad einwandfrei, während die Recherche mit
`HTTP 404: The model ornith-1.5-35b-a3b does not exist` scheiterte. Eine
Abhängigkeit auf eine Datei außerhalb des Repos, die nichts sichtbar machte.

**Geschwindigkeit ist hier fast egal, anders als im Prüfstand.** Piper spricht
langsamer als jedes dieser Modelle schreibt; spürbar ist nur der erste Brocken
(`ERSTER_MAX = 60` Zeichen). Entscheidend sind Deutsch, Kürze und
Werkzeuggebrauch.

**Gemessen am 28.08.2026** (vier Fragen end-zu-end über den WebSocket, Piper als
Sprecher, warm):

| | Ornith-1.5-35B-A3B | Qwen3.8-27B (dicht + MTP) | Qwen3.6-35B-A3B NVFP4 | Nemotron-3.5-L. + DSpark |
|---|---|---|---|---|
| Generierung, warm | 78,4 tok/s | **20,0 tok/s** (ohne MTP ~10) | 78,3 tok/s | **91,9 tok/s** |
| erster Satz | 0,4–2,5 s | 1,1–2,5 s | 1,3–2,2 s | 0,9–2,3 s |
| Werkzeugaufrufe auf 4 Fragen | 1 | 4, einmal ungefragt `web_suchen` | 3 | 1 |
| Rechercheauftrag | 26–63 s | **Abbruch bei 420 s** | 23–46 s | 40 s |
| Nachdenken im Sprachtext | nein | nein | nein | nein |

**Qwen3.8-Flash-Next (125B/6B aktiv, Q3_K_XL, llama-server)** ist der erste
fremde Motor am Sprachdienst und antwortet fachlich am besten — als einziges
Modell fand es zur Beschleunigungsspannung tatsächlich etwas in den Unterlagen
(„der Elektronenstrahl dient selbst als Voltmeter, etwa hundert ppm") statt
„nichts gefunden", und es bildet die deutschen Komposita richtig.

**Ohne `enable_thinking: false` ist es unbrauchbar.** Es schrieb rund 600 Token
Überlegung vor dem ersten Satz: 9–17 s bis zum ersten Ton, und der Sprachdienst
brach mit „Das dauert mir zu lange" selbst ab. Mit dem Schalter 1,1–6,2 s. Die
Tokenrate ändert sich dabei **nicht** (28,1 gegen 29,0 tok/s) — es ist reine
Menge.

| Qwen3.8-Flash-Next | ohne Schalter | mit `enable_thinking: false` |
|---|---|---|
| erster Satz | 9,4–16,7 s, Abbrüche | **1,1–6,2 s** |
| Generierung | 28,1 tok/s | 29,0 tok/s |

Rechercheauftrag: 103 s — deutlich über Ornith, aber weit unter der 420-s-Grenze.
**Hermes bekommt `KIHIWI_LLM_ZUSATZ` nicht**, das sind kihiwis eigene Rümpfe; er
denkt also weiter mit. Kontext 65536 ist Hermes' Untergrenze und ging hier gut.

**Qwen3.8-27B ruft Werkzeuge bereitwillig, aber es hört danach auf zu denken.**
Auf „Unterschied zwischen Sekundär- und Rückstreuelektronen" suchte es in den
Unterlagen, fand nichts und antwortete „Die Unterlagen enthalten keine
Definition" — statt die Frage aus eigenem Wissen zu beantworten, wie Ornith es
tat. Auf eine Lehrbuchfrage („warum ein Vakuum?") ging es sogar ins Netz. Der
Eifer ist das Gegenteil von Ornith' bekannter Schwäche, aber in dieser Form
keine Verbesserung: für einen Laborassistenten ist „steht nicht in den
Unterlagen" auf eine allgemeine Fachfrage die falsche Antwort.

Kein Nachdenken lief in die Sprachausgabe — bei der Qwen-Familie greift
`--default-chat-template-kwargs '{"enable_thinking": false}'`. **Bei Nemotron
nachgeprüft, weil dessen Schalter anders heißt und Piper im Zweifel die
Gedankenkette mitspräche: `content` war sauber, `reasoning_content` leer, kein
`<think>`.** Diese Prüfung gehört vor jeden Sprachtest eines neuen Modells.

**Nemotron ist das schnellste Modell hier und das schwächste im Deutschen.**
91,9 tok/s mit DSpark (der Prüfstand mass 121,4 — dort lief es mit `GPU_UTIL`
0.85; die Entwurfsannahme lag hier bei 36,7 %). Fachbegriffe bildet es
adjektivisch statt als Komposita: „sekundäre Elektronen", „rückstreue
Elektronen" statt Sekundär- und Rückstreuelektronen. *Vorbehalt: die
Sprachsynthese hatte die Frage bereits so verstümmelt, das Modell kann echot
haben.* Werkzeuge ruft es sparsam wie Ornith (1 von 4). Die Recherche lief in
40 s mit Quellen und ausdrücklichen Unsicherheitsvermerken durch — inhaltlich
mit einem Schnitzer (von Ardenne als „Scanning-Tunneling-Elektronenmikroskop").

**Qwen3.6-35B-A3B sucht *und* antwortet — das tut sonst keines der drei.** Es
ruft `dokumente_suchen` wie Qwen3.8, hört aber nicht dort auf: findet es nichts,
antwortet es aus eigenem Wissen weiter. Auf die Frage nach Sekundär- gegen
Rückstreuelektronen gab es als einziges eine fachlich richtige Antwort (Ornith:
„Rückstreuelektronen werden an der Oberfläche abgelenkt" — falsch; Qwen3.8:
verweigert). Der erste Satz kommt etwas später als bei Ornith, weil öfter eine
Werkzeugrunde dazwischenliegt.

**Für die Recherche zählt die Tokenrate sehr wohl.** Der Sprachpfad wird von
Piper begrenzt, Hermes dagegen hängt dutzende volle Generierungen aneinander
(48 vLLM-Anfragen in einem Auftrag). Bei 20 tok/s lief der Wetterauftrag in die
420-s-Grenze und scheiterte; bei 78 tok/s braucht derselbe Auftrag 23 s. Der
Satz „Geschwindigkeit ist hier fast egal" gilt **nur** für den gesprochenen Zug.

## Dienste

Alles läuft über **`./dienste.sh`**:

    ./dienste.sh start          alles hochfahren, idempotent
    ./dienste.sh stop           Sprachdienst + whisper-server (vLLM bleibt)
    ./dienste.sh stop --vllm    zusätzlich das Modell entladen
    ./dienste.sh neustart       nur die beiden lokalen Dienste
    ./dienste.sh status         was läuft, auf welchem Port
    ./dienste.sh log sprach     Protokoll folgen (sprach | whisper | vllm)

Protokolle in `logs/`. **`start` schaltet vLLM nicht ungefragt um** — läuft dort
ein anderes Profil, wird nur gewarnt, denn auf dieser Maschine wird auch gemessen
und ein Wechsel kostet zwei Minuten. Dienste werden über den **Port** gefunden,
nicht über den Prozessnamen.

| Dienst | Adresse | Anmerkung |
|---|---|---|
| vLLM | `127.0.0.1:8889` | Container `vllm-model` |
| whisper-server | `127.0.0.1:8910` | large-v3-turbo, `-l de` |
| Sprachdienst | `127.0.0.1:8920` | Monitor unter `http://127.0.0.1:8920/` |
| ds4-server | `127.0.0.1:8888` | manuell zu starten |
| SearXNG | `127.0.0.1:8088` | Container, für Hermes-Suche |

## Netz und Zugriff

Serverraum, hinter einem Consumer-Router, Ethernet `enP7s7` (Gigabit — bei
Einbruch zuerst `ethtool enP7s7` prüfen, ein zweipaariges Kabel hatte den Link
schon einmal auf 100 Mbit gedrückt). Stabil erreichbar über ein Tailnet.

**Direkt am Router, ohne Firewall davor** — die Maschine hat eine weltweit
geroutete IPv6 ohne NAT, der Schutz hängt allein an der Router-Konfiguration.
Daher die Regel, niemals an `0.0.0.0` oder `[::]` zu binden.
Neue Dienste, besonders der Audio-Gateway, **ausdrücklich an die Tailnet- oder
LAN-Adresse binden, nie an `0.0.0.0`/`[::]`** (`konfig.BIND`).

SSH ist **key-only** (`/etc/ssh/sshd_config.d/99-hardening.conf`), dazu
passwortloses sudo. Passwort-Auth nie beiläufig reaktivieren.

## Einrichten (nach frischem Klon)

Modelle und venv liegen bewusst nicht im Repo:

    python3 -m venv .venv && .venv/bin/pip install piper-tts websockets numpy
    .venv/bin/python -m piper.download_voices de_DE-thorsten-medium --data-dir voices
    .venv/bin/python -m piper.download_voices en_US-lessac-medium  --data-dir voices
    curl -L -o vad/silero_vad.onnx \
      https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx
    .venv/bin/python testaudio/turns.py          # erzeugt das Testmaterial neu

**whisper.cpp** liegt außerhalb des Projekts in `~/code/whisper.cpp`:

    cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121 -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j 18                    # 2 min 10 s

Die `121` ist die Compute Capability des GB10. Ohne sie fällt der Server still
auf die CPU zurück. Erfolgskontrolle im Startbanner: `ARCHS = 1210` und
`BLACKWELL_NATIVE_FP4 = 1`. Modell `ggml-large-v3-turbo.bin` (1,6 GB) in
`models/`.

## Aufbau des Sprachdienstes

`sprachdienst/` — bewusst **ein** Dienst mit schmaler Schnittstelle, getrennt vom
Agenten. Der Agent (heute Ornith direkt, später vielleicht Hermes) hängt hinten
dran und ist austauschbar, ohne dass Turn-Taking und Audio neu gebaut werden.

| Datei | Rolle |
|---|---|
| `konfig.py` | alle Einstellungen an einer Stelle |
| `zustand.py` | Zustand + Beobachter; Aufnahme und Dialog sind **orthogonal** |
| `turn.py` | VAD, Endpointing, lexikalische Prüfung nebenher |
| `stt.py` / `tts.py` / `llm.py` | Dienstanbindungen, je einzeln ersetzbar |
| `doku.py` | Dokumentationspfad, rotiert alle 5 min, behält Roh-Audio |
| `gateway.py` | WebSocket-Dienst |
| `monitor.html` | Laborbildschirm |
| `klient_test.py` | spielt eine WAV in Echtzeit ein, misst Ende-zu-Ende |
| `vad/silero.py` | Silero-VAD v5 über onnxruntime, ohne torch |

**Protokoll** auf `/audio`: Client sendet Binärrahmen (PCM int16 mono 16 kHz) und
JSON-Befehle (`mikro`, `aufnahme`, `ansprechen`, `abbrechen`); der Dienst sendet
JSON (`zustand`, `text`, `ton`, `ton_ende`) und Binäraudio. `/monitor` liefert nur
den Zustand, `GET /` die Monitorseite.

### Browser-Client für den Test

`/klient` liefert einen Sprachclient, der Mikrofon und Lautsprecher des
aufrufenden Rechners benutzt — damit lässt sich ein Jabra an einem beliebigen
Notebook testen, ohne dort etwas zu installieren.

**`getUserMedia` braucht einen „secure context".** Über `http://<ip>:8920`
verweigert der Browser das Mikrofon kommentarlos. Lösung ist die
SSH-Weiterleitung, dann gilt die Seite als `localhost`:

    ssh -L 8920:127.0.0.1:8920 <rechner>
    # dann im Browser: http://localhost:8920/klient

Der Dienst bleibt dabei an `127.0.0.1` gebunden — kein Aufweichen der Bindung,
kein Zertifikat nötig.

Der Client nimmt mit `AudioContext({sampleRate: 16000})` auf, der Browser
resampelt selbst; ein AudioWorklet schneidet 512er-Blöcke als int16 und schickt
sie unverändert im Format des Dienstes. Browserseitige Echounterdrückung bleibt
an, auch wenn das Jabra eigene mitbringt.

Testen ohne Hardware:

    .venv/bin/python -m sprachdienst.klient_test testaudio/s3.wav [--aufnahme]

### Die Antworten gehören in die Aufzeichnung

Die Fragen an Kiwi landen ohnehin im Mitschnitt — der Rekorder läuft unabhängig
von der Gesprächsphase. Kiwis **Antworten** aber nicht: die gehen als TTS direkt
zum Lautsprecher, und die Echounterdrückung des Freisprechers hält sie aus dem
Mikrofonsignal heraus. Im Protokoll stünde nur die halbe Unterhaltung.

`Rekorder.mische()` legt die Sprachausgabe deshalb ins Mikrofonsignal —
**gemischt, nicht angehängt**: Anhängen ließe die Datei schneller wachsen als
die Zeit vergeht und verschöbe alle Zeitstempel. Pegel 0,6, damit nichts
übersteuert.

Das setzt einen **durchgehenden Audiostrom** vom Client voraus, weil die
Beimischung nur beim Schreiben eines Mikrofonblocks abgebaut wird. Ein echter
Client streamt durchgehend; stockt er doch, verwirft `MAX_STAU_S` (4 s) den
Überhang, statt ihn später über eine fremde Stelle zu legen.

## Wissensanbindung

`wissen/` — Volltextindex über die Unterlagen plus Websuche, als Werkzeuge am
Assistenten.

    ./dienste.sh wissen einlesen        alle Quellen einlesen
    ./dienste.sh wissen status          was im Index liegt
    ./dienste.sh wissen suchen ...      Volltextsuche
    ./dienste.sh wissen web ...         SearXNG

**`#` ist NUR in Markdown ein Überschriftszeichen.** Überall sonst leitet es
einen Kommentar ein — in Python, in CSV, in `requirements.txt`, in YAML, in
INI-Dateien. Die Zerlegung machte daraus Überschriften und zerschnitt
zusammenhängende Absätze in Einzeilen: „Untergrenzen statt fester Versionen:
Auf dem GX10 …" wurde zu vier Abschnitten, jeder eine Zeile lang.

Deshalb sagt `MARKDOWN_ENDUNGEN = {".md", ".markdown"}` jetzt, **wo `#`
wirklich eine Überschrift ist** — alles andere fällt automatisch auf die
sichere Seite, auch Dateitypen, die noch niemand eingelesen hat. Die
Seitenmarken `[Seite N]` bleiben überall wirksam; die setzt `_pdf_text` selbst
und sie können nicht aus dem Inhalt stammen.

*Diesen Fehler habe ich dreimal behoben: für Python, für CSV, dann hier.
Zweimal war es der Einzelfall statt der Klasse — eine Liste, wo `#` ein
Kommentar ist, wird nie vollständig.*

**Messdaten (`.csv`, `.tsv`) bekommen Kopf und Werte getrennt.** Auch dort
leitet `#` einen Kommentar ein — die Markdown-Zerlegung machte aus jeder
Kopfzeile einen eigenen Abschnitt (Überschrift „groesse: Radiales Strahlprofil
an der Probenebene", Text identisch dazu), und die kurzen fielen durch den
40-Zeichen-Filter ganz heraus. **Derselbe Fehler wie zuvor bei Python**,
aufgefallen erst, als ein Abgleich 143 CSV-Dateien brachte und sie mit 2209 von
5248 Abschnitten den Index dominierten.

Der Kopf ist bei diesen Dateien das Wertvolle — er sagt, *was* gemessen wurde,
in welcher Einheit, mit welcher Konfiguration. Er bleibt an einem Stück, mitsamt
den Spaltennamen; die Zahlen stehen als eigener Abschnitt daneben. Sie bleiben
im Index: in diesem Labor steht die Auslegung in den Daten, und eine Suche nach
einem Wert soll sie finden — nur soll sie die Beschreibung nicht verdrängen.

**Quelltext wird an Symbolen geschnitten, nicht an `#`.** In Python leitet
`#` einen Kommentar ein — die Markdown-Zerlegung machte daraus Überschriften.
Im Index standen Einträge wie `----------------------------------------`, und
geschnitten wurde an Kommentarzeilen statt an Funktionen. Betroffen waren 879
von 2495 Abschnitten, gut ein Drittel. Jetzt trägt die Überschrift den
Symbolnamen (`Mikroskop_Objekt_4.py — probe_cameras`), und der Block vor dem
ersten Symbol bekommt einen eigenen Abschnitt: **dort stehen die Messwerte**,
`FENSTER_NM = 50.0` etwa.

Dazu zwei Netze: eine **zeilenweise Obergrenze** für alles, was auch nach der
Absatztrennung zu groß bleibt (Messwerttabellen haben keine Absätze), und ein
**Datenblock-Filter** — der größte Abschnitt im Index war
`BILD_B64 = "iVBORw0KGgo…"`, ein eingebettetes PNG mit 178.484 Zeichen in einer
Zeile. Ergebnis: größter Abschnitt 1.596 statt 178.602 Zeichen, **keiner mehr
über der Grenze** (vorher 166).

**`ZERLEGER_FASSUNG` gehört in den Fingerabdruck.** Der erste Versuch blieb
wirkungslos: `einlesen` meldete „0 Dokumente geändert", weil der Fingerabdruck
nur den Inhalt abdeckt. Eine Änderung am Schnitt erzwingt jetzt das Neulesen.

**Erschlossene Schlagwörter (`wissen erschliessen`).** Ein LLM-Durchlauf je
Abschnitt erzeugt 8–14 Suchbegriffe, darunter zu jeder Abkürzung die
ausgeschriebene Form und umgekehrt. Sie liegen in einer eigenen FTS-Spalte
(Gewicht 1,5) und in der Tabelle `erschliessung` nach Inhalts-Hash — dadurch
überleben sie jedes `wissen einlesen`. Gemessen: 1312 neue Abschnitte in 392 s,
1462 kamen ohne Modellaufruf zurück. **Vier Gewichte wurden verglichen; 1,5
bleibt** — mehr bringt fünf Brückentreffer und kostet vier Auszüge, die den
Suchbegriff tragen.

**Katalog (`wissen katalog`, `wissen ueberblick`).** Drei Sätze je Dokument,
179 Stück in 95 s, zusammen 71.713 Zeichen ≈ **19k Token** — der ganze Bestand
passt in ein Siebtel des Kontexts. Abrufbar über das Werkzeug
`unterlagen_ueberblick`, **nicht** im Systemprompt: 19k Token in jedem
Sprachzug wären Sekunden je Antwort. Die Kurzfassungen sind ausdrücklich als
erschlossen gekennzeichnet und nie zitierfähig.

**Umlaute in beiden Schreibweisen.** Der Index faltet sie weg
(`tokenize = unicode61 remove_diacritics 2`): „Sekundärelektronen" liegt dort
als `sekundarelektronen`. Eine Anfrage in ae/oe/ue-Schreibweise traf das nie —
gemessen **„Sekundärelektronen" 8 Treffer, „Sekundaerelektronen" null.**
`begriffe()` hängt jetzt die gefalteten Varianten als zusätzliche ODER-Begriffe
an.

**Alle Kombinationen, nicht alles ersetzen.** In „rueckstreuelektronen" ist das
erste `ue` ein Umlaut, das zweite (streu+elektronen) keiner; sequentielles
Ersetzen machte daraus `ruckstreulektronen` und fand nichts. Bei bis zu drei
Vorkommen werden alle 2ⁿ Schreibweisen erzeugt. Eine unsinnige Variante kostet
nichts — bei ODER trifft sie einfach nicht.

**Es wirkt in beide Richtungen.** Das Korpus selbst mischt die Schreibweisen:
`"saeule"` kommt 81-mal vor (Dateinamen, umlautfrei geschriebene Dokumente),
`"saule"` 189-mal. Vorher fand eine Anfrage je nach Tippweise nur eine der
beiden Mengen, jetzt beide — 260 statt 189.

**Der Auszug liegt UM die Fundstelle, nicht am Anfang des Abschnitts.**
Vorher gingen die ersten 600 Zeichen ans Modell. Über acht Fachfragen gemessen
enthielten **9 % der gelieferten Auszüge keinen einzigen Suchbegriff** — der
Treffer war richtig, die gezeigte Stelle nutzlos, und das Modell antwortete
„steht nicht in den Unterlagen" auf etwas, das dasteht. Nach der Umstellung 6 %,
und **alle drei verbleibenden Fälle haben den Begriff in der Überschrift**, die
ohnehin in der Kopfzeile mitgeht — effektiv null.

Wirkung an einer echten Frage: „Was steht über die Beschleunigungsspannung?"
lieferte vorher „steht nichts", jetzt „dreißig Kilovolt".

**Zwei Dinge, die dabei sichtbar wurden und NICHT behoben sind:**

- **146 von 2420 Abschnitten sind größer als `ABSCHNITT_MAX` (1600),** der
  größte hat 178.602 Zeichen. `einlesen.py` schneidet an Absatzgrenzen; eine
  Messwerttabelle oder eine Codedatei hat keine. BM25 normalisiert über die
  Länge und straft solche Blöcke ab, sie gewinnen also selten — im Test 3 von
  53 Treffern. Seit der Auszug um die Fundstelle liegt, richten sie auch keinen
  Schaden mehr an.
**PDF-Text kommt von `pypdfium2`, nicht von `pypdf`.** Bei den Manuskripten
hier verlor pypdf die Leerzeichen — „SpaltfelderfallensteilerabdieFEMM-Rechnung".
Solcher Text ist für eine Volltextsuche unerreichbar, weil kein Suchbegriff
darin als Wort vorkommt. Weder `extraction_mode="layout"` noch ein kleineres
`space_width` half; die Leerzeichen fehlen im Inhaltsstrom, pypdf kann sie nicht
erfinden.

| über 28 PDFs | verklebte Wörter | Zeit |
|---|---|---|
| pypdf | 1380 | 1,8 s |
| **pypdfium2** | **111** | **0,3 s** |
| pdfminer.six | 111 | 0,5 s |

Im Index: **5,0 % betroffene Abschnitte vorher, 0,7 % nachher.** pdfminer war
gleich gut, pypdfium2 sechsmal schneller als pypdf. *PyMuPDF wäre die dritte
Möglichkeit gewesen und steht unter AGPL-3.0 — für ein MIT-Repo keine gute
Wahl; pypdfium2 ist BSD-3-Clause/Apache-2.0.*

`pypdf` bleibt als **laute** Rückfallebene: fehlt pypdfium2, läuft das Einlesen
weiter und sagt es. Still schlechter Text wäre schlimmer als ein Abbruch.

- **146 von 2420 Abschnitten sind größer als `ABSCHNITT_MAX`** (siehe oben) —
  unverändert, aber seit dem Auszug um die Fundstelle folgenlos.

**`wissen einlesen` zieht alles nach.** Nach dem Lesen folgen Schlagwörter,
Kurzfassungen und Vektoren — alle drei nur für Fehlendes, also nach einem
gewöhnlichen Abgleich Sekunden. `--nur-lesen` überspringt sie.

**Im Sprachpfad im Hintergrund und mit Obergrenze** (`NACHZIEH_GRENZE = 300`).
Der Wissensabgleich lässt sich per Zuruf auslösen, und Nachziehen und Antworten
teilen sich die GPU.

**„Wissen nachziehen" holt es per Stimme nach** (auch „Nachziehen",
„Unterlagen erschließen"). Ohne Obergrenze — wer es ausdrücklich verlangt,
weiß, dass es dauert; die Grenze schützt den beiläufigen Fall. Ein Schloss
(`NACHZIEHEN`) verhindert zwei parallele Läufe, und am Ende kommt **immer**
eine Rückmeldung, auch wenn nichts zu tun war.

*Vorher verwies die Meldung auf „von Hand den Wissensbefehl einlesen". Am
Laborrechner gibt es kein Terminal, am Futro erst recht nicht — ein Assistent,
der auf etwas verweist, das man per Stimme nicht ausführen kann, hilft nicht.*

**Wird übersprungen, SAGT der Assistent es** — gesprochen, nicht nur im
Protokoll. Vorher hörte man „Abgleich fertig, N neue Dokumente", während die
Hälfte des Index nur über die reine Volltextsuche erreichbar war (gemessen 23
statt 40 % bei umschriebenen Fragen). Niemand sieht ins Protokoll, und ein halb
erschlossener Index sieht später aus wie ein schlechtes Modell.

Der **erfolgreiche** Fall wird nur angezeigt, nicht gesprochen („Nachgezogen —
schlagwoerter: 7, kurzfassungen: 3, vektoren: 7"). Nach jedem Abgleich ein
zweites Mal zu reden wäre Lärm; gesprochen wird, was Handeln verlangt.

**Erledigt heißt versucht, nicht ergiebig.** Offen ist ein Abschnitt, dessen
Hash nicht in `erschliessung` steht — nicht einer mit leerer Spalte. Ein
67 Zeichen langes Formelfragment aus einem PDF liefert keine Schlagwörter und
lief vorher bei *jedem* Aufruf wieder mit.

### Volltext und Vektoren gemischt

**Gemessen, nicht geglaubt.** 60 Fragenpaare, vom Modell aus zufällig gezogenen
Abschnitten erzeugt: je eine Frage mit den Fachwörtern des Abschnitts und eine,
die sie ausdrücklich meidet. Gezählt wurde, ob der Quellabschnitt in den ersten
acht Treffern steht.

| | mit den Fachwörtern | umschrieben |
|---|---|---|
| FTS5 ohne Schlagwörter | 85,0 % · MRR 0,657 | 21,7 % · MRR 0,137 |
| FTS5 mit Schlagwörtern | 83,3 % · MRR 0,693 | 23,3 % · MRR 0,134 |
| nur Vektoren (e5-large) | 75,0 % · MRR 0,495 | **41,7 %** · MRR 0,220 |
| **Mischung (RRF)** | **90,0 %** · MRR 0,679 | 38,3 % · MRR 0,185 |

**Die Volltextsuche gewinnt, wo das Wort wörtlich dasteht** — eine Zahl, ein
Bezeichner, `FENSTER_NM`. **Der Vektorindex fast verdoppelt die Trefferquote,
wo jemand die Sache umschreibt.** Die Mischung ist bei wörtlichen Fragen besser
als beide Einzelverfahren.

Der eingebaute Stand, gegen dieselbe Grundwahrheit geprüft: **86,7 %** bzw.
**40,0 %**, Suche 80–116 ms, erster Aufruf nach dem Start 1,4 s (Modell laden).

**onnxruntime-Threads sind auf vier begrenzt.** Ohne Begrenzung nimmt es
einen Thread je Kern (hier 20) und lässt sie **busy-waiten**, auch wenn nichts
zu rechnen ist. Nach einem Einbettungslauf im Sprachdienst: 103 Threads,
**83 % CPU dauerhaft**, 5,6 GB RSS — der Eventloop kam so selten durch, dass
eine HTTP-Anfrage 12,9 s brauchte und die Anzeigetafel stehenblieb. Der Dienst
war nicht abgestürzt, er wurde ausgehungert.

Vier Threads: die Suche bettet *eine* Frage ein (53 ms), da bringt mehr
Nebenläufigkeit nichts. Der Stapellauf wird langsamer — der richtige Tausch,
denn er läuft im Hintergrund, während der Assistent antworten können muss.
Danach 0 % im Leerlauf, `/api/messwerte` in 0,3 ms.

*Der Dienst belegt dauerhaft 1,8 GB, sobald einmal gesucht wurde. Das Modell
nach jeder Suche zu entladen kostete 1,4 s je Frage — bei 37 GiB frei ist das
der schlechtere Tausch.*

**RRF statt Punktemischung.** BM25-Punkte und Kosinusähnlichkeit haben keine
gemeinsame Skala; sie zu addieren verlangt einen Faktor, den man auf 60 Fragen
überanpasst. Reciprocal Rank Fusion benutzt nur die Rangplätze.

**Der Vektorteil darf die Suche nie mitreißen.** Fehlt `fastembed`, fehlt das
Modell oder liefert es NaN, bleibt es beim reinen Volltext — schlechter, aber
nie kaputt. `wissen status` zeigt die Abdeckung.

**Warnung aus dem Verlauf:** das erste Modell (`jina-embeddings-v2-base-de`)
lieferte unter onnxruntime auf aarch64 **stumm NaN**, auch bei trivialem Text.
Die Auswertung meldete daraufhin 0/60 für Vektoren — eine Zahl, die gut in die
vorherige These gepasst hätte. Aufgefallen ist es nur, weil 0/60 bei wörtlichen
Fragen unmöglich ist. Seitdem prüft `vektor.einbetten()` bei jedem Aufruf auf
NaN.

**SQLite FTS5, kein Vektorindex.** Eingebaut in Python, kein Embedding-Modell,
kein GPU-Speicher (den Ornith belegt), kein zusätzlicher Dienst. Für Fachtexte
ist Stichwortsuche stark — gefragt wird nach „Siliziumnitrid", „JEOL",
Typbezeichnungen. Semantische Suche wäre der nächste Schritt, nicht der erste.

Quellen in `wissen/quellen.json`: `lokal` (Ordner), `git` (klont/pullt nach
`wissen/repos/`), `nextcloud` (WebDAV, **nur lesend**: PROPFIND und GET, kein
PUT). Zugangsdaten für Nextcloud über `$KIHIWI_NC_PASS`, nicht in der Datei.

**Die Websuche ist die einzige Stelle, an der etwas das Netz verlässt.** Über
`konfig.WEB_SUCHE` abschaltbar. Zu bedenken: die Suchanfrage enthält, wonach im
Labor gefragt wurde. Audio und Unterlagen verlassen die Maschine nicht, die
Frage schon.

**Der Index räumt auf.** `index.aufraeumen()` entfernt nach jedem Einlesen die
Dokumente einer Quelle, die nicht mehr gesehen wurden. Ohne das bleibt alles
drin, was einmal drin war — gelöschte Dateien wie nachträglich ausgeschlossene.
Gemessen: die Rechercheergebnisse blieben auffindbar, nachdem sie aus der Quelle
genommen worden waren, und **gewannen bei Zahlenfragen gegen die Primärquellen**
— mit Sätzen wie „nicht belegbar ohne weitere Prüfung". Abgeleitetes Material
gehört nicht neben die Quellen, aus denen es abgeleitet wurde.

**Suchen darf niemals schreiben** (`index.lesen()` statt `verbinden()`):
`verbinden()` legt das Schema an und nimmt dabei eine Schreibsperre.

### Rechercheaufträge (Stufe 2)

**Dem Auftrag gehen Auszüge aus den eigenen Unterlagen voran.** Hermes kennt
sie sonst nicht: er läuft in `zustand/hermes/`, damit er nicht ins Repo
schreibt, und sieht von dort aus `wissen/repos/` nicht. Auf „stelle die
geometrischen Angaben für die Säule zusammen" durchsuchte er daraufhin seine
**eigene Sitzungshistorie** und meldete, er finde nichts — während das Material
im Index lag.

Lesen *könnte* er (er hat Datei- und Terminalwerkzeuge), aber er weiß nicht wo,
und `grep` über 5.635 Abschnitte wäre schlechter als die vorhandene Suche aus
Volltext und Vektoren. Also sucht der Dienst und gibt acht Auszüge mit —
dieselbe Zahl wie bei `dokumente_suchen`, rund 5 kB.

Wirkung an genau diesem Auftrag: vorher „keine vorherige Unterhaltung über eine
geometrische Säule gefunden", nachher eine Maßtabelle mit Außendurchmesser,
Gesamthöhe, Wandstärke, Kernbohrung und Spaltabständen, jede Zeile mit
Quellenangabe. 270 s.

`roh=True` (Hermes direkt ansprechen) reicht weiter unverändert durch — ohne
Auszüge und ohne den Auftragszusatz.

**Ein leeres Thema startet keinen Auftrag.** Sagt jemand nur „Hermes Aufgabe:"
ohne Auftrag, fragt der Assistent zurück. Vorher gab `_thema()` bei einer
Äußerung, die nur aus dem Auslösewort besteht, den **ganzen Text** als Thema
zurück (`return rest or text`) — der Agent bekam „Hermes Aufgabe" als
Forschungsfrage, lief drei Minuten durch Dateien, blockierte die nächste
Anfrage und war nicht abzubrechen. `_thema()` liefert jetzt `""`; wo der ganze
Satz ein brauchbarer Suchbegriff ist (Dokumenten- und Websuche), setzt der
Aufrufer ihn selbst ein.

**„Recherche abbrechen" beendet einen laufenden Auftrag** — auch „Brich die
Recherche ab", „Auftrag stoppen", „Hermesaufgabe abbrechen". Das Auslösewort
wird **vor** `recherche` geprüft, sonst startete „Recherche abbrechen" eine
neue. `Recherche.abbrechen()` bricht die Aufgabe ab **und killt den
Kindprozess**: asyncio räumt keine Fremdprozesse auf, hermes liefe sonst
weiter.

Nach einem Abbruch wird **nicht** zusätzlich „hat nicht geklappt" gemeldet —
wer abbricht, weiß es, und der Abbruch ist schon quittiert.

*Warum es das braucht:* auf „kannst du die Recherche abbrechen?" antwortete das
Modell vorher aus der Vorstellung — „da ich keine laufenden Prozesse habe",
während einer lief. **Ohne Werkzeug erfindet es eines.**



`wissen/recherche.py` — aufwendige Fragen gehen an den **Hermes-Agenten**, der
Internet und Unterlagen durchsucht. Werkzeug `rechercheauftrag(frage)`.

**Asynchron, weil eine Sprachschnittstelle nicht warten kann.** Gemessen 31–40 s
je Auftrag. Der Assistent sagt zu und meldet sich; das Ergebnis kommt später von
selbst — an **alle gerade verbundenen** Clients, nicht an die Sitzung, die den
Auftrag gab (die kann längst weg sein, während jemand anders im Labor steht).
Abgelegt wird es immer unter `recherchen/`.

**Höchstens einer gleichzeitig.** Hermes und der Sprachpfad teilen sich ein
Modell auf einer GPU; ein zweiter Auftrag wird abgelehnt und der Assistent sagt,
dass er beschäftigt ist. Der laufende Auftrag steht im System-Prompt und auf dem
Monitor.

**Hermes braucht mindestens 64K Kontext** — bei 65536 brach er mit „Context
length exceeded" ab, deshalb steht `ornith-voice` auf 131072. Aufruf mit
`hermes chat -Q` (programmatischer Modus, nur die Antwort).

**Der Agent bekommt ein eigenes Arbeitsverzeichnis, `zustand/hermes/`.** Sonst
erbt er das des Sprachdienstes — also das Repo — und legt dort ab, was er
unterwegs baut; ein Wetterauftrag hinterliess eine 20-KB-HTML-Seite neben der
Quelle, und das nächste `git add -A` hätte sie eingesammelt. **`--in` allein
genügt nicht:** Hermes stellt ein gemerktes Arbeitsverzeichnis wieder her
(„Shell cwd was reset to …") und schrieb trotz des Schalters weiter ins Repo.
Wirksam ist erst das `cwd=` des Kindprozesses — das kann er nicht überschreiben.
Gesetzt sind jetzt beide. `zustand/` ist ohnehin gitignoriert; man kann trotzdem
nachsehen, was der Agent gebaut hat.

**`computer_use` ist für Rechercheaufträge abgeschaltet.** Browser-Automation
verlangt eine Genehmigung, und im nicht-interaktiven Modus kommt nie eine: der
Agent lief in einen Timeout, war danach verwirrt und fragte zurück, ob GitHub
Issues oder StackExchange gemeint seien — obwohl acht passende Auszüge aus den
eigenen Unterlagen im Auftrag standen. **Websuche und Dateizugriff bleiben**,
nur das Klicken auf Webseiten fällt weg.

`-t` ist eine Positivliste, also stehen alle anderen dreizehn Toolsets in
`WERKZEUGE` — **fest im Code, nicht aus `~/.hermes/config.yaml` gelesen**. Eine
Konfiguration außerhalb des Repos, die still den Umfang ändert, ist genau die
Fehlerklasse, die hier schon zweimal Zeit gekostet hat.

Derselbe Auftrag vorher und nachher: 333 s mit einer Rückfrage statt eines
Ergebnisses, danach **196 s mit einer vollständigen Aufstellung**.

**Laufmeldungen des Agenten fallen aus dem Ergebnis.** Hermes schreibt
Werkzeugbuchführung in seine Ausgabe — „⏱ Timeout — denying command",
„🔧 Auto-repaired tool name", „⚠ Approval: computer_use … timed out". Das stand
im Ergebnis und wurde vorgelesen. `_ohne_statuszeilen()` entfernt sie; wer eine
Recherche hört, will das Ergebnis, nicht das Protokoll.

**Gesprochen wird nur der Anfang — und Tabellen gar nicht.** Eine
Markdown-Tabelle hat keine Satzzeichen; die Kurzfassung nimmt „die ersten zwei
Sätze", und ohne Punkt ist die ganze Tabelle ein Satz. Gesprochen wurde einmal
eine komplette Aufstellung offener Fragen samt Zuständigkeiten und Spaltenköpfen
— 1411 Zeichen, minutenlang, unbrauchbar. `_ohne_struktur()` entfernt Tabellen,
Codeblöcke und Trennlinien **vor** dem Kürzen; dieselbe Antwort ergibt jetzt 318
Zeichen.

Zwei weitere Griffe in `_sprechbar()`:

- **Überschriften bekommen einen Punkt.** Ohne ihn verschmelzen sie mit dem
  Folgetext zu einem Satz — und „die ersten zwei Sätze" waren eine halbe Seite.
- **Dateipfade werden auf den Namen gekürzt.**
  `ap1-…/docs/05_offene_entscheidungen.md` ist gesprochen unverständlich; der
  volle Pfad steht auf der Bühne.

Dazu `KURZ_MAX = 400` als zweites Netz: auch Fließtext kann zwei sehr lange
Sätze haben.

Was zurückkommt, ist **abgeleitet**: die Datei trägt einen Warnhinweis und die
Quellenpflicht steckt im Auftragstext. Nie ungeprüft ins Protokoll.

Gesprochen werden nur die ersten zwei Sätze — acht Sätze vorzulesen dauert vierzig
Sekunden. `_sprechbar()` räumt vorher Markdown, DOIs und URLs weg, sonst liest
Piper Sternchen mit vor.

**Der Dienst sagt den gewählten Weg an**, bevor das Werkzeug läuft: „Ich schaue
in den Unterlagen nach" / „Ich schaue kurz im Netz nach" / „Das gebe ich als
Rechercheauftrag ab, das dauert ein paar Minuten." Fest im Code (`ANSAGE` in
`gateway.py`), nicht per Prompt — das Modell hält sich nicht zuverlässig daran,
und der Nutzer muss wissen, ob er auf Sekunden oder Minuten wartet. Nebeneffekt:
der erste Ton kommt nach 2 s statt nach 4.

Läuft eine Recherche, sagt der Dienst zusätzlich an, dass Antworten gerade
länger dauern — Hermes und der Sprachpfad teilen sich ein Modell.

**Abgrenzung der drei Wissenswerkzeuge:** `dokumente_suchen` für alles aus den
Unterlagen, `web_suchen` für EINE schnell nachzuschlagende Tatsache,
`rechercheauftrag` für Vergleiche und mehrschrittige Recherche. Ohne diese
Schärfung griff das Modell zur Websuche und antwortete synchron nach 9,5 s,
statt den Auftrag abzugeben.

### Absichtserkennung vor dem Modell

`sprachdienst/absicht.py` entscheidet vor dem LLM-Aufruf, worum es geht
(`AUFZEICHNUNG` | `RECHERCHE` | `WISSEN` | `PLAUDEREI`), und gibt dem Modell nur
die passenden Werkzeuge mit einem kurzen, zugeschnittenen Prompt.

Regeln statt Modell: kein Embedding, kein zusätzlicher LLM-Aufruf. Die Absichten
sind wenige und sprachlich deutlich getrennt, und der Router muss in
Mikrosekunden entscheiden, sonst frisst er die Latenz, die er sparen soll.
Erkennt keine Regel etwas, gilt `WISSEN` — Nachschlagen ist der häufigste Fall
und richtet am wenigsten Schaden an.

**Falle bei den Regeln:** Umlaute müssen AUSGESCHRIEBEN normalisiert werden
(ü→ue). `unicodedata.NFKD` macht aus „ü" ein „u", damit passte „ausführlich"
nicht auf das Muster „ausfuehrlich".

### Der Sprechstil-Prompt unterdrückt Werkzeugaufrufe

**Der wichtigste Befund zur Zuverlässigkeit.** Gemessen, dieselbe Frage, je drei
Läufe:

| Variante | Werkzeug gerufen |
|---|---|
| 4 Werkzeuge + voller Prompt | 0/3 |
| 1 Werkzeug + Sprechstil + Zusatz | 0/3 |
| 1 Werkzeug + nur Zusatz, **ohne Sprechstil** | **3/3** |
| 1 Werkzeug + neutraler Prompt | **3/3** |

Nicht die Werkzeugzahl ist das Problem, sondern `konfig.SYSTEM_PROMPT` selbst:
„antworte kurz … höchstens zwei Sätze" bringt das Modell dazu, zu **antworten**
statt zu **handeln**. Es befolgt die Anweisung korrekt — sie ist nur an dieser
Stelle falsch.

**Folge:** `antwort_mit_werkzeugen` nimmt zwei Prompts. `system` gilt für die
Werkzeugrunden (sachlich, ohne Stilvorgabe), `system_antwort` für die
Schlussantwort (Sprechstil). Die beiden Runden waren architektonisch längst
getrennt — nur der Prompt war für beide derselbe.

### Eigene Protokolle sind Wissensquelle

`aufnahmen/` steht als Quelle `protokolle` in `quellen.json` — damit findet
Kiwi frühere Laborgespräche über dieselbe `dokumente_suchen`. „Was habe ich
vorhin über das Rasterelektronenmikroskop gesagt?" wird daraus beantwortet.

**Das geschieht automatisch beim Stoppen der Aufzeichnung** (`nachbereiten()` in
`gateway.py`): transkribieren, Protokoll bauen, neu indizieren, und der Assistent
sagt Bescheid. Im Hintergrund, damit er ansprechbar bleibt; höchstens eine
Nachbereitung gleichzeitig, weil sie sich STT und Modell mit dem Sprachpfad teilt.
Gemessen: 3,4 s für eine 5-s-Aufnahme.

Alle Wege zum Stoppen laufen über `Sitzung.aufzeichnung_stoppen()` — Werkzeug,
Knopf, Mikrofon aus, Verbindungsabbruch. Eine Stelle, damit kein Weg am
Transkribieren vorbeiführt.

Von Hand geht es weiterhin mit `./dienste.sh protokoll`.

**Zeitstempel doppelt:** absolute Ortszeit (`[27.08. 20:29:06]`) und Versatz in
der Aufnahme (`(bei 00:01)`). Die Uhrzeit sagt WANN, der Versatz WO im Audio —
ein Versatz allein beantwortet „was war heute früh" nicht.

### Zwei Prompts, zwei Nachrichtenfolgen

Die Schlussantwort wird **immer neu erzeugt**, mit dem Sprechstil-Prompt und
auf einer **sauberen** Nachrichtenfolge: Frage, Werkzeugbefunde als Text,
fertig. Die Werkzeug-Strukturen (`tool_calls`, `role=tool`) werden nicht
mitgeschleppt — ein Aufruf ohne `tools`, aber mit solchen Einträgen in der
Vorgeschichte, lieferte eine **leere Antwort ohne Fehler und ohne
Protokollzeile**. Den Text aus der Werkzeugrunde zu übernehmen ginge zwar
schneller, brachte aber Aufzählungen und Fettschrift in die Sprachausgabe.

### Offene Mängel (Stand 27.08.2026)

1. **vLLMs Streaming-Parser für Werkzeugaufrufe verträgt die größere
   Werkzeugliste nicht.** Mit einem Werkzeug lief er, mit dreien blieb die
   Antwort komplett aus — ohne Fehler, und die Anfrage tauchte nicht einmal im
   vLLM-Protokoll auf. Durch Halbierung belegt. **Umgehung:** die Werkzeugrunde
   läuft ungestreamt (`llm._einmal`), nur die Schlussantwort wird gestreamt.
   Das kostet in der ersten Runde einen zweiten Durchlauf.
2. **Das Modell ruft `dokumente_suchen` im Sprachpfad oft nicht auf**, sondern
   kündigt nur an: „Ich muss das in den Unterlagen nachsehen." Im direkten Test
   (ohne Sprech-Prompt) ruft es zuverlässig auf — der kurze Sprechstil
   („höchstens zwei Sätze") verdrängt den Werkzeugaufruf. Ein eingebautes
   Nachfassen (`_ANGEKUENDIGT` in `gateway.py`) **greift nicht**, und warum, ist
   ungeklärt: der Code steht an der richtigen Stelle, die Bedingung trifft im
   Test zu, aber die Protokollzeile erscheint nie. **Ungelöst.**

Belegt funktioniert die Anbindung im direkten Test: auf „Wie dick ist unser
Siliziumnitrid-Fenster?" antwortet das Modell mit Suche „Ich habe keine
belastbare Angabe gefunden" statt wie vorher eine Zahl zu erfinden.

## Dokumentationspfad

`sprachdienst/protokoll.py`, Aufruf über `./dienste.sh protokoll [sitzung …] [--neu]`.
Ohne Sitzungsnamen werden alle unter `aufnahmen/` verarbeitet; ohne `--neu` nur
das, was noch kein Transkript hat.

Drei Stufen, in dieser Reihenfolge:

1. **VAD-Zerlegung.** Nur Sprachbereiche gehen ins STT — Pflicht, nicht
   Optimierung, weil large-v3-turbo in Stille halluziniert. Nebeneffekt: jeder
   Abschnitt bekommt seinen Zeitstempel geschenkt. Bereiche mit weniger als
   600 ms Abstand werden verbunden, kürzer als 400 ms verworfen, länger als
   60 s geteilt.
2. **Transkription** je Bereich mit der Vokabelliste als `initial_prompt`.
3. **Korrektur** durch das Modell, mit zwei Wächtern **im Code**, nicht im
   Prompt: eine Ersetzung wird nur übernommen, wenn (a) sie dem Original
   ähnlich genug ist (`difflib`-Verhältnis ≥ 0,65) **und** (b) der Ersatz in
   `vokabular.txt` steht. Einfügungen und Löschungen werden grundsätzlich
   verworfen. Roh- und korrigierter Text stehen beide im Protokoll.

**Warum die Wächter im Code stehen und nicht im Prompt:** Auf echten Aufnahmen
machte das Modell trotz vorsichtiger Anweisung aus „Fenstertechnologie" ein
„Fenster-Attention-Heads" — ein Begriff aus der damals fachfremden Vokabelliste.
Und aus einem korrekten „Hochvakuum" ein falsches „Hochvacuum": ähnlich genug für
Wächter (a), aber nirgends belegt, deshalb braucht es (b). Zurückhaltung wird
erzwungen, nicht erbeten.

**Nach der Transkription wird das Audio verdichtet** (`protokoll.verdichten`):
WAV → Opus mit 24 kbit/s, das Original wird gelöscht. 110 MB je Stunde sind für
eine Dauerablage zu viel — 107 GB im Jahr bei vier Stunden täglich, gegen 415 GB
frei. Opus macht daraus 10,5 MB je Stunde.

Verdichtet wird **erst nach** erfolgreicher Transkription; die arbeitet auf dem
Original. `lies_audio()` liest beides, Opus über ffmpeg — eine spätere
Neu-Transkription funktioniert also weiter. Nachgemessen: dasselbe Transkript,
Wort für Wort.

Erzeugt wird `aufnahmen/<sitzung>/protokoll.md` mit Zusammenfassung (als
**abgeleitet** gekennzeichnet, jede Aussage mit Zeitstempel) und dem
vollständigen Transkript. Unsichere Stellen landen unter „Unklare Stellen"
statt geraten zu werden. Roh-Audio bleibt liegen.

Gemessen: 65 s Audio mit 11 Äußerungen → 11 Abschnitte, komplett in **13 s**;
vier echte Aufnahmen (zusammen 65 s) in 22 s.

Ein Längenwächter davor fängt grobe Ausreißer ab: weicht der korrigierte Text um
mehr als 40 % von der Rohlänge ab, hat das Modell umformuliert statt korrigiert.

### Verdächtige Wörter aufklären

Wenn ein Wort im Transkript falsch aussieht, hilft ein Kandidatentest: mehrere
Möglichkeiten **einzeln** in den `--prompt` geben und schauen, welche einrastet.

    for h in JEOL Zeiss Hitachi Tescan; do
        whisper-cli -m $M -f $F -nt -np -l de --prompt "Fachbegriffe: $VOK, $h."
    done

Die Kandidaten, die **nicht** durchschlagen, sind die eigentliche Aussage. Ohne
sie wäre es bloßes Prompt-Forcing — Whisper übernimmt bereitwillig, was im Prompt
steht. Belegt am 27.08.2026: „IO/Yo/Jeove-Rasterelektronenmikroskop" wurde zu
„JEOL", während Zeiss, Hitachi und Tescan die Stelle unverändert ließen.

### Die Vokabelliste ist der Hebel

`vokabular.txt` im Projektwurzelverzeichnis, ein Begriff je Zeile, `#` ist
Kommentar. Sie speist den `initial_prompt` der Spracherkennung **und** begrenzt,
was die Korrektur überhaupt ersetzen darf.

**Sie muss zur Fachdomäne des Labors passen.** Eine fachfremde Liste ist nicht
bloß nutzlos, sondern schädlich — sie zieht Erkennung und Korrektur in die
falsche Richtung. Belegt: mit einer ML-Liste wurde aus „Fenstertechnologie" ein
„Fenster-Attention-Heads"; mit der passenden REM-Liste kamen
„Rasterelektronenmikroskop" und „Siliziumnitrid-Fenster" direkt und korrekt aus
der Spracherkennung, ganz ohne Korrekturstufe. **Der Hebel sitzt vorn, nicht
hinten.**

## Aktivierungswort und Werkzeuge

**Aktivierungswort ist „Kiwi"** (`konfig.AKTIVIERUNG`), aus KI-Hiwi. Bei offenem
Mikrofon wird jede Äußerung transkribiert; nur eine, die damit **beginnt**, gilt
als Ansprache. Der Rest des Satzes ist gleich die Anweisung — „Kiwi, starte die
Aufzeichnung" kommt in einem Atemzug.

Kein eigenes Wake-Word-Modell: die vortrainierten sind englisch, ein deutsches
Wort bräuchte eigenes Training. Der Text kostet nichts extra, weil die
lexikalische Endpoint-Prüfung ohnehin mitten im Satz transkribiert.

Drei Dinge, die dabei zählen:

- **Nur der Satzanfang wird geprüft.** Sonst löst jede Erwähnung aus, und im
  Labor wird über den Assistenten auch geredet.
- **Schreibvarianten aufzählen statt die Schwelle senken** (`kiwi`, `kivi`,
  `kiwie`, …). „Hiwi" und „Kiwi" trennt ein Buchstabe — Verhältnis 0,75. Eine
  lockerere Schwelle würde im Hochschulumfeld dauernd falsch auslösen.
- **Das Aktivierungswort steht immer im STT-Prompt**, unabhängig von
  `vokabular.txt` (in `stt._vokabular` erzwungen). Ohne das hörte die Erkennung
  aus „Kiwi, stoppe die Aufzeichnung" ein „TV stoppe die Aufzeichnung" — der
  Assistent war taub.

### Gespräch statt Einzelbefehl

Nach einer Ansprache bleibt das Gespräch offen: **Rückfragen brauchen kein
Aktivierungswort mehr.** Beendet wird es durch

- eine Verabschiedung — „Danke, Kiwi", „Kiwi, Ende", „Kiwi, beenden" —, erkannt
  von `aktivierung.ende()`. Verlangt **beides**, Aktivierungswort und
  Abschiedswort, und höchstens fünf Wörter; sonst würde „Danke Kiwi, kannst du
  noch schauen ob …" mitten in der nächsten Frage abbrechen;
- oder `GESPRAECH_STILLE_S` (45 s) ohne Ansprache. **Die Zeitgrenze ist keine
  Bequemlichkeit:** ohne sie reagierte der Assistent auf jedes Laborgespräch,
  sobald jemand vergisst, sich zu verabschieden.

Der offene Gesprächszustand steht auf Monitor und Client — im Raum muss
sichtbar sein, dass Kiwi ohne Zuruf mithört.

Wird nur „Kiwi" gerufen, ohne Anweisung, quittiert der Dienst mit „Ja?", ohne
das Modell zu bemühen — und das Gespräch ist danach offen. Das ist der
verlässlichere Bedienweg: erst rufen, auf die Quittung warten, dann sprechen.

**Auf das blosse Aktivierungswort wird nie abgeschnitten.** Die lexikalische
Prüfung liefert 0, solange nur „Kiwi" dasteht — grammatisch sieht „Kiwi?" wie
ein vollständiger Satz aus, und das Modell hielt ihn dafür. Gemessen wurde
„Kiwi, was kannst du eigentlich?" nach „Kiwi?" zerschnitten; die zweite Hälfte
kam ohne Aktivierungswort an und wurde verworfen. Wer nur ruft und wartet,
bekommt die Quittung über die Decke nach `DECKE_MS`.

### Ablage im Client

`GET /api/liste` liefert Protokolle und Rechercheergebnisse als JSON (neueste
zuerst), `GET /api/datei?k=<kennung>` den Inhalt. Der Client zeigt sie als
anklickbare Liste mit einem kleinen Markdown-Umsetzer; nach einer beendeten
Nachbereitung oder Recherche lädt er sie selbst neu.

**Warum nicht per Sprache:** Blättern und Lesen sind Bildschirmaufgaben. „Zeig
mir das Protokoll" über das Modell zu lösen kostete einen Abend Prompt-Arbeit
und blieb wackelig; eine Liste kann nicht missverstanden werden.

**Aufbau der Liste.** Einträge sind nach Zeitraum gebündelt (Heute, Gestern,
Diese Woche, danach nach Monat), mit Zähler je Block. Darüber eine
Werkzeugleiste, die oben klebt: Suchfeld über Titel und Kennung, Filter-Chips
je Art mit Anzahl, rechts das Löschen aller Aufzeichnungen.

Drei Details, die aus dem Bestand von 41 Einträgen kamen:

- Das Präfix „Recherche:" fällt aus dem Titel — es steht schon im Chip daneben
  und kostete sonst den Anfang jeder Zeile.
- Der Art-Chip erscheint **nur, wenn mehr als eine Art dasteht**. Einundvierzig
  Mal „RECHERCHE" untereinander unterscheidet nichts.
- Titel laufen über zwei Zeilen statt in Auslassungspunkte. Die längsten sind
  fast 300 Zeichen; in einer Zeile stand davon nichts Brauchbares.

Die Suche zeichnet **nur die Liste** neu, nicht die Seite. Würde das Suchfeld
mitgezeichnet, wäre es nach dem ersten Zeichen ein anderes Element und der
Fokus läge im Nichts.

**`buehneBelegt` gilt nur für den Seitenstart.** Das Flag verhindert, dass die
per fetch geholte Ablage die vom Dienst wiederhergestellte Bühne überschreibt —
ein Wettlauf, den es nur in der ersten Sekunde gibt. Die Prüfung stand jedoch
*in* `ablageLaden()` und galt damit für jeden Aufrufer, und das Flag wird
gesetzt, sobald einmal etwas anderes als die Ablage dastand, ohne je
zurückgenommen zu werden. Wer eine Recherche geöffnet und dann zurückgegangen
war, dem tat danach weder „neu laden" noch das Löschen sichtbar etwas:
gestrichene Einträge blieben stehen, bis der Browser die Seite neu holte.
Seither trägt nur der Startaufruf `nurWennFrei`.

**Löschen** läuft über den WebSocket-Befehl `loeschen` — einzeln mit `art` und
`kennung`, alle Protokolle auf einmal mit `alles: true`. Der Client nennt nie
einen Pfad, nur Art und Kennung; `_loeschen()` sucht den Eintrag in der
Sammlung und prüft zusätzlich, dass der Elternordner die erwartete Wurzel ist.
Ein Pfad aus dem Browser wäre ein Weg aus `aufnahmen/` heraus.

Bei einem Protokoll fällt die **ganze Sitzung** — Mitschnitt, Segmente,
Transkript. Nur das Protokoll zu löschen ließe die Aufnahme liegen, und die
ist die heiklere Hälfte (§ 201 StGB). Die laufende Aufzeichnung ist geschützt;
eine noch nicht nachbereitete Sitzung steht ohnehin nicht in der Liste, weil
die über `*/protokoll.md` geht.

Sitzungen **ohne** `protokoll.md` — abgebrochen oder nie nachbereitet — stehen
als eigene Art `mitschnitt` in der Liste. Sie fehlten dort zuerst, weil die
Auflistung über `*/protokoll.md` geht; nach dem ersten „alle löschen" blieben
deshalb 15 rohe Aufnahmen liegen (11 MB), und von außen sah es aus, als wäre
alles weg. **Unsichtbar heißt hier unlöschbar**, und ausgerechnet die Aufnahme
ist die Hälfte, auf die es ankommt. Der Knopf heißt seither „Alle
Aufzeichnungen löschen" und nimmt beide Arten mit.

Im Client ist der Knopf **zweistufig** und fällt nach fünf Sekunden ohne
Antwort zurück — ein Fehlklick wäre nicht rückgängig zu machen, eine
stehengebliebene Rückfrage die nächste Falle. Aus demselben Grund gibt es zum
Löschen **keinen Sprachbefehl**: was eine Fehlerkennung unwiderruflich macht,
gehört auf den Bildschirm.

### Datum und Uhrzeit stehen im Prompt

`_jetzt()` setzt Wochentag, Datum und Uhrzeit in **jeden** Prompt, neu je
Antwort. **Bewusst kein Werkzeug:** ein Werkzeug wäre wieder etwas, das das
Modell aufrufen kann oder eben nicht — und ohne Aufruf erfindet es die Zeit
(„Es ist ungefähr 13:41 Uhr" kam so zustande). Im Prompt steht es immer und
kostet nichts.

Reine Zeitfragen beantwortet der Dienst zusätzlich direkt (Absicht `ZEIT`),
ohne das Modell: über den Umweg suchte er dafür erst in den Unterlagen und dann
im Netz. Abgegrenzt gegen Fragen an die Quellen — „Welches Datum steht im
Protokoll?" ist keine Frage an die Uhr.

### Bei einer Frage an die Unterlagen sucht der Dienst, nicht das Modell

Im Systemprompt für `WISSEN` stand die Bitte „Rufe zuerst `dokumente_suchen`
auf". **Gemessen am 14.09.2026: von elf Fragen in drei Minuten rief das Modell
das Werkzeug zweimal.** In den neun anderen antwortete es aus dem Vorwissen —
bei der Gerätebedienung mit einem erfundenen Schalter, und es blieb dabei, als
der Nutzer ausdrücklich auf die Anleitung verwies.

Die drei Nachhol-Mechanismen greifen alle zu spät: sie prüfen die **fertige**
Antwort auf eine Absage („kann ich nicht"), auf ein Rechercheversprechen oder
auf eine Ankündigung („ich schaue nach"). Eine selbstbewusst erfundene Antwort
sieht nach keinem davon aus — und ist bereits gesprochen, wenn die Prüfung
läuft.

Deshalb sucht der Dienst jetzt **vor** der Modellantwort, sobald die Absicht
`WISSEN` ist, und legt die Fundstellen in den Prompt. Die Absichtserkennung
hat ohnehin schon entschieden, dass die Frage an die Unterlagen geht; dann
wird auch nachgesehen. Dazu die Auflage, sich an die Fundstellen zu halten und
offen zu sagen, wenn die Antwort nicht darunter ist.

**Kurze Rückfragen bekommen die vorige Äußerung als Kontext** (unter 50
Zeichen). „Und wie lange dauert das?" allein führte auf die Absuchzeit von
Bakterien statt auf die Einschaltdauer — sauber belegt und trotzdem zur
falschen Frage. Lange Fragen tragen ihr Thema selbst und würden durch den
Zusatz eher verwässert.

**Nach der Suche entfällt die Werkzeugrunde.** Findet die Suche etwas, wird
dem Modell gar kein Werkzeug mehr angeboten — es formuliert nur noch. Findet
sie nichts, bleiben `web_suchen` und `unterlagen_ueberblick` als Ausweg; dann
gibt es wieder etwas zu entscheiden.

Anfangs lief die Runde weiter mit, damit das Modell nachfassen kann. Gemessen
am 16.09.2026 mit Qwen3.8-Flash-Next rief sie in **zwölf Läufen kein einziges
Mal** ein Werkzeug — die Fundstellen standen ja schon im Prompt — und kostete
dabei 5,3 s.

#### Das Token-Budget der Werkzeugrunde

`WERKZEUG_TOKEN = 80`, nicht die 200 einer Antwort. Die Runde entscheidet nur,
**ob** ein Werkzeug läuft und mit welchen Argumenten; ihr Text wird in jedem
Fall verworfen — bei einem Aufruf ohnehin, und ohne Aufruf wird die Antwort
neu gestreamt, weil der Werkzeug-Prompt keinen Sprechstil trägt.

Das Budget kostet nur dann etwas, wenn das Modell **antwortet statt zu rufen**:
ein echter Aufruf endet bei `tool_calls` nach rund einer Sekunde, egal ob 40
oder 200 Token erlaubt sind. Schreibt es stattdessen Prosa, läuft es bis zum
Anschlag — 5,3 s für Text, der gleich darauf im Papierkorb landet.

**80 und nicht weniger.** Bei 40 Token brach ein Rechercheauftrag mitten im
Argument ab (`finish_reason: length`, unvollständiges JSON), und aus einem
abgeschnittenen Aufruf wird ein Werkzeug mit leeren Argumenten — genau das
`dokumente_suchen{}`, das vorher im Protokoll stand.

Beides zusammen, derselbe Ablauf im direkten Vergleich, warm, je drei Läufe:

| | erster Satz |
|---|---|
| vorher (200 Token, Suche weiter im Angebot) | **11,7 s** |
| nachher (80 Token, Runde entfällt bei Treffern) | **3,9 s** |

Befehlspfade (Aufzeichnung, Wecker) laufen weiter über die Runde und wurden
mit dem kleineren Budget gegengeprüft.

### Auslösewörter: der Dienst handelt, das Modell wird nicht gefragt

Tabelle in `absicht.AUSLOESER`. Fällt eines dieser Wörter, handelt der Dienst
sofort — wie bei „Kiwi" selbst:

| Wort | Wirkung |
|---|---|
| `Internetsuche`, `Netzsuche`, `Websuche` | **eine** Tatsache direkt über SearXNG, sofortige Antwort (~1 s) |
| `Internetrecherche`, `Webrecherche`, `recherchiere` | Rechercheauftrag an Hermes, Ergebnis kommt nach (30–40 s) |
| `Dokumentenrecherche`, `Dokumentensuche` | Suche in den Unterlagen, das Modell formuliert nur noch die Antwort |
| `Hermesaufgabe`, `Hermesauftrag` | Anweisung **unverändert** an Hermes durchreichen |
| `Kiwihilfe`, `Befehlsliste`, `was kannst du` | Liste der Auslösewörter — gesprochen kurz, angezeigt vollständig |

**Warum:** Das Modell hat sich wiederholt geweigert, obwohl das Werkzeug
bereitstand („Ich kann leider keine Internetrecherche durchführen"). Wo die
Handlung eindeutig ist, entscheidet der Dienst. Weitere Auslöser kosten eine
Zeile in der Tabelle.

**Schreibweisen:** alle Auslöser gelten zusammen, getrennt und mit Bindestrich
(`internet[- ]?suche`). Die Erkennung schreibt Komposita variabel — „Internetsuche",
„Internet-Suche" und „Internet Suche" kamen alle vor, und anfangs traf nur die
erste Form; die anderen landeten stumm bei der Dokumentensuche.

**Die Hilfe wird aus der Tabelle erzeugt** (`absicht.hilfe_zeilen()`), damit sie
nicht veraltet, sobald jemand einen Auslöser ergänzt.

**Warum `Kiwihilfe` und nicht `Hilfe`:** dieselbe Überlegung wie bei
`Hermesaufgabe`. „Hilfe" fällt im Labor ständig („ich brauche Hilfe beim
Mikroskop"). Das liess sich zwar mit Stellungsregeln abfangen, aber solche
Wächter sind Näherungen — ein zusammengesetztes Wort braucht keine. Ausnahme:
die **alleinstehende** Äusserung „Hilfe" gilt, weil nach dem Abschneiden des
Aktivierungsworts aus „Kiwi, Hilfe" genau das übrig bleibt.

Die Reihenfolge ist festgelegt: `hilfe` → `hermes` → `dokumente` → `websuche` →
`recherche`. „Dokumentenrecherche" enthält „recherche" und würde sonst zum
Internetauftrag.

**Deutsche Komposita werden aufgebrochen.** Die Spracherkennung schreibt
„Rasterelektronenmikroskop Auflösung" gern als ein Wort, und danach findet weder
FTS5 noch eine Suchmaschine etwas. `index.zerlege()` trennt an den **bekannten
Fachbegriffen** aus `vokabular.txt` — ohne Wörterbuch, dafür ohne
Falschtrennungen. Grundbegriffe wie `Fenster`, `Membran`, `Dicke` stehen deshalb
einzeln in der Liste: sie dienen als Trennstellen.

Zwei Betriebsarten, und der Unterschied ist wesentlich:

- **FTS5** verknüpft mit ODER — dort werden die Bestandteile *angehängt*.
- **Suchmaschinen** verknüpfen mit UND — dort *ersetzt* die Zerlegung das
  Kompositum. Bliebe es stehen, machte das eine unauffindbare Wort die ganze
  Anfrage leer, egal wie gut die übrigen sind.

Die Websuche versucht es erst unverändert und bricht nur bei null Treffern auf.

**Suche gegen Recherche — zwei Wege ins Netz, bewusst getrennt:**

| | `web_suchen` | `rechercheauftrag` |
|---|---|---|
| Weg | direkt an SearXNG | über Hermes |
| Dauer | 1–3 s, synchron | 30–40 s, im Hintergrund |
| Kann | Trefferschnipsel lesen | Suchen verketten, Seiten öffnen, zusammenfassen |

Beide landen bei derselben SearXNG-Instanz; der Unterschied ist, **wer sucht**.
Eine einzelne Tatsache über Hermes zu holen dauert vierzigmal so lange und
bremst dabei die Sprachantworten, weil er sich das Modell mit dem Sprachpfad
teilt. Über die Auslösewörter entscheidet der Sprecher, statt dass der Router
raten muss.

**Warum `Hermesaufgabe` und nicht `Hermes`:** Die Durchreichung gibt einer
gesprochenen Anweisung Zugriff auf Dateien, Browser und Codeausführung, bei
offenem Mikrofon. Über den Agenten wird im Labor aber geredet — „Hermes" allein
fiele versehentlich. Ein zusammengesetztes Wort nicht. Der Dienst sagt zusätzlich
hörbar an, was weitergegeben wird.

### Absagen vergiften den Verlauf

Sagt das Modell einmal „Ich kann nicht", steht das im Gesprächsverlauf und wird
zur Vorlage für alle folgenden Turns. Für die **Werkzeugrunde** wird der Verlauf
deshalb von Absagen befreit (`llm.ohne_absagen`, samt der Frage davor); in der
Schlussantwort dürfen sie bleiben. Zusätzlich wird ein Werkzeug erzwungen, wenn
der Router es vorgesehen hatte und die Antwort trotzdem eine Absage ist.

### Aufzeichnung läuft ohne das Modell

**Befehle und Statusfragen zur Aufzeichnung werden direkt beantwortet**, ohne
LLM-Aufruf. Der Router erkennt die Absicht, `will_aendern()` unterscheidet
Befehl von Statusfrage, `soll_anschalten()` an von aus — dann handelt der Dienst
und bestätigt.

**Warum:** Das Werkzeug wurde zwar zuverlässig gerufen, aber die Schlussantwort
erkannte das Ergebnis nicht als eigene Handlung und sagte „das liegt außerhalb
meiner Möglichkeiten". Diese Absage landete im Gesprächsverlauf und wiederholte
sich danach bei jedem Befehl — eine sich selbst verstärkende Störung. Bei der
Statusfrage kam dasselbe heraus, weil der Zustand nur im Werkzeug-Prompt stand
und nicht im Antwort-Prompt.

Für einen Befehl mit zwei möglichen Werten trägt das Modell ohnehin nichts bei.
Ausführen, bestätigen, fertig — schneller, und nicht ablehnbar.

`soll_anschalten()` schaltet im Zweifel **ein**: ein fälschlich gestarteter
Mitschnitt ist sichtbar, ein fälschlich gestoppter nicht.

### Werkzeuge

Ornith läuft mit `--enable-auto-tool-choice --tool-call-parser qwen3_xml`.
Definiert in `gateway.WERKZEUGE`, ausgeführt von `Sitzung.werkzeug`. Derzeit
eines: `aufzeichnung(an: bool)`. Der **Zustand** steht im System-Prompt, nicht in
einem Werkzeug — sonst fragt das Modell erst nach, bevor es handelt.

**Die Werkzeugansage gehört NICHT in den Gesprächsverlauf.** `melden()`
schreibt sonst alles Gesprochene hinein — bei der Ansage war das eine
Rückkopplung: „Ich schaue in den Unterlagen nach." stand als letzte
Assistentenäußerung da, und das Modell schrieb in der Schlussantwort genau
diesen Satz noch einmal, statt zu suchen. Gemessen mit Nemotron: drei Züge
hintereinander nur dieser Satz, **ohne einen einzigen Werkzeugaufruf dahinter**
(im Protokoll fehlt die `Werkzeug`-Zeile, die `LLM erster Satz`-Zeile nennt
34 Zeichen — die Länge der Ansage). Je öfter sie im Verlauf stand, desto
sicherer wiederholte sie sich.

Drei Änderungen: `melden(ansage, in_verlauf=False)`; dieselbe Ansage nur einmal
je Zug (`angesagt`); und wenn das Netz `_ANGEKUENDIGT` greift, fallen die
Ankündigungssätze aus der Antwort — der Dienst hat gerade nachgeschlagen, sie
sind eingelöst. Nur diese Sätze, nicht die ganze Antwort: das Modell kann
ankündigen und trotzdem etwas sagen.

**Warum das kein Widerspruch zum Verlaufsgebot ist:** `melden()` schreibt mit,
weil das Modell sonst bestreitet, was der Dienst gesagt hat („Das Protokoll ist
fertig" — „das habe ich nicht gesagt"). Die Ansage ist anders: flüchtig, im
selben Zug vom Ergebnis abgelöst, danach gegenstandslos.

**Zwei Netze gegen behauptete Handlungen.** Das Modell hat mehrfach gesagt, es
habe die Aufzeichnung gestoppt, **ohne das Werkzeug aufzurufen**. Bei einer
rechtlich heiklen Funktion ist das inakzeptabel: Wer glaubt, es werde nicht mehr
aufgezeichnet, muss recht haben.

1. Antwort wird gegen `_BEHAUPTUNG` geprüft — behauptet sie eine Änderung, ohne
   dass ein Werkzeug lief, greift Stufe 2.
2. **Nachfassen mit erzwungenem Aufruf** (`llm.erzwinge_werkzeug`, vLLMs
   `tool_choice` auf eine bestimmte Funktion). Damit wird die Handlung
   nachgeholt statt bloß widersprochen — der Nutzer bekommt, worum er gebeten
   hat. Schlägt auch das fehl, wird wenigstens der wahre Zustand angesagt.

Ein verschärfter Prompt allein reichte nicht: isoliert 5/5 korrekt, im echten
Ablauf mit Gesprächsverlauf wieder daneben.

## Latenzbudget (warm gemessen, 27.08.2026)

| Glied | Zeit | Anmerkung |
|---|---|---|
| STT (whisper-server, large-v3-turbo) | ~100 ms | konstant, Whisper rechnet immer ein 30-s-Fenster |
| LLM erstes Token | ~70 ms | 65–70 tok/s |
| LLM erster **sprechbarer** Brocken | 350–670 ms | 30–90 Zeichen |
| TTS erster Chunk (Piper, CPU) | 120–270 ms | je nach Brockenlänge |
| Silero-VAD | 0,07 ms je 32-ms-Block | 0,2 % eines Kerns |
| **Summe Maschine** | **~600–800 ms** | ohne Endpointing und Netz |
| Endpointing | 490–1450 ms | **der eigentliche Engpass** |
| **Ende-zu-Ende gemessen** | **1085–2251 ms** | drei Läufe über den Sprachdienst |

**Warum überhaupt satzweise?** Das Modell schreibt Token für Token, Piper
braucht aber einen ganzen Satz — ein Bruchstück bekommt die falsche Betonung.
Ohne Schnitt müsste man auf die vollständige Antwort warten; mit Schnitt spricht
Piper den ersten Satz, während das Modell den zweiten schreibt. Feste Antworten
(Datum, Aufzeichnungsstatus, Ansagen) werden **nicht** geschnitten, die sind
schon fertig.

**Nicht nach einer Ziffer trennen** (`(?<![0-9]\.)`): „der 28. August" und „die
3. Messung" sind Ordnungszahlen, keine Satzenden. Ungetrennt klingt höchstens
ein Satz zu lang; falsch getrennt hört man die Lücke mitten im Datum. Der erste
Versuch griff nicht, weil beide Rückblicke dieselbe Stelle prüften — hinter dem
Punkt statt davor.

**Achtung, naheliegender Denkfehler:** Die 70 ms sind das erste *Token*, nicht
der erste *sprechbare* Brocken. Piper braucht einen Teilsatz, also 10–25 Token.
Eine Budgetrechnung mit TTFT ist um den Faktor fünf zu optimistisch.

**Stimme:** `de_DE-thorsten_emotional-medium` mit acht Sprechweisen im selben
Modell (`amused`, `angry`, `disgusted`, `drunk`, `neutral`, `sleepy`,
`surprised`, `whisper`). Gewählt über `konfig.STIMM_ART` bzw.
`$KIHIWI_STIMM_ART`, Standard `neutral` — ohne Auswahl nimmt Piper die erste
(`amused`), was für einen Laborassistenten nicht passt. Alle Proben unter
`http://127.0.0.1:8920/stimmen`.

Der Dienst protokolliert beim Start, welche Stimme und Sprechweise geladen
wurde. Ohne das war nicht zu sehen, dass noch die alte lief.

**Feste Sätze sind vorgerendert.** `tts.vorrendern()` erzeugt beim Start 15
stehende Sätze („Ich schaue in den Unterlagen nach", „Ja?", die Bestätigungen)
und behält sie im Speicher. Damit trägt sich die bessere Stimme selbst: die
Ansage kommt aus dem Vorrat und damit sofort, nur die variable Antwort dahinter
zahlt die 343 ms. Gemessen an „Internetsuche …": **416 ms** bis zum ersten Ton
mit `thorsten-high` gegen 847 ms mit `thorsten-medium`.

**Immer warm messen.** Erster CUDA-Lauf 266 statt 100 ms, erster vLLM-Aufruf 226
statt 70 ms, Piper als CLI-Prozess 800 statt 110 ms. Prozesse müssen dauerhaft
laufen, nicht je Anfrage starten.

### Endpointing

Naiv (feste Stilleschwelle), 12 Testfälle mit gebauter Denkpause:

| Schwelle | Fehlschnitte | Zusatzlatenz |
|---|---|---|
| 600 ms | 8/12 | 575 ms |
| 800 ms | 5/12 | 759 ms |
| 1000 ms | 2/12 | 991 ms |
| **1200 ms** | **0/12** | **1183 ms** |

Lexikalisch (bei 300 ms Stille transkribieren und das Modell fragen, ob der Satz
fertig ist): **~510 ms bei 0/12 Fehlschnitten**, Kosten je Prüfung 228 ms
(130 ms STT + 97 ms LLM). Die Prüfung läuft nebenher, während weiter zugehört
wird — fällt sie auf WEITER, hat sie nichts gekostet. **Greift sie, wird ihr
Transkript weiterverwendet und das STT im Antwortpfad übersprungen** (0 ms statt
130–257 ms).

### Namensprüfung vor dem Start

`./dienste.sh namen` (und jeder `start`) sucht Namen, die **nirgends im Modul
gebunden** sind — 124 Dateien in Sekundenbruchteilen, ohne Fremdbibliothek.

Sie sammelt jeden Namen, der irgendwo einen Wert bekommt (oben, in Funktionen,
Parameter, Schleifen, `except … as`, `with … as`, Importe) und meldet jeden
gelesenen Namen, der darin fehlt. **Gültigkeitsbereiche prüft sie nicht** —
eine Variable, die nur in einer anderen Funktion existiert, fällt nicht auf.
Dafür gibt es praktisch keine Fehlalarme: auf dem ganzen Bestand null Funde.

Sie **warnt nur und bricht nicht ab**. Ein laufender Dienst mit einem Fehler
in einem Nebenpfad ist besser als gar keiner, und wer die Warnung sieht, weiß,
wo er suchen muss.

Anlass war der 14.09.2026: ein Umbau in `llm.py` riss acht Definitionen mit,
weil ein Textschnitt über einen Zeilenbereich keine Funktionsgrenzen kennt.
`py_compile` merkt davon nichts — ein `NameError` entsteht erst beim Aufruf.
Der Assistent nahm danach vier Stunden lang jede Wissensfrage entgegen und
antwortete nicht. Gegen den kaputten Stand gehalten meldet die Prüfung genau
die zwei Namen, an denen es brach.

### Die Anzeigetafel bedient nur der Dienst — auch wenn das Modell etwas anderes sagt

`_TAFEL_BEHAUPTUNG` stellt richtig, wenn die Antwort behauptet, die Tafel
bedient zu haben, ohne dass eine Anzeigeaktion lief. Der Auslöserzweig steigt
vorher aus; was diese Prüfung erreicht, hat also nichts an der Tafel getan.

Anlass: Auf „Zeig mir die Auslastung von GPU und RAM auf der Anzeige" kam
„**Ich habe die Anzeigetafel aktiviert** und zeige dir nun die Auslastung" —
und auf der Tafel rührte sich nichts. Die Formulierung trifft das Auslösewort
nicht (`Anzeigetafel` ist bewusst ein Kompositum), landet also bei `WISSEN`,
und die Vorabsuche legt dem Modell prompt die Dokumentation der Tafel vor.
Daraus macht es eine Vollzugsmeldung.

Die Richtigstellung nennt gleich den richtigen Weg: „Sag ‚Anzeigetafel' und
was du sehen willst." Nur die Ich-Form und Vollzugsmeldungen lösen sie aus —
auf „Wie funktioniert die Anzeigetafel?" darf erklärt werden, wie man sie
bedient, ohne dass ihm widersprochen wird. Gegen zwölf Sätze geprüft, echte
wie erfundene.

### Es kommt immer eine Antwort, notfalls eine Absage

Am Ende von `_antworten()` steht ein Riegel: wurde **nichts** gesagt, sagt der
Dienst „Darauf habe ich keine Antwort bekommen." Er sitzt dort und nicht an
den einzelnen Fehlerstellen, weil er so jede Ursache abdeckt.

Zwei davon sind belegt:

- **Der Motor bricht ab.** `antwort_mit_werkzeugen()` kehrte bei einem Fehler
  stumm zurück; im Protokoll stand `HTTPError 500`, im Raum nichts. Jetzt
  liefert sie ein `("fehler", …)`-Ereignis mit dem HTTP-Code, und der Code
  wird ausgesprochen.
- **Eine leere Antwort ohne Fehler.** Reasoning-Modelle schreiben ihr Denken
  nach `reasoning_content`; reicht `max_tokens` nicht bis zur Antwort, kommt
  HTTP 200 mit leerem `content`. GLM-5.3-Flash lieferte bei `max_tokens: 60`
  null Zeichen Text und 288 Token Nachdenken.

Geprüft gegen zwei Testmotoren: einer, der 500 liefert, und einer, der 200
mit leerem Inhalt liefert. **Schweigen ist von „hat mich nicht gehört" nicht
zu unterscheiden** — dieselbe Verwechslung hat in diesem Projekt schon
mehrfach Zeit gekostet.

### Reasoning-Modelle am Sprachdienst

Gemessen am 14.09.2026 mit GLM-5.3-Flash (2 bit, llama.cpp), warm:

| | |
|---|---|
| erster Satz auf eine Wissensfrage | **15,5 s** |
| Werkzeugrunde mit 1778 Token Prompt | 10,7 s |
| derselbe Aufruf mit 27 Token Prompt | 5,4 s |
| Antwort direkt am Motor, mit Denken | 15,1 s (1623 Zeichen Denken) |
| dasselbe mit `enable_thinking: false` | 5,7 s |

`KIHIWI_LLM_ZUSATZ='{"chat_template_kwargs":{"enable_thinking":false}}'`
drückt die Zeit am Motor auf ein Drittel und verhindert vor allem die leeren
Antworten. Bei GLM ändert das die Latenz im Sprachdienst kaum (15,5 s gegen
15,3 s): dort überwiegt die Prompt-Verarbeitung der vorangestellten
Fundstellen. Der Schutz gegen erfundene Antworten kostet bei langsamen
Modellen also Zeit, die er bei den MoE-Modellen nicht kostete.

**Der Schalter wird nicht mehr von Hand gesetzt.** `_denkschalter()` in
`llm.py` ergänzt ihn selbst, sobald der angebotene Modellname in `_DENKER`
steht (`glm-`, `qwen3.8-flash`) — auch wenn das Modell im Betrieb gewechselt
wird. `KIHIWI_LLM_ZUSATZ` von Hand hat Vorrang. Nach dem Namen und nicht nach
einer Probe: eine Probe kostete einen Aufruf bei jedem Start.

#### Qwen3.8-Flash-Next (125B gesamt / 6B aktiv, Q3\_K\_XL, llama.cpp)

Gemessen am 14.09.2026, warm, gegen dieselben Fragen:

| | ohne Denkschalter | mit Denkschalter |
|---|---|---|
| erster Satz, Wissensfrage | **gar keine Antwort** | **5,2–6,2 s** |
| Antwort am Motor (1827 Token Prompt) | 10,1 s, `finish_reason: length` | 7,7 s, `stop` |
| Nachdenken | 664 Zeichen | **0** |
| Antwortlänge | 259 Zeichen (abgeschnitten) | 704 Zeichen |

Ohne den Schalter kam bei Wissensfragen **nichts** — das Nachdenken
verbrauchte `max_tokens`, und `content` blieb leer. Der Riegel in
`_antworten()` meldete das hörbar, aber eine Antwort war es nicht.

Damit ist Qwen3.8-Flash-Next am Sprachdienst **dreimal schneller als
GLM-5.3-Flash** (5,2 s gegen 15,5 s) bei gleicher Quellentreue: Antworten mit
Abschnittsangabe, und auf „Was ist die Hauptstadt von Frankreich?" kommt
„steht in den vorliegenden Unterlagen nicht".

## Fallen, die schon Zeit gekostet haben

- **Silero v5 will 64 Samples Kontext vor dem 512er-Block**, Eingang also 576.
  Ohne den Vorlauf meldet das Modell durchgehend „keine Sprache" — still, ohne
  Fehlermeldung. Sieht aus wie kaputtes Audio. Behandelt in `vad/silero.py`.
- **Whisper halluziniert in Stille.** 5 s Stille ergaben „Vielen Dank.", mit
  Vokabular-Prompt „Untertitelung des ZDF, 2020", Rauschen „Amen."
  **VAD-Gating vor dem STT ist zwingend**, nicht optional.
- **Ohne `-l de` übersetzt Whisper.** Auto-Erkennung kippt bei gehäuften
  englischen Fachbegriffen ins Englische. Immer Sprache hart setzen.
- **Whisper hängt immer ein Satzzeichen an**, auch mitten im Satz. Vor dem
  lexikalischen Endpointing Punkt und Komma entfernen, Fragezeichen behalten.
- **Der System-Prompt landet im Lautsprecher.** ASCII-fiziertes Deutsch im Prompt
  („Aufzaehlungen") bringt das Modell dazu, ebenso zu antworten — Piper spricht
  „fuer" dann als „Fu-er". Umlaute im Prompt sind Pflicht.
- **vLLM ist bei `temperature: 0` nicht deterministisch.** Grenzfälle schwanken
  über die halbe Wahrscheinlichkeitsskala. Keine feinen Schwellen darauf bauen.
- **Worker-Threads dürfen nicht auf einer Queue blockieren.** `await
  asyncio.to_thread(q.get)` auf einer `queue.Queue` lässt den Thread ewig hängen,
  sobald der Verbraucher vorzeitig aufhört — und `asyncio.run` wartet beim
  Beenden **120 Sekunden** auf solche Threads. Richtig herum: der Thread
  *schiebt* per `loop.call_soon_threadsafe` in eine `asyncio.Queue`. Danach
  beendet sich der Dienst in 17 ms statt in zwei Minuten.
- **Dienste mit `setsid --fork nohup … < /dev/null` starten.** `setsid` allein
  genügt nicht: ohne `--fork` forkt es nicht, der Dienst bleibt direktes Kind
  der aufrufenden Shell, und die hängt danach in `do_wait` auf ihm. Sichtbar
  wurde das nur beim Aufruf durch eine Pipe (`./dienste.sh neustart | tail`) —
  mit Umleitung in eine Datei fiel es nicht auf. Mit `--fork` wird der Dienst an
  init durchgereicht, der Aufruf ist in 2 s durch statt nach 120 s abgebrochen.
- **Audiopuffer im Browser nicht nach Länge zurücksetzen.** Die TTS-Stücke
  kommen 25-mal schneller als Echtzeit; eine lange Antwort läuft zu Recht zehn
  Sekunden voraus. Eine Grenze von 8 s hielt das für einen Hänger, setzte die
  Terminierung zurück, und der Rest spielte ÜBER dem schon Geplanten — zwei
  Stimmen gleichzeitig. Nur zurücksetzen, wenn die Warteschlange leergelaufen
  oder der AudioContext angehalten war.
- **`pkill -f` und `pgrep -f` erwischen die eigene Shell**, wenn das Suchmuster
  in deren Kommandozeile steht — an einem Tag dreimal passiert, zuletzt bei
  einer `until ! pgrep -f hermes-agent`-Warteschleife, die deshalb nie endete.
  Über den Port gehen oder auf einen Zustand prüfen, nicht auf Prozessnamen.
- **`pkill -f` erwischt die eigene Shell**, wenn das Muster in deren
  Kommandozeile steht. Über den Port gehen:
  `ss -tlnpH "sport = :8920" | grep -oP 'pid=\K[0-9]+'`.
- **Ein Modellwechsel unter dem laufenden Dienst blieb unbemerkt.** Wer per
  `model-switch` umschaltet, ändert den `served-model-name`; der Sprachdienst
  fragte weiter nach dem Namen von seinem Start und bekam auf **jede** Anfrage
  einen 404. Am 14.09.2026 stand er so **sechs Tage** — aufgefallen ist es
  erst, als jemand mit ihm sprach. Die Prüfung in `dienste.sh` greift nur beim
  Start; der Wechsel passiert im Betrieb. Seither heilt `_anfragen()` in
  `sprachdienst/llm.py` das selbst: bei 404 einmal `/v1/models` fragen, den
  angebotenen Namen übernehmen, Anfrage wiederholen. **Alle** Chat-Anfragen
  laufen durch diese eine Stelle, damit es nicht wieder einen Pfad gibt, der
  es nicht kann.

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
  deutsches Fachvokabular ungeeignet.
- Fürs Codieren taugt Hermes wenig.

## Maschinen-Notizen außerhalb des Repos

Ausführlicher in `~/.claude/projects/<home-scope>/memory/` (Projekt-Scope `~`,
in kihiwi-Sessions nicht automatisch geladen): `gx10-zwei-modelle`,
`gx10-modell-benchmark`, `gx10-netzwerk-serverraum`, `gx10-serverraum-umzug`,
`gx10-ssh-key-only`, `gx10-ds4-opencode`, `hermes-searxng`,
`qwen38-flash-wartet`. Bei Widersprüchen sind jene die Quelle.

### Zahlaussprache (`sprachdienst/zahlwort.py`)

Piper phonemisiert ueber eSpeak, und dessen Zahlbehandlung ist bei deutschen
Datums- und Zeitangaben unbrauchbar: `28.08.2026` wird Ziffer fuer Ziffer samt
Punkten gelesen, `12:38 Uhr` wird zu "zwoelf Uhr achtunddreissig Uhr" -- das
zweite "Uhr" kommt aus dem Doppelpunkt, das erste stand im Text.

Deshalb werden nur diese beiden Muster vor der Ausgabe ausgeschrieben. Einzelne
Zahlen (`50 nm`, `2 bis 5 Nanometer`) bleiben unangetastet, sonst werden
Messwerte unleserlich.

Jahreszahlen folgen einer eigenen Regel: 1937 ist "neunzehnhundert-
siebenunddreissig", nicht "eintausendneunhundert...". Ab 2000 gilt wieder die
normale Form ("zweitausendsechsundzwanzig").

**Nur der Sprechpfad ist betroffen.** `zahlwort.ausschreiben()` haengt in
`_sprechbar()`, das ausschliesslich zwischen Text und Piper sitzt. Was ueber
`melden()` in den Browser und in `verlauf` geht, ist der Originaltext mit
Ziffern -- ausgeschriebene Zahlen waeren im Protokoll kaum lesbar.

### Wissensabgleich per Stimme

**Jede Quelle wird genannt, auch die unveränderte.** „unverändert" trug
inhaltlich nichts bei und fiel deshalb aus dem Satz — die Quelle verschwand
damit ganz, und aus dem Gesagten ließ sich nicht unterscheiden, ob sie geprüft
oder ausgelassen wurde. Jetzt:

```
Abgleich fertig. kihiwi: 2 Dokumente neu oder geändert.
                 protokolle und haupt-repo waren unverändert.
```

Ab fünf stillen Quellen wird nur gezählt („6 weitere Quellen waren
unverändert") — eine Aufzählung vorzulesen dauert länger, als sie anzusehen.
„Sonst war alles auf dem neuesten Stand" fällt weg, wenn es kein Sonst gibt.

„Wissensabgleich" (auch Unterlagenabgleich, Quellenabgleich, Wissensauffrischung)
stoesst denselben Lauf an wie `./dienste.sh wissen einlesen`: `einlesen.git()`
macht `git pull --ff-only`, danach wird neu indiziert. Eigenes Ausloesewort,
weil es Netzverkehr macht und Sekunden dauert.

`einlesen.alles()` gibt seit diesem Schritt je Quelle `{name, art, neu,
entfernt, notiz}` zurueck, damit der Dienst sagen kann, was sich geaendert hat.
`notiz` fuellt nur `git()`: „2 neue Commits", „frisch geklont", „unveraendert",
„Abruf fehlgeschlagen".

Zwei Fallen, beide beim Testen aufgefallen:

- Die Bilanz haengt an den **Notizen**, nicht an den Dokumentzahlen. Ein Repo
  kann Commits bringen, ohne dass sich ein indiziertes Dokument aendert -- das
  als „alles auf dem neuesten Stand" zu melden waere gelogen.
- Eine nicht erreichbare Quelle wird ausdruecklich genannt, bevor irgendeine
  Erfolgsmeldung kommt. Ein Fehlschlag darf nicht als Erfolg durchgehen.

Ein Lauf zur Zeit (`ABGLEICH`, `asyncio.Lock`): zwei parallele Laeufe wuerden
sich gegenseitig als „nicht mehr gesehen" aus dem Index raeumen.

### Index auf WAL

`index.db` laeuft im WAL-Modus. Vorher nahm ein Indexlauf eine Schreibsperre auf
die ganze Datei, und `lesen()` wartet nur 5 s -- ein Abgleich haette jede Suche
ausgesperrt. Der Modus haengt an der Datei, `verbinden()` setzt ihn.

**`.gitignore` musste mit:** WAL legt `index.db-wal` daneben, mit demselben
Volltext aus dem privaten Hauptrepo. Das Muster heisst jetzt `wissen/index.db*`.

### Browseroberflaeche: Verlauf und Buehne

`klient.html` hat zwei Spalten statt einer 44rem-Spalte:

- **links, 25rem** — Kopf, Knoepfe, Pegel, Marken, LEDs und der
  Gespraechsverlauf. Der Verlauf scrollt fuer sich und nimmt den Rest der
  Spalte.
- **rechts, Rest** — die Buehne. Alles Ausfuehrliche landet hier:
  Rechercheergebnisse, Protokolle, die Hilfe, die Ablage. Kopfzeile mit Art,
  Titel, Zeitangabe und den Knoepfen „Ablage" und „neu laden".

Unter 62rem Fensterbreite stapeln sich beide, der Verlauf wird auf 22rem
begrenzt.

**Was in den Verlauf gehoert und was nicht.** In den Verlauf kommt nur, was
gesprochen wurde. Ein volles Rechercheergebnis oder Protokoll schob dort alles
andere aus dem Bild -- und die gesprochene Kurzfassung sagt ohnehin „Das
Ausfuehrliche steht auf dem Monitor". Stattdessen erscheint ein anklickbarer
**Merker**, der den Inhalt auf der Buehne wieder aufruft. Damit bleibt er
erreichbar, ohne den Verlauf zuzuschuetten.

**Texteingabe neben der Stimme.** Ein Feld unter dem Verlauf schickt
`{"befehl":"text","text":…}`. Der Dienst baut daraus einen `Endpoint` mit
`grund="getippt"` und ruft `antworten()` — also genau der Einstieg, den auch
`audio()` nach dem Endpunkt nimmt. VAD, Transkription und Nachschärfen
entfallen, der Text steht fest; alles danach ist unverändert (Absichten,
Auslösewörter, Wecker, Werkzeuge, Bühne).

Drei Entscheidungen dahinter:

- **Kein Aktivierungswort.** Wer tippt, spricht den Assistenten absichtlich an
  — dieselbe Überlegung wie beim Knopf „ansprechen". Ein vorangestelltes
  „Kiwi," wird trotzdem entfernt; `aktivierung.erkannt()` prüft nur den Anfang,
  „Was ist ein Kiwi?" bleibt also heil.
- **Kein Mikrofon nötig.** Am Client kann eine Tastatur hängen, wo kein
  brauchbares Mikrofon steht, und im Labor ist es laut. Das Feld funktioniert
  mit ausgeschalteter Aufnahme.
- **`gespraech=True`.** Getippt und gesprochen sind dasselbe Gespräch: eine
  Rückfrage per Stimme braucht danach kein erneutes „Kiwi".

Eine laufende Antwort wird abgebrochen — wer tippt, während geredet wird, will
das Neue.

**Stummschalter (🔊/🔇) daneben.** `{"befehl":"vorlesen","an":false}` setzt ein
Flag je Verbindung; `sag()` steigt dann sofort aus. **Nicht der Klient hört auf
abzuspielen, sondern der Dienst hört auf zu erzeugen** — sonst liefe Piper
weiter für nichts. Gemessen: stumm 0 Tonströme und 0 Audio-Bytes, laut 1 Strom
und 275 kB, bei identischem Antworttext.

Die Einstellung liegt im `localStorage` des Browsers, nicht im Dienst: sie
gehört zum Arbeitsplatz, nicht zum Assistenten. Sie wird bei jedem `onopen` neu
geschickt und übersteht damit Neuladen und Dienstneustart.

**Wechselwirkung mit der Aufzeichnung:** stumm wird auch nichts in den
Mitschnitt gemischt. Das ist richtig und kein Verlust — der Assistent hat im
Raum tatsächlich nichts gesagt, also gehört im Raumprotokoll auch nichts hin.
Der Text steht im Gesprächsverlauf.

**Die Buehne ueberlebt Neuladen und Dienstneustart.** Der letzte Buehneninhalt
liegt in `zustand/buehne.json` und wird beim Verbindungsaufbau mit `wieder:
true` nachgereicht; der Klient zeigt ihn an, vermerkt „von vorhin" und setzt
**keinen** Merker in den Verlauf — der ist nach dem Neuladen selbst leer, ein
einzelner Verweis darin waere irrefuehrend. Zentral ueber `Sitzung.zur_buehne()`,
damit kein Aufrufer das Merken vergessen kann.

**Warum ueberhaupt:** der Inhalt lebte nur im Browser. Ein korrekt
ausgeliefertes Rechercheergebnis war nach einem Dienstneustart weg, und es sah
aus, als sei es nie angekommen — genau so wurde es gemeldet. Ueber 400.000
Zeichen wird nichts gemerkt; die Datei wird bei jeder Verbindung gelesen.

**Reihenfolge.** `zeile()` haengt unten an (`amEnde()`) und scrollt nach, wenn
der Nutzer ohnehin unten steht. Vorher stand `prepend` darin, der Verlauf las
sich rueckwaerts -- die Antwort ueber der Frage, der Auftrag unter dem
Ergebnis.

**Markdown-Tabellen, Trennlinien, mehrzeilige Zitate.** Der Rechercheagent
antwortet auf Maßfragen mit Tabellen; ungerendert stand dort eine Wand aus
Pipe-Zeichen. `tabellen()` arbeitet **zeilenweise** und ersetzt jeden Block
durch einen Platzhalter — ein Regex über den ganzen Text verschluckt den Absatz
dahinter, und die Zeilenumbrüche weiter unten würden die Tabelle zerreißen.
Erkannt wird nur, was Kopfzeile *und* Trennzeile hat; eine Zeile mit einem
zufälligen `|` bleibt Text.

Dazu `---` als `<hr>` und aufeinanderfolgende Zitatzeilen zu **einem** Block —
der Warnhinweis über jedem Rechercheergebnis stand vorher in drei Kästen
untereinander.

### Anzeigetafel

„Anzeigetafel" (auch „Messanzeige", „Instrumententafel") blendet Messwerte auf
die Bühne: **GPU, CPU, Speicher, Platte**, wahlweise als Balken, Zeiger, Zahl
oder Verlaufsdiagramm. Ohne Angabe: Balken — er zeigt Wert *und* Bereich auf
einen Blick und braucht am wenigsten Platz.

**Hinzufügen ist die Vorgabe, nicht Ersetzen.** Wer nacheinander drei Werte
nennt, will sie nebeneinander sehen. Weggenommen wird nur auf Ansage:

```
Anzeigetafel GPU als Zeiger      → kommt dazu
Anzeigetafel Platte als Diagramm → kommt dazu, mit EIGENER Darstellung
Anzeigetafel GPU entfernen       → nur die eine weg
Anzeigetafel leeren              → alles weg (auch „löschen", „zumachen")
```

**Je Größe eine eigene Darstellung.** Eine schon vorhandene Größe bekommt die
neue Darstellung, wandert aber nicht ans Ende — eine Tafel, die bei jedem
Befehl die Reihenfolge tauscht, ist nicht wiederzuerkennen.

**Der Stand liegt im Dienst (`TAFEL`), nicht im Browser**, damit ein Neuladen
ihn nicht verliert und zwei Klienten dasselbe sehen. Er geht über `zur_buehne`
und überlebt damit auch einen Dienstneustart.

**Größe und Darstellung erkennt der Dienst, nicht das Modell.** Vier Größen mal
vier Darstellungen sind sechzehn Fälle; dafür lohnt kein Modellaufruf, und ein
Router, der rät, wäre langsamer und unzuverlässiger. *Die Wortgrenzen im Muster
sind nicht Kosmetik: ohne sie trifft `ram` mitten in „DiagRAMm", und „CPU und
GPU als Diagramm" zeigte den Arbeitsspeicher mit an.*

**Auf dem GB10 gibt es keinen getrennten GPU-Speicher** — `nvidia-smi` meldet
für `memory.used` ein `[N/A]`, weil Prozessor und Grafikeinheit sich denselben
Unified Memory teilen. Deshalb steht RAM für beides, und die GPU trägt nur ihre
Auslastung bei.

Die Werte kommen über **`/api/messwerte`**, nicht über den WebSocket: die
Anzeige lebt im Browser, und der Dienst muss nicht wissen, wer gerade was
eingeblendet hat. Der Klient fragt im Sekundentakt, `messwerte.py` hält 0,9 s
vor. Ein Zeitgeber je Tafel, den `buehne()` beim Wechsel abräumt — sonst laufen
nach drei Recherchen drei Abfragen weiter.

**Erweiterbar gedacht:** jeder Wert ist ein `Wert` mit Name, Zahl, Einheit,
Bereich und zwei Schwellen. Die Anzeige kennt nur diese Form. Werte von außen
(MQTT) müssen später nur in dieselbe Form gebracht und in `alle()` eingehängt
werden.

**`alsHtml()`** entfernt die `<br>` unmittelbar vor und nach Ueberschriften und
Zitaten. Die Blockelemente bringen eigenen Abstand mit, die `<br>` aus den
Leerzeilen kamen obendrauf und rissen Loecher in den Text.

### Timer und Erinnerungen (`sprachdienst/wecker.py`)

Deterministisch wie die Aufzeichnungssteuerung: die Zeitangabe parst der
Dienst, das Modell wird nicht gefragt. Ein Timer, den das Modell zu stellen
vergisst, faellt erst auf, wenn er nicht klingelt -- und dann ist es zu spaet.

**Deutung.** `deuten()` liefert `(Faelligkeit, Erinnerungstext)`:

- relativ: „in/fuer/nach/auf N Sekunden|Minuten|Stunden", auch als Wort
  („in zehn Minuten"), mit Bruechen („in einer halben Stunde", „anderthalb")
- absolut: „um 15 Uhr", „um 15:30", „um 9 Uhr 30" -- eine Uhrzeit, die schon
  vorbei ist, meint morgen
- Grenzen: unter 1 s und ueber 24 h wird abgelehnt

Der Erinnerungstext ist der Rest ohne Zeitangabe, Einleitung und Nachklapp.
`anlass()` haengt ihn grammatisch passend an: Praepositionalphrasen direkt
(„an die Besprechung"), Infinitive mit „daran," („daran, die Pumpe
abzuschalten").

**Erkennung** (`absicht.wecker_absicht`) verlangt ZWEI Dinge: ein Auftragswort
(timer, wecker, erinner…, weck…) UND eine parsbare Zeit. „In zehn Minuten ist
die Probe fertig" ist eine Feststellung und faellt durch. Zwei Sonderfaelle
gelten nur als GANZE Aeusserung, weil sie im Satz mehrdeutig waeren: „alle
loeschen" und „wie lange noch".

**Ueberdauert einen Neustart** (`zustand/wecker.json`). Laengst Abgelaufenes
klingelt nicht nach: was beim Laden mehr als 5 min ueberfaellig ist, faellt
weg. Ohne Zuhoerer wird nicht ausgeloest -- eine Erinnerung in einen leeren
Raum ist verloren; sie wartet bis zu 10 min auf eine Verbindung und verfaellt
dann mit einer Logzeile. Kommt sie verspaetet, sagt sie das dazu.

**Gong vor der Ansage.** Zwei Sinustoene, im Klienten erzeugt (`gong()`). Im
Labor ist ein Ton schneller verstanden als ein Satz und kommt auch an, waehrend
jemand redet.

**Anzeige.** Der Dienst schickt absolute Zeitpunkte im Zustand (`wecker`), der
Sekundenzaehler laeuft im Browser -- sonst muesste im Sekundentakt gefunkt
werden.

### Direktbefehl ohne Aktivierungswort

„Sprachaufzeichnung", „Audioaufnahme", „Audioaufzeichnung", „Tonaufnahme" oder
„Tonaufzeichnung" — jeweils mit „starten" oder „stoppen" — wirken **ohne**
vorheriges „Kiwi" und **ohne** den Gespraechsmodus zu oeffnen. Der Dienst
transkribiert bei offenem Mikrofon ohnehin jede Aeusserung und verwirft sie nur
mangels Aktivierungswort -- der Direktbefehl kostet also nichts extra.

`absicht.direktbefehl()` ist bewusst eng: das zusammengesetzte Wort muss fallen
(„Aufzeichnung" allein genuegt nicht), dazu ein Tuwort, und der Satz darf
hoechstens sechs Woerter haben. Ohne die Kuerze wuerde „wir sollten die
Sprachaufzeichnung nachher mal starten, wenn alle da sind" mitten im Gespraech
den Mitschnitt anwerfen -- und zwar ohne jede Ansprache.

**Nur `sprach…` zu kennen war zu eng.** Vier Versuche hintereinander
scheiterten, alle korrekt transkribiert: im Protokoll steht viermal „nicht
angesprochen: 'Audioaufzeichnung starten.'" Der Sprecher sagt nicht das Wort,
das im Code steht, und merkt nur, dass nichts passiert. Der Stamm deckt jetzt
`sprach|audio|ton` ab.

**Ein blosses „Aufzeichnung starten" bleibt bewusst draußen.** Im Labor wird
über die Aufzeichnung geredet, und ein Direktbefehl wirkt ohne
Aktivierungswort — das zusammengesetzte Wort ist der ganze Schutz.

### Sprechertrennung (`sprachdienst/sprecher.py`)

Laeuft NICHT im Sprachpfad, sondern in der Nachbereitung auf dem gespeicherten
Rohaudio. Faellt sie aus, entsteht das Protokoll wie bisher, nur ohne
Sprecherangabe. Abschaltbar mit `KIHIWI_SPRECHER=0`.

**Modelle** (35 MB, nicht im Repo, `./dienste.sh sprechermodelle`):
pyannote-segmentation-3.0 als ONNX plus 3D-Speaker CAM++ als Embedding, beide
aus dem sherpa-onnx-Zoo. `sherpa-onnx` laeuft auf CPU ueber onnxruntime, das
fuer Silero-VAD ohnehin schon da ist. Gemessen 17-38x Echtzeit.

**Warum whisper.cpp nicht reicht:** `--diarize` vergleicht linken und rechten
Kanal, das Jabra liefert mono. `--tinydiarize` braucht ein tdrz-Modell, das es
nur auf Englisch gibt, und markiert nur Sprecherwechsel, keine Personen.

**Gemessene Qualitaet.** Gegen die eigenen Aufnahmen ausgewertet, mit den
Ansagen des Assistenten als bekannte Wahrheit: **89-91 % der Transkriptzeilen
sauber zugeordnet**, 54 Zeilen aus sechs Sitzungen. Schwellenwert 0,7 findet
dabei meist genau zwei Cluster; 0,4 zersplittert in vier bis fuenf.

**Die Zahl ist ehrlich, aber sie misst den leichten Fall** -- eine menschliche
Stimme gegen eine synthetische. Zwei Menschen im selben Raum, an einem
Mikrofon, in unterschiedlichem Abstand, ist deutlich schwerer. Ueberlappende
Rede ist die eigentliche Grenze, nicht das Modell.

**Lieber keine Angabe als eine falsche.** `zuordnen()` entscheidet ueber die
groesste zeitliche Ueberlappung und laesst offen, wenn der beste Sprecher unter
60 % des Abschnitts deckt, zwei fast gleichauf liegen (da hat jemand
dazwischengeredet), oder der Abschnitt kuerzer als 0,7 s ist. Fuer ein "mhm"
reicht kein Stimmprofil.

**Kiwi wird nicht geraten.** Der Rekorder mischt die eigene Sprachausgabe
bewusst in die Aufnahme (`doku.mische`, sonst stuende nur die halbe
Unterhaltung im Protokoll) -- und haelt seither in der Begleitdatei fest, WANN
(`eigene_stimme_ms`). Diese Abschnitte heissen "Kiwi", nicht "Sprecher B".
Deterministisch, wie ueberall sonst auch.

### Was nicht ins Repository gehoert

Zwei Konfigurationsdateien verraten, woran gearbeitet wird, und liegen deshalb
nur lokal. Im Repository stehen `.beispiel`-Fassungen; fehlt die echte Datei,
greift automatisch die Beispieldatei, damit ein frischer Klon laeuft.

| lokal (ignoriert) | im Repo | verraet sonst |
|---|---|---|
| `vokabular.txt` | `vokabular.beispiel.txt` | Projekt- und Verfahrensnamen, Ortsnamen |
| `wissen/quellen.json` | `wissen/quellen.beispiel.json` | Adresse des privaten Hauptrepos |

Der Rueckfall steht in `konfig.VOKABULAR` und `einlesen.QUELLEN`. Geprueft mit
einem frischen Klon: er laeuft, benutzt die Beispiele und enthaelt keine
Laborinhalte.

Ebenfalls draussen und aus gleichem Grund: `aufnahmen/` (§ 201 StGB),
`wissen/index.db*` (traegt den Volltext der Quellen), `wissen/repos/`,
`recherchen/`, `zustand/`, `modelle/`.
