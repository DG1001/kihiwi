# aihiwi — Fachliche Beschreibung

Sprachgesteuerter Forschungsassistent für ein KI-Labor, vollständig lokal.
Ein Laborrechner mit Jabra-Freisprecher und Monitor steht im Labor, die
Rechenarbeit läuft auf dem GX10 im Serverraum, verbunden übers Netz.

## Die beiden Aufgaben

**Dokumentation.** Kontinuierlicher Mitschnitt des Laborgesprächs, aber nur wenn
ausdrücklich aktiviert. Daraus entstehen Transkripte und strukturierte Protokolle.

**Dialog.** Der Assistent wird per Aktivierungswort oder Taste angesprochen,
beantwortet Fragen und nimmt Anweisungen entgegen. Die Antwort kommt als Sprache
über den Freisprecher; ergänzend kann der Assistent Inhalte auf dem Monitor
anzeigen.

Beides ist gleichzeitig möglich: Der Assistent kann während einer laufenden
Aufzeichnung angesprochen werden und ebenso außerhalb davon.

**Die beiden Aufgaben haben gegensätzliche Anforderungen und sind deshalb
technisch getrennt.** Bei der Dokumentation zählt Qualität und Latenz ist
gleichgültig; im Dialog ist es umgekehrt und das Transkript wird nach dem Turn
verworfen. Wer beides durch dieselbe Pipeline zwingt, macht beides mittelmäßig.

## Sprecher und Sprache

**Meist eine Person**, deshalb ist keine Sprecher-Trennung nötig. Weil „meist"
nicht „immer" heißt — Besuch, Kollege, Telefonat im Raum — wird das **Roh-Audio
behalten**, nicht nur das Transkript. Damit bleibt die Entscheidung gegen
Diarisierung umkehrbar und ein abgeleitetes Protokoll gegen seine Quelle prüfbar.

**Deutsch mit englischen Fachbegriffen.** Das ist der schwierige Fall: Die
Spracherkennung muss auf Deutsch festgenagelt werden, sonst kippt sie bei
gehäuften englischen Begriffen ins Englische und übersetzt. Die Sprachausgabe hat
das umgekehrte Problem — eine deutsche Stimme spricht „Layer" als „La-yer".

## Die Vokabelliste

`testaudio/vokabular.txt` ist **fachlicher Projektbestandteil**, kein Hilfsmittel.
Sie speist drei Stellen:

1. den `initial_prompt` der Spracherkennung — größter Qualitätshebel bei
   Fachvokabular, kostet nichts;
2. die Nachkorrektur des Transkripts durch das Sprachmodell im Dokumentationspfad;
3. später die Aussprache-Overrides der Sprachausgabe.

Sie muss gepflegt werden, wenn neue Begriffe im Labor auftauchen.

## Latenzziel

**700 ms bis 1,2 s** vom Sprechende bis zum ersten Ton. Der größte Einzelposten
ist nicht das Sprachmodell, sondern das Endpointing — die Entscheidung „der
Mensch hat aufgehört zu sprechen". Belegt in [technisch.md](technisch.md).

Triviale Kommandos („lauter", „Aufzeichnung starten") sollten später über einen
Intent-Router am Sprachmodell vorbeigehen.

## Verfügbarkeit

**Der Assistent muss ohne Sprachmodell weiterarbeiten.** Der GX10 ist auch ein
Modell-Prüfstand; jeder Modellwechsel nimmt dem Assistenten für ein bis zwei
Minuten das Gehirn. In dieser Zeit laufen Aufzeichnung und Transkription weiter
und der Monitor zeigt an, dass gerade nur mitgeschrieben und nicht geantwortet
wird. Ein Assistent, der bei Modellwechsel schweigt, ist im Laborbetrieb
schlimmer als einer, der sagt, dass er gerade nicht kann.

## Recht und Betrieb

**Kontinuierliche Audioaufzeichnung fällt unter § 201 StGB.** Die Aufzeichnung
des nichtöffentlich gesprochenen Wortes ohne Einwilligung *aller* Beteiligten ist
strafbar — das ist kein bloßer Datenschutzverstoß. Daraus folgen harte
Anforderungen:

- **Harte Mute-Taste** am Jabra, mechanisch, nicht softwaregesteuert.
- **Sichtbarer Aufnahmeindikator** auf dem Laborbildschirm, aus jeder Ecke des
  Raums lesbar. Deshalb nimmt der Aufnahmebalken ein Drittel der Bildschirmhöhe
  ein — das ist eine Sicherheitsfunktion, keine Gestaltung.
- **Festes Löschkonzept** mit Aufbewahrungsfrist.
- **Datenschutzbeauftragte und Personalrat vor Inbetriebnahme**, nicht danach.

Stehende Auflage: **nichts verlässt das Netz.** Alle Modelle laufen lokal, es
gibt keinen Cloud-Dienst im Pfad.

## Offene fachliche Fragen

- **Soll der Assistent sehen können?** Whiteboard, Geräteanzeige, Messkurve
  abfotografieren und fragen „was steht da". Auf dem GX10 läuft immer nur ein
  großes Modell, also ist das eine Entscheidung gegen Textqualität und nicht
  zusätzlich möglich.
- **Welches Aktivierungswort?** Die vortrainierten Modelle sind englisch. Ein
  deutsches Wort braucht eigenes Training; pragmatisch wäre ein Wort, das in
  beiden Sprachen gleich klingt.
- **Zugriff auf die Inhalte von außen.** Der Assistent braucht einen Index über
  die Inhalte, keinen Dateiserver — eine Nextcloud allein löst das nicht.
