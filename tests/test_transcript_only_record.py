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


if __name__ == '__main__':
    unittest.main(verbosity=2)
