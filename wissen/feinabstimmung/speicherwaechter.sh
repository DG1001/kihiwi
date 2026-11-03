#!/bin/bash
# speicherwaechter.sh <ausgabe> <min_frei_gb> -- befehl... : bricht ab, wenn der freie Speicher sinkt
aus=$1; min=$2; shift 3
"$@" > "$aus" 2>&1 & P=$!; t0=$SECONDS; tief=999
while kill -0 $P 2>/dev/null; do
  f=$(free -g | awk 'NR==2{print $7}'); [ "$f" -lt "$tief" ] && tief=$f
  if [ "$f" -lt "$min" ]; then kill $P; echo "ABBRUCH: nur $f GB frei" >> "$aus"; fi
  sleep 2
done
wait $P; rc=$?
echo "Ende rc=$rc, tiefster freier Speicher $tief GB, $((SECONDS-t0)) s" >> "$aus"
