#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
bash scripts/start-unix.sh
status=$?
if [ "$status" -ne 0 ]; then read -r -p "Press Return to close. See logs/setup.log for details. " ignored; fi
exit "$status"
