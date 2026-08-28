"""Der Sprachdienst: nimmt Audio vom Client, gibt Sprache und Zustand zurueck.

Bewusst EIN Dienst mit schmaler Schnittstelle, getrennt vom Agenten. Der
Agent (heute Ornith direkt, spaeter vielleicht Hermes) haengt hinten dran und
ist austauschbar, ohne dass Turn-Taking und Audio neu gebaut werden muessen.

Protokoll auf /audio
  Client -> Dienst : Binaerrahmen = PCM int16 mono 16 kHz
                     Textrahmen   = {"befehl": ...}
  Dienst -> Client : Textrahmen   = {"typ": "zustand"|"text"|"ton_ende", ...}
                     Binaerrahmen = PCM int16 mono (Sprachausgabe, Rate in
                                    der vorangehenden "ton"-Nachricht)
Auf /monitor liegt nur der Zustand -- fuer den Bildschirm im Labor.
"""
import asyncio, http, json, logging, re, signal, time
from datetime import datetime
from pathlib import Path
import numpy as np
import websockets
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from wissen import einlesen as wissen_einlesen
from wissen import index as wissen_index, recherche as wissen_recherche
from wissen import web as wissen_web

from . import protokoll as protokoll_modul

from . import absicht as absicht_modul
from . import aktivierung, doku, konfig, llm, stt, tts, zahlwort
from .turn import Turnerkenner
from .zustand import Phase, Zustandshalter

log = logging.getLogger("kihiwi")
HALTER = Zustandshalter()
# Global, nicht je Sitzung: Hermes und der Sprachpfad teilen sich ein Modell,
# zwei parallele Auftraege wuerden die Sprachantworten unbrauchbar traege machen.
RECHERCHE = wissen_recherche.Recherche()
# Ein Abgleich zur Zeit: zwei parallele Laeufe wuerden sich im Index
# gegenseitig als "nicht mehr gesehen" aufraeumen.
ABGLEICH = asyncio.Lock()
# Wer gerade verbunden ist. Ein Rechercheergebnis geht an die AKTUELLEN
# Zuhoerer, nicht an die Sitzung, die den Auftrag gab -- die kann laengst weg
# sein, waehrend jemand anders im Labor steht.
SITZUNGEN: set = set()
# Hoechstens eine Nachbereitung gleichzeitig: sie braucht STT und das Modell,
# beides teilt sie sich mit dem Sprachpfad.
NACHBEREITUNG = asyncio.Lock()
SEITEN = {"/": "monitor.html", "/index.html": "monitor.html",
          "/klient": "klient.html", "/klient.html": "klient.html"}


# ---------------------------------------------------------------- Gesundheit
async def gesundheit():
    """Ornith kann jederzeit verschwinden -- diese Maschine ist auch ein
    Pruefstand. Der Dienst bleibt trotzdem oben: Aufzeichnung und
    Transkription haengen nicht am LLM."""
    while True:
        llm_da, stt_da = await asyncio.gather(llm.erreichbar(), stt.erreichbar())
        hinweis = ""
        if not stt_da:
            hinweis = "Spracherkennung nicht erreichbar"
        elif not llm_da:
            hinweis = "Modell nicht erreichbar — es wird weiter aufgezeichnet"
        HALTER.setzen(llm_da=llm_da, stt_da=stt_da, hinweis=hinweis)

        # Ein abgelaufenes Gespraech muss auch aus der ANZEIGE verschwinden.
        # Vorher blieb die Marke stehen, waehrend der Dienst schon wieder auf
        # das Aktivierungswort wartete -- man sprach ins Leere.
        if HALTER.z.gespraech and not any(s.im_gespraech() for s in SITZUNGEN):
            log.info("Gespräch abgelaufen")
            HALTER.setzen(gespraech=False)
        await asyncio.sleep(5)


# Behauptet die Antwort eine Aenderung an der Aufzeichnung? Grob, absichtlich:
# lieber einmal zu viel den wahren Zustand nachschieben als eine unbemerkte
# Falschaussage stehen lassen.
# Kuendigt die Antwort ein Nachschlagen an, ohne dass gesucht wurde? Dasselbe
# Muster wie bei der Aufzeichnung: das Modell sagt, was es tun wird, statt es
# zu tun. Der kurze Sprech-Prompt ("hoechstens zwei Saetze") verstaerkt das.
# Redet die Antwort ueber eine Recherche, ohne dass eine gestellt wurde?
#
# Bewusst weit gefasst. Ein Versuch mit zwei Bedingungen (Thema UND Zusagewort)
# war zu eng: "Ich gebe dir dafuer einen Rechercheauftrag" und "Ich gebe die
# Recherche jetzt auf" enthalten weder "ab" noch "melde mich", und beide Male
# passierte nichts. Wenn das Modell von einer Recherche spricht und keine
# laeuft, ist der Auftrag gemeint -- also stellen wir ihn.
_R_THEMA = re.compile(r"recherche|rechercheauftrag|rechercheagent|recherchier", re.I)


def _recherche_versprochen(text: str) -> bool:
    return bool(_R_THEMA.search(text))

_ANGEKUENDIGT = re.compile(
    r"(schaue?|sehe?|gucke?|pruefe?|prüfe?)[^.]{0,30}"
    r"(nach|unterlagen|dokument)|(unterlagen|dokumenten)[^.]{0,20}(nachsehen|nachschauen)",
    re.I)

_BEHAUPTUNG = re.compile(
    r"(aufzeichnung|aufnahme|mitschnitt)[^.]{0,40}"
    r"(gestartet|gestoppt|angelaufen|beendet|läuft|aus|an)", re.I)

# Feste Ansage je Werkzeug, gesprochen BEVOR es laeuft. Bewusst im Dienst und
# nicht per Prompt: das Modell haelt sich nicht zuverlaessig daran, und der
# Nutzer muss wissen, ob er auf Sekunden oder auf Minuten wartet.
ANSAGE = {
    "dokumente_suchen": "Ich schaue in den Unterlagen nach.",
    "web_suchen": "Ich schaue kurz im Netz nach.",
    "rechercheauftrag": "Das gebe ich als Rechercheauftrag ab, das dauert ein paar Minuten.",
}

_WOCHENTAG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag",
              "Samstag", "Sonntag"]


def _jetzt() -> str:
    """Datum und Uhrzeit fuer den Prompt.

    Bewusst KEIN Werkzeug: ein Werkzeug waere wieder etwas, das das Modell
    aufrufen kann oder eben nicht -- und ohne Aufruf erfindet es die Zeit
    ("Es ist ungefaehr 13:41 Uhr" kam so zustande). Im Prompt steht es immer
    und kostet nichts. Wird je Antwort neu gebildet, damit die Uhrzeit im
    laufenden Gespraech stimmt.
    """
    n = datetime.now().astimezone()
    return (f"Heute ist {_WOCHENTAG[n.weekday()]}, der {n.strftime('%d.%m.%Y')}, "
            f"es ist {n.strftime('%H:%M')} Uhr.")


def _abgleich_satz(bericht: list[dict]) -> str:
    """Bilanz eines Wissensabgleichs in einen sprechbaren Satz."""
    if not bericht:
        return "Es sind keine Quellen eingerichtet."
    teile = []
    for q in bericht:
        stueck = []
        if q["neu"]:
            stueck.append(f"{q['neu']} Dokument{'e' if q['neu'] != 1 else ''} neu oder geändert")
        if q["entfernt"]:
            stueck.append(f"{q['entfernt']} entfernt")
        # "unveraendert" traegt nichts bei -- eine Quelle ohne Eintrag ist
        # genau das. Alles andere ("2 neue Commits", "frisch geklont") schon.
        if q["notiz"] and q["notiz"] != "unverändert":
            stueck.append(q["notiz"])
        if stueck:
            teile.append(f"{q['name']}: " + ", ".join(stueck))
    # Fehlschlaege zuerst und ungeschoent: eine Quelle, die nicht erreichbar
    # war, darf nicht als "alles aktuell" durchgehen.
    kaputt = [q["name"] for q in bericht if "fehlgeschlagen" in q["notiz"]]
    kopf = ("Abgleich fertig, aber " + " und ".join(kaputt)
            + (" war" if len(kaputt) == 1 else " waren") + " nicht erreichbar. "
            ) if kaputt else "Abgleich fertig. "
    # An "teile" haengen, nicht an den Dokumentzahlen: ein Repo kann neue
    # Commits bringen, ohne dass sich ein indiziertes Dokument aendert. Das
    # als "alles auf dem neuesten Stand" zu melden, waere gelogen.
    uebrig = [t for t in teile if not any(k in t for k in kaputt)]
    if not uebrig:
        return kopf + ("Sonst war alles auf dem neuesten Stand." if kaputt
                       else "Alles war schon auf dem neuesten Stand.")
    return kopf + ". ".join(uebrig) + "."


def _sprechbar(text: str) -> str:
    """Markdown fuer die Sprachausgabe abraeumen.

    Hermes antwortet mit Aufzaehlungen und Fettschrift; ungefiltert liest Piper
    Sternchen und Bindestriche mit vor.
    """
    text = re.sub(r"\*\*|__|`", "", text)
    text = re.sub(r"^\s*[-*•]\s*", "", text, flags=re.M)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\((?:doi|https?)[^)]*\)", "", text)   # DOIs und URLs
    text = re.sub(r"https?://\S+", "", text)
    # Datums- und Zeitangaben ausschreiben: eSpeak liest "28.08.2026" Ziffer
    # fuer Ziffer samt Punkten und macht aus "12:38 Uhr" ein "zwoelf Uhr
    # achtunddreissig Uhr".
    text = zahlwort.ausschreiben(text)
    return re.sub(r"\s{2,}", " ", text.replace("\n", " ")).strip()


WERKZEUGE = [{
    "type": "function",
    "function": {
        "name": "dokumente_suchen",
        "description": ("Durchsucht die Unterlagen des Labors: Protokolle, Notizen, "
                        "Handbücher, Projektdateien. IMMER zuerst benutzen, wenn nach "
                        "Geräten, Messwerten, Verfahren oder früheren Arbeiten gefragt "
                        "wird."),
        "parameters": {
            "type": "object",
            "properties": {"frage": {"type": "string",
                                     "description": "Suchbegriffe oder die Frage"}},
            "required": ["frage"]},
    }}, {
    "type": "function",
    "function": {
        "name": "web_suchen",
        "description": ("Schlägt EINE Tatsache schnell im Internet nach — ein Wert, "
                        "eine Definition, ein Datum. Nur wenn die Unterlagen des "
                        "Labors nichts hergeben. Für alles, was Vergleichen, "
                        "Nachlesen oder mehrere Suchschritte braucht, ist "
                        "'rechercheauftrag' zuständig."),
        "parameters": {
            "type": "object",
            "properties": {"frage": {"type": "string", "description": "Suchbegriffe"}},
            "required": ["frage"]},
    }}, {
    "type": "function",
    "function": {
        "name": "rechercheauftrag",
        "description": ("Gibt eine aufwendige Frage an einen Rechercheagenten ab. "
                        "IMMER benutzen, wenn der Nutzer 'recherchiere', 'vergleiche', "
                        "'schau ausführlich nach' oder Ähnliches sagt, und immer dann, "
                        "wenn eine Frage mehrere Suchschritte, das Lesen von Quellen "
                        "oder einen Vergleich zwischen Unterlagen und Literatur "
                        "verlangt. Der Auftrag läuft im Hintergrund und dauert "
                        "Minuten; das Ergebnis kommt später von selbst. Du antwortest "
                        "danach NUR mit einer Zusage, nicht mit Inhalten."),
        "parameters": {
            "type": "object",
            "properties": {"frage": {"type": "string",
                                     "description": "Der Rechercheauftrag, ausformuliert"}},
            "required": ["frage"]},
    }}, {
    "type": "function",
    "function": {
        "name": "aufzeichnung",
        "description": ("Schaltet die Audioaufzeichnung des Laborgesprächs an oder "
                        "aus. Nur benutzen, wenn ausdrücklich darum gebeten wird."),
        "parameters": {
            "type": "object",
            "properties": {"an": {"type": "boolean",
                                  "description": "true startet, false stoppt"}},
            "required": ["an"]},
    }}]


# ---------------------------------------------------------------- Sitzung
class Sitzung:
    """Eine Client-Verbindung: Audio rein, Sprache raus."""

    def __init__(self, ws):
        self.ws = ws
        self.turn = Turnerkenner()
        self.rek = doku.Rekorder()
        self.verlauf: list[dict] = []
        self.antwort_task: asyncio.Task | None = None
        self.letzte_ansprache = 0.0
        self.web_benutzt = False

    async def schliessen(self):
        self.aufzeichnung_stoppen()
        if self.antwort_task and not self.antwort_task.done():
            self.antwort_task.cancel()

    async def befehl(self, b: dict):
        art = b.get("befehl")
        if art == "mikro":
            an = bool(b.get("an"))
            HALTER.setzen(mikro=an, phase=Phase.BEREIT if an else Phase.LEERLAUF)
            if not an:
                self.aufzeichnung_stoppen()
                HALTER.setzen(gespraech=False)
                self.turn.reset()
        elif art == "aufnahme":
            an = bool(b.get("an"))
            if an and HALTER.z.mikro:
                self.rek.start()
                HALTER.setzen(aufnahme=self.rek.laeuft)
            else:
                self.aufzeichnung_stoppen()
        elif art == "ansprechen":
            # Taste am Client -- gleichwertig zum Aktivierungswort, aber ohne
            # dass es gesagt werden muss. Bleibt nuetzlich, wenn es laut ist.
            if HALTER.z.mikro:
                self.turn.reset()
                HALTER.setzen(phase=Phase.HOEREN)
        elif art == "abbrechen":
            if self.antwort_task and not self.antwort_task.done():
                self.antwort_task.cancel()
            HALTER.setzen(phase=Phase.BEREIT if HALTER.z.mikro else Phase.LEERLAUF)

    async def audio(self, roh: bytes):
        x = np.frombuffer(roh, dtype=np.int16).astype(np.float32) / 32768.0
        for i in range(0, len(x) - konfig.BLOCK + 1, konfig.BLOCK):
            block = x[i:i + konfig.BLOCK]
            # Dokumentationspfad laeuft unabhaengig vom Dialog weiter.
            if self.rek.laeuft:
                self.rek.block(block)
            # Waehrend der Assistent spricht, nicht auf sich selbst reagieren.
            if HALTER.z.phase in (Phase.DENKEN, Phase.ANTWORTEN):
                continue
            ep = await self.turn.block(block)
            if ep is None:
                continue

            gerufen = HALTER.z.phase is Phase.HOEREN     # Knopf gedrueckt
            text = ep.text or await stt.transkribiere(ep.samples, kurz=True)
            if not text.strip():
                continue

            if not gerufen:
                # Mikrofon offen: jede Aeusserung wird transkribiert, aber nur
                # eine mit Aktivierungswort gilt als Ansprache -- ausser das
                # Gespraech laeuft noch, dann genuegt Weitersprechen.
                if self.im_gespraech():
                    if aktivierung.ende(text):
                        log.info("Gespräch beendet: %r", text[:40])
                        await self.gespraech_beenden()
                        continue
                    # Ein vorangestelltes "Kiwi," stoert nicht, muss aber weg.
                    ja, rest = aktivierung.erkannt(text)
                    text = await self.nachschaerfen(ep, rest if ja else text)
                else:
                    ja, rest = aktivierung.erkannt(text)
                    if not ja:
                        # Verworfene Aeusserungen protokollieren: ohne das ist
                        # nicht nachvollziehbar, warum der Assistent schweigt.
                        log.info("nicht angesprochen (%s): %r", ep.grund, text[:70])
                        continue
                    log.info("Aktivierungswort erkannt: %r", text[:60])
                    text = await self.nachschaerfen(ep, rest)
                    HALTER.setzen(gespraech=True)

            self.letzte_ansprache = time.time()
            if not text.strip():
                # Nur gerufen, ohne Anweisung -- kurz quittieren statt das
                # Modell mit einem leeren Satz zu behelligen.
                HALTER.setzen(gespraech=True)
                await self.melden("Ja?")
                HALTER.setzen(phase=Phase.BEREIT)
                continue
            ep.text = text
            log.info("Endpoint: %s nach %.0f ms Aeusserung", ep.grund, ep.dauer_ms)
            HALTER.setzen(phase=Phase.DENKEN)
            self.antwort_task = asyncio.create_task(self.antworten(ep))

    async def recherche_melden(self, auftrag, text, kurz):
        """Ergebnis an DIESE Verbindung ausgeben.

        Setzt die Gespraechsuhr zurueck: wer minutenlang auf ein Ergebnis
        wartet, schweigt zwar, fuehrt aber ein Gespraech. Ohne das lief die
        45-Sekunden-Grenze waehrend der Recherche ab, und die Rueckfrage zum
        Ergebnis brauchte wieder ein "Kiwi".
        """
        self.letzte_ansprache = time.time()
        HALTER.setzen(gespraech=True)
        await self.ws.send(json.dumps({"typ": "recherche", "frage": auftrag.frage,
                                       "text": text, "dauer": round(auftrag.dauer)}))
        await self.melden("Die Recherche ist fertig. " + kurz)

    async def nachschaerfen(self, ep, kurzfassung: str) -> str:
        """Zweiter Durchgang mit dem VOLLEN Fachvokabular.

        Der Dialogpfad transkribiert mit kurzem Prompt, damit das
        Aktivierungswort sicher durchkommt -- das kostet aber die Fachbegriffe
        ("Silizium mit Tretfenster" statt "Siliziumnitrid-Fenster"). Sobald
        feststeht, dass wir angesprochen sind, lohnt der zweite Durchgang: er
        kostet rund 130 ms und rettet genau die Begriffe, nach denen dann
        gesucht wird.
        """
        genau = await stt.transkribiere(ep.samples, kurz=False)
        if not genau:
            return kurzfassung
        ja, rest = aktivierung.erkannt(genau)
        genau = rest if ja else genau
        if genau and genau != kurzfassung:
            log.info("nachgeschärft: %r -> %r", kurzfassung[:45], genau[:45])
        return genau or kurzfassung

    def aufzeichnung_stoppen(self):
        """Stoppt und stoesst die Nachbereitung an. Eine Stelle, damit kein
        Weg am automatischen Transkribieren vorbeifuehrt."""
        if not self.rek.laeuft:
            return
        verz = self.rek.verzeichnis
        self.rek.stop()
        HALTER.setzen(aufnahme=False)
        if verz:
            asyncio.create_task(nachbereiten(verz))

    def im_gespraech(self) -> bool:
        """Laeuft das Gespraech noch? Die Stillegrenze ist keine Bequemlichkeit,
        sondern noetig: ohne sie reagierte der Assistent im Labor auf jedes
        Gespraech, sobald jemand vergisst, sich zu verabschieden."""
        return (HALTER.z.gespraech
                and time.time() - self.letzte_ansprache < konfig.GESPRAECH_STILLE_S)

    async def gespraech_beenden(self, sagen: str | None = "Bis dann."):
        HALTER.setzen(gespraech=False, phase=Phase.ANTWORTEN if sagen else Phase.BEREIT)
        if sagen:
            await self.melden(sagen)
            HALTER.setzen(phase=Phase.BEREIT if HALTER.z.mikro else Phase.LEERLAUF)

    async def antworten(self, ep):
        """Mit hartem Zeitlimit. vLLM haengt sich gelegentlich auf -- ohne
        Grenze blieb der Assistent dann stumm stehen, ohne Fehler, ohne
        Rueckfallebene. Ein haengender Motor sieht aus wie ein langsames
        Modell; hier soll er wie ein Fehler aussehen."""
        try:
            await asyncio.wait_for(self._antworten(ep), timeout=konfig.ANTWORT_MAX_S)
        except asyncio.TimeoutError:
            log.warning("Antwort abgebrochen: laenger als %ds", konfig.ANTWORT_MAX_S)
            try:
                await self.ws.send(json.dumps(
                    {"typ": "text", "rolle": "assistent",
                     "text": "Das dauert mir zu lange, ich breche ab."}))
                await self.sag("Das dauert mir zu lange, ich breche ab.")
            except Exception:
                pass
        finally:
            self.letzte_ansprache = time.time()
            if HALTER.z.phase in (Phase.DENKEN, Phase.ANTWORTEN):
                HALTER.setzen(phase=Phase.BEREIT if HALTER.z.mikro else Phase.LEERLAUF)

    async def _antworten(self, ep):
        import time as _t
        t0 = _t.time()
        try:
            text = ep.text or await stt.transkribiere(ep.samples, kurz=True)
            log.info("  STT %.0f ms%s", (_t.time()-t0)*1000,
                     " (uebersprungen)" if ep.text else "")
            if not text:
                HALTER.setzen(phase=Phase.BEREIT, hinweis="nichts verstanden")
                return
            HALTER.setzen(letzter_text=text)
            await self.ws.send(json.dumps({"typ": "text", "rolle": "nutzer", "text": text}))

            if not HALTER.z.llm_da:
                await self.sag("Das Modell ist gerade nicht erreichbar. "
                               "Ich zeichne weiter auf.")
                HALTER.setzen(phase=Phase.BEREIT)
                return

            HALTER.setzen(phase=Phase.ANTWORTEN, letzte_antwort="")
            ganze = []
            # Waehrend eine Recherche laeuft, teilen sich Hermes und der
            # Sprachpfad ein Modell -- Antworten dauern dann spuerbar laenger.
            # Das anzusagen ist ehrlicher, als den Nutzer warten zu lassen.
            if HALTER.z.recherche:
                await self.melden("Ich recherchiere noch nebenbei, das dauert "
                                  "gerade länger.")
            # Satzweise: der erste Satz geht raus, waehrend der Rest noch
            # geschrieben wird. Nur bis dahin zaehlt die gefuehlte Latenz.
            t1 = _t.time(); erster = True
            werkzeug_gerufen = False
            gerufene: set[str] = set()
            self.web_benutzt = False
            wofuer = absicht_modul.erkennen(text)

            # Ausloesewoerter: der Dienst handelt, das Modell wird nicht
            # gefragt. Es hat sich zu oft geweigert, obwohl das Werkzeug da war.
            art, thema = absicht_modul.ausloeser(text)
            if art and await self.per_ausloeser(art, thema, text):
                return

            # Datum und Uhrzeit kennt der Dienst selbst. Ueber das Modell
            # suchte er dafuer erst in den Unterlagen und dann im Netz.
            if wofuer is absicht_modul.Absicht.ZEIT:
                await self.melden(_jetzt())
                return

            # Protokollabruf: der Dienst weiss, wo die Protokolle liegen.
            # Ueber das Modell ging es schief -- es sagte "ich sehe kein
            # Protokoll", weil es keinen Dateizugriff hat.
            if wofuer is absicht_modul.Absicht.PROTOKOLL:
                await self.protokoll_zeigen()
                return

            # Aufzeichnungsbefehle direkt ausfuehren, ohne das Modell.
            #
            # Das Werkzeug wurde zwar zuverlaessig gerufen, aber die
            # Schlussantwort erkannte das Ergebnis nicht als eigene Handlung
            # und sagte "das liegt ausserhalb meiner Moeglichkeiten" -- was
            # dann im Verlauf stand und sich wiederholte. Fuer einen Befehl mit
            # zwei moeglichen Werten traegt das Modell ohnehin nichts bei:
            # ausfuehren, bestaetigen, fertig. Schneller und nicht ablehnbar.
            if wofuer is absicht_modul.Absicht.AUFZEICHNUNG \
                    and not absicht_modul.will_aendern(text):
                # Reine Statusfrage: aus dem Zustand beantworten. Ueber das
                # Modell ging es schief, weil der Zustand nur im Werkzeug-
                # Prompt stand, nicht im Antwort-Prompt -- es sagte dann "kann
                # ich nicht sehen". Den eigenen Zustand kennt der Dienst besser.
                if HALTER.z.transkription:
                    ergebnis = ("Die Aufzeichnung ist gestoppt, das Protokoll "
                                "wird gerade erstellt.")
                elif HALTER.z.aufnahme:
                    ergebnis = "Ja, die Aufzeichnung läuft."
                else:
                    ergebnis = "Nein, es wird gerade nicht aufgezeichnet."
                log.info("  Aufzeichnungsstatus direkt: %s", ergebnis)
                await self.ws.send(json.dumps({"typ": "text", "rolle": "assistent",
                                               "text": ergebnis}))
                await self.sag(ergebnis)
                HALTER.setzen(letzte_antwort=ergebnis)
                self.verlauf += [{"role": "user", "content": text},
                                 {"role": "assistant", "content": ergebnis}]
                del self.verlauf[:-8]
                return

            if wofuer is absicht_modul.Absicht.AUFZEICHNUNG \
                    and absicht_modul.will_aendern(text):
                an = absicht_modul.soll_anschalten(text)
                ergebnis = await self.werkzeug("aufzeichnung", {"an": an})
                log.info("  Aufzeichnung direkt: an=%s -> %s", an, ergebnis)
                await self.ws.send(json.dumps({"typ": "werkzeug",
                                               "name": "aufzeichnung",
                                               "args": {"an": an},
                                               "ergebnis": ergebnis}))
                await self.ws.send(json.dumps({"typ": "text", "rolle": "assistent",
                                               "text": ergebnis}))
                await self.sag(ergebnis)
                HALTER.setzen(letzte_antwort=ergebnis)
                self.verlauf += [{"role": "user", "content": text},
                                 {"role": "assistant", "content": ergebnis}]
                del self.verlauf[:-8]
                return

            werkzeuge = self.werkzeuge_fuer(wofuer)
            log.info("  Absicht %s -> %d Werkzeug(e)", wofuer.value, len(werkzeuge))
            async for e in llm.antwort_mit_werkzeugen(
                    text, self.verlauf, werkzeuge, self.werkzeug,
                    system=self.system_prompt(wofuer),
                    system_antwort=self.antwort_prompt()):
                if e[0] == "werkzeug_beginnt":
                    ansage = ANSAGE.get(e[1])
                    if ansage:
                        await self.melden(ansage)
                    continue
                if e[0] == "werkzeug":
                    werkzeug_gerufen = True
                    gerufene.add(e[1])
                    log.info("  Werkzeug %s%r -> %s", e[1], e[2], e[3])
                    await self.ws.send(json.dumps({"typ": "werkzeug", "name": e[1],
                                                   "args": e[2], "ergebnis": e[3]}))
                    continue
                satz = e[1]
                if erster:
                    log.info("  LLM erster Satz nach %.0f ms (%d Zeichen)",
                             (_t.time()-t1)*1000, len(satz)); erster = False
                ganze.append(satz)
                HALTER.setzen(letzte_antwort=" ".join(ganze))
                await self.ws.send(json.dumps({"typ": "text", "rolle": "assistent",
                                               "text": satz}))
                await self.sag(satz)
            # Sicherheitsnetz: das Modell hat schon behauptet, die Aufzeichnung
            # gestoppt zu haben, ohne das Werkzeug aufzurufen. Ein verschaerfter
            # Prompt half, aber darauf allein darf sich das nicht stuetzen --
            # wer glaubt, es werde nicht mehr aufgezeichnet, muss recht haben.
            antwort = " ".join(ganze)

            # Absage bei einem klaren Aufzeichnungsbefehl: erzwingen.
            # Sagt das Modell einmal "kann ich nicht", steht das im Verlauf und
            # es wiederholt sich -- die Absage vergiftet alle folgenden Turns.
            if (wofuer is absicht_modul.Absicht.AUFZEICHNUNG
                    and "aufzeichnung" not in gerufene
                    and absicht_modul.will_aendern(text)):
                log.warning("Aufzeichnungsbefehl ohne Werkzeug — erzwinge ihn")
                args = await llm.erzwinge_werkzeug(text, [], WERKZEUGE,
                                                   "aufzeichnung",
                                                   system=self.system_prompt(wofuer))
                if args is not None:
                    ergebnis = await self.werkzeug("aufzeichnung", args)
                    log.info("  nachgeholt: aufzeichnung%r -> %s", args, ergebnis)
                    await self.ws.send(json.dumps({"typ": "werkzeug",
                                                   "name": "aufzeichnung", "args": args,
                                                   "ergebnis": ergebnis,
                                                   "nachgeholt": True}))
                    ganze = [ergebnis]
                    antwort = ergebnis
                    await self.ws.send(json.dumps({"typ": "text",
                                                   "rolle": "assistent",
                                                   "text": ergebnis}))
                    await self.sag(ergebnis)
                    gerufene.add("aufzeichnung")

            # Absage trotz vorhandenem Werkzeug: erzwingen. Der Router hat
            # bereits entschieden, dass es passt -- eine Absage ist dann
            # schlicht falsch, und sie vergiftet den weiteren Verlauf.
            erwartet = {absicht_modul.Absicht.WEB: "web_suchen",
                        absicht_modul.Absicht.RECHERCHE: "rechercheauftrag",
                        absicht_modul.Absicht.WISSEN: "dokumente_suchen"}.get(wofuer)
            if erwartet and erwartet not in gerufene and llm.ist_absage(antwort):
                log.warning("Absage trotz Werkzeug %s — erzwinge es", erwartet)
                args = await llm.erzwinge_werkzeug(text, [], WERKZEUGE, erwartet,
                                                   system=self.system_prompt(wofuer))
                if args is not None:
                    ergebnis = await self.werkzeug(erwartet, args)
                    gerufene.add(erwartet)
                    await self.ws.send(json.dumps({"typ": "werkzeug", "name": erwartet,
                                                   "args": args, "ergebnis": ergebnis,
                                                   "nachgeholt": True}))
                    nach = []
                    async for satz in llm.antwort_saetze_roh(
                            self.antwort_prompt(),
                            f"Das haben deine Werkzeuge geliefert:\n\n{ergebnis}"
                            f"\n\nBeantworte damit: {text}", max_tokens=250):
                        nach.append(satz)
                        await self.ws.send(json.dumps({"typ": "text",
                                                       "rolle": "assistent",
                                                       "text": satz}))
                        await self.sag(satz)
                    if nach:
                        ganze = nach
                        antwort = " ".join(nach)

            # Rechercheauftrag versprochen, aber nicht gestellt: nachholen.
            # Sonst wartet der Nutzer auf ein Ergebnis, das nie kommt --
            # dieselbe Fehlerart wie bei der Aufzeichnung, nur mit laengerer
            # Wirkung, weil man Minuten lang nichts merkt.
            if ("rechercheauftrag" not in gerufene
                    and not RECHERCHE.beschaeftigt
                    and _recherche_versprochen(antwort)):
                log.warning("Recherche versprochen ohne Auftrag — hole ihn nach")
                args = await llm.erzwinge_werkzeug(text, self.verlauf, WERKZEUGE,
                                                   "rechercheauftrag",
                                                   system=self.system_prompt(wofuer))
                frage = (args or {}).get("frage") or text
                ergebnis = await self.werkzeug("rechercheauftrag", {"frage": frage})
                log.info("  nachgeholt: rechercheauftrag -> %s", ergebnis[:60])
                await self.ws.send(json.dumps({"typ": "werkzeug",
                                               "name": "rechercheauftrag",
                                               "args": {"frage": frage},
                                               "ergebnis": ergebnis,
                                               "nachgeholt": True}))
                gerufene.add("rechercheauftrag")

            # Angekuendigt statt getan: Suche nachholen und erneut antworten.
            # Je Werkzeug pruefen, nicht global -- das Modell ruft oft EIN
            # Werkzeug und kuendigt zusaetzlich ein anderes an. Genau daran
            # scheiterte diese Pruefung vorher stumm.
            if ("dokumente_suchen" not in gerufene
                    and _ANGEKUENDIGT.search(antwort)):
                log.info("  Nachschlagen angekündigt — hole die Suche nach")
                ergebnis = await self.werkzeug("dokumente_suchen", {"frage": text})
                verlauf2 = list(self.verlauf) + [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": antwort},
                    {"role": "user", "content":
                     f"Das steht in den Unterlagen:\n{ergebnis}\n\n"
                     f"Beantworte damit jetzt: {text}"}]
                nach = []
                async for satz in llm.antwort_saetze("", verlauf2[:-1] + [verlauf2[-1]],
                                                     max_tokens=200):
                    nach.append(satz)
                    await self.ws.send(json.dumps({"typ": "text",
                                                   "rolle": "assistent", "text": satz}))
                    await self.sag(satz)
                ganze += nach
                antwort = " ".join(ganze)
                werkzeug_gerufen = True

            if not werkzeug_gerufen and _BEHAUPTUNG.search(antwort):
                log.warning("Behauptung ohne Werkzeugaufruf — hole ihn nach")
                args = await llm.erzwinge_werkzeug(text, self.verlauf, WERKZEUGE,
                                                   "aufzeichnung",
                                                   system=self.system_prompt(wofuer))
                if args is not None:
                    ergebnis = await self.werkzeug("aufzeichnung", args)
                    log.info("  nachgeholt: aufzeichnung%r -> %s", args, ergebnis)
                    await self.ws.send(json.dumps({"typ": "werkzeug",
                                                   "name": "aufzeichnung",
                                                   "args": args, "ergebnis": ergebnis,
                                                   "nachgeholt": True}))
                else:
                    # Auch das Nachfassen kann scheitern. Dann wenigstens nicht
                    # die Falschaussage stehen lassen.
                    wahr = ("Zur Sicherheit: die Aufzeichnung läuft weiterhin."
                            if HALTER.z.aufnahme else
                            "Zur Sicherheit: es wird gerade nicht aufgezeichnet.")
                    await self.ws.send(json.dumps({"typ": "text", "rolle": "assistent",
                                                   "text": wahr}))
                    await self.sag(wahr)
                    ganze.append(wahr)

            self.verlauf += [{"role": "user", "content": text},
                             {"role": "assistant", "content": " ".join(ganze)}]
            del self.verlauf[:-8]
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # pragma: no cover
            log.exception("Antwort fehlgeschlagen: %s", e)
        finally:
            self.letzte_ansprache = time.time()
            if HALTER.z.phase in (Phase.DENKEN, Phase.ANTWORTEN):
                HALTER.setzen(phase=Phase.BEREIT if HALTER.z.mikro else Phase.LEERLAUF)

    def antwort_prompt(self) -> str:
        """Sprechstil — NUR fuer die Schlussantwort, nie fuer die Werkzeugrunde."""
        return konfig.SYSTEM_PROMPT + " " + _jetzt()

    def system_prompt(self, wofuer=None) -> str:
        """Kurzer Prompt, zugeschnitten auf die erkannte Absicht.

        Gemessen: mit vier Werkzeugen und langem Prompt ruft Ornith kein
        Werkzeug auf, mit einem und kurzem Prompt zuverlaessig. Bei 3 Mrd.
        aktiven Parametern ist die Menge, die gleichzeitig im Blick bleiben
        muss, die eigentliche Grenze.
        """
        # Bewusst OHNE konfig.SYSTEM_PROMPT: dessen Sprechstil-Anweisung
        # unterdrueckt den Werkzeugaufruf (gemessen 0/3 gegen 3/3).
        teile = ["Du bist der Laborassistent eines KI-Labors und hast Werkzeuge. "
                 "Benutze sie, wenn sie passen.", _jetzt()]
        lauft = "läuft gerade" if HALTER.z.aufnahme else "läuft gerade nicht"
        teile.append(f"Die Audioaufzeichnung des Laborgesprächs {lauft}.")
        if wofuer is not None:
            zusatz = absicht_modul.ZUSATZ_JE_ABSICHT.get(wofuer, "")
            if zusatz:
                teile.append(zusatz.strip())
        if HALTER.z.recherche:
            teile.append("Eine Recherche läuft bereits — nimm keinen zweiten "
                         "Auftrag an und sag, dass er warten muss.")
        return " ".join(teile)

    def werkzeuge_fuer(self, wofuer) -> list:
        namen = absicht_modul.WERKZEUGE_JE_ABSICHT.get(wofuer, [])
        return [w for w in WERKZEUGE if w["function"]["name"] in namen]

    async def werkzeug(self, name: str, args: dict) -> str:
        """Fuehrt einen Werkzeugaufruf aus. Rueckgabe geht ans Modell zurueck."""
        if name == "dokumente_suchen":
            frage = str(args.get("frage", ""))
            # Acht statt vier: bei Zahlenfragen stand die entscheidende Stelle
            # ("FENSTER_NM = 50.0") regelmaessig auf Platz fuenf bis acht,
            # waehrend Uebersichtstexte oben landeten.
            treffer = await asyncio.to_thread(wissen_index.suchen, frage, 8)
            if not treffer:
                return ("Nichts in den Unterlagen gefunden. Sag das offen, statt zu "
                        "raten.")
            teile = []
            for t in treffer:
                # Quelle mitgeben, damit das Modell zitieren kann statt zu behaupten.
                teile.append(f"[{t.quelle} — {t.titel}, Abschnitt: {t.ueberschrift}]\n"
                             f"{t.text[:600]}")
            return "\n\n".join(teile)

        if name == "web_suchen":
            if not konfig.WEB_SUCHE:
                return "Websuche ist abgeschaltet."
            treffer = await wissen_web.suchen(str(args.get("frage", "")))
            if not treffer:
                return "Die Websuche hat nichts geliefert."
            self.web_benutzt = True
            return "\n\n".join(
                f"[Web: {t['titel']}]\n{t['text']}\nQuelle: {t['url']}"
                for t in treffer)

        if name == "rechercheauftrag":
            frage = str(args.get("frage", "")).strip()
            if not frage:
                return "Keine Frage angegeben."
            auftrag = await RECHERCHE.starten(frage, recherche_verteilen)
            if auftrag is None:
                laeuft = RECHERCHE.laufend.frage[:80] if RECHERCHE.laufend else ""
                return ("Es läuft schon eine Recherche zu: " + laeuft +
                        ". Sag dem Nutzer, dass er warten muss, bis die fertig ist.")
            HALTER.setzen(recherche=frage, recherche_seit=time.time())
            log.info("Recherche gestartet: %r", frage[:80])
            return ("Auftrag läuft. Sag in einem kurzen Satz zu, dich zu melden, "
                    "wenn das Ergebnis da ist. Nenne KEINE inhaltliche Antwort — "
                    "du hast noch keine.")

        if name != "aufzeichnung":
            return f"Unbekanntes Werkzeug {name}."
        an = bool(args.get("an"))
        if an and not HALTER.z.mikro:
            return "Fehlgeschlagen: das Mikrofon ist aus."
        if an:
            self.rek.start()
            HALTER.setzen(aufnahme=self.rek.laeuft)
        else:
            self.aufzeichnung_stoppen()
        # Die Aufzeichnung ist rechtlich heikel; sie darf nie still anlaufen.
        # Der Monitor zeigt es ohnehin, die Bestaetigung sagt es zusaetzlich.
        return "Aufzeichnung läuft jetzt." if self.rek.laeuft else "Aufzeichnung ist gestoppt."

    async def per_ausloeser(self, art: str, thema: str, ganzer_text: str) -> bool:
        """Handelt auf ein Ausloesewort hin. True, wenn erledigt."""
        if art == "abgleich":
            if ABGLEICH.locked():
                await self.melden("Der Abgleich läuft schon, das dauert noch "
                                  "einen Moment.")
                return True
            async with ABGLEICH:
                await self.melden("Ich hole die neuen Stände und gleiche die "
                                  "Unterlagen ab.")
                log.info("  Wissensabgleich per Auslösewort")
                # Im Thread: der Lauf holt aus dem Netz und schreibt in den
                # Index, beides dauert Sekunden. Die Suche bleibt derweil
                # benutzbar, seit der Index auf WAL steht.
                bericht = await asyncio.to_thread(wissen_einlesen.alles)
            await self.ws.send(json.dumps({"typ": "werkzeug", "name": "wissensabgleich",
                                           "args": {}, "ergebnis": json.dumps(
                                               bericht, ensure_ascii=False)}))
            await self.melden(_abgleich_satz(bericht))
            return True

        if art == "hilfe":
            zeilen = absicht_modul.hilfe_zeilen()
            # Gesprochen kurz, angezeigt vollstaendig -- vorlesen dauert sonst
            # laenger, als die Liste anzusehen.
            gesagt = ("Du sprichst mich mit Kiwi an. Danach kannst du sagen: "
                      + ", ".join(w for w, _ in zeilen if w != "Hilfe")
                      + ". Die Aufzeichnung steuerst du mit Aufzeichnung starten "
                      + "oder stoppen. Beenden mit Danke, Kiwi. "
                      + "Die ganze Liste steht auf dem Monitor.")
            langtext = "\n".join(
                ["# Was Kiwi versteht", "",
                 "**Ansprechen:** „Kiwi …" + "\u201c — danach bleibt das Gespräch offen, "
                 "Rückfragen brauchen kein „Kiwi" + "\u201c mehr.", "",
                 "## Auslösewörter", ""]
                + [f"- **{w}** — {e}" for w, e in zeilen]
                + ["", "## Aufzeichnung", "",
                   "- **Aufzeichnung starten / stoppen** — der Mitschnitt; beim "
                   "Stoppen entsteht automatisch ein Protokoll",
                   "- **Läuft die Aufzeichnung?** — Zustand abfragen", "",
                   "## Gespräch beenden", "",
                   "- **Danke, Kiwi** oder **Kiwi, Ende** — sonst endet es nach "
                   "45 Sekunden Stille"])
            await self.ws.send(json.dumps({"typ": "hilfe", "text": langtext}))
            await self.melden(gesagt)
            return True

        if art in ("recherche", "hermes"):
            if RECHERCHE.beschaeftigt:
                await self.melden("Es läuft schon eine Recherche, die muss erst "
                                  "fertig werden.")
                return True
            roh = art == "hermes"
            auftrag = await RECHERCHE.starten(thema, recherche_verteilen, roh=roh)
            if auftrag is None:
                return False
            HALTER.setzen(recherche=thema, recherche_seit=time.time())
            log.info("  %s per Auslösewort: %r", art, thema[:70])
            await self.ws.send(json.dumps({"typ": "werkzeug",
                                           "name": "rechercheauftrag",
                                           "args": {"frage": thema, "roh": roh},
                                           "ergebnis": "Auftrag läuft."}))
            # Bei Hermes ansagen, WAS weitergegeben wird -- er darf Dateien
            # lesen, Seiten oeffnen und Code ausfuehren, und das Mikrofon steht
            # offen. Was er tut, soll im Raum hoerbar sein.
            await self.melden(f"Ich gebe an Hermes weiter: {thema}. Ich melde mich."
                              if roh else
                              "Ich recherchiere das und melde mich, wenn ich etwas habe.")
            return True

        if art in ("dokumente", "websuche"):
            # Suche deterministisch, das Modell formuliert nur noch.
            werkzeug = ("dokumente_suchen" if art == "dokumente" else "web_suchen")
            await self.melden("Ich schaue in den Unterlagen nach."
                              if art == "dokumente" else
                              "Ich schaue kurz im Netz nach.")
            ergebnis = await self.werkzeug(werkzeug, {"frage": thema})
            log.info("  %s per Auslösewort: %r", werkzeug, thema[:60])
            gesagt = []
            async for satz in llm.antwort_saetze_roh(
                    self.antwort_prompt(),
                    (f"Das steht in den Unterlagen:\n\n{ergebnis}" if art == "dokumente"
                     else f"Das hat die Internetsuche geliefert:\n\n{ergebnis}\n"
                          f"Sag dazu, dass es aus dem Internet stammt.")
                    + f"\n\nBeantworte damit knapp: {thema}", max_tokens=250):
                gesagt.append(satz)
                await self.ws.send(json.dumps({"typ": "text", "rolle": "assistent",
                                               "text": satz}))
                await self.sag(satz)
            self.verlauf += [{"role": "user", "content": ganzer_text},
                             {"role": "assistant", "content": " ".join(gesagt)}]
            del self.verlauf[:-8]
            return True

        return False

    async def protokoll_zeigen(self):
        """Neuestes Laborprotokoll: Zusammenfassung sprechen, alles anzeigen."""
        verz = sorted((p for p in konfig.AUFNAHMEN.glob("*/protokoll.md")),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        if not verz:
            await self.melden("Es gibt noch kein Protokoll. Zeichne etwas auf, "
                              "dann erstelle ich beim Stoppen eines.")
            return
        pfad = verz[0]
        text = pfad.read_text(encoding="utf-8")
        # Fuer die Sprachausgabe nur die Zusammenfassung -- das Transkript
        # vorzulesen dauert so lange wie die Aufnahme selbst.
        m = re.search(r"## Zusammenfassung\n(.*?)(?=\n## |\Z)", text, re.S)
        roh = m.group(1) if m else text[:800]
        # Den Warnhinweis (Blockzitat) nicht vorlesen -- er steht in jedem
        # Protokoll und sagt ueber diese Sitzung nichts aus.
        roh = re.sub(r"^\s*>.*$", "", roh, flags=re.M)
        kurz = _sprechbar(roh)
        saetze = re.split(r"(?<=[.!?])\s+", kurz)
        gesagt = " ".join(saetze[:3]) or "Das Protokoll ist leer."
        await self.ws.send(json.dumps({"typ": "protokoll",
                                       "sitzung": pfad.parent.name,
                                       "text": text}))
        await self.melden(f"Protokoll der Sitzung {pfad.parent.name}. " + gesagt
                          + " Das Ganze steht auf dem Monitor.")

    async def melden(self, text: str, in_verlauf: bool = True):
        """Sagt etwas UND schreibt es in den Gespraechsverlauf.

        Ohne den Verlaufseintrag weiss das Modell nichts von dem, was der
        Dienst gesagt hat -- und bestreitet es dann. Gemeldet wurde genau das:
        der Dienst sagte "Das Protokoll ist fertig", und auf Nachfrage
        antwortete das Modell "Das habe ich nicht gesagt" und widersprach dem
        Nutzer. Alles Gesprochene gehoert in den Verlauf.
        """
        await self.ws.send(json.dumps({"typ": "text", "rolle": "assistent",
                                       "text": text}))
        vorher = HALTER.z.phase
        if vorher not in (Phase.DENKEN, Phase.ANTWORTEN):
            HALTER.setzen(phase=Phase.ANTWORTEN)
        await self.sag(text)
        if HALTER.z.phase is Phase.ANTWORTEN and vorher is not Phase.ANTWORTEN:
            HALTER.setzen(phase=vorher)
        if in_verlauf:
            self.verlauf.append({"role": "assistant", "content": text})
            del self.verlauf[:-8]

    async def sag(self, satz: str):
        """Spricht einen Satz. Markdown wird IMMER entfernt.

        Vorher galt das nur fuer Rechercheergebnisse -- dann sprach der
        Assistent "Sternchen nicht Sternchen" mitten im Satz, sobald das Modell
        etwas hervorhob.
        """
        import time as _t
        satz = _sprechbar(satz)
        if not satz:
            return
        _ts = _t.time()
        erster = True
        async for pcm, rate in tts.sprich(satz):
            if erster:
                log.info("  TTS erster Ton nach %.0f ms", (_t.time()-_ts)*1000)
                await self.ws.send(json.dumps({"typ": "ton", "rate": rate}))
                erster = False
            await self.ws.send(pcm)
            # Antworten gehoeren in die Aufzeichnung -- sonst steht im
            # Protokoll nur die Haelfte des Gesprächs.
            if self.rek.laeuft:
                self.rek.mische(pcm, rate)
        await self.ws.send(json.dumps({"typ": "ton_ende"}))


# ---------------------------------------------------------------- Verbindungen
async def nachbereiten(verz):
    """Nach dem Stoppen: transkribieren, Protokoll bauen, neu indizieren.

    Im Hintergrund, weil es je nach Laenge Minuten dauert -- der Assistent
    bleibt waehrenddessen ansprechbar. Danach ist die Sitzung ueber
    'dokumente_suchen' auffindbar, ohne dass jemand daran denken muss.
    """
    if NACHBEREITUNG.locked():
        log.info("Nachbereitung laeuft schon, %s wartet", verz.name)
    async with NACHBEREITUNG:
        HALTER.setzen(transkription=verz.name)
        t0 = time.time()
        ziel = None
        try:
            ziel = await protokoll_modul.verarbeite_sitzung(verz)
            # Sofort indizieren, sonst findet Kiwi das eigene Protokoll nicht.
            await asyncio.to_thread(wissen_einlesen.alles, "protokolle")
        except Exception as e:
            log.exception("Nachbereitung gescheitert: %r", e)
        finally:
            HALTER.setzen(transkription="")
        log.info("Nachbereitung %s fertig nach %.0f s", verz.name, time.time() - t0)
        satz = ("Das Protokoll der Aufzeichnung ist fertig, du kannst mich danach fragen."
                if ziel else "Die Aufzeichnung enthielt nichts zum Protokollieren.")
        for s in list(SITZUNGEN):
            try:
                await s.melden(satz)
            except Exception:
                pass


async def recherche_verteilen(auftrag):
    """Rueckmeldung an alle, die gerade verbunden sind.

    Gesprochen wird nur der Anfang -- acht Saetze vorzulesen dauert vierzig
    Sekunden. Das Ganze steht auf dem Monitor und in `recherchen/`.
    """
    HALTER.setzen(recherche="", recherche_seit=0.0)
    if auftrag.fehler:
        text = f"Die Recherche ist gescheitert: {auftrag.fehler}"
        kurz = "Die Recherche hat nicht geklappt."
    else:
        text = auftrag.ergebnis
        saetze = re.split(r'(?<=[.!?])\s+', _sprechbar(text))
        kurz = " ".join(saetze[:2])
        if len(saetze) > 2:
            kurz += " Das Ausführliche steht auf dem Monitor."
    HALTER.setzen(letzte_antwort=text)
    log.info("Recherche fertig nach %.0f s, %d Zuhörer", auftrag.dauer, len(SITZUNGEN))
    for s in list(SITZUNGEN):
        try:
            await s.recherche_melden(auftrag, text, kurz)
        except Exception:
            pass
    if not SITZUNGEN:
        log.info("niemand verbunden — Ergebnis liegt in recherchen/")


async def zustand_senden(ws):
    q = HALTER.abonnieren()
    try:
        while True:
            z = await q.get()
            await ws.send(json.dumps({"typ": "zustand", **z}))
    except websockets.ConnectionClosed:
        pass
    finally:
        HALTER.abbestellen(q)


async def behandeln(ws):
    pfad = ws.request.path
    if pfad.startswith("/monitor"):
        await zustand_senden(ws)
        return
    if not pfad.startswith("/audio"):
        await ws.close(1008, "unbekannter Pfad")
        return

    s = Sitzung(ws)
    SITZUNGEN.add(s)
    senden = asyncio.create_task(zustand_senden(ws))
    try:
        async for nachricht in ws:
            if isinstance(nachricht, bytes):
                await s.audio(nachricht)
            else:
                await s.befehl(json.loads(nachricht))
    except websockets.ConnectionClosed:
        pass
    finally:
        SITZUNGEN.discard(s)
        senden.cancel()
        await s.schliessen()
        HALTER.setzen(phase=Phase.LEERLAUF, mikro=False, aufnahme=False,
                      gespraech=False)


def _liste(wurzel, muster, art):
    """Eintraege fuer die Auflistung. Neueste zuerst."""
    aus = []
    for pfad in wurzel.glob(muster):
        try:
            st = pfad.stat()
        except OSError:
            continue
        name = pfad.parent.name if pfad.name == "protokoll.md" else pfad.stem
        aus.append({"art": art, "kennung": name, "pfad": str(pfad),
                    "geaendert": st.st_mtime, "groesse": st.st_size,
                    "titel": _titel_aus(pfad)})
    return sorted(aus, key=lambda e: e["geaendert"], reverse=True)


def _titel_aus(pfad):
    """Erste Ueberschrift der Datei, sonst der Dateiname."""
    try:
        with open(pfad, encoding="utf-8") as f:
            for zeile in f:
                if zeile.startswith("# "):
                    return zeile[2:].strip()
    except OSError:
        pass
    return pfad.stem


def _sammlung():
    return (_liste(konfig.AUFNAHMEN, "*/protokoll.md", "protokoll")
            + _liste(wissen_recherche.ORDNER, "*.md", "recherche"))


async def http_seite(verbindung, anfrage):
    """GET / liefert den Laborbildschirm, /klient den Sprachclient fuer den
    Browser; alles andere geht an den WebSocket.

    Der Sprachclient braucht einen "secure context", sonst gibt der Browser das
    Mikrofon nicht frei. Ueber SSH weiterleiten und als localhost aufrufen:
        ssh -L 8920:127.0.0.1:8920 <rechner>
        http://localhost:8920/klient
    Damit bleibt der Dienst auf 127.0.0.1 gebunden.
    """
    pfad_teil = anfrage.path.split("?")[0]

    # Auflistung und Abruf: Blaettern gehoert auf den Bildschirm, nicht ins
    # Mikrofon. Eine anklickbare Liste kann nicht missverstanden werden.
    if pfad_teil == "/api/liste":
        leib = json.dumps([{k: v for k, v in e.items() if k != "pfad"}
                           for e in _sammlung()], ensure_ascii=False)
        antwort = verbindung.respond(http.HTTPStatus.OK, leib)
        del antwort.headers["Content-Type"]
        antwort.headers["Content-Type"] = "application/json; charset=utf-8"
        return antwort

    # Stimmproben im Browser anhoerbar machen -- die Bedienoberflaeche ist der
    # Browser, Dateianhaenge nuetzen dort nichts.
    if pfad_teil == "/stimmen":
        proben = sorted((konfig.WURZEL / "stimmproben").glob("*.wav"))
        aktuell = konfig.STIMME.stem
        zeilen = []
        for w in proben:
            name = w.stem
            jetzt = " ← läuft gerade" if name == aktuell else ""
            zeilen.append(
                f'<div class="s"><div class="n">{name}<span class="j">{jetzt}</span></div>'
                f'<audio controls preload="none" src="/api/stimme?n={name}"></audio></div>')
        leib = ("<title>Stimmproben</title><style>"
                "body{background:#101014;color:#e8e8ea;font:15px/1.6 system-ui,sans-serif;"
                "max-width:44rem;margin:0 auto;padding:1.5rem}"
                "h1{font-size:1.1rem}.s{border-top:1px solid #2a2a33;padding:.8rem 0}"
                ".n{font-weight:600;margin-bottom:.4rem}.j{color:#3d9a54;font-weight:400}"
                "audio{width:100%}p{color:#8a8a95}</style>"
                "<h1>Stimmproben</h1><p>Derselbe Satz in allen deutschen Piper-Stimmen. "
                "Der Wechsel ist eine Zeile in <code>konfig.py</code>.</p>"
                + "".join(zeilen))
        antwort = verbindung.respond(http.HTTPStatus.OK, leib)
        del antwort.headers["Content-Type"]
        antwort.headers["Content-Type"] = "text/html; charset=utf-8"
        return antwort

    if pfad_teil == "/api/stimme":
        from urllib.parse import parse_qs, urlparse
        name = parse_qs(urlparse(anfrage.path).query).get("n", [""])[0]
        datei = konfig.WURZEL / "stimmproben" / f"{Path(name).name}.wav"
        if datei.is_file():
            # Response direkt bauen: respond() nimmt Text und kodiert nach
            # UTF-8 -- aus 166 kB Audio wurden dabei 252 kB Unsinn.
            leib = datei.read_bytes()
            return Response(200, "OK", Headers({
                "Content-Type": "audio/wav",
                "Content-Length": str(len(leib)),
            }), leib)
        return verbindung.respond(http.HTTPStatus.NOT_FOUND, "unbekannt\n")

    if pfad_teil == "/api/datei":
        from urllib.parse import parse_qs, urlparse
        wunsch = parse_qs(urlparse(anfrage.path).query).get("k", [""])[0]
        for e in _sammlung():
            if e["kennung"] == wunsch:
                try:
                    inhalt = Path(e["pfad"]).read_text(encoding="utf-8")
                except OSError:
                    break
                antwort = verbindung.respond(http.HTTPStatus.OK, inhalt)
                del antwort.headers["Content-Type"]
                antwort.headers["Content-Type"] = "text/plain; charset=utf-8"
                return antwort
        return verbindung.respond(http.HTTPStatus.NOT_FOUND, "unbekannt\n")

    datei = SEITEN.get(pfad_teil)
    if datei is None:
        return None
    try:
        leib = (konfig.WURZEL / "sprachdienst" / datei).read_text(encoding="utf-8")
    except OSError:
        leib = f"<h1>{datei} fehlt</h1>"
    antwort = verbindung.respond(http.HTTPStatus.OK, leib)
    # respond() setzt text/plain -- der Browser zeigt die Seite sonst als
    # Quelltext an. Headers.__setitem__ HAENGT AN statt zu ersetzen, deshalb
    # erst loeschen.
    del antwort.headers["Content-Type"]
    antwort.headers["Content-Type"] = "text/html; charset=utf-8"
    return antwort


async def haupt():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    konfig.AUFNAHMEN.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(tts.laden)          # Stimme vorladen
    # Feste Saetze einmal rendern -- danach kosten sie nichts mehr.
    feste = list(ANSAGE.values()) + [
        "Ja?", "Bis dann.", "Die Recherche ist fertig.",
        "Ich recherchiere noch nebenbei, das dauert gerade länger.",
        "Ich recherchiere das und melde mich, wenn ich etwas habe.",
        "Das Protokoll der Aufzeichnung ist fertig, du kannst mich danach fragen.",
        "Aufzeichnung läuft jetzt.", "Aufzeichnung ist gestoppt.",
        "Ja, die Aufzeichnung läuft.", "Nein, es wird gerade nicht aufgezeichnet.",
        "Es läuft schon eine Recherche, die muss erst fertig werden.",
        "Das dauert mir zu lange, ich breche ab.",
    ]
    n = await asyncio.to_thread(tts.vorrendern, feste)
    art = getattr(konfig, "STIMM_ART", "")
    log.info("Stimme %s%s geladen, %d feste Sätze im Vorrat",
             konfig.STIMME.name, f" ({art})" if art else "", n)
    asyncio.create_task(gesundheit())

    stopp = asyncio.Event()
    schleife = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        schleife.add_signal_handler(s, stopp.set)

    async with serve(behandeln, konfig.BIND, konfig.PORT,
                     process_request=http_seite, max_size=2**22):
        log.info("Sprachdienst auf ws://%s:%d/audio  (Monitor: http://%s:%d/)",
                 konfig.BIND, konfig.PORT, konfig.BIND, konfig.PORT)
        await stopp.wait()
    log.info("beendet")


if __name__ == "__main__":
    asyncio.run(haupt())
