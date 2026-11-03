# Feinabstimmung der Einbettung

Die Einbettung (multilingual-e5-large) auf die eigenen Unterlagen anpassen, damit
die Suche auch umschriebene Fragen findet. Seit dem 03.11.2026 so im Betrieb;
Zahlen und Begründung in [technisch.md](../../technisch.md) unter „Vektorindex“.

| richtige Stelle unter den ersten acht | Original | feinabgestimmt |
|---|---|---|
| umschriebene Fragen, nur Vektoren | 47 % | 72 % |
| umschriebene Fragen, ganze Suche mit Reranker | 68,3 % | 80,0 % |
| Fragen mit den Fachwörtern, ganze Suche mit Reranker | 98,3 % | 98,3 % |

**Die Bildersuche bleibt beim Original.** Auf Fließtext feinabgestimmt, traf das
Modell die Stichwort-Einträge der Bilder schlechter (5 statt 7 von 7).

## Was man braucht

- Ein Python mit `torch` (CUDA) und `transformers` für Einbetten, Training und
  Export. Der Sprachdienst selbst braucht davon nichts, er rechnet mit ONNX.
  Für den Export zusätzlich `onnx` und `onnxscript`.
- Das Original zum Weitertrainieren: `intfloat/multilingual-e5-large`
  (`model.safetensors` und Tokenizer, 2,2 GB).
- Das Sprachmodell auf `:8889` für die Fragen, und eine Anmeldung bei der
  Belegungsstelle, falls die Maschine geteilt ist.

Die Fragen kommen aus den Unterlagen und gehören **nicht ins Repo**; die Skripte
legen sie unter `zustand/suchmessung/` ab.

## Ablauf

`PY` ist das Python mit torch, `M` der Modellordner (etwa `~/models/einbettung`),
`D=zustand/suchmessung`, `DB=wissen/index.db`. Die beiden letzten Schritte laufen
im venv des Repos.

1. **Testsatz** (einmal, dann aufheben): 60 zufällige Abschnitte ohne Code- und
   Datendateien, je eine Frage mit den Fachwörtern und eine umschriebene.
   `PYTHONPATH=. .venv/bin/python wissen/feinabstimmung/testsatz.py $D/fragen.json`
2. **Trainingsfragen**: 1500 Abschnitte, Dokumente des Testsatzes bleiben draußen.
   Möglichst ein anderes Sprachmodell als für den Testsatz, sonst lernt die
   Einbettung den Fragestil mit. Wiederaufnehmbar.
   `PYTHONPATH=. .venv/bin/python wissen/feinabstimmung/trainingsfragen.py $D/fragen.json $D/training/fragen.jsonl 1500`
3. **Original einbetten** (Vergleich, und Grundlage der schweren Negative), GPU,
   rund 2 Minuten für 16 000 Abschnitte:
   `wissen/feinabstimmung/speicherwaechter.sh $D/vektoren/basis.log 6 -- $PY -u wissen/feinabstimmung/einbetten.py $M/e5-large-basis $D/vektoren/basis $DB $D/fragen.json $D/training/fragen.jsonl`
4. **Schwere Negative**: je Abschnitt ein ähnlicher aus einem anderen Dokument.
   `.venv/bin/python wissen/feinabstimmung/negative.py $D/vektoren/basis $DB $D/training/negative.json`
5. **Training**, eine Epoche, rund 5 Minuten, unter dem Speicherwächter:
   `wissen/feinabstimmung/speicherwaechter.sh $D/training/training.log 5 -- $PY -u wissen/feinabstimmung/training.py $M/e5-large-basis $D/training/fragen.jsonl $D/training/negative.json $DB $M/e5-large-fein 1 1e-5`
6. **Feinabgestimmt einbetten und messen**, gegen das Original:
   `... einbetten.py $M/e5-large-fein $D/vektoren/fein $DB $D/fragen.json`, dann
   `PYTHONPATH=. .venv/bin/python wissen/feinabstimmung/messen_vektoren.py $D/vektoren/fein $D/fragen.json`
   (und dasselbe mit `$D/vektoren/basis`).
7. **Export für den Dienst**:
   `$PY wissen/feinabstimmung/onnx_export.py $M/e5-large-fein $M/e5-large-fein-onnx <Ordner des Originals in ~/.cache/kihiwi-embed/…/snapshots/…>`
   und prüfen, dass fastembed mit dem Export dieselben Vektoren liefert (Kosinus
   1,0000 gegen Schritt 6).
8. **Umstellen**, mit Sicherung:
   - `vektoren` und `bilder` sichern (`CREATE TABLE vektoren_e5basis AS SELECT * FROM vektoren`, ebenso `bilder`).
   - `./dienste.sh stop`, dann alle Abschnitte mit dem neuen Modell auf der GPU
     einbetten und mit dessen Namen in `vektoren` schreiben (Schlüssel ist der
     Hash des Abschnittstexts, `index.abschnitt_hash`).
   - `wissen/einbettung.json` anlegen (Muster: `wissen/einbettung.beispiel.json`).
   - `./dienste.sh start`, dann `messen.py` über den echten Suchweg und
     `wissen.bilder_test`.

**Zurück zum Original:** `wissen/einbettung.json` löschen und die Sicherung
zurückspielen (`INSERT OR REPLACE INTO vektoren SELECT * FROM vektoren_e5basis`),
oder neu einbetten lassen; Vektoren des falschen Modells gelten als fehlend und
werden nie mit den anderen gemischt.

## Fallen

- **Nachdenkende Modelle** (Qwen3.8-Flash) verbrauchten die Token vor der
  Antwort; die Skripte schalten das mit `chat_template_kwargs` ab.
- **Ein Platz im Modellserver:** Gleichzeitige Anfragen machen die Erzeugung
  nicht schneller, sie lassen nur Kiwi länger warten.
- **Speicher:** Training mit eingefrorenen Wortvektoren und
  Gradient-Checkpointing ließ hier ≥ 19 GB frei. Rechnen auf der CPU über
  fastembed wuchs dagegen auf 15,6 GB und kam nicht voran.
