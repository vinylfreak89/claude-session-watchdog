"""A turn that changes after disposal supersedes, it never sticks.

Owner's ruling, 2026-09-20: "Isn't it just a new owed turn which can be marked as closed?
... Nothing should ever be permanently stuck. If the previous decision was overridden, then
a stale entry should be superseded and the new decision should close it via roll up."

Before this, disposing of a turn bound the record to the turn's content hash, so a turn that
kept growing after disposal could never be marked again -- it reported "legacy disposition
lacks auditable provenance" and stayed outstanding forever.

The penalty for disposing early is VISIBILITY, not permanence: the stale entry is kept in
full under `superseded`, with its own hash and timestamp.

Fixtures are synthesised; nothing outside this file can silence it.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wd_turns as TD

RULING = TD.RULINGS['closed']


class FakeTurn:
    def __init__(self, texts):
        self.assistant_texts = [(str(i), t) for i, t in enumerate(texts)]
        self.tool_uses = []
        self.records = [{'t': t} for t in texts]
        self.end_state = 'end_turn'


class FakeSess(dict):
    pass


SESS = {'sessionId': 'target-1'}
STAMP = '2026-09-20T11:39:49.097Z'
ACTOR = 'watchdog-1'


def close(state, turn, reason):
    return TD.record_disposition(SESS, ACTOR, state, 'closed', STAMP, reason, None)


def main():
    partial = FakeTurn(['The pass is running. I will report when it lands.'])
    grown = FakeTurn(['The pass is running. I will report when it lands.',
                      'It finished: the experiment is refuted and I am stopping.'])

    state = {}
    TD.find_turn = lambda sess, stamp: state['__turn__']

    # 1. Dispose of the turn while it is still being written.
    state['__turn__'] = partial
    assert close(state, partial, 'progress narration, nothing to reply to') is True
    first = dict(state['closed_turns'][STAMP])
    assert first['turn_hash'] == TD.fingerprint(partial)
    assert 'superseded' not in first

    # 2. Re-disposing the UNCHANGED turn with the same words is a no-op, and with
    #    different words is still a refused rewrite of history.
    assert close(state, partial, 'progress narration, nothing to reply to') is False
    try:
        close(state, partial, 'a different story about the same text')
        raise AssertionError('rewriting an unchanged turn was allowed')
    except Exception as exc:
        assert 'cannot be rewritten' in str(exc), exc

    # 3. The turn grows. The stale decision must NOT block the new one.
    state['__turn__'] = grown
    assert close(state, grown, 'reports the refutation and stops; asks nothing') is True
    now = state['closed_turns'][STAMP]
    assert now['turn_hash'] == TD.fingerprint(grown), 'new entry does not describe the new turn'
    assert now['reason'] == 'reports the refutation and stops; asks nothing'

    # 4. The early disposal is KEPT, in full, with its own hash -- the visibility that
    #    replaces permanence. It is not quietly dropped.
    hist = now['superseded']
    assert len(hist) == 1, hist
    assert hist[0]['turn_hash'] == TD.fingerprint(partial)
    assert hist[0]['reason'] == 'progress narration, nothing to reply to'
    assert hist[0]['actor'] == ACTOR and hist[0]['at']

    # 5. The new entry actually validates, so owed stops listing the turn. This is the
    #    property that was broken: before the fix, nothing could ever make this true again.
    assert TD.valid_disposition(SESS, grown, now, 'closed'), 'turn is still stuck after re-disposal'

    # 6. A third change rolls up rather than flattening: history keeps BOTH earlier entries.
    grown2 = FakeTurn(grown.records and
                      [t for _, t in grown.assistant_texts] + ['One more correction.'])
    state['__turn__'] = grown2
    assert close(state, grown2, 'carries a correction; still nothing to reply to') is True
    hist2 = state['closed_turns'][STAMP]['superseded']
    assert len(hist2) == 2, hist2
    assert [h['turn_hash'] for h in hist2] == [TD.fingerprint(partial), TD.fingerprint(grown)]
    print('ok')


if __name__ == '__main__':
    main()
