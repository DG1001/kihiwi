"""Einstellungen des Sprachdienstes.

Absichtlich eine einzige Datei ohne Ladelogik -- solange es keine zweite
Installation gibt, ist eine Konfigurationsdatei nur eine weitere Fehlerquelle.
"""
import os
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent

# --- Netz -------------------------------------------------------------------
# NIEMALS 0.0.0.0 oder [::]. Der GX10 hat eine weltweit geroutete IPv6 ohne NAT
# davor; ein Dienst, der Laboraudio fuehrt, gehoert nicht versehentlich dorthin.
# Fuer den Laborclient spaeter auf die Tailnet-Adresse <tailnet-adresse> umstellen.
BIND      = os.environ.get("KIHIWI_BIND", "127.0.0.1")
PORT      = int(os.environ.get("KIHIWI_PORT", "8920"))

STT_URL   = os.environ.get("KIHIWI_STT", "http://127.0.0.1:8910/inference")
LLM_URL   = os.environ.get("KIHIWI_LLM", "http://127.0.0.1:8889/v1")
LLM_MODEL = os.environ.get("KIHIWI_MODEL", "ornith-1.5-35b-a3b")

# --- Audio ------------------------------------------------------------------
RATE       = 16000
BLOCK      = 512               # Samples; 32 ms. Silero ist darauf trainiert.
BLOCK_MS   = BLOCK * 1000 // RATE

# --- Endpointing ------------------------------------------------------------
# Gemessen am 27.08.2026: naiv kostet Fehlerfreiheit 1183 ms, mit lexikalischer
# Pruefung ~510 ms. Siehe CLAUDE.md, Abschnitt "VAD und Endpointing".
T_KURZ_MS   = 300      # Stille, ab der lexikalisch geprueft wird
DECKE_MS    = 1200     # danach wird ohnehin abgeschlossen
VAD_EIN     = 0.5      # Schwelle zum Eintritt in Sprache
VAD_AUS     = 0.35     # Hysterese: niedriger, damit kurze Luecken nicht trennen
MIN_SPRACHE_MS = 160   # kuerzeres gilt als Stoergeraeusch
# Niedrig, weil das Modell nur als Veto gegen offensichtlich Unfertiges taugt:
# vLLM ist bei temperature 0 nicht deterministisch, Grenzfaelle schwanken
# ueber die halbe Skala. Kein feiner Regler.
P_FERTIG_SCHWELLE = 0.1

# --- Aktivierungswort -------------------------------------------------------
# "Kiwi" (aus KI-Hiwi) ist als Aktivierungswort guenstig: zwei klare Silben,
# phonetisch eindeutig, und im Labor faellt das Wort sonst nicht. "Computer"
# waere in einem technischen Umfeld staendig ein Fehlausloeser, "Hiwi" kommt
# im Hochschulalltag zu haeufig vor.
# Schreibvarianten ausdruecklich aufzaehlen statt die Schwelle zu senken:
# "Hiwi" und "Kiwi" trennt EIN Buchstabe (Verhaeltnis 0,75), und im
# Hochschulumfeld faellt "Hiwi" staendig. Eine lockerere Schwelle wuerde jede
# Erwaehnung zur Ansprache machen.
# "kiwi kiwi" war eine spekulative Variante und schadete: sie traf auf
# "Kiwi, wie ..." (Aehnlichkeit 0,82) und schnitt zwei Woerter ab statt einem.
# Varianten nur aufnehmen, wenn sie belegt sind.
AKTIVIERUNG = ["kiwi", "kivi", "kiwie", "hey kiwi"]
# Wie viele Fachbegriffe der Dialogpfad mitnimmt. Mehr verwaessert das
# Aktivierungswort im Prompt -- gemessen: mit voller Liste wurde "Kiwi" wieder
# als "TV" gehoert.
VOKABULAR_KURZ = 12

AKTIVIERUNG_MIN = 0.80   # unscharf, aber nicht zu lose: bei 0,75 galt
                         # schon "Der Hiwi ..." als Ansprache

# Nach einer Ansprache bleibt das Gespraech offen: Rueckfragen brauchen kein
# Aktivierungswort mehr. Beendet wird es ausdruecklich ("Danke, Kiwi") oder
# nach Ablauf der Stille.
GESPRAECH_ENDE = ["danke", "ende", "beenden", "schluss", "fertig", "das wars",
                  "das war's", "tschuess", "tschüss", "aus"]
GESPRAECH_MAX_WOERTER = 5      # laenger ist keine Verabschiedung, sondern eine Frage
# Ohne Zeitgrenze wuerde der Assistent im Labor auf jedes Gespraech reagieren,
# wenn jemand vergisst, sich zu verabschieden.
GESPRAECH_STILLE_S = 45

# Der Kern gegen erfundene Fachaussagen: das Modell weiss ueber dieses Labor
# nichts und darf so tun, als wuesste es etwas. Ohne diese Anweisung kamen auf
# die Frage nach dem Siliziumnitrid-Fenster "durchlaessig fuer Roentgenstrahlen"
# und zwei verschiedene Dicken.
WISSEN_PROMPT = (
    "Für Fragen zu Geräten, Messwerten, Verfahren, Projekten oder früheren "
    "Arbeiten rufst du ZUERST 'dokumente_suchen' auf. Antworte nur mit dem, was "
    "die Unterlagen hergeben, und nenne die Quelle. Findest du nichts, sag das "
    "offen — erfinde keine Zahlen, Materialeigenschaften oder Gerätedaten. "
    "'web_suchen' nur für EINE schnell nachzuschlagende Tatsache, die nicht in "
    "den Unterlagen steht; sag dann ausdrücklich dazu, dass es aus dem Internet "
    "stammt. Verlangt die Frage einen Vergleich, mehrere Suchschritte oder sagt "
    "der Nutzer 'recherchiere', dann gibst du sie mit 'rechercheauftrag' ab und "
    "sagst nur zu, dich zu melden."
)

# --- Wissen -----------------------------------------------------------------
SEARXNG = os.environ.get("KIHIWI_SEARXNG", "http://127.0.0.1:8088")
# Einzeln abschaltbar: die Websuche ist die EINZIGE Stelle, an der etwas das
# Netz verlaesst. Alles andere laeuft lokal.
WEB_SUCHE = os.environ.get("KIHIWI_WEB", "1") not in ("0", "aus", "nein")

# Hartes Zeitlimit fuer einen Antwortlauf inklusive Werkzeugen. vLLM haengt
# sich gelegentlich auf; ohne Grenze bleibt der Assistent stumm stehen.
ANTWORT_MAX_S = 45

# --- Dateien ----------------------------------------------------------------
AUFNAHMEN  = Path(os.environ.get("KIHIWI_AUFNAHMEN", WURZEL / "aufnahmen"))
VAD_MODELL = WURZEL / "vad" / "silero_vad.onnx"
STIMME     = WURZEL / "voices" / "de_DE-thorsten-medium.onnx"
VOKABULAR  = WURZEL / "vokabular.txt"

# Umlaute hier BEWUSST korrekt, auch wenn der Rest der Datei ASCII ist: das
# Modell ahmt den Stil des System-Prompts nach. Mit "Aufzaehlungen" im Prompt
# antwortete es "Ich kann das nicht fuer dich ausfuehren" -- und Piper spricht
# "fuer" als "Fu-er" aus. Was hier steht, landet im Lautsprecher.
SYSTEM_PROMPT = (
    "Du bist der Laborassistent im KI-Labor. Du antwortest kurz und zum Sprechen, "
    "nicht zum Lesen: keine Aufzählungen, keine Formatierung, keine Sonderzeichen, "
    "höchstens zwei Sätze. Zahlen schreibst du als Wort, wenn sie klein sind. "
    "Wenn du etwas nicht weißt, sagst du das."
)
