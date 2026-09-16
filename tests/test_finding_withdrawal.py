#!/usr/bin/python3
import unittest
from send_contract_support import ContractCase, K, ts

class FindingWithdrawal(ContractCase):
    def proposed(self):
        s = self.state()
        s['proposed']['F1'] = dict(key='control', evidence_hash='hash', ts=ts(5), message='Synthetic finding')
        K.save_state(str(self.state_dir), s)

    def test_delivered_unmarked_finding_cannot_be_vetoed(self):
        self.proposed()
        self.deliver('Synthetic finding')
        before = self.state()
        rc, out = self.cli(K, '--veto', 'F1', '--reason', 'withdraw')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), before)

    def test_never_delivered_finding_can_be_vetoed(self):
        self.proposed()
        rc, out = self.cli(K, '--veto', 'F1', '--reason', 'withdraw')
        self.assertEqual(rc, 0, out)
        self.assertNotIn('F1', self.state()['proposed'])

    def test_unreadable_record_cannot_authorize_veto(self):
        self.proposed()
        self.tx.write_text('{')
        before = self.state()
        rc, out = self.cli(K, '--veto', 'F1', '--reason', 'withdraw')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), before)

if __name__ == '__main__': unittest.main(verbosity=2)
