#!/usr/bin/env bash
# Vergleicht drei Whisper-Konfigurationen auf demselben Testaudio.
set -u
W=/home/nutzer/code/whisper.cpp/build/bin/whisper-cli
M=/home/nutzer/code/whisper.cpp/models/ggml-large-v3-turbo.bin
D=/home/nutzer/Developer/github.com/kihiwi/testaudio
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
