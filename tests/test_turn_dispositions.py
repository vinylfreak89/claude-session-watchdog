#!/usr/bin/python3
"""Owner-authorized dispositions remain auditable and cannot retire questions."""
import unittest
from send_contract_support import ContractCase, C, K, SELF, ts

class DispositionContract(ContractCase):
    def test_later_completed_turn_supersedes_narration_only(self):
        self.turn('later', 2, 3)
        out = self.poll()
        self.assertNotIn(ts(1) + '  [', out)
        self.assertIn(ts(3) + '  [not answered or held]', out)

    def test_question_survives_later_completed_turn(self):
        self.records(dict(type='assistant', timestamp=ts(2), message=dict(
            content=[dict(type='text', text='Which option should I use?')], stop_reason='end_turn')))
        self.turn('later', 3, 4)
        out = self.poll()
        self.assertIn(ts(2) + '  [not answered or held]', out)
        self.assertIn(ts(4) + '  [not answered or held]', out)

    def test_open_later_turn_does_not_supersede_head(self):
        self.records(dict(type='user', uuid='open', promptId='open', timestamp=ts(2),
                          origin=dict(kind='human'), message=dict(content='Synthetic new work')))
        out = self.poll()
        self.assertIn(ts(1) + '  [not answered or held]', out)

    def test_supersession_preserves_relay_duty(self):
        self.turn('later', 2, 3)
        state = self.state(); state['last_relay_ts'] = ''
        K.save_state(str(self.state_dir), state)
        out = self.poll()
        self.assertIn(ts(1) + '  [not relayed]', out)
        self.assertIn(ts(3) + '  [not relayed + not answered or held]', out)

    def test_current_message_question_cannot_be_held_or_closed(self):
        self.tool('SendMessage', dict(to=SELF, recipient=SELF, type='message', message='Which option should I implement?'))
        self.records(dict(type='assistant', timestamp=ts(22), message=dict(
            content=[dict(type='text', text='The request was sent.')], stop_reason='end_turn')))
        for mode in ('hold', 'closed'):
            rc, out = self.cli(C, mode, ts(22), 'Waiting on owner')
            self.assertNotEqual(rc, 0, out)
            self.assertIn('question', out)

    def test_closed_nonquestion_is_auditable(self):
        rc, out = self.cli(C, 'closed', ts(1), 'Only confirms the preceding result')
        self.assertEqual(rc, 0, out)
        record = self.state()['closed_turns'][ts(1)]
        for key in ('reason', 'actor', 'ruling', 'turn_hash', 'at'):
            self.assertTrue(record.get(key), key)
        self.assertNotIn(ts(1) + '  [', self.poll())

    def test_hold_nonquestion_requires_relay_and_attribution(self):
        rc, out = self.cli(C, 'hold', ts(1), 'Blocked on owner decision')
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.state()['held_turns'][ts(1)].get('actor'))
        self.assertNotIn(ts(1) + '  [', self.poll())
        state = self.state(); state.pop('last_relay_ts', None)
        K.save_state(str(self.state_dir), state)
        self.assertIn(ts(1) + '  [not relayed]', self.poll())

    def test_question_cannot_be_held_or_closed(self):
        self.records(dict(type='assistant', timestamp=ts(2), message=dict(role='assistant',
                          content=[dict(type='text', text='Which option should I implement?')], stop_reason='end_turn')))
        for mode, reason in [('hold', 'Blocked on owner decision'), ('closed', 'Only needs acknowledgement')]:
            with self.subTest(mode=mode):
                rc, out = self.cli(C, mode, ts(2), reason)
                self.assertNotEqual(rc, 0, out)
                self.assertIn('question', out.lower())
                self.assertIn(ts(2), self.poll())

    def test_nonexistent_turn_cannot_receive_disposition(self):
        rc, out = self.cli(C, 'closed', ts(999), 'Synthetic reason')
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn(ts(999), self.state().get('closed_turns', {}))

    def test_blank_reason_cannot_receive_disposition(self):
        for mode in ('hold', 'closed'):
            rc, out = self.cli(C, mode, ts(1), ' ')
            self.assertNotEqual(rc, 0, out)

    def test_bootstrap_boundary_excludes_pretracking_history(self):
        state = self.state(); state['bootstrap_ts'] = ts(5)
        K.save_state(str(self.state_dir), state)
        self.turn('tracked', 6, 7)
        out = self.poll()
        self.assertNotIn(ts(1) + '  [', out)
        self.assertIn(ts(7), out)
        self.assertEqual(self.state()['turn_tracking']['since'], ts(5))

    def test_legacy_frontier_is_frozen_once(self):
        state = self.state(); state.update(bootstrap_ts=ts(0), last_send_ts=ts(3))
        K.save_state(str(self.state_dir), state)
        self.turn('old', 2, 3)
        self.turn('new', 6, 7)
        out = self.poll()
        self.assertNotIn(ts(3) + '  [', out)
        self.assertIn(ts(7), out)
        self.clock = 5
        self.queue()
        self.deliver('Create artifact')
        self.sent('Q1')
        self.turn('later', 30, 31)
        out = self.poll()
        self.assertIn(ts(31), out)
        self.assertEqual(self.state()['turn_tracking']['legacy_answered_through'], ts(3))

if __name__ == '__main__': unittest.main(verbosity=2)
