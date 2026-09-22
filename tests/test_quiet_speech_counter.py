"""Quiet is about the watchdog's MOUTH, and the counter that proves it is measured, not attested.

The first version of owner-active quiet bound only `relayed`. The store went quiet and the
watchdog kept narrating the target's conversation to the owner every wake -- the exact behaviour
the rule exists to stop. Owner, 2026-09-22: "lmao you are saying you will be quiet because the
relay is held and you still keep yapping".

A counter the watchdog increments itself is a counter it can decline to increment, so this one
reads the harness-written transcript instead. Every case BUILDS the speech it counts; none borrows
a condition from the live session, so no later fix can quietly silence one.
"""
import json, os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import wd_lib as W
import wd_receipts as D


def _speech(path, rows):
    """rows: [(type, ts, has_text, isMeta)] -> a watchdog-side transcript.

    A `user` row is the OWNER only when it carries origin.kind == 'human'; the bare
    `user` rows here stand for the crowd sharing that channel (tool results,
    task-notifications), which is what the first version miscounted as him.
    """
    with open(path, 'w') as fh:
        for i, row in enumerate(rows):
            kind, ts, has_text, meta = row[0], row[1], row[2], row[3]
            origin = row[4] if len(row) > 4 else None
            r = dict(type=kind, uuid='u%d' % i, timestamp=ts,
                     message=dict(role='assistant' if kind == 'assistant' else 'user',
                                  content=([dict(type='text', text='words')] if has_text
                                           else [dict(type='tool_use', id='t', name='Bash', input={})])))
            if origin:
                r['origin'] = dict(kind=origin)
            if meta:
                r['isMeta'] = True
            fh.write(json.dumps(r) + '\n')
    return path


class QuietSpeechCounter(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, 'self.jsonl')
        self.sess = dict(cwd='/nowhere', cli='self')

    def _count(self, since):
        # transcript_path globs by cli name; point it straight at the fixture instead.
        real = W.transcript_path
        W.transcript_path = lambda s: self.p
        try:
            return D.quiet_speech(self.sess, since)
        finally:
            W.transcript_path = real

    def test_fixture_parses(self):
        """If this fails every other case proves nothing -- they would all count zero."""
        _speech(self.p, [('assistant', '2026-01-01T00:01:00.000Z', True, False)])
        self.assertEqual(len(D.read_records(self.p)), 1)

    def test_counts_narration_the_owner_never_prompted(self):
        """THE DEFECT, SYNTHESISED: three watchdog messages, nothing of his to answer."""
        _speech(self.p, [('assistant', '2026-01-01T00:01:00.000Z', True, False),
                         ('assistant', '2026-01-01T00:02:00.000Z', True, False),
                         ('assistant', '2026-01-01T00:03:00.000Z', True, False)])
        self.assertEqual(self._count('2026-01-01T00:00:00.000Z'), (3, 0))

    def test_answering_him_is_not_narration(self):
        """CONTROL: the counter must not fire when he is the one talking."""
        _speech(self.p, [('user', '2026-01-01T00:01:00.000Z', True, False, 'human'),
                         ('assistant', '2026-01-01T00:01:30.000Z', True, False),
                         ('user', '2026-01-01T00:02:00.000Z', True, False, 'human'),
                         ('assistant', '2026-01-01T00:02:30.000Z', True, False)])
        mine, theirs = self._count('2026-01-01T00:00:00.000Z')
        self.assertEqual((mine, theirs), (2, 2))
        self.assertLessEqual(mine, theirs)

    def test_the_crowd_on_the_user_channel_is_not_the_owner(self):
        """THE 193, SYNTHESISED. Counting record TYPES read 193 owner messages in a window
        where he sent one, and since the narration warning is suppressed when his count is
        the larger, the miscount hid the 22 messages the counter existed to surface. Here one
        real message of his sits among the traffic that shares its channel."""
        _speech(self.p, [('user', '2026-01-01T00:01:00.000Z', True, False, 'task-notification'),
                         ('user', '2026-01-01T00:01:10.000Z', True, False, None),
                         ('queue-operation', '2026-01-01T00:01:20.000Z', True, False, None),
                         ('attachment', '2026-01-01T00:01:30.000Z', True, False, None),
                         ('user', '2026-01-01T00:01:40.000Z', True, False, 'human'),
                         ('assistant', '2026-01-01T00:02:00.000Z', True, False),
                         ('assistant', '2026-01-01T00:03:00.000Z', True, False)])
        mine, theirs = self._count('2026-01-01T00:00:00.000Z')
        self.assertEqual(theirs, 1, 'only origin.kind human is the owner')
        self.assertEqual(mine, 2)
        self.assertGreater(mine, theirs, 'the narration warning must not be suppressed')

    def test_speech_before_the_window_is_not_counted(self):
        _speech(self.p, [('assistant', '2026-01-01T00:00:30.000Z', True, False),
                         ('assistant', '2026-01-01T00:02:00.000Z', True, False)])
        self.assertEqual(self._count('2026-01-01T00:01:00.000Z'), (1, 0))

    def test_tool_only_turns_are_not_speech(self):
        """Running checks during quiet is allowed; only user-facing text is speech."""
        _speech(self.p, [('assistant', '2026-01-01T00:01:00.000Z', False, False),
                         ('assistant', '2026-01-01T00:02:00.000Z', False, False)])
        self.assertEqual(self._count('2026-01-01T00:00:00.000Z'), (0, 0))

    def test_meta_records_are_not_the_owner(self):
        """A harness-injected record must not read as him having spoken."""
        _speech(self.p, [('user', '2026-01-01T00:01:00.000Z', True, True, 'human'),
                         ('assistant', '2026-01-01T00:02:00.000Z', True, False)])
        self.assertEqual(self._count('2026-01-01T00:00:00.000Z'), (1, 0))

    def test_no_window_counts_nothing(self):
        _speech(self.p, [('assistant', '2026-01-01T00:01:00.000Z', True, False)])
        self.assertEqual(self._count(None), (0, 0))


def _tx(path, turns):
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


class QuietWindowStart(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, 't.jsonl')
        self.sd = tempfile.mkdtemp()
        # Pin the off switch ON -- see the note in test_owner_active_quiet: the
        # agreement case below calls owner_active_quiet, which reads config.json.
        cfg = os.path.join(self.d, 'config.json')
        with open(cfg, 'w') as fh:
            json.dump(dict(quiet_when_owner_active=True), fh)
        self._prev_cfg = os.environ.get('WD_CONFIG')
        os.environ['WD_CONFIG'] = cfg

    def tearDown(self):
        if self._prev_cfg is None:
            os.environ.pop('WD_CONFIG', None)
        else:
            os.environ['WD_CONFIG'] = self._prev_cfg

    def test_window_opens_at_the_earliest_uncovered_owner_turn(self):
        _tx(self.p, [('human', '2026-01-01T00:05:00.000Z'),
                     ('task-notification', '2026-01-01T00:06:00.000Z'),
                     ('human', '2026-01-01T00:07:00.000Z')])
        D.mark_audit(self.sd, '2026-01-01T00:04:00Z')
        self.assertEqual(D.quiet_window_start(None, {}, path=self.p, state_dir=self.sd),
                         '2026-01-01T00:05:00.000Z')

    def test_an_audit_past_every_owner_turn_closes_the_window(self):
        _tx(self.p, [('human', '2026-01-01T00:05:00.000Z')])
        D.mark_audit(self.sd, '2026-01-01T00:09:00Z')
        self.assertIsNone(D.quiet_window_start(None, {}, path=self.p, state_dir=self.sd))

    def test_the_window_agrees_with_the_refusal(self):
        """A banner that disagreed with `owner_active_quiet` would be its own defect."""
        _tx(self.p, [('task-notification', '2026-01-01T00:04:00.000Z'),
                     ('human', '2026-01-01T00:05:00.000Z')])
        D.mark_audit(self.sd, '2026-01-01T00:03:00Z')
        self.assertIsNotNone(D.owner_active_quiet(None, {}, path=self.p, state_dir=self.sd))
        self.assertIsNotNone(D.quiet_window_start(None, {}, path=self.p, state_dir=self.sd))


if __name__ == '__main__':
    unittest.main()


class AuditBudget(unittest.TestCase):
    """Re-arming the hook must not postpone the release.

    THE DEFECT, SYNTHESISED: the audit used to sleep a fixed window from its own process
    start, so a Monitor re-armed mid-window restarted the 20-minute clock. Measured
    2026-09-22, one quiet period ran ~28 minutes against the 20 the owner agreed to.
    Nothing here reads live state, so no later change can quietly silence it.
    """
    NOW = 1_000_000.0

    def _stamp(self, seconds_ago):
        import datetime as dt
        t = dt.datetime.fromtimestamp(self.NOW - seconds_ago, dt.timezone.utc)
        return t.strftime('%Y-%m-%dT%H:%M:%SZ')

    def test_a_rearm_midwindow_does_not_restart_the_clock(self):
        """12 minutes already elapsed leaves 8, not a fresh 20."""
        self.assertAlmostEqual(
            D.audit_budget(1200, self._stamp(720), self.NOW), 480.0, delta=1.0)

    def test_a_stamp_older_than_the_window_fires_immediately(self):
        self.assertEqual(D.audit_budget(1200, self._stamp(5000), self.NOW), 0.0)

    def test_a_fresh_stamp_waits_the_whole_window(self):
        self.assertAlmostEqual(
            D.audit_budget(1200, self._stamp(0), self.NOW), 1200.0, delta=1.0)

    def test_no_stamp_fails_OPEN_not_closed(self):
        """A backstop that declines to fire is a quiet that never releases."""
        self.assertEqual(D.audit_budget(1200, None, self.NOW), 1200)
        self.assertEqual(D.audit_budget(1200, '', self.NOW), 1200)

    def test_an_unparseable_stamp_fails_open(self):
        self.assertEqual(D.audit_budget(1200, 'not-a-timestamp', self.NOW), 1200)

    def test_a_future_stamp_cannot_extend_the_window(self):
        """A clock that went backwards must not buy extra silence."""
        self.assertEqual(D.audit_budget(1200, self._stamp(-9999), self.NOW), 1200.0)

    def test_the_wait_loop_uses_the_budget_not_max_wait(self):
        """Guards the wiring: the fix is worthless if the loop still sleeps max_wait."""
        src = open(os.path.join(ROOT, 'wd_wait.py')).read()
        i = src.index('if a.audit:')
        block = src[i:i + 1400]
        self.assertIn('audit_budget(', block)
        self.assertIn('while time.time() - t0 < budget:', block)
        self.assertNotIn('while time.time() - t0 < a.max_wait:', block)
