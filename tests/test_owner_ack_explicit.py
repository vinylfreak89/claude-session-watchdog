#!/usr/bin/python3
"""An operator cannot manufacture owner authority through an argument."""
import unittest
from send_contract_support import ContractCase, C

class OwnerAckContract(ContractCase):
    def test_owner_ack_is_retired(self):
        before = self.state()
        rc, out = self.cli(C, 'answered', '--owner-ack', 'Synthetic authority claim')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), before)

if __name__ == '__main__':
    unittest.main(verbosity=2)
