#!/usr/bin/env bash
# dienste.sh — startet, stoppt und zeigt die drei Dienste des Sprachassistenten.
#
#   ./dienste.sh start           alles hochfahren (idempotent)
#   ./dienste.sh stop            Sprachdienst und whisper-server beenden
#   ./dienste.sh stop --vllm     zusaetzlich das Modell entladen
#   ./dienste.sh neustart        stop + start der beiden lokalen Dienste
#   ./dienste.sh status          was laeuft, auf welchem Port
#   ./dienste.sh log [name]      Protokoll folgen (sprach | whisper | vllm)
#   ./dienste.sh protokoll [...] Aufnahmen transkribieren und Protokoll bauen
#   ./dienste.sh wissen [...]    Unterlagen einlesen/durchsuchen
#                                (einlesen | erschliessen | katalog |
#                                 ueberblick | status | suchen ... | web ...)
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

# ------------------------------------------------------------------ Starten
start_vllm() {
    if bereit_vllm >/dev/null; then
        local ctx util frei
        ctx=$(curl -s "http://127.0.0.1:$P_VLLM/v1/models" | modellctx)
        util=$(vllm_util)
        frei=$(free -g | awk 'NR==2{print $7}')
        ok "vLLM laeuft bereits (Kontext $ctx, GPU_UTIL ${util:-?})"
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
    info "starte vLLM (ornith-voice) — das dauert ein bis zwei Minuten"
    "$HOME/.local/bin/model-switch" ornith-voice >"$LOGS/vllm.log" 2>&1
    bereit_vllm >/dev/null && ok "vLLM bereit" || { fehl "vLLM kam nicht hoch, siehe $LOGS/vllm.log"; return 1; }
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

# ------------------------------------------------------------------ Einstieg
case "${1:-status}" in
    start)
        start_vllm; start_whisper; start_sprach; echo; status ;;
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
