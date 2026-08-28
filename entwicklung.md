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
bleibt unter `-home-nutzer-Developer-github-com-aihiwi` liegen; neue Sitzungen
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
