#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 "$SCRIPT_DIR/configure_knowledge_base.py" "$@"
