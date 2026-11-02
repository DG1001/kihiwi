# Lagebild: der Projektstand im normalen Gespräch

Stand: 02.11.2026 · Status: Design, nach Builder-Prüfung überarbeitet

## Wozu

Im normalen Gespräch kommen immer wieder dieselben vier Fragen:

- Wie ist der Stand des Projekts?
- Was steht als Nächstes an?
- Welche Probleme und Herausforderungen sind gerade aktuell?
- Was kann ich vorzeigen?

Heute laufen sie in die Wissenssuche. Die liefert acht Ausschnitte, die zu
einer bestimmten Stelle passen. Diese Fragen fragen aber nicht nach einer
Stelle, sondern nach einem Lagebild: Es setzt sich aus vielen Dokumenten,
Commits und Protokollen zusammen und ändert sich laufend (im Hauptrepo zuletzt
über 60 Commits in zwei Wochen). Die Antworten sind deshalb dünn oder langsam.

Das Lagebild ist ein kurzes, jede Nacht neu erzeugtes Dokument. Kiwi hat
davon immer einen kleinen Kern im Prompt und bei diesen Fragen den passenden
Abschnitt dazu. Damit kommen die Antworten kaum langsamer als beim Plaudern,
und für Tieferes bleiben die Wissenssuche und Hermes.

Die Wissensstufen, in die sich das einordnet:

| Stufe | Wann | Wie schnell |
|---|---|---|
| 0 | Plaudern, Allgemeinwissen | sofort, nur das Modell |
| **1** | **Projektstand, Grundlagen** | **etwa 1 s mehr als Plaudern, Lagebild im Prompt (dieses Design)** |
| 2 | eine bestimmte Stelle in den Unterlagen | Sekunden, Wissenssuche (gibt es) |
| 3 | gründliche Recherche | Minuten, Hermes auf Anweisung (gibt es) |

## Ziele

- Die vier Fragen werden aus dem Lagebild beantwortet, gesprochen in höchstens
  zwei Sätzen, ausführlich auf der Bühne.
- Die Antwort kommt höchstens etwa 1 s später als bei einer Plauderfrage. Dafür
  bleibt im Prompt nur, was die Frage braucht (siehe „Nutzung im Gespräch“).
- Jeder Punkt im Lagebild nennt seine Quelle und ihr Datum. Kiwi kann bei
  Nachfrage gezielt tiefer suchen.
- Das Lagebild entsteht ohne menschliches Zutun und ist deutlich als
  abgeleitet gekennzeichnet.
- Fehler lassen sich per Stimme sofort korrigieren.

## Nicht-Ziele

- Keine Bewertung des Projekts, keine Prognosen. Das Lagebild fasst zusammen,
  was in den Quellen steht.
- Keine Änderung der Wissenssuche (Stufe 2) und keine Einordnung der Ordner
  nach Wichtigkeit. Beides kann folgen, ist aber nicht Teil dieses Designs.
- Kein Vergleich über Wochen („was hat sich seit letzter Woche getan“). Die
  Ablage mit Datum macht ihn später möglich; gebaut wird er hier nicht.

## Anti-Ziele (wäre ein Fehlschlag, auch wenn es läuft)

- Das Lagebild behauptet etwas, das in keiner Quelle steht. Lieber ein Punkt
  weniger als ein erfundener.
- Normale Fragen werden spürbar langsamer: mehr als etwa 1 s, weil der Prompt
  länger wird.
- Das Modell ruft in der Werkzeugrunde seltener Werkzeuge auf, weil der Prompt
  länger wird. Gemessen wurde schon einmal: langer Prompt 0 von 3
  Werkzeugaufrufen, kurzer 3 von 3.
- Die nächtliche Erzeugung schaltet in einen fremden Lauf hinein, der die
  Maschine belegt hat.

## Entschieden

Vom Auftraggeber festgelegt:

1. **Automatisch, mit Kennzeichnung.** Das Lagebild gilt sofort, ohne
   Freigabe. Der Kopf sagt: „Lagebild vom <Datum>, automatisch abgeleitet,
   nicht geprüft.“
2. **Korrektur per Stimme.** Eine Korrektur gilt ab sofort. In jedes folgende
   Lagebild fließt sie als Quelle mit dem höchsten Vorrang ein, bis eine
   neuere Quelle das Thema aufgreift, höchstens zwei Wochen lang.
3. **Fünf Abschnitte**, in dieser Reihenfolge:
   1. Worum es geht (Grundlagen, wenige Sätze, ändert sich selten)
   2. Stand: woran gearbeitet wird, was seit dem letzten Lagebild fertig wurde
   3. Als Nächstes
   4. Probleme und Herausforderungen
   5. Vorzeigbar: Ergebnisse, Abbildungen, Vorträge, Demos
4. **Wann:** automatisch nur nachts, nach dem nächtlichen Wissensabgleich.
   Auf Zuruf nur nach Ansage („Ich bin dann ein paar Minuten nicht
   ansprechbar“) und ausdrücklichem Ja. Grund: Der Modellserver hat einen
   einzigen Slot; während der Erzeugung ist Kiwi blockiert, nicht nur langsamer.
5. **Wie Kiwi es nutzt:** Der Modellserver speichert gleiche Prompt-Anfänge
   praktisch nicht zwischen (siehe Randbedingungen). Deshalb steht immer nur
   ein kleiner Kern („Worum es geht“, etwa 300 bis 400 Token) im Prompt, bei
   den vier Fragen zusätzlich der passende Abschnitt (etwa 500 Token). Das
   ganze Lagebild erscheint nur auf der Bühne. Das model-switch-Profil wird
   nicht geändert; es ist mit anderen Nutzern geteilt und Teil eines
   veröffentlichten Rezepts.
6. **Ablage:** Jedes Lagebild bleibt mit Datum liegen.

## Aufbau

### Eingaben

Der Erzeuger wählt seine Eingaben selbst und deterministisch aus, nicht ein
Agent. So ist nachvollziehbar, was eingeflossen ist. Die Eingaben stehen mit
Quelle und Datum in einer Liste, die mit dem Lagebild abgelegt wird.

| Eingabe | Herkunft | Hinweis |
|---|---|---|
| Korrekturen seit dem letzten Lagebild | Ablage der Korrekturen | höchster Vorrang |
| das vorige Lagebild | Ablage | nur Kontext für „seit dem letzten Lagebild“, **nicht zitierbar** — sonst trüge sich jeder Punkt, auch ein falscher, mit gültigem Verweis selbst weiter |
| Statusdokumente | Index, nach Mustern aus der Konfiguration | z. B. Stand, Übersicht, offene Punkte, offene Entscheidungen, Pläne |
| Commit-Nachrichten der letzten N Tage | Git-Quellen unter `wissen/repos/` | Betreff und Text, gekürzt |
| Zusammenfassungen der Gesprächsprotokolle der letzten N Tage | `aufnahmen/*/protokoll.md` | nur der Abschnitt Zusammenfassung |
| vorzeigbare Dinge | Index und Bilderkatalog, nach Mustern aus der Konfiguration | Vorträge, Abbildungen, Demos |

Die Muster, die Zahl der Tage und ein Gesamtbudget für die Eingaben stehen in
einer Konfigurationsdatei, die nicht ins Repo gehört (wie `quellen.json`); ein
Muster liegt bei (`lagebild.beispiel.json`). Welche Dateien eines Labors als
Statusdokumente taugen, ist Sache der Konfiguration, nicht des Codes.

### Erzeugung

- Ein Aufruf an das Modell auf `:8889` über die bestehende Anbindung
  (`llm.antwort_text`), ohne Werkzeuge, Temperatur niedrig.
- Vorgabe an das Modell: die fünf Abschnitte, je Abschnitt höchstens eine
  feste Zahl von Punkten, jeder Punkt mit Verweis auf eine Eingabe in der Form
  `[Quelle, Datum]`; nichts aufnehmen, was nicht in den Eingaben steht;
  Korrekturen haben Vorrang vor allem anderen.
- Zielgröße des Lagebilds: höchstens 2.500 Token.

### Prüfung im Code

Bevor ein Lagebild gilt:

- Jeder Punkt muss auf eine Eingabe verweisen, die es gab. Punkte ohne
  gültigen Verweis werden entfernt und im Erzeugungsprotokoll gezählt.
- Alle fünf Abschnitte müssen vorhanden sein; ein leerer Abschnitt sagt das
  ausdrücklich („Keine Angaben in den Quellen“).
- Die Größe muss unter dem Budget liegen, sonst wird gekürzt oder neu erzeugt.
- Scheitert die Erzeugung, gilt das vorige Lagebild weiter, und sein Kopf sagt,
  wie alt es ist.

### Ablage

Außerhalb des Repos, unter `zustand/lagebild/`:

- `<Datum>.md`: das Lagebild
- `<Datum>.eingaben.json`: welche Eingaben mit welchem Datum eingeflossen sind,
  und wie viele Punkte die Prüfung entfernt hat
- `korrekturen.jsonl`: Korrekturen mit Zeitpunkt, Wortlaut und Status
  (offen, abgelöst durch <Quelle, Datum>, abgelaufen, zurückgenommen)

Das jüngste gültige Lagebild ist das aktuelle.

### Nutzung im Gespräch

- **Kern im Prompt.** Der Abschnitt „Worum es geht“ (etwa 300 bis 400 Token)
  und die offenen Korrekturen stehen in jedem Prompt der Schlussantwort. Mehr
  nicht: Der Modellserver liest jeden Prompt praktisch neu ein, jedes Token
  kostet also bei jeder Antwort Zeit (siehe Randbedingungen).
- **Abschnitt bei Bedarf.** Bei den vier Fragen kommt der passende Abschnitt
  dazu (etwa 500 Token); bei „Wie ist der Stand?“ der Abschnitt Stand usw. Das
  ganze Lagebild steht nur auf der Bühne.
- **Werkzeugrunde.** Ob der Kern auch im Prompt der Werkzeugrunde steht,
  entscheidet eine Messung (siehe „Abnahme“): Er gehört dort nur hin, wenn die
  Werkzeugaufrufe dadurch nicht seltener werden.
- **Eigene Absicht für die vier Fragen.** Fragen nach Stand, nächsten
  Schritten, Problemen oder Vorzeigbarem bekommen eine eigene Absicht: keine
  Vorabsuche, keine Werkzeuge, Antwort aus dem Lagebild, der passende Abschnitt
  auf die Bühne. Ohne diese Absicht liefe die Frage in die Wissenssuche, und
  deren Ausschnitte mit der Anweisung, sich daran zu halten, würden das
  Lagebild verdrängen.
- **Antwort.** Höchstens zwei Sätze, mit dem Hinweis, dass es ein abgeleitetes
  Lagebild vom <Datum> ist, wenn danach gefragt wird oder es älter als zwei
  Tage ist.
- **Weiter in die Tiefe.** Fragt man nach einem Punkt nach, läuft die normale
  Wissenssuche. Heute sucht sie mit dem wörtlichen Nutzertext, bei kurzen
  Nachfragen mit der vorigen Frage davor; das Modell formuliert keinen
  Suchbegriff. Deshalb merkt sich der Dienst die Quellen der letzten
  Lagebild-Antwort und hängt ihre Titel bei einer kurzen Nachfrage im offenen
  Gespräch an den Suchtext an. Nachfragen nach einer Lagebild-Antwort gehen in
  die Wissenssuche, nicht wieder in die Lagebild-Absicht.

### Korrektur per Stimme

- Auslösewort, zusammengesetzt wie die übrigen: **„Lagebildkorrektur“**,
  gefolgt vom Inhalt („Kiwi, Lagebildkorrektur: der Teststand läuft
  wieder“).
- Kiwi wiederholt den Inhalt kurz („Notiert: …“) und legt die Korrektur mit
  Zeitpunkt ab. Ab dem nächsten Zug steht sie im Prompt unter dem Kern,
  gekennzeichnet als Korrektur vom <Zeit>.
- **„Lagebildkorrektur zurücknehmen“** entfernt die jüngste offene Korrektur.
- **Wie lange sie gilt:** In jedes folgende Lagebild geht eine offene Korrektur
  als Eingabe mit höchstem Vorrang ein, bis eine neuere Quelle das Thema
  aufgreift, höchstens zwei Wochen. Ob eine Quelle sie ablöst, schlägt das
  Modell bei der Erzeugung vor (Korrektur und Quelle mit Verweis); der Code
  übernimmt das nur, wenn die Quelle unter den Eingaben ist und ein Datum nach
  der Korrektur trägt. Sonst bleibt die Korrektur offen. Nach zwei Wochen läuft
  sie ab.

### Anzeige

- **„Lagebild“** zeigt das ganze aktuelle Lagebild auf der Bühne, mit Kopf
  und Datum.
- Bei einer der vier Fragen zeigt die Bühne den passenden Abschnitt.
- Vorzeigbares nennt, wo möglich, den Befehl zum Zeigen („Bildanzeige …“).

### Zeitplan und Betrieb

- **Nachts:** ein Aufruf `./dienste.sh lagebild erneuern` aus der Crontab
  (wie die Log-Rotation gehört der Eintrag zur Maschine, nicht ins Repo). Er
  macht erst einen Wissensabgleich, dann das Lagebild.
- **Auf Zuruf:** „Lagebild erneuern“. Kiwi sagt vorher: „Ich bin dann ein
  paar Minuten nicht ansprechbar. Soll ich?“ und erzeugt erst nach einem Ja.
  Ohne Ja innerhalb des offenen Gesprächs passiert nichts.
- **Tagsüber kein automatisches Erneuern**, auch nicht nach einem Abgleich per
  Stimme: Der Modellserver hat einen Slot, die Erzeugung blockiert Kiwi für
  Minuten, Antworten brächen nach 45 s ab.
- **Belegung, streng:** Hält jemand anderes an der optionalen Belegungsstelle
  eine exklusive Belegung — gleich für welches Modell —, wartet der Lauf und
  versucht es später erneut, nachts bis zu einer festen Uhrzeit; danach gilt
  das vorige Lagebild weiter, mit seinem Alter im Kopf. Läuft er, belegt er die
  Maschine selbst exklusiv, damit niemand mitten in der Erzeugung umschaltet.
  Er erzeugt nur, wenn auf `:8889` das Modell läuft, das der Sprachdienst
  erwartet; einen Modellwechsel löst er nicht aus.
- **Kommandozeile:** `./dienste.sh lagebild [erneuern|zeigen]`.

## Randbedingungen

- Alles lokal; nichts verlässt das Netz außer dem `git pull` des Abgleichs.
- Qwen3.8-Flash liest auf llama.cpp rund 480 bis 550 Token/s ein. Die
  Erzeugung mit einigen zehntausend Token Eingabe dauert damit Minuten; nachts
  ist das egal, im Gespräch nicht.
- **Keine Zwischenspeicherung des Prompt-Anfangs.** Der Server hat einen Slot
  (`--parallel 1`), zwischen zwei Antworten kommen andere Anfragen (etwa die
  Prüfung, ob die Äußerung fertig ist), und das Modell setzt nur an
  Checkpoints auf. Gemessen im Serverprotokoll: Anfragen mit 30 bis 50 %
  gleichem Anfang lesen trotzdem mehrere tausend Token neu ein. Jedes Token im
  Prompt kostet also bei jeder Antwort rund 2 ms. Für Nemotron über vLLM ist
  das ungeprüft; das Design verlässt sich auf keines von beiden.
- Das Repo ist öffentlich: keine Namen von Projekten, Dateien oder Personen
  aus den Unterlagen in Code, Beispielen oder Tests. Statusdokumente werden
  nur über die Konfiguration benannt.
- Der Code entscheidet, das Modell formuliert. Die Absicht für die vier
  Fragen, die Auslösewörter und die Prüfung der Verweise sind Code.

## Abnahme

Gemessen, warm, mit dem Vorgabemodell (Qwen3.8-Flash) und dem Profil mit
Nemotron:

1. **Geschwindigkeit:** Die vier Fragen bis zum ersten Ton höchstens etwa
   1 s langsamer als eine Plauderfrage; Plauderfragen mit dem Kern im Prompt
   höchstens etwa 1 s langsamer als heute.
2. **Werkzeuge:** Einen festen Satz Prüffragen gibt es noch nicht. Der Builder
   legt ihn an (je Werkzeug drei Fragen, je dreimal gestellt) und legt ihn ins
   Repo. Sie müssen mit dem Block im Prompt der Werkzeugrunde mindestens so oft
   ein Werkzeug auslösen wie ohne. Ohne diesen Nachweis bleibt der Block aus
   der Werkzeugrunde heraus.
3. **Treue:** In einem erzeugten Lagebild hat jeder Punkt einen gültigen
   Verweis; Stichprobe von zehn Punkten gegen ihre Quelle von Hand geprüft.
4. **Korrektur:** Eine Korrektur wirkt ab dem nächsten Zug, übersteht einen
   Neustart des Sprachdienstes, steht im nächsten Lagebild und bleibt offen,
   solange keine neuere Quelle sie ablöst; nach zwei Wochen läuft sie ab
   (mit gefälschtem Datum prüfbar).
5. **Absicht:** Die vier Fragen und je zwei Umformulierungen landen in der
   neuen Absicht; zehn andere Wissensfragen nicht.

## Meine Entscheidungen

Vom Auftraggeber nach der Builder-Prüfung bestätigt: kleiner Kern statt ganzes
Lagebild im Prompt, kein Ändern des Modellprofils, automatisch nur nachts,
Korrektur bis zur Ablösung durch eine neuere Quelle (höchstens zwei Wochen).
Die folgenden Punkte habe ich selbst entschieden:

- **Ein Dokument statt zwei:** Die Grundlagen sind der erste Abschnitt des
  Lagebilds, kein eigenes, von Hand gepflegtes Dokument.
- **Eingaben deterministisch, nicht über Hermes.** Nachvollziehbar und ohne
  Agentenlauf in der Nacht.
- **Auslösewörter** „Lagebild“, „Lagebildkorrektur“, „Lagebildkorrektur
  zurücknehmen“, „Lagebild erneuern“ — zusammengesetzt, damit sie im normalen
  Gespräch nicht versehentlich fallen.
- **Höchstens 2.500 Token** fürs ganze Lagebild (es steht nur auf der Bühne),
  Kern etwa 300 bis 400, je Abschnitt etwa 500.
- **Das vorige Lagebild ist nicht zitierbar**, nur Kontext.
- **Nachfragen** nach einer Lagebild-Antwort suchen mit den Quellen der
  Antwort und gehen in die Wissenssuche.
- **Belegung streng:** bei jeder fremden exklusiven Belegung warten; der Lauf
  belegt selbst exklusiv und wechselt kein Modell.
- **Prüffragen für Werkzeuge** legt der Builder als festen Satz an.

## Dem Builder überlassen

- Die genauen Erkennungsmuster der neuen Absicht, solange die Abnahme 5 erfüllt
  ist.
- Prompt-Wortlaut für die Erzeugung, Zahl der Punkte je Abschnitt, Zahl der
  Tage für Commits und Protokolle, Budget der Eingaben.
- Aufbau der Konfigurationsdatei und des Beispiels.

## Außerhalb des Umfangs (später möglich)

- Ordner nach Wichtigkeit einordnen (Kern, Referenz, Ergebnisse, Rohdaten,
  veraltet) und die Wissenssuche danach gewichten.
- Tiefe Suche auf Zuruf mit Reranker und ganzen Dokumenten.
- Vergleich zweier Lagebilder („seit letzter Woche“).
- Das Embedding-Modell an die eigenen Unterlagen anpassen.
