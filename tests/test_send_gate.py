#!/usr/bin/python3
"""Exercise the send gate's command handler with real queued requirements and transcripts."""
import unittest
from send_contract_support import ContractCase, C, K, ts

class SendGate(ContractCase):
    def test_unanswered_relayed_turn_allows_one_ready_item(self):
        q = self.queue()
        rc, out = self.cli(C, 'next')
        self.assertEqual(rc, 0, out)
        self.assertIn('SEND EXACTLY THIS ONE ITEM', out)
        self.assertIn('sent1 ' + q, out)

    def test_open_turn_blocks_send(self):
        self.queue()
        self.records(dict(type='user', promptId='open', timestamp=ts(6), message=dict(role='user', content='More work')))
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('TARGET BUSY', out)

    def test_unrelayed_turn_blocks_send(self):
        self.queue()
        state = self.state(); state.pop('last_relay_ts', None)
        K.save_state(str(self.state_dir), state)
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('UNRELAYED', out)

    def test_held_item_is_skipped_for_ready_item(self):
        held = self.queue('Held request')
        rc, out = self.cli(K, '--queue-hold', held, '--hold-until', 'Owner decision')
        self.assertEqual(rc, 0, out)
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('HELD', out)
        ready = self.queue('Ready request', 'file second.txt')
        rc, out = self.cli(C, 'next')
        self.assertEqual(rc, 0, out)
        self.assertIn('sent1 ' + ready, out)
        self.assertNotIn('Held request', out)

    def test_empty_queue_sends_nothing(self):
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn('SEND EXACTLY', out)

    def test_owner_urgent_pacing_exception_remains(self):
        rc, out = self.cli(K, '--queue-add', 'Urgent request', '--queue-urgent', '--acted-when', 'file urgent.txt')
        self.assertEqual(rc, 0, out)
        self.records(dict(type='user', promptId='open', timestamp=ts(6), message=dict(role='user', content='More work')))
        state = self.state(); state.pop('last_relay_ts', None)
        K.save_state(str(self.state_dir), state)
        rc, out = self.cli(C, 'next')
        self.assertEqual(rc, 0, out)
        self.assertIn('URGENT', out)
        self.assertIn('SEND EXACTLY', out)
        self.assertFalse(self.state()['owner_queue'][0].get('sent'))

if __name__ == '__main__': unittest.main(verbosity=2)
