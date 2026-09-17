#!/usr/bin/python3
"""The owner-authorized exception requires an explicit, nonempty acknowledgement."""
import unittest
from send_contract_support import ContractCase, C, ts

class OwnerAckContract(ContractCase):
    def test_explicit_owner_ack_restores_turn_disposition(self):
        self.turn('second', 2, 3)
        self.assertIn(ts(1), self.poll())
        self.clock = 5
        rc, out = self.cli(C, 'answered', '--owner-ack', 'Owner authorizes acknowledgement')
        self.assertEqual(rc, 0, out)
        self.assertIn('owner acknowledged', out)
        self.assertEqual(self.state()['owner_ack'], dict(words='Owner authorizes acknowledgement', at=ts(5)))
        self.assertEqual(self.state()['last_send_ts'], ts(5))
        out = self.poll()
        self.assertNotIn(ts(1) + '  [', out)
        self.assertNotIn(ts(3) + '  [', out)
        self.turn('later', 6, 7)
        self.assertIn(ts(7) + '  [', self.poll())

    def test_empty_owner_ack_is_not_acknowledgement(self):
        for words in ('', '   '):
            with self.subTest(words=words):
                before = self.state()
                rc, out = self.cli(C, 'answered', '--owner-ack', words)
                self.assertNotEqual(rc, 0, out)
                self.assertIn('empty --owner-ack', out)
                self.assertEqual(self.state(), before)

    def test_missing_owner_ack_words_are_refused(self):
        before = self.state()
        rc, out = self.cli(C, 'answered', '--owner-ack')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), before)

    def test_stray_words_cannot_supply_owner_authority(self):
        before = self.state()
        rc, out = self.cli(C, 'answered', 'Owner authorizes acknowledgement')
        self.assertNotEqual(rc, 0, out)
        self.assert_receipt_refusal_preserves_obligations(before)

    def test_owner_ack_does_not_archive_sent_work(self):
        q = self.queue(); self.deliver('Create artifact'); self.sent(q)
        self.clock = 20
        rc, out = self.cli(C, 'answered', '--owner-ack', 'Owner acknowledges the turn')
        self.assertEqual(rc, 0, out)
        self.assertIn(q, self.poll())
        self.assertEqual(len(self.state()['owner_queue']), 1)
        self.assertFalse(self.state().get('owner_queue_sent'))

if __name__ == '__main__': unittest.main(verbosity=2)
