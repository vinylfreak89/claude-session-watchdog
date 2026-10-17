#!/usr/bin/python3
"""A record the host never delivered to the model opens no turn and changes no turn state.

Measured 2026-10-16: after a subagent's report had already arrived as a message, the host wrote
its task-notification with `queueTranscriptOnly: true`. The target never saw it, never answered,
and every reader counted it as an open turn: the send gate said BUSY for nine hours on an idle
target. The defect is synthesised here, not borrowed from the live transcript.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_lib as W  # noqa: E402


def user(pid, text, ts, kind='peer', **extra):
    r = dict(type='user', promptId=pid, timestamp=ts, uuid='u-' + ts,
             origin=dict(kind=kind), message=dict(role='user', content=text))
    r.update(extra)
    return r


def assistant(text, ts, stop='end_turn'):
    return dict(type='assistant', timestamp=ts, uuid='a-' + ts,
                message=dict(role='assistant', stop_reason=stop, content=[dict(type='text', text=text)]))


def transcript(undelivered_flag):
    recs = [
        user('p1', 'hand-back report', '2026-10-16T15:44:26Z'),
        assistant('merge needs your go-ahead', '2026-10-16T15:44:36Z'),
        dict(type='system', subtype='stop_hook_summary', timestamp='2026-10-16T15:44:40.377Z'),
        dict(type='queue-operation', operation='dequeue', timestamp='2026-10-16T15:44:40.391Z'),
    ]
    note = user('p2', '<task-notification><task-id>a9</task-id><status>completed</status></task-notification>',
                '2026-10-16T15:44:40.393Z', kind='task-notification')
    if undelivered_flag:
        note.update(queueTranscriptOnly=True, queueSkipAttachments=True)
    recs.append(note)
    return recs


class TranscriptOnlyRecord(unittest.TestCase):
    def test_undelivered_record_opens_no_turn(self):
        turns = W.split_turns(transcript(True))
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[-1].end_state, 'end_turn')
        self.assertEqual(turns[-1].end_ts, '2026-10-16T15:44:36Z')
        self.assertEqual(len(turns[-1].records), 5, 'kept for audit in the turn it followed')

    def test_control_delivered_record_still_opens(self):
        # The same record WITHOUT the flag must still open a turn, or this test proves nothing.
        turns = W.split_turns(transcript(False))
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[-1].end_state, 'open')

    def test_next_delivered_input_still_opens(self):
        recs = transcript(True) + [user('p2', 'a real message with the same promptId', '2026-10-16T16:00:00Z')]
        turns = W.split_turns(recs)
        self.assertEqual(len(turns), 2, 'prev_pid must not absorb the undelivered record\'s promptId')
        self.assertEqual(turns[-1].end_state, 'open')

    def test_tail_reader_counts_the_same_turns(self):
        with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as f:
            for r in transcript(True): f.write(json.dumps(r) + '\n')
            path = f.name
        try:
            off, recs = W.read_tail_turns(path, need_turns=1, start_bytes=64)
            self.assertEqual([t.end_state for t in W.split_turns(recs)][-1], 'end_turn')
        finally:
            os.unlink(path)


class IdentityIgnoresUndelivered(unittest.TestCase):
    """A disposition recorded before the host appended a transcript-only record must still validate."""

    def test_fingerprint_unchanged_by_undelivered_record(self):
        import wd_turns as T
        before = W.split_turns(transcript(True)[:-1])[-1]
        after = W.split_turns(transcript(True))[-1]
        self.assertEqual(len(after.records), len(before.records) + 1)
        self.assertEqual(T.fingerprint(before), T.fingerprint(after))

    def test_control_delivered_content_changes_fingerprint(self):
        import wd_turns as T
        recs = transcript(True)[:-1]
        before = W.split_turns(recs)[-1]
        after = W.split_turns(recs[:2] + [assistant('a second reply', '2026-10-16T15:44:38Z')] + recs[2:])[-1]
        self.assertNotEqual(T.fingerprint(before), T.fingerprint(after))


class OpenMustBeEarned(unittest.TestCase):
    """An open turn with nothing behind it is UNKNOWN, not busy (W.turn_unconfirmed)."""
    T0 = W.ms_of_iso('2026-10-16T15:44:40.393Z') / 1000.0

    def open_turn(self, extra=()):
        recs = transcript(False)   # delivered: a real open turn whose input was never answered
        return W.split_turns(recs + list(extra))[-1]

    @staticmethod
    def call(ts, *ids):
        return dict(type='assistant', timestamp=ts, uuid='a-' + ts,
                    message=dict(role='assistant', stop_reason='tool_use',
                                 content=[dict(type='tool_use', id=i, name='Bash', input={}) for i in ids]))

    @staticmethod
    def result(ts, tid):
        return dict(type='user', promptId='p2', timestamp=ts, uuid='r-' + ts,
                    message=dict(role='user', content=[dict(type='tool_result', tool_use_id=tid, content='ok')]))

    def test_silent_open_turn_is_unconfirmed(self):
        silent = W.turn_unconfirmed('s', self.open_turn(), now=self.T0 + 25 * 60)
        self.assertIsNotNone(silent)
        self.assertAlmostEqual(silent, 25.0, places=1)

    def test_recent_record_still_counts_as_busy(self):
        self.assertIsNone(W.turn_unconfirmed('s', self.open_turn(), now=self.T0 + 5 * 60))

    def test_outstanding_tool_call_supports_the_claim(self):
        turn = self.open_turn([self.call('2026-10-16T15:44:50Z', 't1')])
        self.assertIsNone(W.turn_unconfirmed('s', turn, now=self.T0 + 25 * 60))

    def test_parallel_call_still_running_after_first_result(self):
        # Review finding: the first result to return must not hide the call still running.
        turn = self.open_turn([self.call('2026-10-16T15:44:50Z', 'agent', 'bash'),
                               self.result('2026-10-16T15:44:55Z', 'bash')])
        self.assertIsNone(W.turn_unconfirmed('s', turn, now=self.T0 + 25 * 60))

    def test_all_calls_answered_and_silent_is_unconfirmed(self):
        turn = self.open_turn([self.call('2026-10-16T15:44:50Z', 'bash'),
                               self.result('2026-10-16T15:44:55Z', 'bash')])
        self.assertIsNotNone(W.turn_unconfirmed('s', turn, now=W.ms_of_iso('2026-10-16T15:44:55Z') / 1000.0 + 25 * 60))

    def test_undelivered_record_does_not_refresh_the_clock(self):
        late = user('p2', 'transcript-only', '2026-10-16T16:00:00Z', kind='task-notification',
                    queueTranscriptOnly=True)
        turn = self.open_turn([late])
        self.assertEqual(turn.records[-1], late)
        self.assertIsNotNone(W.turn_unconfirmed('s', turn, now=self.T0 + 25 * 60))

    def test_ended_turn_is_not_unconfirmed(self):
        turn = W.split_turns(transcript(True))[-1]
        self.assertIsNone(W.turn_unconfirmed('s', turn, now=self.T0 + 600 * 60))


if __name__ == '__main__':
    unittest.main(verbosity=2)
