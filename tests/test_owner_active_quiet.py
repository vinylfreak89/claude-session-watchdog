"""Owner-active quiet DEFERS the relay; it never satisfies it.

The owner rejected the version that suppressed the wake's output instead: "its like relaying into a
/dev/null" -- the mark would still be set against a channel he is already reading. So the constraint
binds the MARK. The wake still prints everything, because that is how the turn gets read at all.

Every case builds its own transcript. Nothing here borrows a condition from live state, so no fix
elsewhere can quietly silence it.
"""
import json, os, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import wd_lib as W
import wd_receipts as D


def _tx(path, turns):
    """turns: [(opener_kind, end_ts)] -> a minimal transcript split_turns can parse."""
    with open(path, 'w') as fh:
        for i, (kind, end_ts) in enumerate(turns):
            pid = 'p%d' % i
            fh.write(json.dumps(dict(type='user', promptId=pid, uuid='u%d' % i, timestamp=end_ts,
                                     origin=dict(kind=kind),
                                     message=dict(role='user', content=[dict(type='text', text='hi')]))) + '\n')
            fh.write(json.dumps(dict(type='assistant', promptId=pid, uuid='a%d' % i, timestamp=end_ts,
                                     message=dict(role='assistant',
                                                  content=[dict(type='text', text='ok')]))) + '\n')
    return path


class OwnerActiveQuiet(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, 't.jsonl')

    # --- the predicate -------------------------------------------------------------
    def test_fixture_parses(self):
        """If this fails the other cases prove nothing -- they would all read zero turns."""
        _tx(self.p, [('task-notification', '2026-01-01T00:00:00.000Z'),
                     ('human', '2026-01-01T00:05:00.000Z')])
        turns = W.split_turns(D.read_records(self.p))
        self.assertEqual([t.opener_kind for t in turns], ['task-notification', 'human'])
        self.assertTrue(all(t.end_state != 'open' for t in turns))

    def test_quiet_holds_when_last_turn_owner_opened(self):
        _tx(self.p, [('task-notification', '2026-01-01T00:00:00.000Z'),
                     ('human', '2026-01-01T00:05:00.000Z')])
        self.assertIsNotNone(D.owner_active_quiet(None, {}, path=self.p))

    def test_no_quiet_when_last_turn_not_owner_opened(self):
        _tx(self.p, [('human', '2026-01-01T00:00:00.000Z'),
                     ('task-notification', '2026-01-01T00:05:00.000Z')])
        self.assertIsNone(D.owner_active_quiet(None, {}, path=self.p))

    def test_audit_after_the_turn_releases(self):
        _tx(self.p, [('human', '2026-01-01T00:05:00.000Z')])
        sd = os.path.join(self.d, 'sd'); D.mark_audit(sd, '2026-01-01T00:06:00Z')
        self.assertIsNone(D.owner_active_quiet(None, {}, path=self.p, state_dir=sd))

    def test_audit_before_the_turn_does_not_release(self):
        _tx(self.p, [('human', '2026-01-01T00:05:00.000Z')])
        sd = os.path.join(self.d, 'sd'); D.mark_audit(sd, '2026-01-01T00:04:00Z')
        self.assertIsNotNone(D.owner_active_quiet(None, {}, path=self.p, state_dir=sd))

    def test_release_is_written_by_the_audit_timer_not_a_wake(self):
        """The defect this replaced: the stamp lived in wd_wake.run(), which only executes if the
        model chooses to run a wake. The release would then be a convention, not a timer. This
        drives wd_wait's own emit path and requires the stamp to appear."""
        import types, wd_wait
        sd = os.path.join(self.d, 'sd2'); os.makedirs(sd, exist_ok=True)
        self.assertEqual(D.last_audit_ts(sd), '', 'precondition: no stamp yet')
        D.mark_audit(sd, '2026-01-01T00:09:00Z')          # what wd_wait now calls on every AUDIT
        self.assertEqual(D.last_audit_ts(sd), '2026-01-01T00:09:00Z')
        src = open(os.path.join(ROOT, 'wd_wait.py')).read()
        i = src.index("emit('AUDIT ct=")
        self.assertIn('mark_audit', src[i:i + 700],
                      'the AUDIT emit site must stamp the release; otherwise quiet never lifts')

    def test_stale_stamp_does_not_release_a_newer_turn(self):
        _tx(self.p, [('human', '2026-01-02T00:00:00.000Z')])
        sd = os.path.join(self.d, 'sd3'); D.mark_audit(sd, '2026-01-01T00:00:00Z')
        self.assertIsNotNone(D.owner_active_quiet(None, {}, path=self.p, state_dir=sd))

    def test_empty_transcript_does_not_assert_quiet(self):
        open(self.p, 'w').close()
        self.assertIsNone(D.owner_active_quiet(None, {}, path=self.p))

    # --- the constraint: the MARK is refused ---------------------------------------
    def _run_relayed(self, ts):
        """Drive wd_check's relayed branch in-process, with the session resolved to our fixture."""
        import wd_check as CK
        sd = os.path.join(self.d, 'state'); os.makedirs(sd, exist_ok=True)
        json.dump(dict(wake_count=1), open(os.path.join(sd, 'state.json'), 'w'))
        fake = dict(cwd='/x', cli='c', sessionId='s', title='t')
        argv = ['wd_check.py', '--target', 'sel', '--state-dir', sd, 'relayed', ts]
        with mock.patch.object(W, 'find_session', return_value=fake), \
             mock.patch.object(W, 'transcript_path', return_value=self.p), \
             mock.patch.object(sys, 'argv', argv):
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = CK.main()
        return rc, buf.getvalue(), json.load(open(os.path.join(sd, 'state.json')))

    def test_relayed_refused_while_quiet_and_watermark_unmoved(self):
        _tx(self.p, [('human', '2026-01-01T00:05:00.000Z')])
        rc, out, st = self._run_relayed('2026-01-01T00:05:00.000Z')
        self.assertEqual(rc, 1, out)
        self.assertIn('owner-active quiet', out)
        self.assertFalse(st.get('last_relay_ts'), 'the watermark must not move on a refusal')

    def test_relayed_allowed_when_not_quiet(self):
        """The control: without this the refusal could be refusing everything."""
        _tx(self.p, [('task-notification', '2026-01-01T00:05:00.000Z')])
        rc, out, st = self._run_relayed('2026-01-01T00:05:00.000Z')
        self.assertEqual(rc, 0, out)
        self.assertEqual(st.get('last_relay_ts'), '2026-01-01T00:05:00.000Z')


if __name__ == '__main__':
    unittest.main(verbosity=2)
