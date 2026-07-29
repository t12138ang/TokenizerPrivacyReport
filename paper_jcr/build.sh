#!/usr/bin/env sh
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
pass=1
while [ "$pass" -le 3 ]; do
  printf '[XeLaTeX %s/3]\n' "$pass"
  xelatex -interaction=nonstopmode -halt-on-error -file-line-error main.tex
  pass=$((pass + 1))
done
printf 'SUCCESS: %s/main.pdf\n' "$PWD"
