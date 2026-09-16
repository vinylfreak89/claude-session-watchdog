#!/usr/bin/python3
"""Real-handler receipt binding and provenance controls."""
import unittest
from send_contract_support import ContractCase, C, K, SELF, ts

class ReceiptContract(ContractCase):
    def test_one_receipt_answers_only_one_prior_turn(self):
        self.turn('second', 2, 3)
        self.queue()
        self.deliver('Create artifact')
        rc, out = self.cli(C, 'answered', 'delivery-1')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.state()['send_receipts']['delivery-1']['turn_ts'], ts(3))
        self.assertIn(ts(1), self.poll())
        self.assertNotIn(ts(3) + '  [', self.poll())

    def test_unregistered_body_cannot_bypass_item_action(self):
        self.deliver('Synthetic acknowledgement')
        rc, out = self.cli(C, 'answered', 'delivery-1')
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn('last_send_ts', self.state())

    def test_rebranding_receipt_is_refused(self):
        q = self.queue()
        self.deliver('Create artifact')
        self.sent(q)
        before = self.state()
        rc, out = self.cli(C, 'sent1', q, 'caller-invented-id')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), before)

    def test_mark_order_is_identical(self):
        for order in ('item-first', 'finding-first'):
            with self.subTest(order=order):
                # Independent state; retain a single fixture transcript delivery.
                K.save_state(str(self.state_dir), dict(owner_queue=[], proposed={}, raised={}, last_relay_ts=ts(599)))
                self.clock = 5
                q = self.queue()
                s = self.state()
                s['proposed']['F1'] = dict(key='control', evidence_hash='hash', message='Synthetic finding', ts=ts(5))
                K.save_state(str(self.state_dir), s)
                if order == 'item-first':
                    self.deliver('Create artifact\n\nSynthetic finding')
                    self.sent(q)
                else:
                    rc, out = self.cli(K, '--sent', 'F1', '--message-id', 'delivery-1')
                    self.assertEqual(rc, 0, out)
                before = self.state()
                rc, out = (self.cli(K, '--sent', 'F1', '--message-id', 'delivery-1') if order == 'item-first'
                           else self.cli(C, 'sent1', q, 'delivery-1'))
                self.assertEqual(rc, 0, out)
                self.assertEqual(self.state(), before)
                self.assertEqual(len(self.state()['send_receipts']), 1)
                self.assertEqual(self.state()['sent_findings']['F1']['message_id'], 'delivery-1')

    def test_queued_only_is_not_delivery(self):
        self.queue()
        self.records(dict(type='queue-operation', operation='enqueue', uuid='delivery-1', timestamp=ts(10),
                          content='Create artifact', origin=dict(kind='peer', **{'from': SELF})))
        rc, out = self.cli(C, 'answered', 'delivery-1')
        self.assertNotEqual(rc, 0, out)

    def test_attachment_without_provenance_is_undecidable(self):
        self.queue()
        self.records(dict(type='attachment', uuid='delivery-1', timestamp=ts(10),
                          attachment=dict(type='queued_command', commandMode='prompt',
                          prompt='<cross-session-message from="%s">Create artifact</cross-session-message>' % SELF)))
        rc, out = self.cli(C, 'answered', 'delivery-1')
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn('last_send_ts', self.state())

if __name__ == '__main__':
    unittest.main(verbosity=2)
