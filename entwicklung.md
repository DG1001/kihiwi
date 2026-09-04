# kihiwi — Entwicklungsprotokoll

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

### Browser-Client statt Software auf dem Notebook

Für den ersten Test mit echtem Mikrofon soll das Jabra an Freds Notebook hängen.
Statt dort etwas zu installieren liefert der Dienst jetzt unter `/klient` eine
Seite, die Mikrofon und Lautsprecher des aufrufenden Rechners benutzt.

**Stolperstein:** `getUserMedia` funktioniert nur im „secure context" — über
`http://<ip>:8920` gibt der Browser das Mikrofon kommentarlos nicht frei.
**Lösung:** SSH-Portweiterleitung, dann gilt die Seite als `localhost`. Nutzt
die ohnehin bestehende SSH-Verbindung und lässt den Dienst auf `127.0.0.1`
gebunden — besser als Bindung aufweichen oder ein Zertifikat basteln.

### Problem: der Dienst brauchte zwei Minuten zum Beenden

Fiel als scheinbar hängender `dienste.sh neustart` auf, mehrfach hintereinander.
**Ursache:** `tts.sprich` und `llm.antwort_saetze` liefen als Thread, der in eine
`queue.Queue` legte, während die Schleife per `await asyncio.to_thread(q.get)`
abfragte. Hört der Verbraucher vorzeitig auf — abgebrochene Antwort, geschlossene
Verbindung — blockiert der Thread für immer auf der leeren Queue, und
`asyncio.run` wartet beim Beenden 120 Sekunden auf genau solche Threads.

**Fix:** Richtung umdrehen. Der Thread *schiebt* per `loop.call_soon_threadsafe`
in eine `asyncio.Queue`, die Schleife wartet asynchron. Der Thread läuft immer zu
Ende und kann nicht mehr hängen. Beenden dauert jetzt 17 ms.

**Warum das zählt:** Nicht nur lästig — ein Dienst, der sich nicht sauber beenden
lässt, ist auch für systemd-Units untauglich, und die stehen noch an.

### Problem: `dienste.sh` hing beim Starten — aber nur durch eine Pipe

Mehrfach lief `./dienste.sh neustart` in die Zeitüberschreitung, während
dasselbe mit Umleitung in eine Datei in Sekunden durchlief. Zwei falsche Fährten
verfolgt (stdin nicht abgeklemmt, dann `setsid` ohne `--fork`), bevor die
Diagnose kam: nachsehen, wer die Pipe offen hält.

`sed` wartete auf Eingabe, die Schreibseite hielten zwei `bash ./dienste.sh` —
beide im Zustand `do_wait`, mit whisper-server bzw. dem Gateway als **eigenem
Kind**. Das Skript war also nicht fertig, es wartete auf die Dienste.

**Ursache:** `setsid` forkt nur, wenn der Aufrufer bereits Prozessgruppenführer
ist. Im Hintergrundjob einer nicht-interaktiven Shell ist er das nicht, also
ersetzte sich `setsid` selbst und der Dienst blieb direktes Kind der Shell.
**Fix:** `setsid --fork`, das immer forkt. Der Dienst wird an init durchgereicht,
die Shell hat nichts mehr zu warten. 2 s statt Zeitüberschreitung.

**Lehre:** Der Unterschied Datei/Pipe war der entscheidende Hinweis und ich habe
ihn zweimal übergangen. Bei „hängt manchmal" lohnt es, sofort nachzusehen, wer
den Deskriptor hält, statt plausible Ursachen der Reihe nach auszuprobieren.

### Problem: Seiten kamen als Quelltext an

`respond()` aus websockets setzt `Content-Type: text/plain`. Fiel nicht auf, weil
Monitor und Client bis dahin nur mit `curl` geprüft worden waren — ein Frontend
ohne Frontend getestet. Beim Korrigieren die zweite Falle:
`Headers.__setitem__` hängt an statt zu ersetzen, ein blosses Setzen hätte zwei
Content-Type-Zeilen ergeben.

### Zum Futro-Client: Einschätzung korrigiert

Ich hatte den Futro S520 für zu schwach für einen Browser gehalten. Nach den
tatsächlichen Daten — AMD G-Series (GX-210HA/GX-212ZC), Jaguar-Kerne von 2013,
dual-core amd64 mit SSE4.2 und AVX, dazu eine GCN-Radeon — stimmt das nicht:
Chromium läuft dort, und das Compositing ist hardwarebeschleunigt.

**Der eigentliche Engpass ist der verlötete 4-GB-MLC-Flash**, nicht die
Rechenleistung: nicht tauschbar, und ein Browser schreibt permanent Cache.
Empfehlung deshalb, vom USB-Stick zu booten und den internen Flash gar nicht zu
benutzen — dann ist auch das Klonen auf mehrere Futros einfach.

**Folge:** Die Idee, statt eines Browsers eine eigene Oberfläche (LVGL o. ä.) zu
bauen, ist damit vom Tisch — ihre Begründung war die vermeintlich zu schwache
Hardware. Was bleibt, ist der Gedanke dahinter: ein festes Anzeige-Protokoll mit
wenigen Ansichtstypen statt „die KI schickt HTML". Das hält den Renderer
austauschbar, ohne dass eine Bibliothek geschrieben werden muss.

### Dokumentationspfad fertiggestellt

`sprachdienst/protokoll.py`: VAD-Zerlegung, Transkription, Korrektur,
Zusammenfassung. Auf einer synthetischen Sitzung (65 s, 11 Äußerungen) fand die
Zerlegung genau 11 Abschnitte, der Durchlauf dauerte 13 s.

**Entscheidung: Korrektur bleibt vorsichtig.** Ein erster, forscherer Prompt
("klingt es wie ein Begriff aus der Liste, ersetze es") reparierte zwar
„Vaseline" → „Baseline", machte aus „Putsch" aber „Baseline" statt „Patch" —
also eine plausibel aussehende Fälschung. Für ein Protokoll ist das der
schlimmste Fall: **ein sichtbar falsches Wort ist besser als ein glaubhaft
falsches.** Jetzt wird nur ersetzt, wenn genau ein Begriff eindeutig passt;
alles andere bleibt stehen und die Zusammenfassung führt es unter „Unklare
Stellen" auf. Im Testlauf hat das Modell genau das getan, ohne zu raten.

**Problem: Markdown lief auf eine Zeile zusammen.** `antwort_saetze` zerlegt den
Strom in Sätze — im Sprachpfad genau richtig, in einem Dokument falsch: beim
Zusammensetzen gehen Zeilenumbrüche verloren und Aufzählungen kollabieren.
**Fix:** `llm.antwort_text` gibt den Strom unverändert zurück; erzeugte Dokumente
benutzen die.

Nebenbei: Das Modell setzte trotz Anweisung gelegentlich `#`-Überschriften mitten
in die Datei und brach damit die Gliederung. Sicherheitsnetz per Regex, das
Ebene 1 und 2 auf Ebene 3 herunterstuft.

### Erste echte Aufnahmen: STT ist gut, meine Vokabelliste war das Problem

Vier Aufnahmen über das Jabra am Notebook, damit ist die seit dem Vormittag
offene Frage nach der echten Erkennungsgenauigkeit beantwortet: **sie ist gut.**
Ein langer Fachsatz über Rasterelektronenmikroskopie kam praktisch fehlerfrei an
— „Hochvakuum", „Strahlengang", „Elektronenröhre", „reflektierte Elektronen".
Der synthetische Test vom Vormittag hatte Whisper deutlich unterschätzt, weil er
in Wahrheit Piper gemessen hat.

**Aber die Korrekturstufe hat etwas erfunden:** aus „Fenstertechnologie" wurde
„Fenster-Attention-Heads". Ursache war meine Vokabelliste — sie enthielt
ML-Jargon aus meinen eigenen Testsätzen, während das Labor über
Elektronenmikroskopie spricht. Das Modell sollte gegen eine fachfremde Liste
korrigieren und griff daneben.

**Zwei Wächter, beide im Code statt im Prompt:**

1. Wortweiser Ähnlichkeitsvergleich (`difflib`, Schwelle 0,65). Gemessen an den
   echten Fällen trennt das sauber: Erfindung 0,54, echte Korrekturen 0,73–0,88.
2. Der Ersatz muss in `vokabular.txt` stehen. Ohne diesen zweiten Wächter machte
   das Modell aus einem **korrekten** „Hochvakuum" ein falsches „Hochvacuum" —
   ähnlich genug für Wächter 1, aber nirgends belegt.

Einfügungen und Löschungen werden grundsätzlich verworfen: die Korrektur darf
Wörter ersetzen, nichts hinzufügen und nichts weglassen.

**Die eigentliche Lehre:** Mit einer passenden Vokabelliste kamen
„Rasterelektronenmikroskop" und „Siliziumnitrid-Fenster" direkt aus der
Spracherkennung — die Korrekturstufe hatte gar nichts mehr zu tun. Der Hebel
sitzt im `initial_prompt`, nicht in der Nachbearbeitung. `vokabular.txt` ist
deshalb aus `testaudio/` ins Projektwurzelverzeichnis gewandert: es ist ein
fachliches Artefakt, kein Testmaterial.

### „IO-Rasterelektronenmikroskop" aufgeklärt

Fred wies darauf hin, dass „IO" eine Fehlerkennung war — er hatte das so nicht
gesagt. Drei Läufe ergaben drei Varianten desselben Lauts: „IO", „Yo", „Jeove".
Letzteres klang nach **JEOL**, einem REM-Hersteller.

**Prüfung per Kandidatentest:** JEOL, Zeiss, Hitachi und Tescan einzeln in den
Prompt. Nur JEOL rastete ein, die anderen drei ließen die Stelle unverändert —
damit war ausgeschlossen, dass der Prompt die Antwort bloß erzwingt. Fred hat
bestätigt: es ist ein JEOL.

Die Methode ist übernommen worden: Kandidaten einzeln testen, und die
**Nicht**-Treffer sind der eigentliche Beleg.

### Aktivierungswort „Kiwi" und Werkzeugsteuerung

Mikrofon dauerhaft offen, Ansprache per Wort, Aufzeichnung über einen
Werkzeugaufruf des Modells.

**Entscheidung: kein Wake-Word-Modell.** Die vortrainierten sind englisch, ein
deutsches Wort bräuchte eigenes Training. Stattdessen wird der Text benutzt, den
die Spracherkennung ohnehin liefert — die lexikalische Endpoint-Prüfung
transkribiert bereits mitten im Satz, das Erkennen kostet also fast nichts.

Fred schlug „Kiwi" statt „Hiwi" vor (aus KI-Hiwi). Phonetisch die bessere Wahl:
zwei klare Silben, fällt im Labor sonst nicht.

**Falle 1: „Der Hiwi hat das gemacht" galt als Ansprache.** Mit Schwelle 0,75
traf „der hiwi" auf den Kandidaten „hey hiwi". Behoben durch Wortzahl-Abgleich
und Schwelle 0,80.

**Falle 2: „Kivi" wurde verfehlt.** Die Schwelle zu senken wäre hier gefährlich —
„Hiwi" und „Kiwi" trennt ein Buchstabe (0,75), und im Hochschulalltag fällt
„Hiwi" ständig. Stattdessen Schreibvarianten aufgezählt.

**Falle 3: „TV stoppe die Aufzeichnung".** Das Aktivierungswort stand nicht im
Vokabular-Prompt, also hatte Whisper keinen Grund, es zu bevorzugen — derselbe
Mechanismus wie bei JEOL. Jetzt wird es in `stt._vokabular` **immer**
angehängt, unabhängig von `vokabular.txt`.

**Der ernsteste Fund: das Modell behauptete Handlungen.** Beim Stoppen sagte es
„die Aufzeichnung ist jetzt gestoppt", **ohne das Werkzeug aufzurufen** — der
Zustand blieb an. Bei einer Funktion mit § 201 im Rücken ist das inakzeptabel.

Ein verschärfter Prompt half isoliert (5/5), im echten Ablauf mit
Gesprächsverlauf aber nicht. Deshalb zwei Netze im Code: Erkennung der
Behauptung per Regex, dann **Nachfassen mit erzwungenem Werkzeugaufruf** über
vLLMs `tool_choice`. Damit wird die Handlung nachgeholt statt bloß widersprochen.
Im Durchlauf greift genau das: das Modell behauptet, der Dienst holt den Aufruf
nach, die Aufzeichnung stoppt wirklich.

**Allgemeiner:** Bei folgenreichen Werkzeugen nicht darauf bauen, dass das Modell
den Aufruf macht — prüfen, ob er stattgefunden hat, und ihn andernfalls erzwingen.

### Umbenannt: aihiwi → kihiwi

Fred fand `kihiwi` stimmiger (KI-Hiwi), passend auch zum Aktivierungswort
„Kiwi". Umbenannt wurde vollständig, nicht nur der Ordner:

- Verzeichnis `~/Developer/github.com/aihiwi` → `kihiwi` (Git-Historie zieht mit)
- alle 21 Textstellen in Code und Dokumentation
- die Umgebungsvariablen `AIHIWI_*` → `KIHIWI_*`
- der fest verdrahtete Pfad in `testaudio/lauf.sh`
- die acht venv-Skripte mit absolutem Shebang und `pyvenv.cfg`

**Der venv war die einzige echte Stolperstelle.** `.venv/bin/python` ist ein
Symlink auf das System-Python und überlebt einen Umzug, aber die
Konsolenskripte (`pip`, `piper`, `websockets`, …) tragen den alten Pfad im
Shebang. Ohne `sed` darüber wären sie stumm kaputtgegangen.

**Nebenwirkung ausserhalb des Repos:** Claude Code legt seinen Projektstand
unter einem vom Pfad abgeleiteten Verzeichnis ab. Die alte Sitzungshistorie
bleibt unter dem alten Projektpfad (noch mit „aihiwi“) liegen; neue Sitzungen
bekommen ein neues Verzeichnis. Die dauerhaften Notizen sind davon nicht
betroffen — sie liegen bewusst im Home-Scope.

### Gesprächsmodus und Beimischung der Antworten

Fred wollte zweierlei: „Kiwi" nur zum Eröffnen, danach Rückfragen ohne
Aktivierungswort bis zu „Danke, Kiwi" — und die Zwiegespräche in der
Aufzeichnung.

**Beim Zweiten war die Hälfte schon erfüllt und die andere eine echte Lücke.**
Die Fragen an Kiwi standen bereits im Mitschnitt, weil der Rekorder unabhängig
von der Gesprächsphase läuft. Kiwis Antworten fehlten: sie gehen als TTS zum
Lautsprecher, und genau die Echounterdrückung, die Barge-In erst möglich macht,
hält sie aus dem Mikrofonsignal heraus. Gelöst durch Beimischung ins
Mikrofonsignal — **gemischt, nicht angehängt**, sonst verschieben sich alle
Zeitstempel.

**Der Testclient hat dabei einen falschen Eindruck erzeugt.** Weil er zwischen
den Äußerungen pausiert, staute sich die Beimischung und landete später über der
nächsten Frage — im Transkript fehlte eine Frage. Mit durchgehendem Strom, wie
ihn ein echter Client liefert, stimmt die Reihenfolge. Als Sicherung verwirft
`MAX_STAU_S` den Überhang, wenn der Strom stockt.

**Zeitgrenze für das Gespräch (45 s)** ist bewusst gesetzt: ohne sie reagierte
der Assistent im Labor auf jedes Gespräch, sobald jemand vergisst, sich zu
verabschieden.

**Nebenbefund, unbehoben:** Das Modell erfindet Fachaussagen. Auf die Frage nach
dem Siliziumnitrid-Fenster kam „durchlässig für Röntgenstrahlen" und einmal
1 µm, einmal 3 µm Dicke. Für einen Laborassistenten ist das die eigentliche
offene Baustelle — sie braucht Anbindung an echte Dokumente, nicht mehr
Modellgröße.

### Wissensanbindung: Unterlagen und Websuche

Quellen (lokal, Git, Nextcloud) in einen FTS5-Index, dazu SearXNG als Websuche,
beides als Werkzeuge am Assistenten.

**Entscheidung gegen Embeddings.** SQLite FTS5 ist in Python eingebaut, braucht
kein Modell, keinen GPU-Speicher und keinen Dienst. Für Fachtexte ist
Stichwortsuche stark. Semantik wäre der nächste Schritt, nicht der erste.

**Der Nutzen ist belegt.** Im direkten Test beantwortet das Modell Fragen aus
den Unterlagen mit Zahlen und Quelle — und sagt bei „Wie dick ist unser
Siliziumnitrid-Fenster?" jetzt „keine belastbare Angabe gefunden", statt wie
vorher 1 oder 3 Mikrometer zu erfinden. Genau dafür war das gedacht.

**Ein langer Fehlschlag unterwegs.** Nach dem Einbau blieb der Dienst stumm
stehen: keine Antwort, keine Fehlermeldung, die Anfrage nicht einmal im
vLLM-Protokoll. Die Diagnose hat lange gedauert, und ich habe dabei mehrere
falsche Fährten verfolgt (Imports, SQLite-Sperre, die Endpoint-Prüfung, den
Thread-Pool). Erst eine saubere Halbierung brachte es: **mit einem Werkzeug lief
es, mit dreien nicht** — vLLMs Streaming-Parser für Werkzeugaufrufe. Umgangen,
indem die Werkzeugrunde ungestreamt läuft.

**Lehre daraus:** Ich hätte viel früher halbieren sollen, statt Hypothesen
einzeln durchzuprobieren. Und: `_strom` verschluckte Ausnahmen stillschweigend
(`except ...: pass`) — dieser blinde Fleck hat die Suche zusätzlich verlängert.
Jetzt wird protokolliert.

**Zwei Mängel bleiben offen**, siehe technisch.md: das Modell ruft die Suche im
Sprachpfad oft nicht auf (der kurze Sprechstil verdrängt den Werkzeugaufruf),
und das dagegen eingebaute Nachfassen greift aus ungeklärtem Grund nicht.

### Rechercheaufträge an Hermes

Freds Idee, aufwendige Anfragen an einen anderen Agenten zu geben, ist umgesetzt
— aber als **zweite Stufe**, nicht als Weiterleitung. Einschrittiges
Nachschlagen bleibt inline bei 3 s; nur Vergleiche und mehrschrittige Recherche
gehen an Hermes.

**Der Test hat meinen Vorbehalt gegen Hermes entkräftet.** Er recherchierte in
18,7 s über drei Werkzeugaufrufe, nannte konkrete Zahlen und Quellen und erfand
nichts — von der befürchteten Konfabulation keine Spur. Voraussetzung war
CTX=65536, weil er unter 64K den Dienst verweigert.

**Asynchron ist der Kern.** 31–40 s sind für einen Sprachdialog zu lang, für
einen Auftrag mit Rückmeldung völlig in Ordnung: „Ich melde mich" — später kommt
das Ergebnis.

**Ein Entwurfsfehler unterwegs:** Die Rückmeldung hing zuerst an der Sitzung, die
den Auftrag gab. Im Protokoll stand dann „Ergebnis nur abgelegt, kein Client
verbunden". Im Labor soll das Ergebnis an den gehen, der **gerade** da ist —
jetzt wird an alle verbundenen Clients verteilt.

**Werkzeugbeschreibungen mussten geschärft werden.** Zuerst griff das Modell zur
synchronen Websuche und antwortete nach 9,5 s selbst, statt den Auftrag
abzugeben. Erst die klare Abgrenzung — eine Tatsache gegen mehrschrittige
Recherche — brachte es dazu, `rechercheauftrag` zu wählen.

### Der Assistent sagt an, welchen Weg er nimmt

Fred hatte eine Recherche erwartet und gewartet — tatsächlich hatte Kiwi seine
Frage („recherchiere in unseren Unterlagen…") korrekt als Dokumentensuche
behandelt und längst geantwortet. Richtig entschieden, aber von aussen nicht
unterscheidbar.

Jetzt sagt der Dienst den Weg an, **bevor** das Werkzeug läuft. Fest im Code,
nicht per Prompt: das Modell hält sich nicht zuverlässig daran, und diese Ansage
ist genau das, worauf der Nutzer seine Erwartung stützt. Nebeneffekt: der erste
Ton kommt nach 2 s statt nach 4.

Läuft eine Recherche, wird zusätzlich angesagt, dass Antworten gerade länger
dauern. Das ist keine Höflichkeit, sondern die vorhergesagte Modellkonkurrenz —
im Test brauchte eine Dokumentenfrage während einer laufenden Recherche
merklich länger.

**Die Änderung hat einen Fehler in meinem Testclient offengelegt:** er hörte beim
ersten `ton_ende` auf und schnitt damit die eigentliche Antwort ab, die nach der
Ansage folgt. Er wartet jetzt auf Ruhe statt auf das erste Tonende.

### Intent-Router — und der eigentliche Grund für die Werkzeug-Aussetzer

Fred fragte, ob Ornith generell zu schwach ist. Die Messung sagt: nein, aber an
einer Stelle schon. Mit vier Werkzeugen und vollem Prompt rief es kein Werkzeug,
mit einem und kurzem Prompt zuverlässig — also wurde ein Regel-Router gebaut,
der vorher entscheidet und nur die passenden Werkzeuge weiterreicht.

**Der Router allein half nicht.** Beim Nachmessen fiel auf: auch mit einem
einzigen Werkzeug blieb es bei 0/3, solange der Sprechstil-Prompt dabei war.
Erst ohne ihn wurden es 3/3. **Nicht die Werkzeugzahl war die Ursache, sondern
`konfig.SYSTEM_PROMPT`** — „antworte kurz, höchstens zwei Sätze" bringt das
Modell dazu, zu antworten statt zu handeln. Es befolgt die Anweisung; sie ist
nur an dieser Stelle falsch.

Behoben durch zwei getrennte Prompts: sachlich für die Werkzeugrunde,
Sprechstil für die Schlussantwort. Die Runden waren architektonisch längst
getrennt, nur der Prompt war derselbe.

**Lehre:** Bevor man einem kleinen Modell mangelnde Fähigkeit unterstellt, lohnt
der Blick darauf, ob der eigene Prompt ihm das Gegenteil aufträgt. Zwei
Anweisungen, die sich widersprechen — „sei kurz" und „ruf ein Werkzeug" — löst
es nicht zu unseren Gunsten auf.

Nebenbei: Hermes scheiterte bei 65536 Kontext mit „Context length exceeded: max
compression attempts reached" und reichte die Fehlermeldung als
Rechercheergebnis durch. 64K ist seine Untergrenze, nicht sein Arbeitsbereich —
jetzt 131072, und solche Meldungen werden als Fehler erkannt statt weitergegeben.

### Zwei überlappende Sprachausgaben

Beim Rechercheergebnis sprach Kiwi doppelt. Ursache war meine eigene Korrektur
von vorhin: Gegen die Stille nach einem angehaltenen AudioContext hatte ich eine
Driftsicherung eingebaut, die bei mehr als 8 s Vorlauf zurücksetzt.

Die Regel war zu grob. TTS-Stücke kommen 25-mal schneller als Echtzeit, eine
lange Antwort läuft zu Recht darüber hinaus — die Sicherung hielt das für einen
Hänger und terminierte den Rest über das schon Geplante. Bei kurzen Antworten
fiel es nie auf, weil sie die Grenze nie erreichen; erst das Rechercheergebnis
mit seinen langen Sätzen stolperte darüber.

Jetzt wird nur zurückgesetzt, wenn die Warteschlange leergelaufen ist oder der
Context angehalten war. Die Stille-Behebung bleibt damit erhalten.

**Lehre:** Eine Schwelle auf eine Größe zu legen, die im Normalbetrieb
schwankt (Pufferlänge), ist fragil. Besser auf das Ereignis prüfen, das man
wirklich meint — hier: war der Context angehalten.

### Transkripte über Kiwi abrufbar

Fred fragte, ob er sein Transkript über Kiwi abrufen kann. Der eleganteste Weg
war, `aufnahmen/` schlicht als Wissensquelle einzutragen — dann findet die
vorhandene Dokumentensuche die Protokolle mit. „Was habe ich vorhin über das
Rasterelektronenmikroskop gesagt?" wird jetzt daraus beantwortet.

**Zeitstempel absolut, nicht nur relativ** (Freds Hinweis): `[00:01]` sagt nicht,
WANN etwas gesagt wurde. Das Protokoll trägt jetzt beides — Ortszeit für die
Orientierung, Versatz zum Wiederfinden im Audio.

**Zwei Fehler dabei behoben.** Erstens kam die Antwort nach einem Werkzeugaufruf
als Aufzählung: sie stammte aus der Werkzeugrunde, und die trägt bewusst keinen
Sprechstil. Zweitens lieferte die neu erzeugte Schlussantwort **gar nichts** —
ohne Fehler und ohne Protokollzeile —, weil die Nachrichtenfolge noch die
Werkzeug-Strukturen enthielt, der Aufruf aber keine `tools` mehr mitgab. Jetzt
wird für die Antwort eine saubere Folge gebaut: Frage, Befunde als Text, fertig.

**Und wieder der Testclient:** er brach nach 4 s Ruhe ab, die Antwort brauchte
nach der Wegansage aber 5,3 s. Das sah zweimal wie ein Dienstfehler aus. Grenze
auf 10 s.

### Automatisch transkribieren beim Stoppen

Bisher musste jemand `./dienste.sh protokoll` aufrufen, sonst blieb die
Aufzeichnung unauffindbar. Jetzt stösst das Stoppen die Nachbereitung selbst an:
transkribieren, Protokoll bauen, neu indizieren, Bescheid sagen.

Im Hintergrund, weil es je nach Länge Minuten dauert und der Assistent
ansprechbar bleiben soll; höchstens eine gleichzeitig, weil sie sich STT und
Modell mit dem Sprachpfad teilt.

**Alle Wege zum Stoppen** — Werkzeugaufruf, Knopf, Mikrofon aus,
Verbindungsabbruch — laufen jetzt über `aufzeichnung_stoppen()`. Vorher rief
jeder Weg `rek.stop()` einzeln auf; ein neuer Weg hätte das Transkribieren
stillschweigend übersprungen.

Gemessen: Stopp bei +5,4 s, Protokoll fertig bei +8,8 s, danach über
`dokumente_suchen` auffindbar.

### Die Aufzeichnung ließ sich nicht mehr steuern

Fred meldete: „Ich kann keine Aufzeichnung starten, das liegt außerhalb meiner
Möglichkeiten" — auf jeden Befehl.

**Die Diagnose war lehrreich.** Der Router arbeitete richtig, das Werkzeug wurde
gerufen, das Protokoll zeigte `aufzeichnung{'an': True} -> Aufzeichnung läuft
jetzt.` Die Absage entstand erst in der Schlussantwort: sie bekommt die
Werkzeugergebnisse als Text, erkannte sie aber nicht als eigene Handlung.

Ausgelöst hatte es eine Statusfrage. „Läuft die Aufzeichnung?" ging an WISSEN,
wo es kein Aufzeichnungswerkzeug gibt — die Antwort „kann ich nicht prüfen"
landete im Gesprächsverlauf, und ab da wiederholte das Modell sie auch bei
echten Befehlen. **Eine einzelne falsche Antwort vergiftete alle folgenden.**

**Behoben, indem das Modell aus dem Weg genommen wurde.** Befehle und
Statusfragen zur Aufzeichnung beantwortet der Dienst jetzt selbst: Router
erkennt die Absicht, `will_aendern()` unterscheidet Befehl von Frage,
`soll_anschalten()` an von aus. Für zwei mögliche Werte trägt ein Sprachmodell
nichts bei.

**Lehre:** Wo eine Handlung deterministisch ist, gehört sie nicht ins Modell.
Und ein Gesprächsverlauf verstärkt Fehler — eine falsche Antwort bleibt stehen
und wird zur Vorlage für die nächste.

### Kanalaufteilung: Blättern gehört auf den Bildschirm

Fred stellte den Ansatz in Frage — ob wir vom Sprachkanal nicht zu viel
verlangen. Das trifft zu, und das Muster des Abends belegt es: **alles, was
deterministisch gemacht wurde, funktioniert; alles, was dem Modell überlassen
blieb, war fragil.** Aufzeichnung steuern, Statusfrage, Protokoll zeigen — jedes
Mal ging es erst, als das Modell herausgenommen wurde.

Der Grund ist nicht die Modellgröße, sondern die Aufgabe. Sprache taugt für
Befehle mit den Händen an der Arbeit, für kurze Fragen und fürs Diktieren. Für
Blättern, Vergleichen und Nachlesen taugt sie nicht — dafür gibt es einen
Bildschirm.

Der Client hat deshalb eine **Ablage**: Protokolle und Recherchen als
anklickbare Liste, neueste zuerst. Weniger Code als die Sprachvariante und nicht
missverständlich.

Dass die Sprachvariante zuerst entstand, war Trägheit statt Entwurf: Der
Sprachpfad war da, also wurde alles hineingelegt.

### Auslösewörter statt Überredung

Fred schlug vor, „Internetrecherche" wie ein zweites Aktivierungswort zu
behandeln — und das ist genau die Verallgemeinerung des Musters, das an diesem
Projekt durchgehend funktioniert: **wo die Handlung eindeutig ist, entscheidet
der Dienst, nicht das Modell.**

Anlass war wieder eine Weigerung: „Ich kann leider keine Internetrecherche
durchführen", obwohl der Router `Absicht recherche -> 1 Werkzeug` gemeldet
hatte. Und wie zuvor bei der Aufzeichnung vergiftete die erste Absage den
Verlauf, sodass sie sich wiederholte.

Umgesetzt als Tabelle mit drei Einträgen (Internet-, Dokumenten-, Hermes-
Durchreichung), erweiterbar in einer Zeile. Dazu zwei Netze: der Verlauf wird
für die Werkzeugrunde von Absagen befreit, und ein vom Router vorgesehenes
Werkzeug wird erzwungen, wenn die Antwort trotzdem eine Absage ist.

**Ausloeser für Hermes ist `Hermesaufgabe`, nicht `Hermes`.** Die Durchreichung
gibt einer gesprochenen Anweisung Zugriff auf Dateien, Browser und
Codeausführung, bei offenem Mikrofon — und über den Agenten wird im Labor
geredet. Ein zusammengesetztes Wort fällt nicht versehentlich; das ist billiger
und verlässlicher als eine Rückfrage. Der Dienst sagt zusätzlich an, was er
weitergibt.

### Internetsuche und Internetrecherche trennen

Fred fragte, ob die Internetsuche immer über Hermes läuft. Tut sie nicht: es gibt
den direkten Weg über SearXNG (1–3 s, sofortige Antwort, sieht nur
Trefferschnipsel) und den über Hermes (30–40 s, kann Suchen verketten und Seiten
lesen).

Die Trennung ist sinnvoll — eine einzelne Tatsache über Hermes zu holen dauert
vierzigmal so lange und bremst dabei die Sprachantworten. Sie war aber unsichtbar,
und der Router musste raten. Jetzt entscheidet das gesprochene Wort:
`Internetsuche` für den schnellen Weg, `Internetrecherche` für den gründlichen.

Gemessen: „Internetsuche Rasterelektronenmikroskop-Auflösung" → 847 ms bis zum
ersten Ton, mit Quellenhinweis.

### Auslösewörter müssen zusammengesetzt sein

Fred stellte auch „Hilfe" in Frage — zu Recht, und aus demselben Grund wie bei
„Hermes": ein Wort, das im Labor natürlich vorkommt, braucht Wächter, und
Wächter sind Näherungen. „Ich brauche Hilfe beim Mikroskop" liess sich mit einer
Stellungsregel abfangen, aber die kostete schon zwei Anläufe (erst in Zeichen
statt in Wörtern gerechnet, wodurch ein vorangestelltes „Kiwi," den Treffer
ausschloss).

Jetzt `Kiwihilfe` — zusammengesetzt, unmöglich versehentlich, und die Sonderregel
ist ersatzlos entfallen. Einzige Ausnahme: die alleinstehende Äusserung „Hilfe",
weil nach dem Abschneiden des Aktivierungsworts aus „Kiwi, Hilfe" genau das
übrig bleibt.

**Als Regel für künftige Auslöser:** zusammengesetzt und im Fachgespräch nicht
vorkommend. Das ist billiger als jede Erkennungsheuristik.

### Nie auf das blosse Aktivierungswort abschneiden

Der Satz „Kiwi, was kannst du eigentlich?" wurde nach „Kiwi?" zerschnitten — die
lexikalische Prüfung hielt das für vollständig, was grammatisch stimmt. Die
zweite Hälfte kam dann ohne Aktivierungswort an und wurde verworfen. Das
untergrub Auslösewörter und Ansprache gleichermassen.

Behoben in `turn.py`: solange nur das Aktivierungswort dasteht, liefert die
Prüfung 0 und es wird weitergehört. Wer nur ruft und wartet, bekommt die
Quittung über die Decke.

Fred beschrieb dabei sein tatsächliches Bedienmuster: erst „Kiwi" sagen, auf
„Ja?" warten, dann sprechen. Das ist der verlässlichere Weg und funktioniert
durchgehend — verifiziert: Quittung nach 2,2 s, die folgende Anweisung wird
ohne erneutes Aktivierungswort angenommen. Es steht jetzt so in fachlich.md,
weil es die Bedienung erklärt, die tatsächlich benutzt wird.

### Deutsche Komposita aufbrechen

„Internetsuche Rasterelektronenmikroskopauflösung" kam mit null Treffern zurück
— die Erkennung hatte zwei Wörter zu einem verschmolzen, und danach findet
niemand etwas. Typisch deutsch und deshalb wiederkehrend.

Zerlegt wird an den **bekannten Fachbegriffen** statt mit einem Wörterbuch: kein
zusätzliches Modell, keine Falschtrennungen, und die Vokabelliste ist ohnehin
gepflegt. Grundbegriffe wie „Fenster" und „Dicke" wurden dafür einzeln
aufgenommen; sie dienen als Trennstellen.

**Ein Denkfehler unterwegs:** Zuerst hängte ich die Bestandteile an das
Kompositum an. Für FTS5 ist das richtig (ODER-Verknüpfung), für eine
Suchmaschine falsch (UND) — das unauffindbare Wort blieb drin und machte die
Anfrage weiterhin leer. Jetzt ersetzt die Zerlegung dort das Original.

Ergebnis: „Rasterelektronenmikroskopauflösung" findet Wikipedia,
„Siliziumnitridfensterdicke" findet plano-em.de mit den Membranstärken.

### Aufnahmen verdichten, Stimme verbessern

Zwei Fragen von Fred, beide mit Messungen beantwortet.

**Platz:** 110 MB je Stunde, also 107 GB im Jahr bei vier Stunden täglich —
gegen 415 GB frei reicht das ein paar Jahre, wächst aber stetig. Opus mit
24 kbit/s macht daraus 10,5 MB je Stunde. Entscheidend war die Gegenprobe: das
Transkript aus der verdichteten Datei ist mit dem aus dem Original **identisch**,
Wort für Wort. Verdichtet wird erst nach der Transkription; `lies_audio()` liest
Opus über ffmpeg, eine spätere Neu-Transkription funktioniert also weiter.

Eine erste Messung sah nach Qualitätsverlust aus — die Transkripte wichen ab. Das
war Whisper-Streuung auf einer langen Datei, nicht die Kompression; auf einer
kurzen Datei stimmten sie überein.

**Stimme:** `thorsten-high` klingt besser, kostet aber 343 statt 86 ms bis zum
ersten Ton. Statt zu wählen wurden die **festen Sätze vorgerendert** — 15 stehende
Ansagen einmal beim Start, danach kosten sie null. Da fast jede Antwort mit einer
solchen Ansage beginnt, kommt der erste Ton jetzt **früher** als vorher: 416 ms
gegen 847 ms. Bessere Stimme und kürzere Latenz zugleich.

### „Internet-Suche" traf nicht, und der Index war verschmutzt

Zwei Fehler in einer Äusserung. „Internet-Suche zum Thema
Rasterelektronenmikroskopie" landete bei der Dokumentensuche statt im Netz: die
Auslöser kannten nur die zusammengeschriebene Form, die Erkennung schreibt
Komposita aber variabel. Jetzt gelten alle drei Schreibweisen.

Und die Antwort darauf war seltsam („Die Recherche stammt von einem Agenten und
ist ungeprüft"), weil die **Rechercheergebnisse noch im Wissensindex standen**.
Sie waren zwar aus der Quelle genommen worden, aber der Index entfernte nie
etwas — was einmal drin war, blieb drin. Abgeleitetes Material konkurrierte so
mit den Primärquellen und gewann bei Zahlenfragen sogar, weil seine
Vorsichtsformeln gut auf solche Fragen passen.

`index.aufraeumen()` löscht jetzt nach jedem Einlesen, was nicht mehr gesehen
wurde. Die Quelle `kihiwi` schrumpfte damit von 16 auf 5 Dokumente.

### Stimme: viel gemessen, nichts gewonnen

Fred wollte die Sprachausgabe verbessern. Ergebnis nach dem Vergleich aller
sieben deutschen Stimmen: **kein hörbarer Gewinn.** `thorsten-high` ist derselbe
Sprecher bei gleicher Abtastrate und kostet 305 statt 83 ms je Satz;
`thorsten_emotional` (neutral) ist mit 80 ms sogar am schnellsten, klingt aber
ebenso ununterscheidbar. Wieder zurück auf `thorsten-medium` — gleiche
Geschwindigkeit, ein bewegliches Teil weniger.

**Der eigentliche Gewinn kam nicht von der Stimme, sondern vom Vorrat:** die 15
festen Ansagen einmal beim Start zu rendern drückte den ersten Ton von 847 auf
319 ms, unabhängig vom Modell. Das bleibt.

Zwei Fehler unterwegs, beide erst durch Nachmessen sichtbar: die neue Stimme lief
gar nicht (konfig.py war nach dem Neustart nochmal angefasst worden — der Dienst
protokolliert die geladene Stimme jetzt), und die Hörproben kamen im Browser
verfälscht an, weil ich Rohbytes durch eine Textschnittstelle geschickt hatte.

### Datum und Uhrzeit

Fred merkte an, dass der Assistent das Datum nicht kennt, und vermutete ein
Werkzeug. Nach diesem Tag lag die andere Antwort näher: **in den Prompt damit.**
Ein Werkzeug wäre wieder etwas, das das Modell aufrufen kann oder eben nicht —
ohne Aufruf erfindet es die Zeit. Reine Zeitfragen beantwortet der Dienst
zusätzlich direkt, weil er sonst erst in den Unterlagen und dann im Netz suchte.

### Satzteiler trennte Ordnungszahlen

Fred meldete „Heute ist Freitag, der 28." / „August 2026." als zwei Ausgaben —
und fragte zu Recht, warum überhaupt geschnitten wird. Antwort: damit die
Sprachausgabe beginnt, während das Modell noch schreibt; feste Antworten werden
nicht geschnitten.

Der Fehler war `(?<=[.!?])\s+` — „28. August" sieht damit wie ein Satzende aus.
Der erste Korrekturversuch `(?<![0-9])(?<=[.!?])\s+` griff nicht, weil **beide
Rückblicke dieselbe Stelle prüfen**: hinter dem Punkt steht kein Ziffer, sondern
der Punkt selbst. Richtig ist ein Rückblick über zwei Zeichen: `(?<![0-9]\.)`.

Freds konkreter Fall war ohnehin schon behoben — die Zeit-Absicht antwortet
seither ohne Modell und damit ohne Teiler. Der Fehler betraf aber jede
Modellantwort mit einem Datum.

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

## Zahlen in der Sprachausgabe ausschreiben

Nach dem Satzteiler-Fix kam die Datumsantwort zwar als ein Stueck, wurde aber
falsch gesprochen: die Ziffern von `28.08.2026` einzeln samt Punkten, und
`12:38 Uhr` als "zwoelf Uhr achtunddreissig Uhr".

Kein Fehler von Piper, sondern der Textform: eSpeak vokalisiert den Doppelpunkt
bereits als "Uhr", das zusaetzliche Wort im Text verdoppelte es.

`sprachdienst/zahlwort.py` schreibt Datums- und Zeitangaben aus, angehaengt an
`_sprechbar()`. Bewusst nur diese beiden Muster -- alles auszuschreiben machte
Messwerte unleserlich. Jahreszahlen bekamen eine eigene Regel
(neunzehnhundert... statt eintausendneunhundert...).

Der Browser bleibt bei Ziffern: `_sprechbar()` liegt allein im Sprechpfad,
`melden()` schickt den Originaltext an Websocket und Verlauf. Auf den Hinweis
"sonst kann man das im Protokoll im Browser kaum lesen" nachgeprueft und
bestaetigt -- die Trennung stand schon.

## Repo-Aktualisierung ueber die Stimme

Frage war, ob Kiwi das Hauptrepo aktualisieren kann. Konnte er -- aber nur ueber
die Kommandozeile: `einlesen.git()` zieht bei jedem Indexlauf per
`git pull --ff-only`. Es fehlte der Weg ueber die Stimme.

Ausloesewort „Wissensabgleich", nach demselben Muster wie Hermesaufgabe:
zusammengesetzt, damit es nicht versehentlich faellt. Netzverkehr und ein
Schreiblauf ueber den Index sollen nicht nebenbei passieren.

Drei Dinge fielen beim Bauen auf:

1. **Kein WAL.** Ein Indexlauf haette waehrend des Abgleichs jede Suche
   gesperrt (`lesen()` wartet 5 s). Umgestellt -- und dabei gemerkt, dass
   `.gitignore` nur `wissen/index.db` fuehrte. `index.db-wal` traegt denselben
   privaten Volltext; das Muster heisst jetzt `wissen/index.db*`. Dieselbe
   Leckklasse, die schon einmal ein `filter-branch` gekostet hat.

2. **Die Bilanz log.** Zum Testen den Klon einen Commit zurueckgesetzt: der Pull
   holte zwei Commits, gesprochen wurde trotzdem „alles war schon auf dem
   neuesten Stand" -- weil kein indiziertes Dokument sich geaendert hatte und die
   Bilanz nur an den Dokumentzahlen hing. Haengt jetzt an den Notizen.

3. **`hilfe_zeilen()` hatte eine zweite Liste.** Der Kommentar versprach, die
   Hilfe werde aus der Tabelle erzeugt „damit sie nicht veraltet" -- darunter
   stand eine handgepflegte `reihe`, und „Wissensabgleich" fehlte prompt. Jetzt
   wirklich abgeleitet.

Offen und bewusst nicht gebaut: die **Schreibrichtung**. Kiwi holt Staende, legt
aber nichts ins Repo. Nextcloud ist aus demselben Grund ausdruecklich nur
lesend.

## Browseroberflaeche: Gespraech links, Buehne rechts

Anlass war ein Hermesauftrag, dessen Verlauf so aussah:

    Assistent   Die Recherche ist fertig. ... Das Ausfuehrliche steht auf dem Monitor.
    Assistent   Rechercheergebnis (126s) zu ... [zwei Bildschirmseiten]
    Assistent   Ich gebe an Hermes weiter: ...
    Du          Hermes-Aufgabe. Erstelle eine Uebersichtsseite ...

Zwei Fehler auf einmal. Die Reihenfolge war umgekehrt -- `zeile()` benutzte
`prepend`, die Antwort stand ueber der Frage. Und das vollstaendige Ergebnis lag
mitten im Verlauf, obwohl die gesprochene Kurzfassung direkt darueber sagte, es
stehe auf dem Monitor. Einen Monitor in diesem Sinn gab es nicht: `hilfe` ging
in eine Inhaltsflaeche, `recherche` und `protokoll` aber in den Chat.

Jetzt zwei Spalten: links der Verlauf (chronologisch, unten angehaengt), rechts
die Buehne fuer alles Ausfuehrliche. Im Verlauf bleibt ein anklickbarer Merker
stehen, der den Inhalt zurueckholt.

Nachgeprueft mit Chromium headless in 1600x1000 und 760x1100. Dabei noch
gefunden und behoben: doppelte Leerraeume um Ueberschriften (die `<br>` aus den
Markdown-Leerzeilen kamen zu den Block-Abstaenden hinzu), ein Merker, der ueber
drei Zeilen lief, und die leere Verlaufsspalte ohne jeden Hinweis.

Nebenbei zwei doppelte IDs entfernt: `m-transkript` stand in `monitor.html`
zweimal, `b-leer` waere im neuen Klienten zweimal entstanden.

Die Schreibrichtung ins Repo bleibt weiter aus -- auf Nachfrage bestaetigt,
„nur lesen reicht fuers erste".

## Timer und Erinnerungen

Gebaut nach dem Muster der Aufzeichnungssteuerung: der Dienst parst die
Zeitangabe selbst, das Modell kommt nicht vor.

Gepruefit wurde mit einer Sprechprobe -- Piper spricht den Satz, das Audio geht
als PCM in den Dienst, die ganze Kette von der Erkennung bis zum Klingeln
laeuft durch. Das hat drei Fehler gefunden, die eine reine Modulprobe nicht
gezeigt haette:

1. **„Time" statt „Timer".** Whisper schrieb „starte ein Time fuer 30
   Sekunden", damit griff die Erkennung nicht und die Frage landete bei der
   Dokumentensuche. „Timer", „Wecker" und „Erinnerung" stehen jetzt im
   `vokabular.txt`, und das Muster akzeptiert „time" zusaetzlich.

2. **„erinnert" statt „erinner".** Das Einleitungsmuster hatte feste Endungen
   (`erinnere?`), schnitt bei „Kiwi erinnert mich" nur „erinner" weg und liess
   das „t" stehen. Kiwi sagte daraufhin „erinnere ich dich daran, t mich daran,
   die Pumpe abzuschalten". Die Verben stehen jetzt mit `\w*`.

3. **Verb am Satzende.** Aus dem laufenden Betrieb gemeldet (Fred testete
   nebenher im Browser): „Timer eine Minute setzen" machte „setzen" zum
   Erinnerungstext. Das Verb wird nur noch gestrichen, wenn sonst nichts uebrig
   bleibt -- der erste Versuch strich es blind am Satzende und machte aus „die
   Messung zu starten" ein „die Messung zu".

Zweimal ging beim Umschreiben die Klammerung verloren, die den Artikel nur
zusammen mit dem Substantiv schluckt; „die Pumpe abzuschalten" wurde dann zu
„Pumpe abzuschalten". Beim dritten Mal als eigene, kommentierte Gruppe gesetzt.

Nicht optimiert wurde auf „Tanne" -- so verstand die Erkennung Piper's „Timer"
in einem spaeteren Lauf. Freds eigener Test im Log zeigt „Timer 1 Minute
setzen." korrekt; das ist ein Artefakt der synthetischen Stimme, und genau
dieser Fehlschluss steht weiter oben schon einmal.

## Aufzeichnung ohne Ansprache steuern

Wunsch: den Mitschnitt starten und stoppen, ohne erst „Kiwi" zu rufen und auf
das „Ja?" zu warten.

Ging billig, weil der Hoerpfad ohnehin alles transkribiert und nur mangels
Aktivierungswort verwirft. Der Direktbefehl haengt vor dieser Pruefung und
oeffnet den Gespraechsmodus nicht.

Geprueft mit der Sprechprobe, einmal MIT simuliertem Knopfdruck und einmal ohne
-- der zweite Lauf ist der eigentliche Fall. Dazwischen lief „Wie dick ist das
Fenster?" als Kontrolle und wurde korrekt verworfen ("nicht angesprochen").

## Sprechertrennung im Protokoll

Frage war, wie gut die Transkription Personen unterscheiden kann. Antwort
vorher: gar nicht, das Protokoll war ein Textstrom ohne Sprecherfeld.
whisper.cpp bringt zwei Optionen mit, die beide nicht taugen -- `--diarize`
braucht Stereo, `--tinydiarize` gibt es nur auf Englisch und markiert nur
Wechsel.

Gebaut mit sherpa-onnx (pyannote-Segmentierung + CAM++-Embeddings, beides
ONNX), passend zum vorhandenen onnxruntime. Als eigener Schritt in der
Nachbereitung, abschaltbar -- der Wunsch war ausdruecklich, es notfalls wieder
herausnehmen zu koennen.

**Gemessen statt geschaetzt.** Die eigenen Aufnahmen tragen eine brauchbare
Wahrheit in sich: Kiwis Ansagen sind ein fester Satzvorrat, alles andere ist
Fred. Ueber sechs Sitzungen und 54 Transkriptzeilen kamen 89-91 % sauber heraus,
und der Schwellenwert 0,7 fand meist genau zwei Cluster statt vier. Die Zahl
gilt aber fuer den LEICHTEN Fall, Mensch gegen synthetische Stimme; zwei
Menschen an einem Mikrofon sind schwerer. Das gehoert dazugesagt.

**Was die Messung nebenbei zeigte:** lange Passagen sind stabil (ein
dreissigsekuendiger Fachvortrag blieb durchgehend derselbe Cluster), Ein- und
Zweisekuender bekommen eigene Etiketten. Deshalb wird unterhalb von 0,7 s und
bei unklarer Ueberlappung gar nichts zugeordnet.

**Beinahe falsch repariert.** Beim Durchsehen fiel auf, dass Kiwis eigene
Antworten im Protokoll stehen. Das sah nach einem Fehler aus -- ist aber
Absicht, `doku.mische()` mischt sie bewusst bei, sonst stuende nur die halbe
Unterhaltung drin. Statt sie zu entfernen haelt der Rekorder jetzt fest, wann
er beigemischt hat; diese Abschnitte heissen "Kiwi" und muessen nicht geraten
werden.

**Ein Fehler lag im Testclient, nicht im Code.** Zwischen den Saetzen schickte
er sechs Sekunden gar kein Audio statt Stille. Der Rekorder rueckt aber nur mit
ankommenden Bloecken vor -- die Zeitachse schrumpfte, und die gepufferte
Beimischung landete ueber der naechsten Aeusserung. Genau der Stau, vor dem der
Kommentar in `mische()` warnt. Nach der Korrektur 6 von 6 Zeilen richtig
zugeordnet.

**Und noch eine Verstuemmelung:** die Erkennung schrieb "Sprachaufzeichen und
starten". Der Direktbefehl haengt jetzt an "sprachauf" statt an der genauen
Schreibung -- unscharf wie das Aktivierungswort, aber immer noch eng genug,
dass "Aufzeichnung starten" allein nicht ausloest.

## Vorbereitung auf ein oeffentliches Repository

Erster Schritt: Lizenz und die Stellen, die den Rechner, das Netz und das Labor
benennen.

- **MIT-Lizenz** (`LICENSE`) plus `DRITTANBIETER.md` fuer die fremden
  Bestandteile. Die Wahl faellt auf MIT, weil der ganze Unterbau -- whisper.cpp,
  Silero, pyannote-Segmentierung -- ebenfalls MIT ist.
- **Rechner und Netz** aus `technisch.md`, `konfig.py`, `klient.html` und
  `gateway.py`: Hostname, Tailnet-Adresse und das LAN-Praefix sind raus, die
  Lehren daran bleiben (die `ethtool`-Falle, die weltweit geroutete IPv6).
- **Absolute Pfade** in `testaudio/lauf.sh` durch Umgebungsvariablen ersetzt.
- **Vokabular und Quellenliste** als `.beispiel` ins Repo, die echten
  ignoriert, mit Rueckfall im Code.

Zwei Dinge fielen dabei auf, die vorher nicht auf der Liste standen:

1. **Der erste Suchausdruck war zu eng.** Er fand `/home/nutzer`, aber nicht
   die Pfadform mit Bindestrichen (`-home-nutzer-...`), und uebersah zwei
   weitere Vorkommen des Hostnamens in `gateway.py` und `technisch.md`. Erst
   der zweite, breitere Durchgang war vollstaendig.

2. **Die Historie ist NICHT sauber** -- entgegen der ersten Einschaetzung. Die
   trug nur fuer Aufnahmen und Index (die wurden nie committet). Hostname,
   Tailnet, LAN-Praefix, die Repo-Adresse und die Projektnamen aus dem
   Vokabular stehen in je einem bis drei der 50 Commits. Vor einer
   Veroeffentlichung muss die Historie also doch angefasst werden.

Piper laeuft unter **GPL-3.0-or-later** (`piper-tts` 1.7.0, OHF-voice), nicht
mehr unter MIT wie die aeltere rhasspy-Fassung. kihiwi ruft es als Bibliothek
auf und liefert es nicht mit; wer ein Gesamtwerk weitergibt, prueft das fuer
sich. In `DRITTANBIETER.md` vermerkt.

## Veroeffentlichung

Der Arbeitsstand war entschaerft, die Historie nicht. Beides ist jetzt erledigt
-- aber nicht per Force-Push, sondern ueber ein frisches Repository.

**Warum nicht Force-Push.** Fred fragte nach, ob die alten Commits danach nicht
weiter auffindbar sind. Sie sind es: GitHub loescht unreferenzierte Objekte
nicht zuverlaessig, sie bleiben unter `/commit/<sha>` abrufbar, und die eigene
Dokumentation verweist fuer eine wirkliche Entfernung auf den Support. Truffle
Security hat das 2024 als *Cross Fork Object Reference* beschrieben. Da das Repo
privat war, sind die SHAs nirgends oeffentlich und Raten ist aussichtslos -- das
Risiko war klein, aber es blieb ein Wahrscheinlichkeitsargument. Ein frisches
Repository hat die alten Objekte nie gesehen; das ist eine Zusicherung.

**Vorgehen.** `git filter-repo --replace-text` ueber 17 Regeln, angewandt auf
Dateiinhalte UND Commit-Nachrichten -- letzteres war noetig, zwei Nachrichten
nannten die Ortsnamen und die Projektbegriffe. Danach `--mailmap` fuer die
Autorenkennung. Dann `DG1001/kihiwi` (privat) umbenannt zu
`DG1001/kihiwi-labor` und der Name fuer ein neues, oeffentliches Repository
freigemacht.

**`kihiwi-labor` traegt die unbereinigte Historie und muss privat bleiben.**

Geprueft wurde am Ende nicht der lokale Stand, sondern ein frischer Klon des
oeffentlichen Repositorys: 52 Commits, keines der elf Muster in Dateien,
Nachrichten oder Metadaten, und der Klon laeuft mit den Beispieldateien.

**Ein Suchausdruck schlug falschen Alarm:** "frederik" fand 52 Treffer -- den
neuen Autorennamen, nicht den alten Tailnet-Namen. Gezielt auf `e034` und
`.frederik` geprueft: null.

## Modellwechsel: Qwen3.8-27B gegen Ornith

### Zuerst ein Fehler in `dienste.sh`

`start_vllm` erkannte das Sprachprofil daran, dass der Kontext 32768 war. Seit
`ornith-voice` wegen Hermes auf 131072 steht, traf das nie mehr zu -- der Start
warnte bei jedem Aufruf "laeuft nicht mit dem Sprachprofil", gerade wenn alles
richtig war. **Eine Warnung, die immer kommt, ist keine Warnung mehr**, und beim
Modellvergleich waere sie vollends nutzlos geworden.

Der Kontext taugt nicht mehr als Merkmal: `ornith` und `ornith-voice`
unterscheiden sich inzwischen nur noch in `GPU_UTIL` (0.55 gegen 0.85), und das
steht nirgends in `/v1/models`. Geprueft wird jetzt nicht mehr das Profil,
sondern das, wovon es abhaengt -- **ob neben dem Modell noch Speicher fuer
whisper.cpp, Piper und sherpa-onnx bleibt** (`MIN_FREI=20` GiB). Das gilt auch
fuer ein fremdes Modell auf :8889, und genau darum ging es hier.

### Was Modelle fuer diesen Dienst koennen muessen

Vier Dinge: OpenAI-kompatibles `/chat/completions`, Werkzeugaufrufe, kein
sichtbares Nachdenken, mindestens 64K Kontext. Alles andere ist Umgebung --
`KIHIWI_LLM` und `KIHIWI_MODEL` reichen, kein Codeeingriff.

**Geschwindigkeit zaehlt hier viel weniger als im Pruefstand.** Der misst
Durchsatz ueber Stunden; kihiwi spricht satzweise, und Piper ist langsamer als
jedes Modell auf dieser Maschine. Spuerbar ist nur der erste Brocken. Das oeffnet
die Kandidatenliste erheblich -- ein dichtes Modell mit 20 tok/s ist als
Programmierhilfe unbrauchbar und als Sprachassistent noch benutzbar.

Am Speicher ist ohnehin nichts zu unterscheiden: vLLM reserviert
`GPU_UTIL x 121 GiB` vorab, unabhaengig davon, ob die Gewichte 21 oder 31 GB
wiegen. Alle vorhandenen Profile passen bei 0.55.

### Die Messung

Vier Fragen end-zu-end ueber den WebSocket, mit Piper gesprochen, warm gemessen,
erst Ornith als Basislinie, dann Qwen3.8-27B auf demselben Audio.

| | Ornith-1.5-35B-A3B | Qwen3.8-27B + MTP |
|---|---|---|
| Generierung, warm | 78,4 tok/s | 20,0 tok/s |
| erster Satz | 0,4-2,5 s | 1,1-2,5 s |
| Werkzeugaufrufe auf 4 Fragen | 1 | 4 |

20,0 statt der 24,8 tok/s aus dem Pruefstand -- dort lief vLLM 0.27.1, hier
`latest`. MTP greift (58 % Entwurfsannahme, mittlere Annahmelaenge 2,15 von 3);
ohne waeren es rund 10.

**Der Befund ist nicht die Geschwindigkeit, sondern das Verhalten.**
Qwen3.8-27B ruft Werkzeuge viel bereitwilliger als Ornith -- und hoert danach
auf. Auf "Unterschied zwischen Sekundaer- und Rueckstreuelektronen" suchte es in
den Unterlagen, fand nichts und sagte "Die Unterlagen enthalten keine
Definition". Ornith beantwortete dieselbe Frage aus eigenem Wissen. Auf eine
Lehrbuchfrage ("warum ein Vakuum?") ging Qwen3.8 sogar ungefragt ins Netz.

Das ist genau das Gegenteil von Ornith' bekannter Schwaeche -- dass es
`dokumente_suchen` im Sprachpfad oft *nicht* aufruft -- und trotzdem keine
Verbesserung. Fuer einen Laborassistenten ist "steht nicht in den Unterlagen"
auf eine allgemeine Fachfrage die falsche Antwort. **Ein Modell zu suchen, das
lieber sucht, loest das Problem nicht; die Werkzeugwahl gehoert weiter in den
Dienst.**

### Zwei Fallen beim Messen selbst

**Der erste Durchlauf mass Piper, nicht das Modell.** Testfrage war "was ist ein
Siliziumnitrid-Fenster?"; die synthetische Stimme machte daraus ein
"Silizimetrie-Tenster", und beide Modelle antworteten korrekt, sie kennten das
Wort nicht. Dieselbe Falle wie bei "Timer"/"Tanne" -- der Testsatz muss durch
Piper und Whisper heil hindurchkommen, sonst misst man die Kette davor.

**`GPU_UTIL=0.55` muss man mitgeben.** Alle Profile ausser `ornith-voice` haben
0.85 als Vorgabe. Ohne den Zusatz laedt das fremde Modell und der Sprachstapel
hungert -- derselbe Zustand, wegen dem `ornith-voice` ueberhaupt entstand.

### Was nicht getestet werden kann

`ds4` (DeepSeek-V4-Flash, im Pruefstand das staerkste Modell dieser Maschine)
belegte mit `-c 65536` bereits 113 von 121 GiB. Uebrig blieben ~8 GiB fuer den
ganzen Sprachstapel, und kleiner als 65536 geht nicht, weil Hermes daran schon
gescheitert ist. Qwen3.8-Flash-Next wartet weiter auf llama.cpp PR #27742.

### Nachtrag: die Recherche folgte dem Modellwechsel nicht

Beim Ausprobieren von Qwen3.8 scheiterte der Rechercheauftrag mit `HTTP 404:
The model ornith-1.5-35b-a3b does not exist`, waehrend der Sprachpfad
einwandfrei lief. **Der Grund lag ausserhalb des Repos:** `wissen/recherche.py`
rief `hermes chat` ohne Modellangabe auf, und Hermes nahm daraufhin
`model.default` aus `~/.hermes/config.yaml` -- dort steht Ornith fest verdrahtet.

`KIHIWI_MODEL` galt damit nur fuer den halben Dienst. Behoben durch ein
ausdrueckliches `-m konfig.LLM_MODEL` beim Aufruf; Hermes nimmt auch einen
Namen an, der nicht in seiner `models:`-Liste steht.

**Die Lehre ist nicht der Tippfehler, sondern die Bauart:** ein Umschalter, der
nur einen Teil der Kette erreicht, sieht im Test genau so lange richtig aus, wie
man den anderen Teil nicht anfasst. Meine vier Probefragen liefen alle ueber den
Sprachpfad -- die Luecke haette ich damit nie gefunden. Fred fand sie im ersten
eigenen Versuch.

Die leere Hermes-Sitzungskennung in den Recherchenotizen ist davon unberuehrt:
sie fehlt in allen 21 aelteren Notizen ebenso und ist ein eigener, kosmetischer
Punkt.

### Nachtrag: Qwen3.6-35B-A3B, und eine zu breite Behauptung von mir

Der Vergleich, auf den es ankam -- gleiche Bauart, gleiche Groesse, gleiche
Quantisierung, gleiche Parser wie Ornith, nur anderes Training. Ergebnis auf
denselben vier Fragen: 78,3 tok/s, erster Satz 1,3-2,2 s, drei Werkzeugaufrufe.

**Es sucht und antwortet trotzdem.** Findet `dokumente_suchen` nichts, faellt es
auf eigenes Wissen zurueck, statt "steht nicht in den Unterlagen" zu sagen. Auf
die Frage nach Sekundaer- gegen Rueckstreuelektronen war es das einzige der drei
mit einer fachlich richtigen Antwort -- Ornith sagte "werden an der Oberflaeche
abgelenkt" (falsch), Qwen3.8 verweigerte. Ein Rechercheauftrag lief in 23 s
durch, mit genannter Quelle.

**Korrektur an mir selbst:** Ich hatte geschrieben, Geschwindigkeit sei fuer
diesen Dienst fast egal, weil Piper ohnehin langsamer spreche. Das stimmt fuer
den gesprochenen Zug und ist fuer die Recherche falsch. Hermes haengt dutzende
volle Generierungen aneinander -- gemessen 48 vLLM-Anfragen in einem einzigen
Auftrag. Bei 20 tok/s lief der Wetterauftrag in die 420-s-Grenze; bei 78 tok/s
dauerte derselbe 23 s. **Die Aussage war nicht falsch, sondern zu breit: sie galt
fuer den halben Dienst und ich habe sie fuer den ganzen formuliert.** Dieselbe
Form von Fehler wie beim Hermes-Modellnamen, nur eine Ebene hoeher.

### Nachtrag: Hermes schrieb ins Repo

Ein Rechercheauftrag hinterliess `elektronenmikroskopie-uebersicht.html`, 20 KB,
in der Repo-Wurzel. **Der Agent erbt das Arbeitsverzeichnis des Sprachdienstes**
-- und das ist das Repo. Fred hat es gesehen, bevor es jemand committet hat; mein
eigenes `git add -A` haette es beim naechsten Mal eingesammelt.

Unangenehmer als die Unordnung ist die Reichweite: dasselbe Verzeichnis enthaelt
`aufnahmen/` (§ 201 StGB) und den Quellcode.

**Der naheliegende Schalter reichte nicht.** `hermes chat --in DIR` sieht nach
der Loesung aus, ist aber keine: Hermes stellt ein gemerktes
Arbeitsverzeichnis wieder her -- im Protokoll steht "Shell cwd was reset to
/home/.../kihiwi" -- und der Testauftrag legte seine Seite trotz `--in` wieder in
der Repo-Wurzel ab. `--no-restore-cwd` betrifft nur fortgesetzte Sitzungen.

Wirksam ist das `cwd=` des Kindprozesses. Das kann der Agent nicht
ueberschreiben, weil es der Kernel setzt, bevor Hermes ueberhaupt laeuft. Gesetzt
sind jetzt beide -- der Schalter fuer Hermes' eigene Buchfuehrung, `cwd=` als
das, worauf Verlass ist.

Nachgeprueft mit einem Auftrag, der ausdruecklich eine Datei anlegen sollte:
sie landete in `zustand/hermes/`, die Repo-Wurzel blieb sauber.

**Die Lehre:** ein Schalter, der das Richtige verspricht, ist noch keine
Zusicherung. Ich haette den ersten Versuch fuer erledigt halten koennen -- der
Aufruf lief ja fehlerfrei durch. Gesehen habe ich es nur, weil ich danach
`git status` laufen liess.

### Die Buehne ueberlebt jetzt den Neustart

Fred vermisste ein Rechercheergebnis auf der Buehne. Nachgestellt mit einem
Mithoerer, der jede Nachricht mitschreibt: der Dienst liefert `typ: recherche`
korrekt aus, der Klient legt es korrekt auf die Buehne. **Die Kette war nie
kaputt.**

Zwei andere Gruende kamen zusammen, beide auf meiner Seite. Sein eigener
Auftrag war unter Qwen3.8 in die 420-s-Grenze gelaufen -- auf der Buehne stand
dann korrekt, aber unscheinbar "Die Recherche ist gescheitert". Und ich hatte
den Dienst dreimal neu gestartet, ohne es anzusagen; jeder Neustart kappt die
Verbindung, und **der Buehneninhalt lebte allein im Browser**. Meine eigenen
erfolgreichen Laeufe hatte ich direkt ueber `Recherche._hermes` aufgerufen --
die gehen am Gateway vorbei und erreichen den Browser nie. Bei mir sah deshalb
alles gut aus, waehrend er nichts hatte.

**Behoben, wo der Zustand hingehoert: in den Dienst.** Der letzte Buehneninhalt
liegt in `zustand/buehne.json` und wird beim Verbindungsaufbau mit
`wieder: true` nachgereicht. Der Klient zeigt ihn, vermerkt "von vorhin" und
setzt keinen Merker in den Verlauf -- der ist nach dem Neuladen selbst leer, ein
einzelner Verweis darin waere irrefuehrend. Gemerkt wird zentral in
`Sitzung.zur_buehne()`, damit keine der vier Ausgabestellen es vergessen kann.

Geprueft mit dem Weg, der vorher versagte: Recherche ausloesen, Dienst neu
starten, frisch verbinden -- der Stand kommt mit `wieder=True` zurueck.

**Die Lehre betrifft nicht den Code:** Ich habe waehrend seiner Arbeit dreimal
den Dienst neu gestartet und es nicht gesagt. Ein Fehlerbild, das ich selbst
erzeugt habe, kostete ihn die Suche danach.

### Nachtrag: Nemotron-3.5-Lightning mit DSpark

Das schnellste Modell auf dieser Maschine -- 91,9 tok/s warm, erster Satz
0,9-2,3 s, beides der beste Wert im Vergleich. Der Rechercheweg lief in 40 s
durch, mit Quellen und ausdruecklichen Unsicherheitsvermerken.

**Zuerst geprueft, ob Piper die Gedankenkette vorliest.** Nemotron ist ein
Denkmodell, und der globale Schalter `enable_thinking: false` ist ein
Qwen-Argument -- ob Nemotron darauf hoert, stand offen. Ein direkter Aufruf gegen
:8889 vor jedem Sprachtest: `content` sauber, `reasoning_content` leer, kein
`<think>`. **Diese Reihenfolge gehoert zur Routine bei jedem neuen Modell**;
andersherum haette der erste Satz aus Pipers Mund die Antwort erklaert, statt
sie zu geben.

**Schwach im Deutschen.** Fachbegriffe bildet es adjektivisch statt als
Komposita: "sekundaere Elektronen", "rueckstreue Elektronen". Der Vorbehalt
gehoert dazu -- die Sprachsynthese hatte die Frage schon so verstuemmelt, das
Modell kann echot haben. Werkzeuge ruft es sparsam wie Ornith, 1 von 4.

Damit steht der Vergleich: Nemotron ist am schnellsten, Qwen3.6-35B-A3B
antwortet am besten, Ornith liegt dazwischen, Qwen3.8 faellt am Rechercheweg
aus. **Geschwindigkeit war bei keinem der vier das Unterscheidungsmerkmal, das
den Ausschlag gab** -- ausser dort, wo sie in einen Abbruch umschlug.

### Der Dienst hat sich selbst vergiftet

Fred schickte einen Gespraechsmitschnitt: dreimal hintereinander nur "Ich schaue
in den Unterlagen nach.", ohne Antwort. Dazwischen, mitten im Fachgespraech,
"Es laeuft gerade kein Timer."

**Zwei verschiedene Fehler, beide im Dienst, beide modellunabhaengig.**

**1. Die Ansage als Rueckkopplung.** `melden()` schreibt alles Gesprochene in
den Gespraechsverlauf -- mit gutem Grund, das Modell bestritt sonst, was der
Dienst gesagt hatte. Bei der Werkzeugansage kippt das: "Ich schaue in den
Unterlagen nach." steht als letzte Assistentenaeusserung da, und das Modell
schreibt in der Schlussantwort genau diesen Satz noch einmal, statt zu suchen.

Im Protokoll steht es unmissverstaendlich: `LLM erster Satz nach 519 ms
(34 Zeichen)` -- die Laenge der Ansage -- und **keine `Werkzeug`-Zeile
dahinter**. Das Netz `_ANGEKUENDIGT` griff, holte die Suche nach und liess neu
antworten; die Neuantwort sah denselben Satz im Verlauf und wiederholte ihn.
Eine Schleife, die sich mit jedem Zug festzog.

Behoben an drei Stellen: `in_verlauf=False` fuer die Ansage, dieselbe Ansage nur
einmal je Zug, und wenn das Netz greift, fallen die Ankuendigungssaetze aus der
Antwort -- sie sind dann eingeloest. Nachgestellt mit Freds eigener
Gespraechsfolge: die Wiederholung ist weg, jeder Zug endet mit Inhalt.

**Nemotron hat den Fehler nur sichtbar gemacht, nicht verursacht.** Ein
schwaecheres Modell faellt auf das Muster im Verlauf frueher herein. Ornith war
dagegen nicht immun, nur seltener betroffen -- der Fehler lag die ganze Zeit da.

**2. "Erinnerung" als Weckwort.** Auf "wenn ich es richtig mich in Erinnerung
habe, Backscattered und Secondary" antwortete der Dienst "Es laeuft gerade kein
Timer." `_WECK_WORT` trifft `erinnerung`, und `wecker_absicht()` hatte am Ende
ein `return "zeigen"` als Auffangfall -- **war das Wort einmal im Satz, gab es
keinen Weg zurueck zu None.**

"Erinnerung", "erinnere", "melde dich" sind alltaegliche Woerter. Der Auffangfall
ist jetzt `None`: ohne Zeitangabe UND ohne Frage- oder Zeigewort traegt das
Weckwort allein die Entscheidung nicht, und erkennen() faellt auf die normale
Unterhaltung zurueck. Zwoelf Faelle geprueft, darunter "Behalt das mal in
Erinnerung" und "Wenn ich mich recht erinnere" gegen "Erinner mich um 15 Uhr" --
alle richtig.

**Lieber nichts erkennen als das Falsche** -- dieselbe Regel, die schon fuer die
Sprechertrennung galt.

### Ein fremder Motor: Qwen3.8-Flash-Next ueber llama-server

Fred hatte das Modell in einer anderen Sitzung zum Laufen gebracht (llama.cpp
kann `qwen4exp` jetzt) und wollte es als Sprachmodell probieren. Damit haengt
erstmals **kein vLLM** an 8889.

Drei Dinge vorher geprueft, nicht nachher: `--jinja` ist gesetzt und
Werkzeugaufrufe kommen als `tool_calls` zurueck; der Alias heisst
`qwen3.8-flash-next`; und -- das Entscheidende -- **wohin das Nachdenken im
Strom geht.** Es ist ein Denkmodell, aber llama.cpp trennt sauber: 145 Zeichen
`content`, 352 `reasoning_content`. Piper spraeche also nur die Antwort.

**Trotzdem war es unbrauchbar.** 9 bis 17 Sekunden bis zum ersten Satz, und der
Dienst brach zweimal selbst ab ("Das dauert mir zu lange"). Die Messung zeigte
warum: rund 600 Token Ueberlegung vor jeder Antwort. **Die Tokenrate war nicht
schuld** -- 28,1 tok/s mit Denken gegen 29,0 ohne. Es ist die Menge, nicht das
Tempo. Ohne die Gegenmessung haette ich es fuer ein zu langsames Modell gehalten
und den falschen Schluss gezogen.

**Der Schalter existiert, er wurde nur nicht mitgeschickt.** vLLM setzt
`--default-chat-template-kwargs '{"enable_thinking": false}'` beim Start;
`llama-server` hat dafuer keine Vorgabe, der Klient muss es in den Rumpf legen.
Neu: `KIHIWI_LLM_ZUSATZ` als JSON-Objekt, das in jede Anfrage gemischt wird --
roh durchgereicht, nicht auf bekannte Schalter beschraenkt. Welcher Motor haengt,
entscheidet der Betrieb, nicht dieser Code.

Danach 1,1 bis 6,2 Sekunden, keine Abbrueche mehr, und **fachlich die besten
Antworten bisher**: als einziges Modell fand es zur Beschleunigungsspannung
etwas in den Unterlagen ("der Elektronenstrahl dient selbst als Voltmeter, etwa
hundert ppm") statt "nichts gefunden", und die deutschen Komposita stimmen.
Rechercheauftrag in 103 s.

Nebenbei: `dienste.sh` las den Kontext nur aus `max_model_len`, llama.cpp legt
ihn in `meta.n_ctx` -- die Statuszeile sagte "ctx ?". Beide Felder werden jetzt
gelesen. Auf 8889 muss kein vLLM liegen; die README verspricht das seit der
Veroeffentlichung, gepruegt war es nie.

**Der Speicher ist der Haken:** 84 GB Gewichte lassen 18 GiB fuer alles andere,
gegen 41 GiB bei den 22-GB-Modellen. Es laeuft, aber ohne Reserve.

### Texteingabe neben der Stimme

Fuer den Fall, dass am Client eine Tastatur haengt. Kein zweiter Pfad im Dienst:
`getippt()` baut einen `Endpoint` mit `grund="getippt"` und ruft `antworten()` --
denselben Einstieg, den `audio()` nach dem Endpunkt nimmt. Damit gilt alles
Vorhandene automatisch weiter: Absichten, Ausloesewoerter, Wecker, Werkzeuge,
Buehne. **Was schon einmal richtig gebaut wurde, muss man nicht zweimal
bauen** -- die Trennung von Endpunkterkennung und Antwort hat sich hier
ausgezahlt, ohne dass sie dafuer gedacht war.

Drei Entscheidungen: ohne Aktivierungswort (wer tippt, spricht absichtlich an),
ohne Mikrofon (am Futro steht vielleicht keins, und im Labor ist es laut), und
`gespraech=True`, damit eine gesprochene Rueckfrage nach einer getippten Frage
kein erneutes "Kiwi" braucht.

Geprueft ueber den WebSocket, **bewusst ohne** `{"befehl":"mikro"}`: Zeitfrage
beantwortet, "Kiwi, warum ..." korrekt auf "warum ..." gekuerzt, "Was ist ein
Kiwi?" ungekuerzt durchgelassen (erkannt() prueft nur den Anfang), "Kiwihilfe"
loeste aus und landete auf der Buehne. Danach die Gegenprobe auf dem Sprachweg
-- unveraendert.

### Stummschalter

Nachgereicht zur Texteingabe: wer tippt, will vielleicht nicht, dass der Rechner
im Raum antwortet.

**Serverseitig geloest, nicht im Klienten.** Der naheliegende Weg waere gewesen,
den Ton einfach nicht abzuspielen -- dann erzeugt Piper ihn aber weiter und
schiebt ihn ueber die Leitung. `sag()` steigt jetzt vorne aus. Gemessen: stumm
0 Tonstroeme und 0 Audio-Bytes gegen 1 Strom und 275 kB, bei identischem
Antworttext.

Die Einstellung liegt im localStorage des Browsers und wird bei jedem `onopen`
neu geschickt. **Sie gehoert zum Arbeitsplatz, nicht zum Dienst** -- am
Laborrechner mag laut richtig sein und am Schreibtisch stumm, und der Dienst
kennt beide.

Bedacht, aber kein Problem: stumm landet auch nichts im Mitschnitt. Das ist
richtig -- der Assistent hat im Raum nichts gesagt. Beinahe haette ich hier
`doku.mische()` "repariert", wie schon einmal.

### Der Auszug lag an der falschen Stelle

Aufgefallen beim Erklaeren der Suche, nicht beim Suchen: der laengste Abschnitt
im Index hat 178.602 Zeichen, und ans Modell gingen immer die ersten 600.

Gemessen ueber acht Fachfragen, 53 Treffer: **9 % der gelieferten Auszuege
enthielten keinen einzigen Suchbegriff.** Der Treffer war richtig, die gezeigte
Stelle nutzlos -- und das Modell antwortete "steht nicht in den Unterlagen" auf
etwas, das dasteht. Genau die Schwaeche, die mir vorher bei mehreren Modellen
als Modellfehler erschien.

`auszug()` schneidet jetzt um die erste Fundstelle, ein Drittel Vorlauf, zwei
Drittel danach. Danach 6 %, und **alle drei Restfaelle haben den Begriff in der
Ueberschrift**, die ohnehin in der Kopfzeile mitgeht -- effektiv null. An einer
echten Frage: "Was steht ueber die Beschleunigungsspannung?" lieferte vorher
"steht nichts", jetzt "dreissig Kilovolt".

**Nicht behoben, aber jetzt bekannt:** 146 von 2420 Abschnitten sind groesser als
das Maximum (Tabellen und Code haben keine Absatzgrenzen zum Schneiden), und
**5 % der Abschnitte haben beim PDF-Auslesen die Leerzeichen verloren.** Die
zweite Zahl ist der groessere Hebel -- solche Stellen sind fuer eine
Wortsuche unerreichbar, egal wie gut der Auszug liegt.

### Der groesste Hebel lag im PDF-Auslesen

Aus der vorigen Messung: 5 % der Abschnitte hatten beim Auslesen die
Leerzeichen verloren. Solcher Text ist fuer eine Wortsuche unerreichbar --
kein noch so guter Auszug hilft, wenn der Begriff im Index nicht als Wort
existiert.

**Erst der billige Weg, und er hat nicht funktioniert.** pypdf kann
`extraction_mode="layout"` und einen `space_width`-Schwellenwert; beides
getestet, von 200 bis 5 -- 21 verklebte Woerter auf der Testseite, unveraendert.
Die Leerzeichen fehlen im Inhaltsstrom, pypdf kann sie nicht erfinden.

Drei Auswerter verglichen, ueber 28 PDFs:

| | verklebte Woerter | Zeit |
|---|---|---|
| pypdf | 1380 | 1,8 s |
| pypdfium2 | 111 | 0,3 s |
| pdfminer.six | 111 | 0,5 s |

pdfminer war gleich gut, pypdfium2 sechsmal schneller als pypdf. **PyMuPDF habe
ich nicht genommen, obwohl es der bekannteste Kandidat ist: AGPL-3.0, und das
Repo ist MIT.** pypdfium2 ist BSD-3-Clause/Apache-2.0.

Nach dem Neueinlesen: **5,0 % betroffene Abschnitte auf 0,7 %.** Und die
Wirkung ist keine Statistik -- die Frage "Was sagen die Unterlagen zur
FEMM-Rechnung und den Spaltfeldern?" wird jetzt beantwortet. Derselbe Text lag
vorher als `Spaltfelderfallensteilerab-dieFEMM-Rechnung` im Index und war
unauffindbar.

Zusammen mit dem Auszug um die Fundstelle: **0 von 60 Treffern ohne Suchbegriff
in Auszug oder Ueberschrift**, vorher 9 %.

**pypdf bleibt als laute Rueckfallebene.** Fehlt pypdfium2, laeuft das Einlesen
weiter und sagt es -- still schlechter Text waere schlimmer als ein Abbruch.

### Umlaute: ein Fehler, der erst durch die Tastatur wichtig wurde

Beim Vorbereiten von Punkt 2 gemessen und dabei gestolpert:
**"Sekundaerelektronen" lieferte null Treffer, "Sekundärelektronen" acht.**

Der Index faltet Umlaute (`remove_diacritics 2`), "Sekundärelektronen" liegt
dort als `sekundarelektronen`. Eine ae/oe/ue-Anfrage trifft das nicht. Whisper
schreibt Umlaute richtig, im Sprachpfad fiel es deshalb nie auf -- **mit der
neuen Tastatureingabe schon**, denn wer an einem fremden Layout tippt, schreibt
"ae".

Erster Versuch war zu einfach: alle ae/oe/ue ersetzen. Daraus wurde aus
"rueckstreuelektronen" ein `ruckstreulektronen`, weil das zweite "ue" in
streu+elektronen kein Umlaut ist. Jetzt werden bei bis zu drei Vorkommen alle
Kombinationen erzeugt; bei einer ODER-Suche kostet eine falsche Variante nichts.

**Der Befund war groesser als der Fehler.** `"saeule"` kommt selbst 81-mal im
Index vor -- Dateinamen und umlautfrei geschriebene Dokumente. Das Korpus mischt
also beide Schreibweisen, und vorher fand eine Anfrage je nach Tippweise nur die
eine Haelfte. Jetzt beide: 260 Treffer statt 189.

### Quelltext, Schlagwoerter, Katalog

**Quelltext lief durch die Markdown-Zerlegung.** In Python ist `#` ein
Kommentar, kein Ueberschriftszeichen -- also wurde jede Kommentarzeile zur
Ueberschrift, und das Feld wiegt in BM25 doppelt. Im Index standen
Ueberschriften wie `------------------------` und "genau der Fehler, den man nur
einmal macht". Geschnitten wurde an Kommentaren statt an Funktionen; wo keine
Kommentare standen, gar nicht. 879 von 2495 Abschnitten betroffen.

Jetzt wird an `def`/`class`/`function` geschnitten, die Ueberschrift ist der
Symbolname, und der Block davor heisst "Kopf und Konstanten" -- dort stehen die
Messwerte. Dazu eine zeilenweise Obergrenze und ein Datenblock-Filter: der
groesste Abschnitt war ein eingebettetes PNG als base64, 178.484 Zeichen in
einer Zeile. **Groesster Abschnitt jetzt 1.596 statt 178.602, keiner mehr ueber
der Grenze.**

**Der erste Versuch aenderte nichts** -- `einlesen` meldete "0 Dokumente
geaendert", weil der Fingerabdruck den Inhalt abdeckt und nicht die Zerlegung.
`ZERLEGER_FASSUNG` geht jetzt in den Fingerabdruck ein.

**Fred wollte Quelltext durch Zusammenfassungen ersetzen.** Dagegen sprach ein
konkreter Fall: `FENSTER_NM = 50.0` steht ausschliesslich in
`saeulen_auslegung.py` -- genau der Wert, wegen dem das Trefferlimit von 4 auf 8
stieg. Eine Zusammenfassung im Sinne von "was wird wie gemacht" enthaelt keine
50.0. In einem Labor, dessen Auslegung im Code steht, ist der Code teilweise
Primaerquelle. Ergebnis: beides -- Code bleibt indiziert, und der Katalog
liefert zusaetzlich das "was wird wie gemacht".

**Drei eigene Fehler in dieser Runde**, alle behoben und alle lehrreich:

1. Der Stapellauf fragte nach `ornith-1.5-35b-a3b`, weil die Kommandozeile ohne
   KIHIWI_MODEL laeuft -- 100 Anfragen in einen 404, gemeldet als "100
   gescheitert" ohne Grund. Jetzt fragt er den Endpunkt nach dem angebotenen
   Modell und nennt den ersten Fehler im Klartext.
2. Ein Wartewaechter suchte mit `pgrep -f "wissen erschliessen"` und fand **sich
   selbst** -- 54 Minuten Deadlock. Steht so in CLAUDE.md, und ich bin trotzdem
   hineingelaufen. Ueber PID warten, nicht ueber Namensmuster.
3. `kurzfassungen()` benutzte die SQLite-Verbindung des Hauptthreads in einem
   Arbeitsthread. Die Dokumenttexte werden jetzt vorher gelesen.

**Und vLLM haengte sich mitten im Lauf auf** -- `/v1/models` antwortete weiter
mit 200, `/chat/completions` nie wieder. Genau die dokumentierte Falle. Der
Stapellauf fordert jetzt vor dem Start eine kurze Antwort ab und scheitert
laut, statt zehn Minuten auf `ep_poll` zu warten.

### Der Embedding-Vergleich -- und wo ich falsch lag

Ich hatte argumentiert, ein Vektorindex bringe hier wenig: die gefundenen Fehler
laegen in der Aufbereitung, und die Fragen seien lexikalisch. Fred wollte es
gemessen haben. Zu Recht.

60 Fragenpaare, vom Modell aus zufaellig gezogenen Abschnitten erzeugt -- je eine
Frage mit den Fachwoertern und eine, die sie meidet:

| | mit Fachwoertern | umschrieben |
|---|---|---|
| FTS5 mit Schlagwoertern | 83,3 % | 23,3 % |
| nur Vektoren | 75,0 % | **41,7 %** |
| Mischung (RRF) | **90,0 %** | 38,3 % |

**Bei umschriebenen Fragen fast verdoppelt der Vektorindex die Trefferquote.**
Das ist kein Randfall. Mein Fehlschluss war, aus "die gefundenen Fehler lagen in
der Aufbereitung" zu folgern, dass es sonst keine gibt -- Aufbereitungsfehler und
Vokabularluecke sind zwei verschiedene Probleme, und ich hatte nur nach dem
ersten gesucht.

Recht behalten habe ich beim lexikalischen Fall: bei woertlichen Fragen verliert
der Vektorindex deutlich (75 gegen 83 %, MRR 0,495 gegen 0,693). Er findet das
Thema, nicht die Stelle mit der Zahl. **Der eigentliche Befund ist die dritte
Zeile: nicht entweder-oder.**

**Beinahe haette ich eine erfundene Null veroeffentlicht.** Das erste
Einbettungsmodell lieferte auf dieser Maschine stumm NaN, und die Auswertung
meldete pflichtschuldig 0/60 fuer Vektoren -- passend zu meiner These. Aufgefallen
ist es nur, weil 0/60 bei woertlichen Fragen unmoeglich ist. Ein Ergebnis, das
die eigene Erwartung bestaetigt, verdient die genauere Pruefung, nicht die
nachlaessigere.

Eingebaut als RRF-Verschmelzung in `index.suchen()`, Vektoren nach Inhalts-Hash
wie die Schlagwoerter. Nachgemessen am eingebauten Stand: 86,7 % und 40,0 %,
Suche 80-116 ms, erster Aufruf 1,4 s. Der Vektorteil steht in einem try und
faellt auf reinen Volltext zurueck -- schlechter, aber nie kaputt.

### Einlesen zieht nach

Bis hierhin musste man nach jedem `wissen einlesen` drei weitere Befehle von
Hand starten. Jetzt haengen sie dran -- alle drei arbeiten ohnehin nur das
Fehlende ab, nach einem gewoehnlichen Abgleich sind das Sekunden (gemessen:
16 Abschnitte, 3 Dokumente, 16 Vektoren in 16 s).

**Im Sprachpfad war das nicht so einfach.** Der Wissensabgleich laesst sich per
Zuruf ausloesen, und der Assistent sagt danach, was sich geaendert hat. Haenge
ich dort eine Einbettung an, wartet der Sprecher Minuten. Geloest mit zwei
Entscheidungen: das Nachziehen laeuft als Hintergrundaufgabe NACH der Antwort,
und es hat eine Obergrenze. Bleibt mehr offen, wird es protokolliert statt halb
getan -- ein halb erschlossener Index sieht aus wie ein schlechtes Modell, und
genau solche Verwechslungen haben diesen Abend mehrfach Zeit gekostet.

**Ein Abschnitt scheiterte seit dem ersten Lauf**, immer derselbe: 67 Zeichen
Formelfragment aus einem PDF, mathematische Symbole ohne ein einziges Wort. Kein
Fehler -- aber mein Code fragte bei jedem Lauf wieder das Modell. Das Merkmal
"offen" haengt jetzt am Hash in `erschliessung` statt an der leeren Spalte:
**erledigt heisst versucht, nicht ergiebig.**

### KIHIWI_PROFIL, und ein Namensabgleich beim Start

`dienste.sh` startete `ornith-voice` fest verdrahtet. Seit qwen36nvfp4 der
bessere Kandidat fuer den Sprachbetrieb ist, war das inkonsequent: ein
`./dienste.sh start` bei ausgeschaltetem vLLM haette Ornith geladen, waehrend
KIHIWI_MODEL auf Qwen zeigt -- und dann laeuft jede Anfrage in einen 404.

Zwei Aenderungen. `KIHIWI_PROFIL` waehlt das Profil (Vorgabe bleibt
`ornith-voice`, damit sich nichts unangekuendigt aendert). Und `dienste.sh`
vergleicht nach dem Start den angebotenen Modellnamen mit KIHIWI_MODEL und
**warnt laut**, wenn beide auseinanderlaufen -- auch im Zweig "laeuft schon",
wo es am wichtigsten ist.

**Diese Fehlerklasse hat jetzt dreimal zugeschlagen** (Hermes, der
Erschliessungslauf, und beinahe hier). Sie ist immer dieselbe: irgendwo steht
ein Modellname, der nicht der ist, den der Server anbietet. Jedes Mal war das
Symptom ein stiller 404, nie eine verstaendliche Meldung.

Nebenbei ein neues Profil `qwen36nvfp4-voice` in `~/.local/bin/model-switch`,
parallel zu `ornith-voice`: dasselbe Modell, derselbe served-model-name,
derselbe Kontext, nur GPU_UTIL 0.55 statt 0.85. Geprueft ueber eine isolierte
Ausfuehrung von `load_spec`, nicht ueber einen echten Start -- ein Neustart
haette zwei Minuten gekostet und nichts geaendert.

Zwei veraltete Angaben im Kopf der Datei mitkorrigiert: "Projekt aihiwi" (so
hiess das Verzeichnis vor der Umbenennung) und "ctx 32k" bei ornith-voice, das
seit Hermes' Kontextuntergrenze 131072 ist. Die Datei liegt ausserhalb des
Repos; die Kopie im Pruefstands-Repo war 269 Zeilen hinterher und ist jetzt
abgeglichen.

### "Audioaufnahme starten" tat nichts

Fred meldete, der Direktbefehl ausserhalb des Gespraechsmodus gehe nicht mehr.
Erkennung und Code waren in Ordnung -- ein Test mit "Sprachaufzeichnung
starten" lief sauber durch. **Die Antwort stand im Protokoll:**

    nicht angesprochen (lexikalisch): 'Aufzeichnung starten.'
    nicht angesprochen (lexikalisch): 'Audioaufzeichnung starten.'
    nicht angesprochen (lexikalisch): 'Audioaufnahme starten.'
    nicht angesprochen (decke):       'Audioaufzeichnung starten.'

Vier Versuche, alle korrekt transkribiert, alle am Wortstamm gescheitert: das
Muster kannte nur `sprach…`. **Kein Fehler im Code, eine zu enge Vokabel** --
und aus Sicht des Sprechers nicht zu unterscheiden, weil in beiden Faellen
schlicht nichts passiert.

Stamm jetzt `sprach|audio|ton`. Ein blosses "Aufzeichnung starten" bleibt
draussen: im Labor wird ueber die Aufzeichnung geredet, und ein Direktbefehl
wirkt ohne Aktivierungswort -- das Kompositum ist der ganze Schutz. Zehn
Faelle geprueft, darunter "wir sollten die Audioaufzeichnung nachher mal
starten" (bleibt None).

**Die Lehre ist die Diagnose, nicht die Zeile.** Ich haette lange im Gateway
gesucht; die Zeile "nicht angesprochen" mit dem WOERTLICHEN Transkript hat es
in einer Minute geklaert. Sie steht dort, seit protokolliert wird, warum der
Assistent schweigt -- das war eine gute Entscheidung.

### Qwen3.6-35B-A3B ist der Standard

`KIHIWI_PROFIL` steht jetzt auf `qwen36nvfp4-voice`. Grundlage ist die Messung
vom 28.08.: gleiche Bauart, gleiche Groesse, gleiche Quantisierung, gleicher
Durchsatz wie Ornith -- **der Unterschied ist das Verhalten.** Es ruft
`dokumente_suchen` haeufiger und bleibt trotzdem nicht bei "steht nicht in den
Unterlagen" stehen, wenn nichts zu finden ist. Ornith bleibt als Profil.

**Ein Standard ist nie eine Zeile.** Mit dem Profil musste `KIHIWI_MODEL` in
`konfig.py` mitziehen -- sonst haette ein nacktes `./dienste.sh start` Qwen
geladen und Ornith angefragt, also genau den 404 erzeugt, den ich einen Commit
zuvor sichtbar gemacht habe. Und in `vad/lexikalisch.py` stand der Name noch
zweimal fest verdrahtet; die Datei gehoert zum verworfenen lexikalischen
Endpointing und wird nirgends importiert, aber ein toter Modellname darin
haette beim naechsten Versuch wieder eine halbe Stunde gekostet. Jetzt zieht
sie ihn aus `konfig`.

Geprueft mit `env -u KIHIWI_MODEL -u KIHIWI_PROFIL -u KIHIWI_LLM_ZUSATZ` und
entladenem vLLM -- also wirklich ohne Umgebung. Startet Qwen3.6, beantwortet
eine Dokumentenfrage mit "dreissig Kilovolt", und die Warnung schlaegt an,
sobald man KIHIWI_MODEL absichtlich verstellt.

### Der Testlauf von ornith-voice deckte zwei Loecher auf

Fred wollte nur wissen, ob das Profil nach der Standardumstellung noch geht. Es
ging -- Ornith lud sauber, 43 GiB frei. **Der Dienst antwortete trotzdem nicht.**

`KIHIWI_MODEL` war nicht gesetzt, also nahm er die Vorgabe aus `konfig.py`, und
die zeigt seit dem Vormittag auf Qwen. Jede Anfrage lief in einen 404.

**Zwei Fehler, beide meine, beide aus derselben Woche.**

Der erste: `modell_pruefen`, gebaut um genau das zu verhindern, sah nur die
UMGEBUNGSVARIABLE an. War sie leer, kehrte die Pruefung zurueck -- und leer ist
sie genau dann, wenn die Vorgabe aus dem Code greift. **Die Pruefung schwieg im
gefaehrlichsten Fall.** Sie vergleicht jetzt die wirksame Vorgabe, und
`start_sprach` uebernimmt bei nicht gesetzter Variable den ANGEBOTENEN Namen:
ein Profilwechsel allein kann keinen 404 mehr erzeugen.

Der zweite ist der schlimmere. Die Zeitfrage braucht das Modell gar nicht, und
sie blieb trotzdem unbeantwortet -- **ohne eine Zeile im Protokoll.**
`antworten()` fing nur `asyncio.TimeoutError`; jede andere Ausnahme verliess die
Aufgabe und wurde von asyncio verschluckt. Jetzt wird sie protokolliert, und
der Nutzer hoert einen Satz statt Stille. **Stumm ist die schlechteste
Fehlermeldung, die ein Sprachassistent geben kann** -- man kann sie nicht von
"hat mich nicht gehoert" unterscheiden, und genau daran haben wir diese Woche
schon einmal Zeit verloren (der Direktbefehl, der am Wortstamm scheiterte).

Und eine dritte Kleinigkeit, die beim Nachbessern entstand: die Warnung feuerte
auch dort, wo der naechste Schritt den Fehler gerade behob. Eine Warnung, die
etwas Falsches ankuendigt, ist schlimmer als keine.

### Derselbe Fehler, neuer Dateityp

Fred loeste den Wissensabgleich per Stimme aus und meldete, er sei schnell
gewesen. War er -- **weil er den teuren Teil bewusst ausgelassen hat:**

    WARNING Nachziehen uebersprungen: 2530 Abschnitte ohne Schlagwoerter,
            2530 ohne Vektor (Grenze 300)

Genau dafuer ist die Grenze da, und sie hat gehalten. Der Abgleich holte 179 auf
351 Dokumente, darunter 143 neue CSV-Dateien.

**Und die waren zerhackt.** In CSV leitet "#" einen Kommentar ein, nicht eine
Ueberschrift -- die Markdown-Zerlegung machte aus jeder Kopfzeile einen eigenen
Abschnitt, Text identisch zur Ueberschrift. Der Metadatenkopf, der sagt WAS
gemessen wurde, wurde in Einzeilen zerrissen; die kurzen fielen durch den
40-Zeichen-Filter ganz heraus.

**Das ist derselbe Fehler wie bei Python, vier Tage spaeter.** Damals habe ich
`CODE_ENDUNGEN` angelegt und dabei nicht zu Ende gedacht: `#` ist nicht nur in
Python ein Kommentarzeichen, sondern auch in CSV, in Shell-Konfigurationen, in
INI-Dateien. Ich habe den Einzelfall behoben statt der Klasse. Aufgefallen ist
es erst, als der Dateityp haeufig genug wurde, um den Index zu dominieren --
2209 von 5248 Abschnitten.

Jetzt `DATEN_ENDUNGEN` mit eigener Zerlegung: Kopf und Spaltennamen an einem
Stueck, Messwerte als eigener Abschnitt. Die Zahlen bleiben im Index -- in
diesem Labor steht die Auslegung in den Daten.

Nach dem Neueinlesen 4954 statt 5248 Abschnitte. Von denen kamen **2714 ohne
einen einzigen Modellaufruf zurueck**, obwohl die Zerlegung neu ist: die
Aufbewahrung nach Inhalts-Hash trug zum ersten Mal ueber eine
Strukturaenderung hinweg. Die restlichen 2240 in 28 Minuten
(596 s Schlagwoerter, 186 s Kurzfassungen, 920 s Vektoren), null Fehlschlaege.

### Dreimal derselbe Fehler, endlich die Klasse

Nach dem CSV-Fund habe ich gefragt, ob die anderen Kommentarzeichen-Formate
mitkommen sollen. Beim Nachsehen zeigte sich der eigentliche Fall:

    Ueberschrift: 'Untergrenzen statt fester Ve'
    Text:         '# Untergrenzen statt fester Versionen: Auf dem GX10 ...'
    Ueberschrift: 'jede Version als aarch64-Whee'
    Text:         '# jede Version als aarch64-Wheel verfuegbar. Die hier ...'

Ein zusammenhaengender Absatz in `requirements.txt`, in Einzeilen zerschnitten.
Keine Datentabelle, kein Quelltext -- **meine beiden Kategorien waren von der
falschen Seite gedacht.**

Ich hatte zweimal eine Liste gepflegt, WO "#" ein Kommentar ist: erst
CODE_ENDUNGEN, dann DATEN_ENDUNGEN. Diese Liste ist naturgemaess nie
vollstaendig. Jetzt sagt `MARKDOWN_ENDUNGEN = {".md", ".markdown"}`, wo "#"
wirklich eine Ueberschrift ist -- alles andere faellt automatisch auf die
sichere Seite, auch was noch niemand eingelesen hat.

**Und ein Anzeigefehler, der schlimmer war als kosmetisch.** `wissen status`
zaehlte die Zeilen in `kurzfassung`, ohne den Fingerabdruck zu pruefen: es
meldete "351 von 351", waehrend 350 davon eine Fassung beschrieben, die es
nach der Zerlegeraenderung nicht mehr gab. Eine Zahl, die etwas anderes misst
als sie behauptet, wiegt in falscher Sicherheit. Gezaehlt wird jetzt nur, was
zum aktuellen Fingerabdruck gehoert.

**Zum zweiten Mal die falsche PID beobachtet.** `pgrep -f "wissen einlesen"`
lieferte die Wrapper-Shell, nicht den Python-Prozess; der Waechter meldete
"fertig", waehrend der Lauf noch bei den Kurzfassungen war. Ueber die PID zu
warten reicht nicht -- man muss auch die richtige haben.

Endstand: 351 Dokumente, 4931 Abschnitte, alle drei Ebenen vollstaendig. Von
4931 Abschnitten waren 4756 ohne Modellaufruf wieder da.

### Der uebersprungene Teil wird jetzt ausgesprochen

Fred wollte den naechsten Abgleich per Zuruf ausloesen und fragte, ob das geht.
Es geht -- aber beim Nachsehen fiel auf, dass er dabei genau in dieselbe Falle
laufen wuerde wie beim letzten Mal: das Nachziehen wird bei mehr als 300 offenen
Abschnitten uebersprungen, und das stand **nur im Protokoll**.

Gehoert haette er "Abgleich fertig, N neue Dokumente" -- und die Haelfte des
Index waere nur ueber die reine Volltextsuche erreichbar gewesen, gemessen 23
statt 40 % Trefferquote bei umschriebenen Fragen. **Niemand sieht ins
Protokoll.** Genau so entsteht der Eindruck, das Modell sei schlechter
geworden.

Jetzt sagt der Assistent es: "Es sind N neue Abschnitte dazugekommen, zu viele,
um sie nebenbei zu erschliessen ... starte dafuer bitte einmal von Hand den
Wissensbefehl einlesen." Der ERFOLGREICHE Fall wird dagegen nur angezeigt, nicht
gesprochen -- nach jedem Abgleich ein zweites Mal zu reden waere Laerm.
Gesprochen wird, was Handeln verlangt.

Geprueft mit einer kuenstlich auf -1 gesetzten Grenze, ohne Daten anzufassen.
Dabei kam "0 neue Abschnitte, zu viele" heraus -- unmoeglich bei einer Grenze
ab 0, aber ich habe einen Waechter eingebaut: lieber schweigen als eine
unsinnige Zahl nennen.

### "Der Teil mit dem Haupt-Repo fehlt"

Fred nach einem Abgleich. Und er hatte recht: unveraenderte Quellen fielen aus
dem gesprochenen Satz, weil "unveraendert" inhaltlich nichts beitraegt. Die
Ueberlegung war richtig, die Folge falsch -- **die Quelle verschwand ganz**, und
aus dem Gesagten liess sich nicht unterscheiden, ob sie geprueft und
unveraendert war oder gar nicht drankam.

Das ist dieselbe Verwechslung, die diese Woche schon mehrfach Zeit gekostet hat:
beim Direktbefehl, der am Wortstamm scheiterte, bei der verschluckten Ausnahme,
beim uebersprungenen Nachziehen. **Schweigen sieht aus wie Ausfall**, und der
Nutzer kann es nicht auseinanderhalten.

Jetzt wird jede Quelle genannt. Ab fuenf stillen nur noch gezaehlt -- eine
Aufzaehlung vorzulesen dauert laenger, als sie anzusehen. Sechs Faelle geprueft,
darunter der, in dem die einzige Quelle scheitert: dort faellt "Sonst war alles
auf dem neuesten Stand" weg, weil es kein Sonst gibt.

### Ein leerer Auftrag, der sich nicht abbrechen liess

Fred sagte "Hermes Aufgabe:" und danach, in einer zweiten Aeusserung, den
eigentlichen Auftrag. Der erste Satz startete bereits eine Recherche -- mit dem
Ausloesewort selbst als Forschungsfrage:

    hermes per Ausloesewort: 'Hermes Aufgabe'
    -q "Hermes Aufgabe"

Der Agent irrte drei Minuten durch Dateien, blockierte den echten Auftrag ("Es
laeuft schon eine Recherche") und war nicht zu stoppen. Auf "kannst du die
laufende Recherche abbrechen?" antwortete das Modell: **"da ich keine laufenden
Prozesse habe"** -- waehrend einer lief. Ohne Werkzeug erfindet es eines; das
steht seit Wochen als Grundsatz im Projekt und ist hier wieder eingetreten.

**Zwei Fehler.**

Der erste in `_thema()`: `return rest or text`. Bleibt nach dem Ausloesewort
nichts uebrig, wurde der ganze Text zum Thema. Fuer die Dokumentensuche ist das
sinnvoll -- der Satz ist ein brauchbarer Suchbegriff --, fuer einen
Rechercheauftrag ist es Unsinn. Jetzt gibt `_thema()` `""` zurueck, und **der
Aufrufer entscheidet**: Suche faellt auf den ganzen Satz zurueck, Recherche
fragt nach ("Was soll ich recherchieren?").

Der zweite: es gab keinen Abbruch. Neues Ausloesewort `abbruch`, geprueft VOR
`recherche` (sonst startet "Recherche abbrechen" eine neue).
`Recherche.abbrechen()` bricht die Aufgabe ab und **killt den Kindprozess** --
asyncio raeumt keine Fremdprozesse auf, hermes liefe sonst weiter. Geprueft:
nach dem Abbruch null hermes-Prozesse.

**Und beim Pruefen bin ich wieder in den Selbsttreffer gelaufen:**
`pgrep -f "hermes chat"` zaehlte drei Prozesse, und alle drei waren meine eigene
Shell und `ps` -- die Zeichenkette steht in ihrer Kommandozeile. Das ist die
dritte Wiederholung derselben Falle in dieser Woche.

### "Was bedeutet von Hand starten?"

Der Abgleich meldete 607 neue Abschnitte und verwies auf "starte bitte einmal
von Hand den Wissensbefehl einlesen". Fred fragte, was das heisst.

**Die Antwort war einfach, die Frage der eigentliche Befund.** Gemeint war
`./dienste.sh wissen einlesen` im Terminal -- aber am Laborrechner gibt es
keins, und am Futro erst recht nicht. Ein Assistent, der auf etwas verweist,
das man per Stimme nicht tun kann, hilft nicht weiter. Dieselbe Luecke wie bei
der Recherche, die sich nicht abbrechen liess: eine Handlung, die der Dienst
beherrscht, aber nicht anbietet.

Neues Ausloesewort `nachziehen` ("Wissen nachziehen", "Nachziehen",
"Unterlagen erschliessen"), und die Meldung nennt es jetzt. Der Befehl laeuft
OHNE die 300er-Grenze -- die schuetzt den beilaeufigen Fall nach einem
Abgleich; wer ihn ausdruecklich ruft, weiss dass es dauert.

**Der erste Test fand prompt den naechsten stummen Fall:** "Wissen nachziehen"
bei vollstaendigem Index sagte "ich melde mich" -- und dann nichts, weil die
Erfolgsmeldung an `if teile:` hing und nichts erledigt worden war. Bei einem
ausdruecklich angeforderten Lauf kommt jetzt IMMER eine Rueckmeldung ("Es war
schon alles erschlossen, nichts nachzuholen").

Das ist in dieser Woche der vierte Fall derselben Art. **Schweigen ist von
einem Ausfall nicht zu unterscheiden**, und ich baue ihn immer noch nach --
diesmal in genau der Funktion, die ich gegen dieses Problem geschrieben hatte.

Die 607 Abschnitte liefen nebenher durch: 180 s Schlagwoerter, 23 s
Kurzfassungen, 259 s Vektoren, null Fehlschlaege. Index jetzt 385 Dokumente,
5487 Abschnitte, alle drei Ebenen vollstaendig.

### Hermes kannte die Unterlagen nicht

Fred gab einen Auftrag zu den geometrischen Angaben der Saeule. Hermes
antwortete nach Minuten, er habe "sehr gruendlich in deiner gesamten
Session-Historie und im Repo gesucht" und finde nichts -- und fragte zurueck,
ob eine UI-Saeule oder eine 3D-Saeule gemeint sei.

**Mein Konstruktionsfehler von letzter Woche.** Ich hatte Hermes ein eigenes
Arbeitsverzeichnis gegeben, damit er nicht mehr ins Repo schreibt -- und ihm
dabei den Weg zu den Unterlagen abgeschnitten. Er sah nur die drei HTML-Dateien
seiner frueheren Auftraege. Was er durchsuchte, war seine eigene
Sitzungshistorie.

Lesen HAETTE er koennen: er hat Datei- und Terminalwerkzeuge und darf ueberall
lesen. **Er wusste nur nicht wo**, und der Auftragstext sagte es ihm nicht.

Geloest, indem der Dienst vorher sucht und acht Auszuege voranstellt -- mit der
Mischung aus Volltext und Vektoren, die ohnehin gebaut ist. Das ist besser als
ihm den Pfad zu nennen: `grep` ueber 5.635 Abschnitte findet weniger als eine
Suche, die auf Umschreibungen ausgelegt ist. Und er bekommt keinen
Schreibzugriff auf die Quellen.

Derselbe Auftrag danach: eine Masstabelle mit Aussendurchmesser, Gesamthoehe,
Wandstaerke, Kernbohrung und Spaltabstaenden, jede Zeile mit Quellenangabe,
270 s. **Aus "ich finde nichts" wurde die Antwort, nach der gefragt war.**

Nebenbei bestaetigt: die Buehnen-Wiederherstellung funktioniert. Beim Verbinden
kam Freds alte Recherche zurueck, sodass im Testprotokoll das Vorher und das
Nachher direkt untereinander standen.

### Tabellen und die Anzeigetafel

Zwei Dinge auf einmal: die Buehne sollte Markdown-Tabellen rendern, und
Messwerte sollten sich einblenden lassen.

**Tabellen.** Der Rechercheagent antwortet auf Massfragen mit Tabellen, und der
Renderer kannte sie nicht -- auf der Buehne stand eine Wand aus Pipe-Zeichen.
Zeilenweise geloest statt mit einem Regex ueber den ganzen Text: Tabellen sind
mehrzeilig, ein gieriger Ausdruck verschluckt den Absatz dahinter, und die
Zeilenumbrueche weiter unten zerreissen den Block. Erkannt wird nur, was
Kopfzeile UND Trennzeile hat. Dabei fielen zwei weitere Luecken auf: `---`
stand als Zeichenfolge da, und ein mehrzeiliges Zitat wurde in ebenso viele
Kaesten zerlegt.

**Anzeigetafel.** Neues Ausloesewort, vier Groessen (GPU, CPU, Speicher,
Platte) mal vier Darstellungen. Fred hatte "Anzeigeanpassung" vorgeschlagen und
um Alternativen gebeten -- "Anzeigetafel" passt besser zu den uebrigen
Komposita und spricht sich leichter; "Anzeige" allein faellt im Labor staendig.

Erkannt wird im Dienst, nicht vom Modell: sechzehn Kombinationen sind kein Fall
fuer einen Modellaufruf. **Und die Wortgrenzen im Muster sind kein Detail** --
ohne sie traf `ram` mitten in "DiagRAMm", und "CPU und GPU als Diagramm" zeigte
den Arbeitsspeicher mit an. Neun Testfaelle, alle richtig.

**Drei Fehler beim Bauen**, alle im Screenshot sichtbar geworden:

1. Ich hatte CSS-Variablen `--gruen/--gelb/--rot` benutzt, die es nicht gibt.
   Das Blatt kennt `--an`. Zwei Warnfarben ergaenzt, Gruen wiederverwendet.
2. Ein Wettlauf beim Seitenstart: `ablageLaden()` holt die Liste per fetch,
   gleichzeitig schickt der Dienst den letzten Buehnenstand nach. Wessen
   Antwort zuerst da ist, gewinnt -- im Screenshot stand der Kopf "Ablage"
   ueber den Messinstrumenten. Die wiederhergestellte Buehne hat jetzt Vorrang.
3. Das Diagramm streckte sechs Messpunkte auf eine feste 60-Sekunden-Achse und
   sah aus wie ein Defekt. Jetzt werden die vorhandenen Punkte ueber die volle
   Breite verteilt, mit Beschriftung "letzte N s" -- die Aufloesung waechst,
   statt dass die Kurve von links kriecht.

**Und ein Fehler in meinem eigenen Test:** ein `cd` in der Schleife blieb
wirksam, danach schlug der relative Pfad zum venv fehl. Nur der erste von drei
Befehlen kam an -- die Screenshots zeigten dreimal denselben Stand, und ich
haette daraus beinahe auf einen Fehler in der Wiederherstellung geschlossen.
