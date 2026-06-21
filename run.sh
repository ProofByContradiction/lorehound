#!/usr/bin/env bash
# Start the Lorehound Discord bot using the project's virtualenv.
# Usage:
#   ./run.sh                 run in the foreground (Ctrl-C to stop)
#   nohup ./run.sh >> bot.log 2>&1 &   run detached, logging to bot.log
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python bot.py
