#!/bin/bash
# Run one or more campaigns in order: ./run_campaign.sh campaigns/psychic_local campaigns/psychic_wide
# Each runs from its own folder (state, seen lists, results_<term>.csv live there) and is
# restarted after a crash; it picks up from scraper_state.json. Exit 0 = all terms x cities done.
# Stop with Ctrl-C (twice), pause with: touch <campaign>/pause.flag
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON=/home/web/Programs/venv/bin/python3
for dir in "$@"; do
    cd "$HERE/$dir" || exit 1
    tries=0
    until "$PYTHON" "$HERE/scraper_automated.py"; do
        tries=$((tries + 1))
        if [ "$tries" -ge 25 ]; then echo "$dir: crashed $tries times, giving up"; exit 1; fi
        echo "$dir: scraper exited with an error, restarting in 10s ($tries)"
        sleep 10
    done
    echo "$dir: done"
done
