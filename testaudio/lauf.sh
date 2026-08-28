#!/usr/bin/env bash
# Vergleicht drei Whisper-Konfigurationen auf demselben Testaudio.
set -u
# Pfade ueber die Umgebung, damit das Skript nicht an einem Rechner klebt.
W=${WHISPER_CLI:-$HOME/code/whisper.cpp/build/bin/whisper-cli}
M=${WHISPER_MODELL:-$HOME/code/whisper.cpp/models/ggml-large-v3-turbo.bin}
D=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROMPT=$(cat "$D/vokabular.txt")

lauf() {  # $1=Bezeichnung  $2=Datei  $3...=Flaggen
    local name=$1 datei=$2; shift 2
    local t0 t1
    t0=$(date +%s.%N)
    local out
    out=$("$W" -m "$M" -f "$datei" -nt -np "$@" 2>/dev/null | tr -s ' \n' ' ' | sed 's/^ *//;s/ *$//')
    t1=$(date +%s.%N)
    printf '%-22s %6.2fs  %s\n' "$name" "$(echo "$t1 - $t0" | bc)" "${out:-<leer>}"
}

for f in s1 s2 s3 s4 s5; do
    echo "=== $f ==="
    lauf "A auto"        "$D/$f.wav"
    lauf "B -l de"       "$D/$f.wav" -l de
    lauf "C -l de+prompt" "$D/$f.wav" -l de --prompt "$PROMPT"
    echo
done
