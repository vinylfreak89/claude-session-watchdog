#!/usr/bin/python3
"""Control: a background task is LAUNCHED by a tool call, never by a sentence in its output.

The detector used to `re.search` the harness's confirmation anywhere in a tool result. That phrase
is not rare in output: any command that prints a transcript, a log, or a grep hit can carry it,
and there it names SOMEBODY ELSE'S task.

Cost, 2026-09-16. The target ran a FOREGROUND command that printed a ten-day-old transcript from
another session. One line of it read "Command running in background with ID: ba51t4akr. Output is
being written to: .../dd696660-.../tasks/ba51t4akr.output". The watchdog reported that as a task
this session had launched and abandoned, and raised a `task_dead` finding demanding the target
reconcile work nobody owed. The task had in fact completed ten days earlier in the session that
really launched it; its output file was gone because macOS sweeps /tmp after about three days, so
the "no output, no process, no exit marker" evidence looked damning and was entirely consistent
with a task that was never this session's.

That is the shape to keep in mind: an INVENTED obligation cannot be discharged. A real stalled task
eventually exits or is killed and the finding clears; a task this session never started has nothing
that can ever happen to it, so the finding is immortal and the only way out is for the accused to
disprove it by hand. A false positive that cannot decay is worse than a missed detection.

Control 1 is the one that keeps the rest honest: a detector that returns nothing passes controls
2-5 trivially, so a genuine launch MUST still be found. Controls 2-5 each fail on the old code.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_lib as W

MINE = 'aaaaaaaa-1111-2222-3333-444444444444'
THEIRS = 'dd696660-af96-4884-b7ab-aa7e55201adf'

LAUNCH = ('Command running in background with ID: %s. Output is being written to: '
          '/private/tmp/claude-501/-Users-x/%s/tasks/%s.output.')


class FakeTurn:
    """The two fields background_launches reads. Built by hand so the fixture states exactly what
    the transcript would carry -- a parsed real turn would hide the distinction under test."""
    def __init__(self, calls):
        self.tool_uses, self.tool_results = [], {}
        for i, (background, result_text) in enumerate(calls):
            tid = 'tu%d' % i
            inp = {'command': 'whatever', 'description': 'd'}
            if background is not None:
                inp['run_in_background'] = background
            self.tool_uses.append(dict(id=tid, name='Bash', input=inp, ts='2026-09-16T10:06:31Z'))
            self.tool_results[tid] = dict(text=result_text, is_error=False)

    launches = W.Turn.background_launches


def main():
    fails = []

    def check(name, got, want):
        ok = got == want
        print('%-62s %s%s' % (name, 'PASS' if ok else 'FAIL', '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    def ids(turn, cli=None):
        return [b['task_id'] for b in turn.launches(cli)]

    # 1. POSITIVE CONTROL -- a real launch is still detected. Without this the rest is vacuous.
    real = FakeTurn([(True, LAUNCH % ('byzywrr39', MINE, 'byzywrr39'))])
    check('a real launch is still detected', ids(real, MINE), ['byzywrr39'])
    check('and is detected with no session scope given', ids(real), ['byzywrr39'])

    # 2. THE REAL DEFECT -- a foreground call printing another session's transcript.
    printed = ('rows: lines 20-262\n'
               '2026-09-06T17:19:24 RESULT: ' + (LAUNCH % ('ba51t4akr', THEIRS, 'ba51t4akr')) + '\n'
               'next line of the old transcript')
    quoted = FakeTurn([(None, printed)])
    check('a task id QUOTED in foreground output is not a launch', ids(quoted, MINE), [])
    check('and not a launch even unscoped', ids(quoted), [])

    # 3. run_in_background is not enough on its own: the confirmation must BE the result, not sit
    #    inside it. A backgrounded command can print a transcript too.
    buried = FakeTurn([(True, 'some output first\n' + (LAUNCH % ('ba51t4akr', THEIRS, 'ba51t4akr')))])
    check('a backgrounded call quoting a launch mid-output is not one', ids(buried, MINE), [])

    # 4. the path names the owner: a launch line for another session's tasks dir is not ours.
    foreign = FakeTurn([(True, LAUNCH % ('ba51t4akr', THEIRS, 'ba51t4akr'))])
    check('a launch under ANOTHER session\'s tasks dir is rejected', ids(foreign, MINE), [])

    # 5. ...but scoping is optional, and absent a session id it must not silently reject everything
    #    -- a check that drops all input looks identical to a clean board.
    check('with no session id, that same launch is still reported', ids(foreign), ['ba51t4akr'])

    # 6. leading whitespace in a result must not defeat the anchor
    spaced = FakeTurn([(True, '\n  ' + (LAUNCH % ('byzywrr39', MINE, 'byzywrr39')))])
    check('leading whitespace does not hide a real launch', ids(spaced, MINE), ['byzywrr39'])

    # 7. mixed turn: the real launch survives beside the quoted one
    mixed = FakeTurn([(None, printed), (True, LAUNCH % ('byzywrr39', MINE, 'byzywrr39'))])
    check('one real launch beside a quoted id yields only the real one', ids(mixed, MINE), ['byzywrr39'])

    print('\n%d check(s) FAILED' % len(fails) if fails else '\nall checks passed')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
