#!/bin/sh
set -eu
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/src"
if [ "$#" -gt 0 ]; then
  exec python3 -m subject01.observer --candidate "$@"
fi
exec python3 -m subject01.launch
