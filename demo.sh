#!/bin/sh
# Grainwell demo - the three calculators end to end, non-interactively.
#
#   sh demo.sh
#
# Nothing is stored, nothing is networked and no prompt is ever shown; the live
# countdown is deliberately not part of this transcript (it owns the terminal),
# so the mash timer is demonstrated with --plan-only.
set -eu

cd "$(dirname "$0")"
PY="${PYTHON:-python3}"

gw() {
    "$PY" grainwell.py "$@"
}

show() {
    echo
    echo "\$ grainwell $*"
    echo
    gw "$@"
}

show bill --add "Pilsner,5,1.8" --add "Munich,1.5,9" --add "Crystal 60,0.5,60" --volume 23
show mash strike --ratio 3.0 --target 66 --grain-temp 12
show mash timer --step "Protein rest,55,15" --step "Saccharification,66,60" --step "Mash out,76,10" --ratio 3.0 --plan-only
show ibu --hop "Cascade,5.5,30,60" --hop "Saaz,3.5,20,15" --volume 23 --gravity 1.050

echo
echo "\$ grainwell bill --volume 23    # empty state: no malts yet"
echo
gw bill --volume 23

echo
echo "\$ grainwell ibu --hop \"Cascade,55,30,60\" --volume 23 --gravity 1.050    # validation"
echo
set +e
gw ibu --hop "Cascade,55,30,60" --volume 23 --gravity 1.050
status=$?
set -e
echo "  (exit code $status - invalid input)"

echo
echo "demo complete: 3 calculators, no network, no files written."
