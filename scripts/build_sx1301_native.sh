#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 ]] || { echo "Usage: $0 /path/to/lora_gateway" >&2; exit 2; }
HAL_DIR=$(cd "$1" && pwd)
ROOT=$(cd "$(dirname "$0")/.." && pwd)
[[ $(cat "$HAL_DIR/VERSION") == 5.0.1 ]] || { echo 'SX1301 HAL 5.0.1 required' >&2; exit 1; }
# Shared shim is private to this checkout. Never install/overwrite libloragw.so.
make -C "$HAL_DIR/libloragw" -B libloragw.a DEBUG_HAL=1 DEBUG_REG=1 \
    CFLAGS='-O2 -Wall -Wextra -std=c99 -fPIC -Iinc -I.'
mkdir -p "$ROOT/build"
"${CC:-cc}" -O2 -Wall -Wextra -Werror -std=c99 -fPIC -shared \
    -I"$HAL_DIR/libloragw/inc" "$ROOT/scripts/sx1301_native.c" \
    "$HAL_DIR/libloragw/libloragw.a" -lrt -lm -o "$ROOT/build/libsx1301_meshpoint.so"
git -C "$HAL_DIR" rev-parse HEAD > "$ROOT/build/sx1301-hal-revision.txt"
echo "Built $ROOT/build/libsx1301_meshpoint.so"
