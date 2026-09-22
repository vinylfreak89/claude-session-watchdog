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
