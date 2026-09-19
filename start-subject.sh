#!/bin/sh
set -eu
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/src"
exec python3 -m subject01.observer --candidate "$@"
