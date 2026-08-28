# kihiwi — Technische Beschreibung

## Die Maschine

`gx10` — ASUS Ascent GX10, GB10, aarch64, Ubuntu (Kernel 6.17-nvidia),
20 Kerne (Cortex-X925 + A725), **121 GiB Unified Memory**, 916 GB NVMe.

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

**Für kihiwi `model-switch ornith-voice` benutzen**, nicht `ornith`. Gleiches
Modell und gleicher `served-model-name`, aber `DEF_CTX=65536` und `GPU_UTIL` auf
0.55 statt der Prüfstands-Vorgaben 131072/0.85. Die 64K sind Hermes' Mindestmass;
sie kosten nichts (41,1 GiB KV-Cache, 58-fache Nebenläufigkeit). Am Kontext in
`model-switch status` sieht man, welches Profil läuft.

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

Serverraum, `<lan-praefix>` an einer FRITZ!Box, Ethernet `enP7s7` (Gigabit —
bei Einbruch zuerst `ethtool enP7s7` prüfen, ein zweipaariges Kabel hatte den
Link schon einmal auf 100 Mbit gedrückt). Stabil erreichbar über Tailnet:
`<tailnet-adresse>` / `<rechner>`.

**Direkt an der FRITZ!Box, ohne IPFire davor** — die Maschine hat eine weltweit
geroutete IPv6 ohne NAT, der Schutz hängt allein an der FRITZ!Box-Konfiguration.
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

**Suchen darf niemals schreiben** (`index.lesen()` statt `verbinden()`):
`verbinden()` legt das Schema an und nimmt dabei eine Schreibsperre.

### Rechercheaufträge (Stufe 2)

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

**Hermes braucht mindestens 64K Kontext** — deshalb steht `ornith-voice` auf
65536. Aufruf mit `hermes chat -Q` (programmatischer Modus, nur die Antwort).

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

**Achtung, naheliegender Denkfehler:** Die 70 ms sind das erste *Token*, nicht
der erste *sprechbare* Brocken. Piper braucht einen Teilsatz, also 10–25 Token.
Eine Budgetrechnung mit TTFT ist um den Faktor fünf zu optimistisch.

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

Ausführlicher in `~/.claude/projects/-home-nutzer/memory/` (Projekt-Scope `~`,
in kihiwi-Sessions nicht automatisch geladen): `gx10-zwei-modelle`,
`gx10-modell-benchmark`, `gx10-netzwerk-serverraum`, `gx10-serverraum-umzug`,
`gx10-ssh-key-only`, `gx10-ds4-opencode`, `hermes-searxng`,
`qwen38-flash-wartet`. Bei Widersprüchen sind jene die Quelle.
