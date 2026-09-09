#!/bin/bash
# wd.sh -- thin wrapper so the watchdog session types short, literal commands.
#   wd.sh wait  <target> [extra wd_wait args]    block until the target ends a turn / goes stale (prints one event line)
#   wd.sh wake  <target> [--trigger 'LINE'] ...  analyse the latest completed turn and print the report
#   wd.sh boot  <target>                         bootstrap state (first run only)
#   wd.sh sent  F1,F2 [message_id]               record that findings were sent
#   wd.sh veto  F3 "reason"                      record a veto
#   wd.sh cost  <self>                           append this session's per-turn token cost to state/cost.tsv
# State lives in /private/tmp/bm-meta-analysis/watchdog/state (override with WD_STATE).
D=/private/tmp/bm-meta-analysis/watchdog
S=${WD_STATE:-$D/state}
PY=/usr/bin/python3
cmd=$1; shift
case "$cmd" in
  wait) t=$1; shift; exec $PY $D/wd_wait.py --target "$t" --state-dir "$S" "$@" ;;
  wake) t=$1; shift; exec $PY $D/wd_wake.py --target "$t" --state-dir "$S" "$@" ;;
  boot) t=$1; shift; exec $PY $D/wd_wake.py --target "$t" --state-dir "$S" --bootstrap "$@" ;;
  sent) ids=$1; mid=$2; exec $PY $D/wd_wake.py --state-dir "$S" --sent "$ids" --message-id "${mid:-}" ;;
  veto) id=$1; shift; exec $PY $D/wd_wake.py --state-dir "$S" --veto "$id" --reason "$*" ;;
  cost) t=$1; shift; exec $PY $D/wd_cost.py --self "$t" --state-dir "$S" "$@" ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
