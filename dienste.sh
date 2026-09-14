#!/usr/bin/env bash
# dienste.sh — startet, stoppt und zeigt die drei Dienste des Sprachassistenten.
#
#   ./dienste.sh start           alles hochfahren (idempotent)
#   ./dienste.sh stop            Sprachdienst und whisper-server beenden
#   ./dienste.sh stop --vllm     zusaetzlich das Modell entladen
#   ./dienste.sh neustart        stop + start der beiden lokalen Dienste
#   ./dienste.sh status          was laeuft, auf welchem Port
#   ./dienste.sh log [name]      Protokoll folgen (sprach | whisper | vllm)
#   ./dienste.sh waechter [...]  vLLM ueberwachen und bei Ausfall neu starten
#                                (start | stop | log)
#   ./dienste.sh protokoll [...] Aufnahmen transkribieren und Protokoll bauen
#   ./dienste.sh wissen [...]    Unterlagen einlesen/durchsuchen
#                                (einlesen | erschliessen | vektoren |
#                                 katalog | ueberblick | status | suchen ...
#                                 | web ...)
#   ./dienste.sh namen           sucht Namen, die nirgends definiert sind
#   ./dienste.sh sprechermodelle laedt die Modelle der Sprechertrennung nach
#                                (35 MB, liegen nicht im Repo)
#
# Die Dienste werden ueber ihren PORT gefunden, nicht ueber den Prozessnamen:
# `pkill -f sprachdienst.gateway` bringt die eigene Shell um, weil das Muster in
# deren Kommandozeile steht.
set -uo pipefail

WURZEL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGS="$WURZEL/logs"
VENV="$WURZEL/.venv/bin/python"
WHISPER="$HOME/code/whisper.cpp"
MODELL="$WHISPER/models/ggml-large-v3-turbo.bin"

P_VLLM=8889
P_WHISPER=8910
P_SPRACH=8920

C_VLLM=vllm-model          # Containername, den model-switch vergibt
# Welches model-switch-Profil `start` laedt, wenn nichts auf :8889 laeuft.
# Ueberschreibbar: KIHIWI_PROFIL=qwen36nvfp4-voice ./dienste.sh start
# Immer ein *-voice-Profil nehmen -- die Pruefstandsprofile reservieren 0.85
# des Speichers, und dann hungert der Rest des Sprachstapels.
#
# Qwen3.6-35B-A3B statt Ornith seit dem 01.09.2026: gleiche Bauart, gleiche
# Groesse, gleicher Durchsatz (78,3 gegen 78,4 tok/s), aber es sucht in den
# Unterlagen UND antwortet trotzdem aus eigenem Wissen, wenn nichts zu finden
# ist. Ornith bleibt als Profil bestehen.
PROFIL="${KIHIWI_PROFIL:-qwen36nvfp4-voice}"
# Untergrenze in GiB, unter der es fuer den Sprachstapel eng wird.
# whisper.cpp (CUDA), Piper, sherpa-onnx und der Sprachdienst brauchen
# zusammen rund 13 GiB. Mit dem Sprachprofil (GPU_UTIL 0.55) bleiben etwa
# 41 GiB frei, mit dem Pruefstandsprofil (0.85) rund 11 -- die Schwelle
# liegt bequem dazwischen und haengt nicht am Modellnamen.
MIN_FREI=20

mkdir -p "$LOGS"

# ------------------------------------------------------------------ Hilfsmittel
pid_auf() { ss -tlnpH "sport = :$1" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1; }
belegt()  { ss -tlnH  "sport = :$1" 2>/dev/null | grep -q .; }

warte() {  # warte <sekunden> <befehl...>
    local n=$1; shift
    for _ in $(seq 1 "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
    return 1
}

bereit_vllm()    { curl -sf --max-time 2 "http://127.0.0.1:$P_VLLM/v1/models"; }

# Liest die /v1/models-Antwort von stdin. Als Funktion statt als Einzeiler:
# ein f-string mit maskierten Anfuehrungszeichen scheitert in python3 -c.
# Der Kontext steht je nach Motor woanders: vLLM nennt ihn max_model_len,
# llama.cpp meta.n_ctx. Beide werden gelesen -- auf 8889 muss nicht vLLM
# liegen, jeder lokale OpenAI-kompatible Server tut es.
modellzeile() { python3 -c 'import sys,json
try:
    d = json.load(sys.stdin)["data"][0]
    ctx = d.get("max_model_len") or (d.get("meta") or {}).get("n_ctx") or "?"
    print("%s, ctx %s" % (d["id"], ctx))
except Exception:
    pass' 2>/dev/null; }

modellname() { python3 -c 'import sys,json
try:
    print(json.load(sys.stdin)["data"][0]["id"])
except Exception:
    pass' 2>/dev/null; }

modellctx() { python3 -c 'import sys,json
try:
    d = json.load(sys.stdin)["data"][0]
    print(d.get("max_model_len") or (d.get("meta") or {}).get("n_ctx") or "")
except Exception:
    pass' 2>/dev/null; }

# Liest --gpu-memory-utilization aus der Kommandozeile des laufenden
# vLLM-Containers. Ueber /v1/models ist das Sprachprofil nicht mehr zu
# erkennen: seit Hermes mindestens 64K verlangt, laeuft 'ornith-voice' mit
# demselben Kontext wie das Pruefstandsprofil 'ornith' (131072). Frueher
# stand hier ein Vergleich auf 32768 -- der traf seitdem nie mehr zu und
# warnte bei jedem Start, gerade wenn alles richtig war.
vllm_util() { docker inspect --format '{{range .Args}}{{println .}}{{end}}' \
    "$C_VLLM" 2>/dev/null \
    | grep -A1 -Fx -- '--gpu-memory-utilization' | tail -1; }
bereit_whisper() { curl -sf --max-time 2 -o /dev/null "http://127.0.0.1:$P_WHISPER/"; }
bereit_sprach()  { curl -sf --max-time 2 -o /dev/null "http://127.0.0.1:$P_SPRACH/"; }

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
info() { printf '  \033[2m·\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fehl() { printf '  \033[31m✗\033[0m %s\n' "$*"; }

# ------------------------------------------------------------------ Waechter
# vLLM faellt im Dauerbetrieb aus, und zwar auf zwei Arten:
#
#   1. Es HAENGT: /v1/models antwortet weiter mit 200, /chat/completions nie.
#   2. Es STIRBT: "CUDA error: an illegal memory access was encountered",
#      danach 500 auf jede Anfrage, dann beendet sich der Container.
#
# Beobachtet am 03.09. nach zwei Tagen und am 07.09. nach dreieinhalb. Beide
# Male blieb der Assistent stumm, bis jemand nachsah.
#
# **Der Test muss eine echte Antwort abfordern.** `bereit_vllm` prueft nur
# /v1/models -- das antwortete beim Haenger weiter mit 200 und haette nichts
# gemerkt.
WAECHTER_INTERVALL=${KIHIWI_WAECHTER_S:-120}
WAECHTER_PID="$LOGS/waechter.pid"
# Mehr als drei Neustarts in einer Stunde heisst: der Neustart hilft nicht.
# Dann lieber aufhoeren und es im Protokoll stehen lassen, als die Maschine
# im Kreis neu zu starten.
WAECHTER_MAX=3

motor_antwortet() {
    local modell
    modell=$(curl -sf --max-time 5 "http://127.0.0.1:$P_VLLM/v1/models" | modellname)
    [ -z "$modell" ] && return 1
    curl -sf --max-time 30 "http://127.0.0.1:$P_VLLM/v1/chat/completions" \
        -H 'Content-Type: application/json' \
        -d "{\"model\":\"$modell\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"max_tokens\":2}" \
        -o /dev/null
}

waechter_lauf() {
    local neustarts=0 fenster
    fenster=$(date +%s)
    echo "$(date '+%F %T') Waechter gestartet, Intervall ${WAECHTER_INTERVALL}s" >> "$LOGS/waechter.log"
    while :; do
        sleep "$WAECHTER_INTERVALL"
        motor_antwortet && continue
        # Zweite Chance: unter Last kann eine Anfrage auch mal 30 s brauchen.
        sleep 15
        motor_antwortet && continue

        # Stundenfenster zuruecksetzen
        [ $(( $(date +%s) - fenster )) -gt 3600 ] && { neustarts=0; fenster=$(date +%s); }
        if [ "$neustarts" -ge "$WAECHTER_MAX" ]; then
            echo "$(date '+%F %T') Motor tot, aber schon $neustarts Neustarts in dieser Stunde — ich lasse es" >> "$LOGS/waechter.log"
            sleep 1800
            continue
        fi
        # Vorher bei der Belegungsstelle fragen. Am 08.09.2026 hat dieser
        # Waechter zweimal mitten in einen laufenden Gutachtenlauf
        # hineingeschaltet, weil er von ihm nichts wusste -- und der Motor
        # antwortete nicht, weil dort gerade ein ANDERES Modell geladen wurde.
        #
        # Ist die Stelle nicht erreichbar (Rueckgabe 30), wird neu gestartet
        # wie bisher: eine ausgefallene Buchfuehrung darf den Assistenten
        # nicht dauerhaft stumm lassen.
        local k="$HOME/Developer/github.com/modellbelegung/belegung-klient.sh"
        if [ -x "$k" ]; then
            "$k" belegen kihiwi-waechter "$PROFIL" mit 900 "Sprachassistent" >/dev/null 2>&1
            case $? in
                20) echo "$(date '+%F %T') Motor tot, aber jemand anderes hat das Modell belegt — ich warte" >> "$LOGS/waechter.log"
                    continue ;;
                30) echo "$(date '+%F %T') Belegungsstelle nicht erreichbar — starte trotzdem neu" >> "$LOGS/waechter.log" ;;
            esac
        fi
        neustarts=$((neustarts + 1))
        echo "$(date '+%F %T') Motor antwortet nicht — Neustart $neustarts ($PROFIL)" >> "$LOGS/waechter.log"
        "$HOME/.local/bin/model-switch" "$PROFIL" >> "$LOGS/waechter.log" 2>&1
        if motor_antwortet; then
            echo "$(date '+%F %T') wieder da" >> "$LOGS/waechter.log"
        else
            echo "$(date '+%F %T') Neustart hat nicht geholfen" >> "$LOGS/waechter.log"
        fi
    done
}

waechter_start() {
    if [ -f "$WAECHTER_PID" ] && kill -0 "$(cat "$WAECHTER_PID")" 2>/dev/null; then
        ok "Waechter laeuft bereits (PID $(cat "$WAECHTER_PID"))"; return 0
    fi
    setsid --fork nohup "$0" waechter-lauf </dev/null >>"$LOGS/waechter.log" 2>&1
    sleep 1
    # Ueber das Muster suchen ist hier sicher: der Waechter ist der einzige
    # Prozess mit genau diesem Argument, und die eigene Shell traegt es nicht.
    pgrep -f "$0 waechter-lauf" | head -1 > "$WAECHTER_PID"
    if [ -s "$WAECHTER_PID" ]; then
        ok "Waechter gestartet (PID $(cat "$WAECHTER_PID"), alle ${WAECHTER_INTERVALL}s)"
    else
        rm -f "$WAECHTER_PID"; fehl "Waechter kam nicht hoch"; return 1
    fi
}

waechter_stopp() {
    if [ ! -f "$WAECHTER_PID" ]; then info "Waechter laeuft nicht"; return 0; fi
    kill "$(cat "$WAECHTER_PID")" 2>/dev/null
    rm -f "$WAECHTER_PID"
    ok "Waechter beendet"
}

# ------------------------------------------------------------------ Starten
# Der Name, unter dem der Server das Modell anbietet, muss zu KIHIWI_MODEL
# passen -- sonst antwortet vLLM auf jede Anfrage mit 404. Diese Fehlerklasse
# hat schon zweimal zugeschlagen (Hermes mit eigenem Namen aus seiner Konfig,
# und ein Stapellauf ohne gesetztes KIHIWI_MODEL: 100 Anfragen in den 404,
# gemeldet als "100 gescheitert" ohne Grund). Deshalb hier laut, sofort.
# Die Vorgabe steht in konfig.py, nicht hier -- sonst laufen zwei Wahrheiten
# nebeneinander her.
vorgabe_modell() {
    "$VENV" -c 'from sprachdienst import konfig; print(konfig.LLM_MODEL)' 2>/dev/null
}

modell_pruefen() {
    local angeboten gewuenscht
    angeboten=$(curl -s "http://127.0.0.1:$P_VLLM/v1/models" | modellname)
    # Die WIRKSAME Vorgabe, nicht nur die Umgebungsvariable. Ohne das schwieg
    # die Pruefung genau im gefaehrlichsten Fall: KIHIWI_MODEL nicht gesetzt,
    # der Dienst nimmt die Vorgabe aus konfig.py, und die passt nicht zum
    # geladenen Profil. Aufgefallen beim Testlauf von ornith-voice, nachdem
    # die Vorgabe auf Qwen umgestellt war.
    gewuenscht="${KIHIWI_MODEL:-}"
    # Getrennte Bedingungen: "A || B && C" liest sich wie eine Absicht und
    # ist keine -- in bash binden || und && gleich stark, von links.
    if [ -z "$angeboten" ]; then return 0; fi
    # Ist KIHIWI_MODEL NICHT gesetzt, uebernimmt start_sprach den angebotenen
    # Namen. Dann hier zu warnen waere falsch: die Warnung kuendigte einen 404
    # an, den der naechste Schritt gerade behebt.
    if [ -z "$gewuenscht" ]; then return 0; fi
    if [ "$angeboten" = "$gewuenscht" ]; then return 0; fi
    warn "KIHIWI_MODEL='$gewuenscht', angeboten wird '$angeboten' — jede Anfrage"
    warn "  laeuft in einen 404. Selbst gesetzt, also selbst anpassen."
}

start_vllm() {
    if bereit_vllm >/dev/null; then
        local ctx util frei
        ctx=$(curl -s "http://127.0.0.1:$P_VLLM/v1/models" | modellctx)
        util=$(vllm_util)
        frei=$(free -g | awk 'NR==2{print $7}')
        ok "vLLM laeuft bereits (Kontext $ctx, GPU_UTIL ${util:-?})"
        modell_pruefen
        # Nicht das Profil pruefen, sondern was davon abhaengt: bleibt neben
        # dem Modell genug Speicher fuer whisper.cpp, Piper und sherpa-onnx?
        # Das gilt auch fuer ein anderes Modell auf :8889 -- beim Vergleichen
        # laeuft hier absichtlich nicht immer Ornith.
        if [ "${frei:-99}" -lt "$MIN_FREI" ]; then
            # Nicht ungefragt umschalten: auf dieser Maschine wird auch gemessen,
            # und ein Wechsel kostet zwei Minuten Ladezeit.
            warn "nur ${frei} GiB frei — der Sprachstapel braucht mehr Luft"
            info "  Sprachprofil: model-switch ornith-voice (GPU_UTIL 0.55)"
            info "  anderes Modell: GPU_UTIL=0.55 model-switch <profil>"
        fi
        return 0
    fi
    info "starte vLLM ($PROFIL) — das dauert ein bis zwei Minuten"
    "$HOME/.local/bin/model-switch" "$PROFIL" >"$LOGS/vllm.log" 2>&1
    bereit_vllm >/dev/null || { fehl "vLLM kam nicht hoch, siehe $LOGS/vllm.log"; return 1; }
    ok "vLLM bereit"
    modell_pruefen
}

start_whisper() {
    if belegt $P_WHISPER; then ok "whisper-server laeuft bereits (:$P_WHISPER)"; return 0; fi
    [ -x "$WHISPER/build/bin/whisper-server" ] || { fehl "whisper-server fehlt — erst bauen"; return 1; }
    [ -f "$MODELL" ] || { fehl "Modell fehlt: $MODELL"; return 1; }
    info "starte whisper-server"
    # setsid --fork, nicht bloss setsid: ohne --fork forkt setsid nicht, der
    # Dienst bleibt direktes Kind dieser Shell, und die haengt danach in
    # do_wait auf ihn. Mit --fork wird er an init durchgereicht und die Shell
    # ist sofort fertig. </dev/null klemmt zusaetzlich stdin ab.
    ( cd "$WHISPER" && setsid --fork nohup ./build/bin/whisper-server \
        -m "$MODELL" --host 127.0.0.1 --port $P_WHISPER -l de -t 8 \
        </dev/null >"$LOGS/whisper.log" 2>&1 & )
    warte 60 bereit_whisper && ok "whisper-server bereit (:$P_WHISPER)" \
        || { fehl "whisper-server kam nicht hoch, siehe $LOGS/whisper.log"; return 1; }
}

start_sprach() {
    if belegt $P_SPRACH; then ok "Sprachdienst laeuft bereits (:$P_SPRACH)"; return 0; fi
    [ -x "$VENV" ] || { fehl "venv fehlt: $VENV"; return 1; }
    info "starte Sprachdienst"
    # Passt die Vorgabe nicht zum geladenen Profil, den ANGEBOTENEN Namen
    # nehmen. Wer KIHIWI_MODEL selbst setzt, behaelt die Kontrolle; wer nichts
    # setzt, soll nicht an einem 404 scheitern, weil er das Profil gewechselt
    # hat.
    if [ -z "${KIHIWI_MODEL:-}" ]; then
        local angeboten; angeboten=$(curl -s "http://127.0.0.1:$P_VLLM/v1/models" | modellname)
        if [ -n "$angeboten" ] && [ "$angeboten" != "$(vorgabe_modell)" ]; then
            info "KIHIWI_MODEL=$angeboten (aus dem laufenden Profil)"
            export KIHIWI_MODEL="$angeboten"
        fi
    fi
    ( cd "$WURZEL" && setsid --fork nohup "$VENV" -m sprachdienst.gateway \
        </dev/null >"$LOGS/sprach.log" 2>&1 & )
    warte 30 bereit_sprach && ok "Sprachdienst bereit (:$P_SPRACH)" \
        || { fehl "Sprachdienst kam nicht hoch, siehe $LOGS/sprach.log"; return 1; }
}

# ------------------------------------------------------------------ Stoppen
stopp_port() {  # stopp_port <port> <name>
    local pid; pid=$(pid_auf "$1")
    if [ -z "$pid" ]; then info "$2 laeuft nicht"; return 0; fi
    kill "$pid" 2>/dev/null
    for _ in $(seq 1 15); do belegt "$1" || { ok "$2 beendet"; return 0; }; sleep 1; done
    kill -9 "$pid" 2>/dev/null; sleep 1
    belegt "$1" && fehl "$2 laeuft weiter (PID $pid)" || ok "$2 beendet (hart)"
}

# ------------------------------------------------------------------ Status
zeile() {  # zeile <name> <port> <bereit-befehl> <zusatz>
    local farbe symbol zustand
    if "$3" >/dev/null 2>&1; then farbe=32; symbol="✓"; zustand="bereit"
    elif belegt "$2";        then farbe=33; symbol="~"; zustand="startet"
    else                          farbe=31; symbol="✗"; zustand="gestoppt"; fi
    printf '  \033[%sm%s\033[0m %-16s :%-5s %-9s %s\n' "$farbe" "$symbol" "$1" "$2" "$zustand" "${4:-}"
}

status() {
    local modell=""
    bereit_vllm >/dev/null 2>&1 && modell=$(curl -s "http://127.0.0.1:$P_VLLM/v1/models" | modellzeile)
    echo "Dienste:"
    zeile "vLLM"           $P_VLLM    bereit_vllm    "$modell"
    zeile "whisper-server" $P_WHISPER bereit_whisper "large-v3-turbo, -l de"
    zeile "Sprachdienst"   $P_SPRACH  bereit_sprach  "Monitor: http://127.0.0.1:$P_SPRACH/"
    echo
    free -h | awk 'NR==2{printf "  Speicher: %s frei von %s\n", $7, $2}'
}

# Namen pruefen, bevor der Dienst startet. Ein Umbau hat am 14.09.2026 acht
# Definitionen mitgerissen; py_compile merkt davon nichts, weil ein NameError
# erst beim Aufruf entsteht. Der Assistent nahm danach jede Wissensfrage
# entgegen und antwortete nicht.
#
# WARNT nur, bricht nicht ab: ein laufender Dienst mit einem Fehler in einem
# Nebenpfad ist besser als gar keiner -- und wer die Warnung sieht, weiss
# wenigstens, wo er suchen muss.
namen_pruefen() {
    local aus
    aus="$("$VENV" "$WURZEL/namenspruefung.py" 2>&1)" || {
        warn "Namenspruefung schlaegt an:"
        echo "$aus" | grep -v "Dateien geprüft" | sed 's/^/      /'
    }
}

# ------------------------------------------------------------------ Einstieg
case "${1:-status}" in
    start)
        namen_pruefen; start_vllm; start_whisper; start_sprach; echo; status ;;
    namen)
        exec "$VENV" "$WURZEL/namenspruefung.py" "${@:2}" ;;
    waechter)
        case "${2:-start}" in
            start) waechter_start ;;
            stop)  waechter_stopp ;;
            log)   tail -n 30 "$LOGS/waechter.log" 2>/dev/null || info "noch kein Protokoll" ;;
            *)     echo "  ./dienste.sh waechter [start|stop|log]" ;;
        esac ;;
    waechter-lauf)
        waechter_lauf ;;
    stop)
        stopp_port $P_SPRACH  "Sprachdienst"
        stopp_port $P_WHISPER "whisper-server"
        if [ "${2:-}" = "--vllm" ]; then
            info "entlade Modell"; "$HOME/.local/bin/model-switch" stop >/dev/null 2>&1 \
                && ok "vLLM gestoppt"
        else
            info "vLLM bleibt (mit --vllm auch entladen)"
        fi ;;
    neustart)
        stopp_port $P_SPRACH  "Sprachdienst"
        stopp_port $P_WHISPER "whisper-server"
        start_whisper; start_sprach; echo; status ;;
    status)  status ;;
    protokoll)
        # Dokumentationspfad. Braucht whisper-server; das Modell nur fuer
        # Korrektur und Zusammenfassung -- ohne es entsteht trotzdem ein
        # Transkript.
        belegt $P_WHISPER || { fehl "whisper-server laeuft nicht — erst ./dienste.sh start"; exit 1; }
        bereit_vllm >/dev/null || warn "Modell nicht erreichbar — nur Transkript, keine Zusammenfassung"
        shift
        exec "$VENV" -m sprachdienst.protokoll "$@" ;;
    wissen)
        shift
        exec "$VENV" -m wissen "$@" ;;
    sprechermodelle)
        # Die einzige Stelle, die etwas aus dem Netz holt, und sie laeuft nur
        # auf ausdrueckliche Anweisung. Zur Laufzeit geht nichts hinaus.
        ziel="$WURZEL/modelle"; mkdir -p "$ziel"
        basis=https://github.com/k2-fsa/sherpa-onnx/releases/download
        if [ -d "$ziel/sherpa-onnx-pyannote-segmentation-3-0" ]; then
            echo "  Segmentierung liegt schon da"
        else
            echo "  Segmentierung holen ..."
            curl -fsSL --retry 2 -o "$ziel/seg.tar.bz2" \
                "$basis/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2" \
                && tar xjf "$ziel/seg.tar.bz2" -C "$ziel" && rm -f "$ziel/seg.tar.bz2" \
                || { fehl "Segmentierungsmodell nicht geladen"; exit 1; }
        fi
        if [ -f "$ziel/campplus.onnx" ]; then
            echo "  Sprecher-Embedding liegt schon da"
        else
            echo "  Sprecher-Embedding holen ..."
            curl -fsSL --retry 2 -o "$ziel/campplus.onnx" \
                "$basis/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx" \
                || { fehl "Embedding-Modell nicht geladen"; exit 1; }
        fi
        "$VENV" -c "import sherpa_onnx" 2>/dev/null \
            || { echo "  sherpa-onnx fehlt — $VENV -m pip install sherpa-onnx"; exit 1; }
        exec "$VENV" -c "import sys; sys.path.insert(0,'$WURZEL')
from sprachdienst import sprecher
print('  bereit:', sprecher.laden() is not None)" ;;
    log)
        case "${2:-sprach}" in
            sprach)  tail -f "$LOGS/sprach.log" ;;
            whisper) tail -f "$LOGS/whisper.log" ;;
            vllm)    docker logs -f vllm-model ;;
            *)       echo "unbekannt: $2 (sprach | whisper | vllm)"; exit 2 ;;
        esac ;;
    *)  sed -n '2,16p' "$0"; exit 2 ;;
esac
