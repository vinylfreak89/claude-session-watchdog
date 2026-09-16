#!/usr/bin/python3
"""An explanation is not independent evidence that an obligation ended."""
import unittest
from send_contract_support import ContractCase, C, ts

class ClosureContract(ContractCase):
    def test_operator_closure_is_retired(self):
        before = self.state()
        rc, out = self.cli(C, 'closed', ts(1), 'Synthetic one-line exception')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), before)
        self.assertIn(ts(1), self.poll())

if __name__ == '__main__':
    unittest.main(verbosity=2)
