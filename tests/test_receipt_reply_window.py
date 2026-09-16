#!/usr/bin/python3
import unittest
from send_contract_support import ContractCase, C, K, W, ts

class ReceiptReplyWindow(ContractCase):
    def proposal(self, poke=False):
        state = self.state()
        state['proposed']['F1'] = dict(key='control', evidence_hash='hash', ts=ts(5), message='Synthetic finding',
                                        asks_reply=True, is_poke=poke, reply_min=3)
        K.save_state(str(self.state_dir), state)

    def test_item_first_opens_reply_window_at_delivery(self):
        q = self.queue()
        self.proposal()
        self.deliver('Create artifact\n\nSynthetic finding')
        self.sent(q)
        waiting = self.state().get('awaiting_reply')
        self.assertIsNotNone(waiting)
        self.assertEqual(waiting['sent_ts'], ts(10))
        self.assertEqual(W.epoch_from_iso(waiting['deadline']), W.epoch_from_iso(ts(10)) + 180)
        before = self.state()
        self.clock = 50
        rc, out = self.cli(K, '--sent', 'F1', '--message-id', 'delivery-1')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.state(), before)

    def test_finding_first_records_poke_without_resetting_deadline(self):
        state = self.state()
        original = dict(message_id='prior', sent_ts=ts(0), deadline=ts(2), findings=['F0'], poked=False)
        state['awaiting_reply'] = original
        K.save_state(str(self.state_dir), state)
        self.proposal(poke=True)
        self.deliver('Synthetic finding')
        rc, out = self.cli(K, '--sent', 'F1', '--message-id', 'delivery-1')
        self.assertEqual(rc, 0, out)
        waiting = self.state()['awaiting_reply']
        self.assertTrue(waiting['poked'])
        self.assertEqual(waiting['deadline'], original['deadline'])
        self.assertEqual(waiting['poke_message_id'], 'delivery-1')

if __name__ == '__main__': unittest.main(verbosity=2)
