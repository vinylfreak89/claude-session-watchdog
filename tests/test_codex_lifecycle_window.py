#!/usr/bin/python3
"""Control: "I did not see it start" must not be reported as "it never started".

codex_thread_state read a fixed 1 MB tail of the rollout and took the absence of `task_started`
there as absence of a turn. A busy Codex turn writes megabytes of reasoning and tool records, so
the start event scrolls out of that window -- and it scrolls out FASTEST when the turn is most
active. The check therefore failed hardest exactly when the dispatch was healthiest.

Cost, 2026-09-16. A Codex review of A9 started 18 seconds after its dispatch and was still running
six minutes later. Its `task_started` sat 3.7 MB behind a 1 MB window, so the watchdog raised
`dispatch_no_turn` -- "no task_started after the dispatch" -- against a turn that was writing new
records while it looked. The rollout was 538 MB; no tail size fixes this, because the right size
depends on how much the turn has produced since it began.

The repair is two things, and the second matters more than the first: search backwards for the
event, AND report whether the search was conclusive. A bounded search that comes up empty has
found an unknown, not a negative, so `lifecycle_known` is false and no finding may be raised from
it. Control 4 is the one that would have prevented the incident.

Control 5 is the positive control: a genuinely absent lifecycle in a SMALL file is conclusive, and
must stay reportable -- a fix that made every answer "unknown" would silence real stalls.
"""
import sys, os, json, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_lib as W

THREAD = '01a0aaaa-bbbb-cccc-dddd-eeeeffff0000'


def rollout(tmp, lines):
    d = os.path.join(tmp, '2026', '09', '16')
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, 'rollout-2026-09-16T00-00-00-%s.jsonl' % THREAD)
    with open(p, 'w') as fh:
        for l in lines:
            fh.write(json.dumps(l) + '\n')
    return p


def filler(n, ts='2026-09-16T13:00:00.000Z'):
    """Records of the shape a working turn emits -- reasoning and tool traffic, no lifecycle."""
    blob = 'x' * 4000
    return [dict(timestamp=ts, type='response_item', payload=dict(type='reasoning', text=blob)) for _ in range(n)]


def main():
    fails = []

    def check(name, got, want):
        ok = got == want
        print('%-64s %s%s' % (name, 'PASS' if ok else 'FAIL', '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    tmp = tempfile.mkdtemp(prefix='wd-rollout-')
    real_sessions = W.CODEX_SESSIONS
    W.CODEX_SESSIONS = tmp
    try:
        # A turn that started, then produced ~4 MB of records -- the real incident's shape.
        start_ts = '2026-09-16T12:55:59.557Z'
        rollout(tmp, [dict(timestamp=start_ts, type='event_msg', payload=dict(type='task_started'))] + filler(1000))
        s = W.codex_thread_state(THREAD)
        check('1. a start buried behind a busy turn is still found', s['last_started'], start_ts)
        check('2. and the turn reads as in flight', s['in_flight'], True)
        check('3. the lifecycle is known', s['lifecycle_known'], True)

        # THE INCIDENT: with a tail-only read the start is invisible. Prove the old window misses
        # it, so control 1 is measuring something real rather than a file that always fit.
        size = os.path.getsize(s['rollout'])
        with open(s['rollout'], 'rb') as fh:
            fh.seek(max(0, size - 1024 * 1024))
            tail = fh.read()
        check('4. CONTROL: the old 1 MB tail genuinely could not see it', b'task_started' in tail, False)

        # A search that runs out of budget must answer UNKNOWN, never "never started".
        s2 = W.codex_thread_state(THREAD)
        real_scan = W._last_marked_line
        W._last_marked_line = lambda *a, **k: (None, False)      # searched, hit the cap, saw nothing
        try:
            s3 = W.codex_thread_state(THREAD)
        finally:
            W._last_marked_line = real_scan
        check('5. an exhausted search reports lifecycle_known False', s3['lifecycle_known'], False)
        check('6. and does not invent a start time', s3['last_started'], None)

        # POSITIVE CONTROL: a small file with genuinely no lifecycle is a conclusive negative.
        shutil.rmtree(tmp); os.makedirs(tmp, exist_ok=True)
        rollout(tmp, filler(3))
        s4 = W.codex_thread_state(THREAD)
        check('7. a genuinely empty lifecycle in a small file IS conclusive', s4['lifecycle_known'], True)
        check('8. with no start recorded', s4['last_started'], None)
        check('9. and not in flight', s4['in_flight'], False)

        # A completed turn is not in flight.
        shutil.rmtree(tmp); os.makedirs(tmp, exist_ok=True)
        rollout(tmp, [dict(timestamp='2026-09-16T12:55:59.557Z', type='event_msg', payload=dict(type='task_started'))]
                + filler(600)
                + [dict(timestamp='2026-09-16T13:02:53.875Z', type='event_msg',
                        payload=dict(type='task_complete', last_agent_message='done'))]
                + filler(600))
        s5 = W.codex_thread_state(THREAD)
        check('10. a completed turn reports its completion', s5['last_complete'], '2026-09-16T13:02:53.875Z')
        check('11. and is not in flight', s5['in_flight'], False)

        # A thread with no rollout at all: every key readable, and NOT a conclusive negative.
        check('12. a missing rollout is not a conclusive lifecycle',
              W.codex_thread_state('no-such-thread-0000')['lifecycle_known'], False)
    finally:
        W.CODEX_SESSIONS = real_sessions
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n%d check(s) FAILED' % len(fails) if fails else '\nall checks passed')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
