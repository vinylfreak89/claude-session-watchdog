#!/usr/bin/python3
"""Controls for the nudge: it must ride in the owed alarm, and a re-send must actually count.

Two defects, both silent. `resends` was written as 0 at registration and NOTHING ever incremented it
-- there was no verb -- so a question read "0 resend(s)" no matter how often it was re-sent. And due
questions were listed in a section below the owed counts rather than counted as owed, so 21 of them
sat DUE for 30+ turns while the alarm above them said "nothing owed". A list is not an alarm.

Owner, 2026-09-11: "throw nudge inside owed so it fires consistently... You should be as annoying to
it as the hook is to you lol."

Controls are derived from how the nudge can fail rather than from the case that prompted it:
  A  a question past the gate is DUE                       (it fires at all)
  B  recording a re-send quiets it                          (the verb does something)
  C  ...and the count actually increments                   (the counter counts)
  D  a re-sent question comes back DUE at the next gate     (temporarily satisfied, never silent)
  E  a fresh question is NOT due                            (no false alarm on the turn it is asked)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_check as C, wd_lib as W

def _state(asked_ct, resends=0):
    return dict(open_questions=dict(K=dict(text='q', asked_ts='2026-09-10T00:00:00Z',
                                           asked_ct=str(asked_ct), resends=resends,
                                           last_send='2026-09-10T00:00:00Z')))

def _at(ct):
    W.read_state = lambda sess: dict(ct=ct)
    W.activity_ms = lambda sess: W.ms_of_iso(W.now_iso())      # peer active, so only the GATE can fire

def main():
    fails = 0
    def check(name, cond):
        nonlocal fails; fails += not cond
        print('%-52s : %s' % (name, 'PASS' if cond else 'FAIL'))

    _at(10)
    st = _state(asked_ct=1)                                    # 9 turns past the ask
    due = C.due_questions(None, st, quiet_min=10.0)
    check('A  past the gate -> DUE', len(due) == 1)

    _at(10)
    st_fresh = _state(asked_ct=10)                             # asked this very turn
    check('E  fresh question -> NOT due', len(C.due_questions(None, st_fresh, 10.0)) == 0)

    # B and C: exercise the real verb through main(), not by mutating the dict here
    import tempfile, json, subprocess
    sd = tempfile.mkdtemp()
    json.dump(st, open(os.path.join(sd, 'state.json'), 'w'))
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
    r = subprocess.run([sys.executable, os.path.join(root, 'wd_check.py'), '--target',
                        'BlackMagic Intensity USB driver for Apple Silicon', '--state-dir', sd,
                        'nudged', 'K'], capture_output=True, text=True)
    after = json.load(open(os.path.join(sd, 'state.json')))['open_questions']['K']
    check('C  the re-send COUNTS (resends 0 -> 1)', int(after.get('resends', 0)) == 1)

    _at(int(after['asked_ct']))                                 # clock re-armed to now
    check('B  recording a re-send quiets it', len(C.due_questions(None, dict(open_questions=dict(K=after)), 10.0)) == 0)

    _at(int(after['asked_ct']) + 5)                              # five turns later
    check('D  re-sent question returns DUE at the next gate',
          len(C.due_questions(None, dict(open_questions=dict(K=after)), 10.0)) == 1)

    print('RESULT: %s' % ('all controls pass' if not fails else '%d FAILED' % fails))
    return 1 if fails else 0

if __name__ == '__main__':
    raise SystemExit(main())
