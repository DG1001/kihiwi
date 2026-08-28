# kihiwi — Fachliche Beschreibung

Sprachgesteuerter Forschungsassistent für ein KI-Labor, vollständig lokal.
Ein Laborrechner mit Jabra-Freisprecher und Monitor steht im Labor, die
Rechenarbeit läuft auf dem GX10 im Serverraum, verbunden übers Netz.

## Die beiden Aufgaben

**Dokumentation.** Kontinuierlicher Mitschnitt des Laborgesprächs, aber nur wenn
ausdrücklich aktiviert. Beim Stoppen entsteht **automatisch** ein Protokoll mit
Transkript und Zusammenfassung; es ist danach sofort abrufbar — „Kiwi, was habe
ich vorhin über das Rasterelektronenmikroskop gesagt?"

**Dialog.** Der Assistent wird mit dem Aktivierungswort **„Kiwi"** oder per Taste
angesprochen. Zwei Wege, beide funktionieren:

- **„Kiwi" sagen, auf „Ja?" warten, dann sprechen.** Der verlässlichere Weg —
  man weiss, dass man gehört wurde, bevor man die Anweisung gibt.
- **„Kiwi, starte die Aufzeichnung"** in einem Zug.

Danach bleibt das Gespräch offen: Rückfragen brauchen kein Aktivierungswort
mehr, bis „Danke, Kiwi" fällt oder 45 Sekunden nichts kommt. Er
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

`vokabular.txt` ist **fachlicher Projektbestandteil**, kein Hilfsmittel.
Sie speist drei Stellen:

1. den `initial_prompt` der Spracherkennung — größter Qualitätshebel bei
   Fachvokabular, kostet nichts;
2. die Nachkorrektur des Transkripts durch das Sprachmodell im Dokumentationspfad;
3. später die Aussprache-Overrides der Sprachausgabe.

Sie muss zur Fachdomäne passen und gepflegt werden, wenn neue Begriffe
auftauchen. Eine fachfremde Liste ist nicht neutral, sondern schädlich: Sie zieht
Erkennung und Korrektur in die falsche Richtung. Stand 27.08.2026 ist sie ein
**Entwurf**, aus den ersten Aufnahmen zur Rasterelektronenmikroskopie gezogen.

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

## Was gehört auf welchen Kanal

**Sprache ist gut für manches und schlecht für anderes.** Die Aufteilung ist
bewusst gewählt, nicht gewachsen:

| Über Sprache | Über den Bildschirm |
|---|---|
| Aufzeichnung an/aus, während die Hände beschäftigt sind | Protokolle durchsehen und öffnen |
| Kurze Frage, kurze Antwort | Lange Texte lesen, vergleichen |
| Einen Rechercheauftrag abgeben | Rechercheergebnisse nachlesen |
| Diktieren, laut denken | Gezielt navigieren |

Blättern gehört nicht ins Mikrofon: Eine anklickbare Liste kann nicht
missverstanden werden, „zeig mir das Protokoll" schon. Die **Ablage** im Client
listet Protokolle und Recherchen, neueste zuerst, zum Anklicken.

## Auslösewörter

Wer eines dieser Wörter sagt, bekommt genau diesen Weg — ohne dass das
Sprachmodell darüber entscheidet:

| Wort | Wirkung |
|---|---|
| `Internetsuche` | eine Tatsache schnell aus dem Netz, Antwort sofort |
| `Internetrecherche` | gründliche Recherche, Ergebnis kommt in einigen Minuten |
| `Dokumentenrecherche` | Suche in den eigenen Unterlagen |
| `Hermesaufgabe` | Anweisung unverändert an den Rechercheagenten |
| `Kiwihilfe` | zählt auf, was Kiwi versteht |

## Zwei Stufen beim Antworten

**Nachschlagen** (Sekunden): Der Assistent durchsucht die Unterlagen und
antwortet mit Quellenangabe. Deckt den Laboralltag ab.

**Rechercheauftrag** (Minuten): Fragen, die Vergleichen, Nachlesen oder mehrere
Suchschritte brauchen, gehen an einen Rechercheagenten. Der Assistent sagt zu
und meldet sich, wenn das Ergebnis da ist — man arbeitet währenddessen weiter.
Es läuft immer nur ein Auftrag; der Monitor zeigt ihn an.

Ergebnisse von Stufe 2 sind **abgeleitet** und werden als solche gekennzeichnet.
Vor der Verwendung im Protokoll gegen die genannten Quellen prüfen.

## Offene fachliche Fragen

- **Soll der Assistent sehen können?** Whiteboard, Geräteanzeige, Messkurve
  abfotografieren und fragen „was steht da". Auf dem GX10 läuft immer nur ein
  großes Modell, also ist das eine Entscheidung gegen Textqualität und nicht
  zusätzlich möglich.
- **Zugriff auf die Inhalte von außen.** Der Assistent braucht einen Index über
  die Inhalte, keinen Dateiserver — eine Nextcloud allein löst das nicht.
