#!/usr/bin/python3
"""Control: `answered` must refuse unless a message actually reached the target, or the owner said so.

`wd.sh answered` records "a message was just sent to the target". Nothing checked that. Measured from
this session's own transcript, FIVE invocations had no send behind them, every one in the same shape:

    ./wd.sh relayed <ts> && ./wd.sh answered

Relaying to the OWNER was chained to discharging the obligation to the TARGET, so the two different
acts were fused by a habit of typing. The cost was not bookkeeping: three of those turns carried
claims -- a proposed test, a judgement that an item did not need the owner, and a conclusion committed
at e1323fe -- that I never checked, while the alarm recorded them as handled and stopped asking. One
of them reached the owner before Codex withdrew it.

A sixth invocation credited ONE send to TWO turns.

The evidence is independent of anything this session asserts: a delivered message appears in the
TARGET's own transcript as a user record carrying `<cross-session-message from="<self>"`. So the guard
reads the target's file, not its own memory, and each send can be credited once.

The owner's second route is explicit and must stay narrow: --owner-ack "<his words>" requires the
words. An empty string cannot launder a bypass.
"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_check as C

SELF = 'local_TEST-SELF'

def _tx(tmp, stamps):
    """A synthetic TARGET transcript containing inbound messages from us at `stamps`."""
    p = os.path.join(tmp, 'target.jsonl')
    with open(p, 'w') as f:
        for ts in stamps:
            f.write(json.dumps({
                "type": "user", "timestamp": ts,
                "message": {"content": 'Another Claude session sent a message:\n'
                                       '<cross-session-message from="%s" name="wd">hi</cross-session-message>' % SELF}
            }) + "\n")
    return p

def main():
    fails = []
    tmp = tempfile.mkdtemp()

    # 1. THE DEFECT: no inbound message at all -> must refuse.
    st = {}
    ok, why = C.answered_allowed(_tx(tmp, []), SELF, st, None)
    if ok:
        fails.append('allowed with NO message ever delivered')

    # 2. A genuine send -> allowed, and it is credited.
    tx = _tx(tmp, ['2026-09-11T01:00:00.000Z'])
    st = {}
    ok, why = C.answered_allowed(tx, SELF, st, None)
    if not ok:
        fails.append('refused a genuine send: %s' % why)
    if st.get('credited_send_ts') != '2026-09-11T01:00:00.000Z':
        fails.append('did not credit the send it accepted: %r' % st)

    # 3. THE DOUBLE-COUNT: the same send must not pay for a second turn.
    ok, why = C.answered_allowed(tx, SELF, st, None)
    if ok:
        fails.append('credited ONE send to TWO turns')

    # 4. A newer send re-opens it.
    tx2 = _tx(tmp, ['2026-09-11T01:00:00.000Z', '2026-09-11T02:00:00.000Z'])
    ok, why = C.answered_allowed(tx2, SELF, st, None)
    if not ok:
        fails.append('refused a genuinely newer send: %s' % why)

    # 5. The owner's route works, and records his words.
    st2 = {}
    ok, why = C.answered_allowed(_tx(tmp, []), SELF, st2, 'keep firing the alarm')
    if not ok:
        fails.append('refused an explicit owner acknowledgement')
    if 'keep firing' not in json.dumps(st2):
        fails.append('did not record the owner words it relied on: %r' % st2)

    # 6. An EMPTY ack cannot launder a bypass -- otherwise the route is a hole.
    ok, why = C.answered_allowed(_tx(tmp, []), SELF, {}, '   ')
    if ok:
        fails.append('an empty owner-ack was accepted')

    # 7. A message from someone ELSE is not my send.
    p = os.path.join(tmp, 'other.jsonl')
    open(p, 'w').write(json.dumps({"type": "user", "timestamp": "2026-09-11T03:00:00.000Z",
        "message": {"content": '<cross-session-message from="local_SOMEONE_ELSE">hi</cross-session-message>'}}) + "\n")
    ok, why = C.answered_allowed(p, SELF, {}, None)
    if ok:
        fails.append("credited another session's message as mine")

    # 8. A MID-TURN delivery is evidence too. It does not land as a `user` record: it is absorbed
    #    into the target's context as an `attachment` whose `rendered` block is the system-reminder.
    #    Reading `user` only, the gate refused a send that had demonstrably arrived -- measured on
    #    the live transcript at 2026-09-11T05:58:30Z -- and would have nagged forever on an answered
    #    turn. The body must be EXTRACTED from `rendered`, never `json.dumps`ed, or the inner quotes
    #    are re-escaped and the marker cannot match.
    p = os.path.join(tmp, 'midturn.jsonl')
    open(p, 'w').write(json.dumps({
        "type": "attachment", "timestamp": "2026-09-11T04:00:00.000Z",
        "rendered": [{"content": '<system-reminder>\nAnother Claude session sent a message while '
                                 'you were working:\n<cross-session-message from="%s">hello'
                                 '</cross-session-message>\n</system-reminder>' % SELF}]}) + "\n")
    ok, why = C.answered_allowed(p, SELF, {}, None)
    if not ok:
        fails.append('refused a mid-turn delivery absorbed as an attachment: %s' % why)

    # 9. QUEUING IS NOT DELIVERY. `queue-operation/enqueue` proves the message was queued, never
    #    that it arrived, so crediting it would be exactly the false positive this gate exists to
    #    prevent. It must NOT count on its own.
    p = os.path.join(tmp, 'queued.jsonl')
    open(p, 'w').write(json.dumps({
        "type": "queue-operation", "operation": "enqueue",
        "timestamp": "2026-09-11T04:30:00.000Z",
        "content": '<cross-session-message from="%s">queued only</cross-session-message>' % SELF}) + "\n")
    ok, why = C.answered_allowed(p, SELF, {}, None)
    if ok:
        fails.append('credited a QUEUED message as delivered -- queuing is not arrival')

    for f in fails:
        print('FAIL:', f)
    print('SELFTEST', 'FAILED' if fails else 'PASS', '(9 controls)')
    return 1 if fails else 0

if __name__ == '__main__':
    raise SystemExit(main())
