"""Sucht Namen, die nirgends im Modul gebunden sind.

**Warum es das gibt.** Am 14.09.2026 hat ein Umbau in `llm.py` acht
Definitionen mitgerissen -- ein Textschnitt ueber einen Zeilenbereich kennt
keine Funktionsgrenzen. `py_compile` merkt davon nichts, denn ein `NameError`
entsteht erst beim Aufruf. Der Assistent antwortete danach vier Stunden lang
auf keine Wissensfrage mehr, und im Protokoll stand der Grund erst, als
jemand nachsah.

**Was die Pruefung kann und was nicht.** Sie sammelt alle Namen, die im Modul
*irgendwo* gebunden werden -- oben, in Funktionen, als Parameter, in
Schleifen, in `except ... as`, `with ... as`, Importen -- und meldet jeden
gelesenen Namen, der in dieser Menge fehlt. Gueltigkeitsbereiche prueft sie
NICHT: eine Variable, die nur in einer anderen Funktion existiert, faellt
nicht auf. Dafuer gibt es praktisch keine Fehlalarme, und genau die Luecke,
die hier Schaden angerichtet hat, findet sie sicher.

Aufruf: ./dienste.sh namen
"""
import ast
import builtins
import sys
from pathlib import Path


def _gebunden(baum: ast.AST) -> set[str]:
    """Alle Namen, die irgendwo im Baum einen Wert bekommen."""
    namen = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__"}
    for k in ast.walk(baum):
        if isinstance(k, ast.Name) and isinstance(k.ctx, (ast.Store, ast.Del)):
            namen.add(k.id)
        elif isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            namen.add(k.name)
        elif isinstance(k, (ast.Import, ast.ImportFrom)):
            for a in k.names:
                namen.add((a.asname or a.name).split(".")[0])
        elif isinstance(k, ast.arg):
            namen.add(k.arg)
        elif isinstance(k, ast.ExceptHandler) and k.name:
            namen.add(k.name)
        elif isinstance(k, ast.Global | ast.Nonlocal):
            namen.update(k.names)
    return namen


def pruefen(datei: Path) -> list[str]:
    """Meldungen zu einer Datei. Leer heisst: nichts gefunden."""
    quelle = datei.read_text(encoding="utf-8")
    try:
        baum = ast.parse(quelle, filename=str(datei))
    except SyntaxError as e:
        return [f"{datei}:{e.lineno}: {e.msg}"]
    bekannt = _gebunden(baum)
    # Nach Zeile sortiert melden, sonst springt die Ausgabe im Modul umher.
    offen = {}
    for k in ast.walk(baum):
        if isinstance(k, ast.Name) and isinstance(k.ctx, ast.Load) \
                and k.id not in bekannt:
            offen.setdefault(k.id, k.lineno)
    return [f"{datei}:{zeile}: {name} ist nirgends definiert"
            for name, zeile in sorted(offen.items(), key=lambda p: p[1])]


def main(argv):
    wurzel = Path(__file__).parent
    ziele = ([Path(a) for a in argv] if argv else
             sorted(p for ordner in ("sprachdienst", "wissen", "vad", "zustand")
                    for p in (wurzel / ordner).rglob("*.py")))
    meldungen = [m for z in ziele for m in pruefen(z)]
    for m in meldungen:
        print(m)
    print(f"{len(ziele)} Dateien geprüft, {len(meldungen)} Fund(e)")
    return 1 if meldungen else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
