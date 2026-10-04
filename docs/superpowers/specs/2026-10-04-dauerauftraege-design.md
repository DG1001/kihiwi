# Daueraufträge — Entwurf

Stand 04.10.2026. Abgestimmt im Gespräch, noch nicht gebaut.

## Wozu

Kiwi reagiert heute nur auf Zuruf. Ein Dauerauftrag bleibt bestehen: er wird
von einem Ereignis oder einem Zeitplan ausgelöst, prüft etwas und legt einen
Befund ab. Drei Dinge sollen so laufen:

- **Neue Stände prüfen** — kommt in einer Git-Quelle ein neuer Commit, wird er
  gesichtet und gegen die eigenen Unterlagen gehalten.
- **Protokolle nachhalten** — nach jeder Aufzeichnung werden Zusagen, offene
  Punkte und Widersprüche zu Früherem herausgezogen.
- **Thema beobachten** — ein Thema wird im Netz verfolgt; gemeldet wird nur,
  was neu ist.

Ein Dauerauftrag **liest und berichtet**. Er ändert nichts, weder in den
Quellen noch im Wissensindex.

## Entscheidungen

| Frage | Entscheidung |
|---|---|
| Wer führt aus? | Ein eigener, schlanker Lauf. Kiwi sammelt den Stoff fest verdrahtet, das Modell bewertet ihn in einem Aufruf. Kein Agent mit Werkzeugen. |
| Wie meldet sich Kiwi? | Gar nicht von selbst. Der Befund liegt in der Ablage; auf „Auftragslage" fasst Kiwi zusammen. |
| Wo stehen die Aufträge? | In einer Datei, nicht per Stimme angelegt. Per Stimme wird nur gesteuert. |
| Was ist mit Hermes? | Bleibt für Rechercheaufträge. Eine spätere Vertiefung von Web-Funden durch Hermes ist möglich, hier nicht vorgesehen. |

Hintergrund der ersten Entscheidung: ein Versuch mit einem fertigen
Agenten-Laufwerk am 04.10.2026 scheiterte zunächst am Zeitlimit, weil es bei
jedem Schritt rund 53.000 Token Repo-Abbild mitschickte (99 s reines
Einlesen), und der Wächter hielt das rechnende Modell für tot. Kleiner Stoff
und kurze Aufrufe sind deshalb die Vorgabe, nicht die Optimierung.

## Die Auftragsdatei

`auftraege.json` im Projektwurzelverzeichnis, **nicht versioniert** — die
Ziele verraten, woran gearbeitet wird, und dieses Repo ist öffentlich. Im
Repo liegt `auftraege.beispiel.json` als Rückfall und Vorlage, wie bei
`quellen.json` und `vokabular.txt`.

```json
{
  "abgleich_minuten": 30,
  "auftraege": [
    {"name": "stand-hauptrepo", "art": "stand", "aktiv": true,
     "quelle": "beispielrepo",
     "ziel": "Prüfe neue Commits gegen die Spezifikation und die Liste offener Entscheidungen. Melde Widersprüche und berührte offene Punkte.",
     "dazu": ["Spezifikation", "offene Entscheidungen"]},
    {"name": "protokoll-nachhalten", "art": "protokoll", "aktiv": true,
     "ziel": "Ziehe Zusagen, offene Punkte und Widersprüche zu früheren Protokollen heraus."},
    {"name": "thema-beispiel", "art": "thema", "aktiv": true,
     "ziel": "Melde neue Veröffentlichungen und Produktankündigungen.",
     "suchen": ["beispielbegriff eins", "beispielbegriff zwei"],
     "alle_stunden": 24}
  ]
}
```

| Feld | Bedeutung |
|---|---|
| `name` | eindeutig, kleingeschrieben, mit Bindestrich; wird gesprochen („Auftragslauf …") |
| `art` | `stand`, `protokoll` oder `thema` |
| `ziel` | was geprüft werden soll, in Worten; geht wörtlich an das Modell |
| `quelle` | nur `stand`: Name einer Git-Quelle aus `quellen.json` |
| `dazu` | nur `stand`: Suchbegriffe, mit denen Vergleichsstoff aus dem Wissensindex geholt wird |
| `suchen` | nur `thema`: Suchanfragen für SearXNG |
| `alle_stunden` | nur `thema`: Abstand der Läufe |
| `abgleich_minuten` | Abstand des automatischen Abgleichs; `0` schaltet ihn ab |

Eine fehlerhafte Datei wird beim Laden abgewiesen, mit Meldung im Protokoll
und auf der Bühne; es läuft dann **kein** Auftrag. Ein halb gelesener
Auftragsbestand ist schlimmer als keiner.

## Auslöser

| Art | Auslöser | Woran erkannt |
|---|---|---|
| `stand` | neuer Commit in der Quelle | der Abgleich meldet für die Quelle einen anderen HEAD als vorher; der Bereich `vorher..nachher` wird mitgegeben |
| `protokoll` | ein Protokoll ist fertig | Ende von `nachbereiten()`, nachdem das Protokoll eingelesen ist |
| `thema` | Zeitplan | letzter Lauf liegt länger als `alle_stunden` zurück |

**Der Abgleich läuft künftig auch von selbst**, alle `abgleich_minuten`
(Vorgabe 30). Heute stößt ihn nur das Wort „Wissensabgleich" an. Das ist
regelmäßiger Netzverkehr zu den Git-Servern. Der automatische Lauf nimmt
dasselbe Schloss `ABGLEICH` und denselben Weg wie der gesprochene; er
spricht nichts.

Dazu muss `einlesen.git()` den Commit-Bereich herausgeben. Heute steht er
nur in der Ausgabe; der Bericht von `alles()` bekommt je Quelle die Felder
`vorher` und `nachher`.

Ein ausgelöster Lauf kommt in eine **Warteschlange** in
`zustand/auftraege.json`. Sie überlebt einen Neustart des Dienstes. Derselbe
Auftrag steht höchstens einmal darin: kommen drei Commits, während einer
wartet, wird der Bereich erweitert, nicht dreimal gelaufen.

## Der Lauf

Drei Schritte, für alle Arten gleich:

**1. Stoff sammeln** — fest verdrahtet, ohne Modell, höchstens rund 6.000
Token (24.000 Zeichen). Jedes Stück trägt eine Kennung und eine Herkunft.

| Art | Stoff |
|---|---|
| `stand` | Commit-Texte und Diff des Bereichs (gekürzt je Datei), dazu die besten Abschnitte aus dem Wissensindex zu den Begriffen in `dazu` |
| `protokoll` | das neue Protokoll, dazu die ähnlichsten Abschnitte aus früheren Protokollen und Unterlagen |
| `thema` | Titel, Auszug und Adresse der Treffer aus SearXNG, ohne die Adressen, die dieser Auftrag schon gesehen hat |

Reicht der Platz nicht, wird gekürzt und im Befund vermerkt, **was** fehlt.
Ist kein Stoff da (bei `thema`: nichts Neues), endet der Lauf ohne
Modellaufruf als „nichts zu prüfen".

**2. Bewerten** — ein Modellaufruf über `llm.py`, mit Denkschalter wie bei
den Stapelläufen. Verlangt wird JSON:

```json
{"auffaellig": true,
 "kurz": "Ein Satz, der vorgelesen werden kann.",
 "punkte": [
   {"aussage": "Was auffällt.",
    "beleg": "wörtliches Zitat aus dem Stoff",
    "stueck": "S3"}
 ]}
```

**3. Belege prüfen** — für jeden Punkt wird geprüft, ob `beleg` wörtlich im
genannten Stück steht (Leerraum und Groß-/Kleinschreibung angeglichen).
Steht er nicht dort, wird der Punkt als **unbelegt** markiert. Bleibt kein
belegter Punkt, gilt der Lauf als „nichts gefunden", auch wenn das Modell
`auffaellig` gesetzt hat. Unbrauchbares JSON wird einmal neu angefordert;
scheitert auch das, ist der Lauf gescheitert und steht so im Prüfprotokoll.

Das Modell entscheidet also nicht, was es liest, und nicht allein, was als
Fund gilt.

## Ablage und Prüfprotokoll

**Befund.** Ein Lauf mit Fund schreibt
`befunde/<auftrag>/<zeitstempel>.md` — nicht versioniert, wie `recherchen/`.
Kopf: Auftrag, Art, Auslöser (Commit-Bereich, Protokoll oder Suchanfragen),
Modell, Dauer, Hinweis auf Gekürztes. Darunter die Punkte mit Beleg und
Herkunft, zuletzt die unbelegten, deutlich abgesetzt. Über allem steht
derselbe Warnhinweis wie über Rechercheergebnissen: **abgeleitet**, vor
Verwendung gegen die Quellen prüfen.

**Ablage.** `_sammlung()` bekommt die Art `befund`. Befunde erscheinen in der
Ablage der Bühne und lassen sich dort öffnen und löschen wie Recherchen. Ein
neuer Befund setzt einen Merker in den Verlauf, wenn ein Klient verbunden
ist.

**Kein Rückfluss.** `befunde/` ist keine Quelle des Wissensindex. Ein
späterer Lauf würde sonst Abgeleitetes als Unterlage lesen.

**Gelesen.** `zustand/auftraege.json` hält je Befund fest, ob er schon
berichtet wurde.

**Prüfprotokoll.** Jeder Lauf, auch ohne Fund und auch gescheitert, hängt
eine Zeile an `zustand/auftraege.log.jsonl`: Zeit, Auftrag, Auslöser, die
Kennungen und Herkünfte der Stoffstücke, Modell, Dauer, Urteil
(`fund`, `nichts`, `nichts zu prüfen`, `gescheitert`, `abgebrochen`), Zahl
der belegten und unbelegten Punkte.

## Steuerung per Stimme

Drei Auslösewörter, zusammengesetzt wie die übrigen:

| Wort | Wirkung |
|---|---|
| `Auftragslage` | Kiwi nennt die ungelesenen Befunde in zwei, drei Sätzen (die `kurz`-Sätze) und zeigt auf der Bühne alle Aufträge mit letztem Lauf, Urteil und Zustand. Danach gelten die genannten Befunde als gelesen. |
| `Auftragslauf <Name>` | stellt den Auftrag sofort in die Warteschlange. Bei `stand` ohne neuen Commit: der letzte Commit wird geprüft. |
| `Auftragspause <Name>` | hält den Auftrag an. Mit „fortsetzen" im Satz nimmt er ihn wieder auf. Der Zustand liegt in `zustand/auftraege.json`, nicht in der Auftragsdatei. |

Der Name wird unscharf zugeordnet (Bindestriche als Leerzeichen, Abstand wie
bei den Auslösewörtern). Passt keiner oder passen zwei, nennt Kiwi die
vorhandenen Namen.

Gibt es nichts Ungelesenes, sagt „Auftragslage" genau das, mit dem Zeitpunkt
des letzten Laufs — damit Stille nicht wie ein Ausfall aussieht.

## Rücksicht auf das Modell

Das Modell hat je nach Profil **einen** Platz. Daraus folgt:

- **Nur im Leerlauf.** Ein Lauf startet nur, wenn kein Gespräch offen ist,
  keine Recherche und keine Nachbereitung läuft und das Modell erreichbar
  ist. Sonst bleibt er in der Warteschlange; geprüft wird im Takt der
  Gesundheitsprüfung.
- **Einer zur Zeit.** Ein eigenes Schloss `AUFTRAG`.
- **Stimme hat Vorrang.** Wird Kiwi angesprochen, während ein Lauf rechnet,
  wird der Aufruf abgebrochen (Urteil `abgebrochen`) und der Lauf wieder
  eingereiht. Nach drei Abbrüchen in Folge wartet er zehn Minuten.
- **Belegungsstelle.** Der Lauf meldet sich als Mitbenutzer an und nach dem
  Ende ab. Ist die Stelle nicht erreichbar, läuft er trotzdem.
- **Zeitgrenze.** 120 s je Modellaufruf. Bei 6.000 Token Stoff liegt das
  Einlesen bei gemessenen rund 530 Token je Sekunde um 12 s.

Der Wächter prüft das Modell mit einer eigenen Anfrage und 30 s Grenze. Ein
Lauf dieser Größe bleibt darunter; der Verdrängungsschutz in `model-switch`
greift zusätzlich.

## Netz

`thema`-Aufträge schicken ihre Suchanfragen nach Zeitplan über SearXNG an
fremde Suchmaschinen, ohne dass jemand fragt. Das ist neu: bisher verließ nur
eine ausdrücklich gestellte Frage die Maschine. Der Schalter `KIHIWI_WEB`
schaltet auch diese Läufe ab; sie enden dann als „nichts zu prüfen" mit
Vermerk. Vom Stoff der anderen Arten verlässt nichts die Maschine.

## Aufbau

| Datei | Aufgabe |
|---|---|
| `sprachdienst/auftraege.py` (neu) | Auftragsdatei laden und prüfen, Warteschlange, Zustand, Prüfprotokoll |
| `sprachdienst/auftrag_lauf.py` (neu) | Stoff sammeln je Art, Bewerten, Belege prüfen, Befund schreiben |
| `wissen/einlesen.py` | Commit-Bereich in den Bericht von `alles()` |
| `sprachdienst/absicht.py` | drei Auslösewörter mit Beschreibung |
| `sprachdienst/gateway.py` | automatischer Abgleich, Anstoß nach Abgleich und Nachbereitung, Leerlaufprüfung, Vorrang der Stimme, `befund` in der Ablage, Bühnentyp `auftragslage` |
| `sprachdienst/klient.html` | Darstellung von `auftragslage` und der Art `befund` |
| `auftraege.beispiel.json`, `.gitignore` | Vorlage; `auftraege.json` und `befunde/` ausgenommen |
| `fachlich.md`, `technisch.md`, `entwicklung.md` | Doku |

`auftrag_lauf.py` kennt den Sprachdienst nicht: es bekommt einen Auftrag,
einen Auslöser und eine Funktion, die das Modell fragt, und gibt ein Ergebnis
zurück. So lässt es sich ohne Dienst und ohne Modell prüfen.

## Fehlerfälle

| Fall | Verhalten |
|---|---|
| Auftragsdatei fehlt | Beispiel-Datei wird gelesen; darin ist alles `aktiv: false` |
| Auftragsdatei fehlerhaft | kein Auftrag läuft; Meldung auf der Bühne und im Protokoll |
| Quelle in `stand` unbekannt | dieser Auftrag wird abgewiesen, die übrigen laufen |
| Abgleich scheitert (Netz) | kein Auslöser; nächster Versuch im Takt |
| Modell nicht erreichbar | Lauf bleibt in der Warteschlange |
| Modell liefert kein JSON | ein zweiter Versuch, dann `gescheitert` |
| SearXNG nicht erreichbar | `gescheitert`, gesehene Adressen bleiben unverändert |
| Dienst startet neu | Warteschlange und Zustand werden gelesen; ein laufender Lauf gilt als abgebrochen und wird neu eingereiht |

## Prüfen

**Ohne Modell** (`.venv/bin/python -m sprachdienst.auftraege_test`, mit einer
Modellfunktion, die feste Antworten gibt):

- Auftragsdatei: gültig, fehlerhaft, unbekannte Quelle, doppelter Name
- Auslöser: Commit-Bereich aus einem Probe-Repo im Temp-Ordner; Zusammenlegen
  mehrerer Auslöser in der Warteschlange; Zeitplan für `thema`
- Stoffsammlung: Obergrenze eingehalten, Kürzung vermerkt, leerer Stoff
- Belegprüfung: wörtlich, mit anderem Leerraum, erfunden, falsches Stück
- Befund und Prüfprotokoll: Inhalt und Urteil je Fall
- Warteschlange übersteht Schreiben und Lesen
- Namenszuordnung für die Stimme

**Am echten Modell**, von Hand: je Art ein Lauf. `stand` gegen einen
zurückliegenden Commit-Bereich einer eigenen Quelle, `protokoll` gegen ein
vorhandenes Protokoll, `thema` mit einer Suchanfrage. Geprüft wird, ob die
belegten Punkte stimmen und ob ein Ansprechen während des Laufs ihn abbricht.

Probedaten aus den eigenen Quellen liegen dafür außerhalb des Repos.

## Nicht dabei

- Aufträge per Stimme anlegen oder ändern
- eine Morgenlage zu fester Zeit
- Vertiefung durch Hermes
- Aufträge, die etwas schreiben, verschieben oder verschicken
- Ansagen ohne Zuruf
