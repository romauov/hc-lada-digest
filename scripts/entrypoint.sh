#!/bin/bash
set -e

if [ -f scripts/reset_data.py ]; then
    echo "[entrypoint] One-shot data reset detected, running..."
    python scripts/reset_data.py
    echo "[entrypoint] Reset done."
fi

exec "$@"
