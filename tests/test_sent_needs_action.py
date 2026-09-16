#!/usr/bin/python3
"""Command-handler controls for receipts, immutable requirements and observed action."""
import json
import sys
import unittest
from unittest.mock import patch
from send_contract_support import ContractCase, C, K, W, SELF, ts

class SendContract(ContractCase):
    def test_unknown_finding_cannot_record_send(self):
        before = self.state()
        rc, out = self.cli(K, '--sent', 'F999', '--message-id', 'absent')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), before)

    def test_finding_without_delivery_cannot_record_send(self):
        s = self.state()
        s['proposed']['F1'] = dict(key='control', evidence_hash='hash', ts=ts(5), message='Synthetic finding')
        K.save_state(str(self.state_dir), s)
        rc, out = self.cli(K, '--sent', 'F1', '--message-id', 'absent')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('F1', self.state()['proposed'])
        self.assertNotIn('last_send_ts', self.state())

    def test_verified_finding_records_delivery(self):
        s = self.state()
        s['proposed']['F1'] = dict(key='control', evidence_hash='hash', ts=ts(5), message='Synthetic finding')
        K.save_state(str(self.state_dir), s)
        self.deliver('Synthetic finding')
        self.clock = 50
        rc, out = self.cli(K, '--sent', 'F1', '--message-id', 'delivery-1')
        self.assertEqual(rc, 0, out)
        self.assertNotIn('F1', self.state()['proposed'])
        self.assertEqual(self.state()['raised']['control']['message_id'], 'delivery-1')
        self.assertEqual(self.state().get('last_send_ts'), ts(10))
        before = self.state()
        rc, out = self.cli(K, '--sent', 'F1', '--message-id', 'delivery-1')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.state(), before)

    def test_file_closes_only_after_target_write(self):
        q = self.queue()
        self.deliver('Create artifact')
        self.sent(q)
        self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1)
        self.write_target('artifact.txt', 'created')
        self.poll()
        self.assertEqual(self.state()['owner_queue'], [])
        self.assertEqual(self.state()['owner_queue_sent'][0]['id'], q)
        self.assertTrue(self.state()['owner_queue_sent'][0]['acted_evidence'])

    def test_same_receipt_does_not_answer_later_turn(self):
        q = self.queue()
        self.deliver('Create artifact')
        self.sent(q)
        self.turn('later', 30, 31)
        self.clock = 50
        before = self.state()
        rc, out = self.cli(C, 'sent1', q, 'delivery-1')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.state(), before, 'repeated receipt changed state')
        self.assertIn(ts(31), self.poll())

    def test_one_receipt_cannot_mark_two_items(self):
        q1 = self.queue()
        q2 = self.queue('Other request', 'file other.txt')
        self.deliver('Create artifact')
        self.sent(q1)
        rc, out = self.cli(C, 'sent1', q2, 'delivery-1')
        self.assertNotEqual(rc, 0, out)
        self.assertFalse(self.state()['owner_queue'][1].get('sent'))

    def test_delayed_answer_does_not_answer_later_turn(self):
        self.queue()
        self.deliver('Create artifact')
        self.turn('later', 30, 31)
        self.clock = 50
        rc, out = self.cli(C, 'answered', 'delivery-1')
        self.assertEqual(rc, 0, out)
        self.assertIn(ts(31), self.poll())
        self.assertEqual(self.state().get('last_send_ts'), ts(10))

    def test_sent_requirement_is_immutable(self):
        q = self.queue()
        self.deliver('Create artifact')
        self.sent(q)
        (self.root / 'existing').write_text('already exists')
        before = self.state()
        rc, out = self.cli(K, '--queue-acted-when', q, '--acted-when', 'file existing')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), before)
        self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1)

    def test_delivered_unmarked_requirement_is_immutable(self):
        q = self.queue()
        self.deliver('Create artifact')
        rc, out = self.cli(K, '--queue-acted-when', q, '--acted-when', 'file existing')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state()['owner_queue'][0]['acted_when'], 'file artifact.txt')

    def test_delivered_unmarked_item_cannot_be_dropped(self):
        q = self.queue()
        self.deliver('Create artifact')
        rc, out = self.cli(K, '--queue-drop', q, '--reason', 'withdraw')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(len(self.state()['owner_queue']), 1)

    def test_preexisting_file_does_not_close(self):
        self.write_target('artifact.txt', 'old', at=2)
        q = self.queue()
        self.deliver('Create artifact')
        self.sent(q)
        self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1)

    def test_unattributed_new_file_does_not_close(self):
        q = self.queue()
        self.deliver('Create artifact')
        self.sent(q)
        (self.root / 'artifact.txt').write_text('not written by the target')
        self.poll()
        self.assertEqual(len(self.state()['owner_queue']), 1)

    def test_unknown_acceptance_refused_on_add(self):
        for spec in ('running', 'unknown x', 'grep artifact.txt', 'csv artifact.txt invalid'):
            with self.subTest(spec=spec):
                before = self.state()
                rc, out = self.cli(K, '--queue-add', 'Synthetic request', '--acted-when', spec)
                self.assertNotEqual(rc, 0, out)
                self.assertEqual(self.state(), before)

    def test_human_quote_is_not_delivery(self):
        self.queue()
        self.deliver('Create artifact', origin=False)
        rc, out = self.cli(C, 'answered', 'delivery-1')
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn('last_send_ts', self.state())

    def test_text_block_delivery_is_usable(self):
        q = self.queue()
        self.deliver('Create artifact', blocks=True)
        self.sent(q)
        self.write_target('artifact.txt', 'created')
        self.poll()
        self.assertEqual(self.state()['owner_queue'], [])

    def test_next_blocks_sent_unacted_item(self):
        q = self.queue()
        self.queue('Other request', 'file other.txt')
        self.deliver('Create artifact')
        self.sent(q)
        self.turn('later', 30, 31)
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn('SEND EXACTLY', out)

if __name__ == '__main__':
    unittest.main(verbosity=2)
