#!/bin/bash
# wd.sh -- the watchdog session's commands, driven by config.json beside this file (see config.example.json).
#   wd.sh boot                      bootstrap state (first run only)
#   wd.sh wait [extra args]         the hook: block until the target ends a turn, work stalls, or a reply is overdue (one event line)
#   wd.sh wake --trigger '<line>'   analyse the latest completed turn / the stall / the reply; print the report
#   wd.sh sent F1,F2 <message_id>   record that findings were sent (opens the reply window when one asked for a reply)
#   wd.sh veto F3 "reason"          record a veto
#   wd.sh owed                      what the watchdog still owes: turns nobody relayed, and actions the target declared and did not take
#   wd.sh answered                 record that a message was just sent to the target (the MCP send tool cannot do it itself)
#   wd.sh relayed <turn end_ts>     mark turns up to here as relayed to the owner (a send answers them separately)
#   wd.sh hold <turn end_ts> "why"  deliberately hold a turn: it is blocked on the owner
#   wd.sh cost                      append this session's per-turn token cost to state/cost.tsv
#   wd.sh queue add "<text>"        hold an owner item until the next wake (add --urgent to send at once)
#   wd.sh queue list | clear <id>   show held items; clear them once delivered in a message
#   wd.sh due                       what is undelivered (owner items + findings) and whether the target is receptive
#   wd.sh owe add|list|ungate|done   decisions the owner owes, each READY or GATED behind unfinished target work
#   wd.sh status                    one-screen state summary
#   wd.sh check <kind> [args]       verify one thing now (running | commit <sha> | file <path> [since] | task <id> | row <ID> | msg-to-watchdog [since] | dispatch <thread> | tree [path] | grep <path> <regex> | csv <path> <col><op><val> [idcol])
#   wd.sh finding <class> "<quote>" check <kind> [args]   build a fixed-form finding from a check the model chose; the result text is the script's
# Overrides: WD_STATE (state dir), WD_CONFIG (config file). Everything is read-only except the state dir.
D="$(cd "$(dirname "$0")" && pwd)"
S="${WD_STATE:-$D/state}"
C="${WD_CONFIG:-$D/config.json}"
PY=/usr/bin/python3
if [ ! -f "$C" ]; then echo "no config: copy $D/config.example.json to $C and fill in target/self" >&2; exit 2; fi
cfg() { $PY -c "import json,sys; d=json.load(open(sys.argv[1])); v=d.get(sys.argv[2], sys.argv[3] if len(sys.argv)>3 else ''); print(','.join(v) if isinstance(v,list) else ('' if v is None else v))" "$C" "$@"; }
TARGET="$(cfg target)"; SELF="$(cfg self)"
if [ -z "$TARGET" ] || [ "${TARGET#<}" != "$TARGET" ]; then echo "config.json: 'target' is not set" >&2; exit 2; fi
WAKE_ARGS=(--target "$TARGET" --state-dir "$S" --quiet-min "$(cfg quiet_min 10)" --stale-turns "$(cfg stale_turns 5)" --dead-min "$(cfg stall_min 20)" --codex-quiet-min "$(cfg stall_min 20)" --reply-min "$(cfg reply_min 20)" --row-pattern "$(cfg row_pattern '[A-Z]{1,2}\d{1,3}')")
[ -n "$SELF" ] && [ "${SELF#<}" = "$SELF" ] && WAKE_ARGS+=(--self "$SELF")
[ -n "$(cfg repo)" ] && WAKE_ARGS+=(--repo "$(cfg repo)")
[ -n "$(cfg ledger)" ] && WAKE_ARGS+=(--ledger "$(cfg ledger)")
[ -n "$(cfg other_ref)" ] && WAKE_ARGS+=(--other-ref "$(cfg other_ref)")
[ -n "$(cfg perm_paths)" ] && WAKE_ARGS+=(--perm-paths "$(cfg perm_paths)")
[ -n "$(cfg standing_instruction)" ] && WAKE_ARGS+=(--standing-instruction "$(cfg standing_instruction)")
WAIT_ARGS=(--target "$TARGET" --state-dir "$S" --stale-after "$(cfg stale_after_s 1800)" --stall-min "$(cfg stall_min 20)" --idle-after "$(cfg idle_after_s 0)")
CHECK_ARGS=(--target "$TARGET" --state-dir "$S" --quiet-min "$(cfg quiet_min 10)" --row-pattern "$(cfg row_pattern '[A-Z]{1,2}\d{1,3}')")
[ -n "$SELF" ] && [ "${SELF#<}" = "$SELF" ] && CHECK_ARGS+=(--self "$SELF")
[ -n "$(cfg repo)" ] && CHECK_ARGS+=(--repo "$(cfg repo)")
[ -n "$(cfg ledger)" ] && CHECK_ARGS+=(--ledger "$(cfg ledger)")
[ -n "$SELF" ] && [ "${SELF#<}" = "$SELF" ] && WAIT_ARGS+=(--self "$SELF")
cmd=$1; shift
case "$cmd" in
  boot)   exec $PY "$D/wd_wake.py" "${WAKE_ARGS[@]}" --bootstrap "$@" ;;
  wait)   exec $PY "$D/wd_wait.py" "${WAIT_ARGS[@]}" "$@" ;;
  wake)   exec $PY "$D/wd_wake.py" "${WAKE_ARGS[@]}" "$@" ;;
  sent)   ids=$1; mid=$2; exec $PY "$D/wd_wake.py" --state-dir "$S" --reply-min "$(cfg reply_min 20)" --sent "$ids" --message-id "${mid:-}" ;;
  veto)   id=$1; shift; exec $PY "$D/wd_wake.py" --state-dir "$S" --veto "$id" --reason "$*" ;;
  cost)   [ -n "$SELF" ] || { echo "config.json: 'self' is not set" >&2; exit 2; }; exec $PY "$D/wd_cost.py" --self "$SELF" --state-dir "$S" "$@" ;;
  owed)   exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" owed ;;
  answered) exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" answered ;;
  relayed) exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" relayed "$@" ;;
  ask)    exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" ask "$@" ;;
  resolved) exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" resolved "$@" ;;
  open)   exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" open ;;
  next)   exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" next ;;
  sent1)  exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" sent1 "$@" ;;
  nudged) exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" nudged "$@" ;;
  conditional) exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" conditional "$@" ;;
  fired)  exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" fired "$@" ;;
  hold)   exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" hold "$@" ;;
  check)  exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" check "$@" ;;
  finding) exec $PY "$D/wd_check.py" "${CHECK_ARGS[@]}" finding "$@" ;;
  queue)  sub=$1; shift
          case "$sub" in
            add)   urgent=""; [ "$1" = "--urgent" ] && { urgent="--queue-urgent"; shift; }
                   exec $PY "$D/wd_wake.py" --state-dir "$S" --queue-add "$*" $urgent ;;
            list)  exec $PY "$D/wd_wake.py" --state-dir "$S" --queue-list ;;
            clear) exec $PY "$D/wd_wake.py" --state-dir "$S" --queue-clear "$1" ;;
            hold)  id=$1; shift; exec $PY "$D/wd_wake.py" --state-dir "$S" --queue-hold "$id" --hold-until "$*" ;;
            *) echo "wd.sh queue add [--urgent] \"<owner's words>\" | list | clear <message_id>" >&2; exit 2 ;;
          esac ;;
  outcome) id=$1; v=$2; shift 2; exec $PY "$D/wd_wake.py" --state-dir "$S" --outcome "$id" "$v" --reason "$*" ;;
  due)    exec $PY "$D/wd_wake.py" --state-dir "$S" --target "$TARGET" --due ;;
  owe)    sub=$1; shift
          case "$sub" in
            add)    g=""; [ "$1" = "--gated-on" ] && { g="$2"; shift 2; }
                    exec $PY "$D/wd_wake.py" --state-dir "$S" --owe-add "$*" --gated-on "$g" ;;
            list)   exec $PY "$D/wd_wake.py" --state-dir "$S" --owe-list ;;
            ungate) exec $PY "$D/wd_wake.py" --state-dir "$S" --owe-ungate "$1" ;;
            done)   exec $PY "$D/wd_wake.py" --state-dir "$S" --owe-clear "$1" ;;
            *) echo 'wd.sh owe add [--gated-on "<what must finish first>"] "<decision>" | list | ungate <id> | done <id>' >&2; exit 2 ;;
          esac ;;
  status) $PY - "$S" <<'PYS'
import json,sys,os
p=os.path.join(sys.argv[1],'state.json'); d=json.load(open(p)) if os.path.exists(p) else {}
print('wakes', d.get('wake_count'), 'last', d.get('last_wake_ts'), 'last_ct', d.get('last_ct'), 'findings', d.get('finding_counter'), 'raised', len(d.get('raised') or {}), 'proposed(unmarked)', list((d.get('proposed') or {}).keys()))
print('in_flight:', [(i.get('kind'), i.get('id') or (i.get('thread') or '')[:8], i.get('launched_ts')) for i in d.get('in_flight') or []])
print('awaiting_reply:', d.get('awaiting_reply')); print('last_reply:', (d.get('last_reply') or {}).get('ts'))
PYS
  ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
