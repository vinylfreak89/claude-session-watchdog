#!/usr/bin/python3
"""A sent finding holds the send gate until it is graded, and only until then.

Defect, 2026-09-30 to 2026-10-02: the gate refused every non-urgent owner item while
`sent_findings` was non-empty, but that map is also the permanent receipt record and nothing
ever removed an entry, so one sent finding (F17, already graded) held the gate for two days.
"""
import unittest
from send_contract_support import ContractCase, C, K

BLOCK = 'SENT WORK IS NOT YET VERIFIED'


class SentFindingGate(ContractCase):
    def with_sent_finding(self, fid='F1', **extra):
        state = self.state()
        state.setdefault('sent_findings', {})[fid] = dict(
            key='announced_nothing_running:synthetic', evidence_hash='0', message='[watchdog] synthetic',
            message_id='delivery-x', sent='2026-09-16T10:00:03Z', **extra)
        K.save_state(str(self.state_dir), state)

    def gate(self):
        rc, out = self.cli(C, 'next')
        return out

    def test_ungraded_sent_finding_holds_the_gate(self):
        self.queue()
        self.with_sent_finding()
        self.assertIn(BLOCK, self.gate())

    def test_grading_releases_the_gate_and_keeps_the_receipt(self):
        self.queue()
        self.with_sent_finding()
        self.assertIn(BLOCK, self.gate())
        rc, out = self.cli(K, '--outcome', 'F1', 'accepted', '--reason', 'target acted on it')
        self.assertEqual(rc, 0, out)
        self.assertNotIn(BLOCK, self.gate())
        kept = self.state()['sent_findings']['F1']
        self.assertEqual(kept['message_id'], 'delivery-x')
        self.assertEqual(kept['outcome'], 'accepted')
        self.assertNotIn('F1 sent', self.poll())

    def test_grading_another_id_does_not_release_it(self):
        self.queue()
        self.with_sent_finding()
        rc, out = self.cli(K, '--outcome', 'F2', 'wrong', '--reason', 'unrelated')
        self.assertEqual(rc, 0, out)
        self.assertIn('gate unaffected', out)
        self.assertIn(BLOCK, self.gate())
        self.assertIn('wd.sh outcome F1', self.poll())

    def test_one_graded_one_open_still_holds(self):
        self.queue()
        self.with_sent_finding('F1', outcome='accepted')
        self.with_sent_finding('F2')
        self.assertIn(BLOCK, self.gate())


if __name__ == '__main__':
    unittest.main()
