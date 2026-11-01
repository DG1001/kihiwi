"""Jitsi-Waechter: schickt Kiwi in die Konferenzen eines bestimmten Nutzers.

Fragt in festem Takt, welche Konferenzen laufen und wer sie eroeffnet hat.
Laeuft eine Konferenz von KIHIWI_JITSI_NUTZER und ist keine Bruecke aktiv,
startet er eine (jitsi_bruecke.py). Kiwi klopft an der Lobby an; wer ihn nicht
braucht, laesst ihn nicht herein, und die Bruecke gibt nach KIHIWI_JITSI_WARTEN
Sekunden auf. Je Konferenz versucht er es nur einmal: Abgelehnt heisst
abgelehnt, bis die Konferenz neu eroeffnet wird.

Nur die Konferenzen EINES Nutzers, nicht alle: Kiwi soll nicht bei fremden
Meetings anklopfen und dort zuhoeren, sobald jemand ihn hereinlaesst.

Umgebung (bei dienste.sh in zustand/jitsi.env, Muster: jitsi.beispiel.env):
  KIHIWI_JITSI_URL     Adresse des Jitsi-Servers, z. B. https://<jitsi>
  KIHIWI_JITSI_NUTZER  Konto, dessen Konferenzen Kiwi betritt
  KIHIWI_JITSI_RAEUME  Shell-Befehl, der die laufenden Konferenzen als JSON
                       ausgibt: [{"raum": ..., "nutzer": ..., "seit": ...,
                       "belegt": ...}] (belegt = Teilnehmer samt Jicofo)
  KIHIWI_JITSI_NAME    Anzeigename (Kiwi)
  KIHIWI_JITSI_TAKT    Sekunden zwischen zwei Abfragen (10)
  KIHIWI_JITSI_WARTEN  Sekunden, die Kiwi an der Lobby wartet (180)
"""
import json, logging, os, signal, subprocess, sys, time

log = logging.getLogger("kihiwi.jitsi.waechter")


def laufende(befehl: str) -> list[dict]:
    r = subprocess.run(befehl, shell=True, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"Abfrage scheiterte ({r.returncode}): {r.stderr.strip()[:200]}")
    daten = json.loads(r.stdout or "[]")
    return daten if isinstance(daten, list) else []


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        basis = os.environ["KIHIWI_JITSI_URL"].rstrip("/")
        nutzer = os.environ["KIHIWI_JITSI_NUTZER"]
        befehl = os.environ["KIHIWI_JITSI_RAEUME"]
    except KeyError as e:
        sys.exit(f"Umgebungsvariable fehlt: {e.args[0]} (siehe jitsi.beispiel.env)")
    name = os.environ.get("KIHIWI_JITSI_NAME", "Kiwi")
    takt = float(os.environ.get("KIHIWI_JITSI_TAKT", "10"))
    warten = os.environ.get("KIHIWI_JITSI_WARTEN", "180")

    bruecke: subprocess.Popen | None = None
    raum_der_bruecke = None
    versucht: set[tuple] = set()
    ende = False

    def beenden(*_):
        nonlocal ende
        ende = True
    signal.signal(signal.SIGTERM, beenden)
    signal.signal(signal.SIGINT, beenden)

    log.info("Waechter laeuft: Konferenzen von '%s' auf %s, alle %.0f s", nutzer, basis, takt)
    fehler_gemeldet = False
    while not ende:
        if bruecke and bruecke.poll() is not None:
            log.info("Bruecke fuer '%s' beendet (Rueckgabe %s%s)", raum_der_bruecke,
                     bruecke.returncode,
                     ", nicht hereingelassen" if bruecke.returncode == 3 else "")
            bruecke = raum_der_bruecke = None

        if not bruecke:
            try:
                raeume = laufende(befehl)
                fehler_gemeldet = False
            except Exception as e:
                if not fehler_gemeldet:
                    log.warning("Konferenzen nicht abfragbar: %s", e)
                    fehler_gemeldet = True
                raeume = []
            # belegt >= 2: Jicofo und mindestens ein Mensch. Eine eben
            # eroeffnete, schon wieder verlassene Konferenz zaehlt nicht.
            for r in raeume:
                schluessel = (r.get("raum"), r.get("seit"))
                if r.get("nutzer") != nutzer or (r.get("belegt") or 0) < 2 \
                        or schluessel in versucht:
                    continue
                versucht.add(schluessel)
                raum_der_bruecke = r["raum"]
                log.info("Konferenz '%s' von %s laeuft, Kiwi klopft an", raum_der_bruecke, nutzer)
                bruecke = subprocess.Popen(
                    [sys.executable, "-m", "sprachdienst.jitsi_bruecke",
                     f"{basis}/{raum_der_bruecke}", "--name", name, "--warten", warten])
                break

        for _ in range(int(takt * 2)):
            if ende:
                break
            time.sleep(0.5)

    if bruecke and bruecke.poll() is None:
        bruecke.terminate()
        try:
            bruecke.wait(15)
        except subprocess.TimeoutExpired:
            bruecke.kill()
    log.info("Waechter beendet")


if __name__ == "__main__":
    main()
