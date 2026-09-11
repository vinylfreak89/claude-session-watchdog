#!/usr/bin/python3
"""Control for `check dispatch`: an id it cannot look up must not read as a dispatch that never happened.

The check globs Codex rollout files for a THREAD id (UUID-shaped). Handed a harness background-task
id -- short, alphanumeric, a different namespace entirely -- it found no file and returned
`no rollout found` with `found: false`. That reads as "the claimed dispatch does not exist", and this
watchdog has a finding class (`dispatch_claim_no_call`) that would have been filed against the target
for work it had actually done. The dispatch was real: Codex rollouts were written minutes before the
claim and the reply carried findings that checked out in the source.

Answers-a-different-question, in the instrument that exists to catch exactly that: the check answers
"is there a rollout whose thread id is X" while being asked "did the dispatch X refers to happen",
and those come apart the moment X is not a thread id. A negative must therefore distinguish
NOT-FOUND from NOT-A-THREAD-ID.

The subject is synthesised: ids of each form are passed directly, so the control does not depend on
any rollout existing on this machine.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_check as C

class A:
    repo = '/tmp'

def run(*ident):
    # *ident so control 4 can call it with NOTHING, which is the case under test.
    return C.check(A(), {'cwd': '/tmp'}, 'dispatch', list(ident), {})

def main():
    fails = []

    # 1. THE DEFECT: a harness task id must not come back as a plain negative.
    checked, result, ev = run('byccn01nz')
    if ev.get('found') is False and 'not a' not in result.lower():
        fails.append('a non-thread id returned a bare negative: %r' % result)
    if ev.get('id_form') != 'unrecognised':
        fails.append('a non-thread id was not reported as an unrecognised id form: %r' % ev)

    # 2. A genuine thread id that has no rollout must STILL report a real negative --
    #    the fix must not turn every miss into "unrecognised" and hide true absences.
    # A UUID that exists nowhere -- an id that DOES resolve would test the found path instead.
    checked, result, ev = run('deadbeef-0000-0000-0000-000000000000')
    if ev.get('id_form') == 'unrecognised':
        fails.append('a UUID-shaped id was called unrecognised')
    if ev.get('found') is None:
        fails.append('a UUID-shaped id with no rollout did not report found=False: %r' % ev)

    # 3. A bare hex PREFIX is a legitimate way to name a thread here ("prefix ok" in the usage),
    #    so it must not be rejected as unrecognised either.
    checked, result, ev = run('01a08bf7')
    if ev.get('id_form') == 'unrecognised':
        fails.append('a hex thread-id prefix was called unrecognised')

    # 4. NO id at all is a FOURTH outcome and must be a refusal, never a crash. It used to raise
    #    IndexError, so a caller reading the last line saw a traceback and could not tell a missing
    #    argument from a missing dispatch -- the two have opposite consequences.
    try:
        checked, result, ev = run()
        if ev.get('id_form') != 'absent':
            fails.append('a missing id was not reported as an absent id form: %r' % ev)
        if ev.get('found') is not None:
            fails.append('a missing id reported a found verdict it cannot have: %r' % ev)
        if 'NOT a missing dispatch' not in result:
            fails.append('the refusal does not say it is a missing ARGUMENT: %r' % result)
    except IndexError:
        fails.append('check dispatch with no id raised IndexError instead of refusing')

    for f in fails:
        print('FAIL:', f)
    print('SELFTEST', 'FAILED' if fails else 'PASS', '(4 controls)')
    return 1 if fails else 0

if __name__ == '__main__':
    raise SystemExit(main())
