#!/usr/bin/python3
"""Mutation checks: positive CLI controls must detect inert handlers or missing kinds."""
import io
import unittest
from unittest.mock import patch
import test_acceptance_contract as controls_a
import test_sent_needs_action as controls_s
import wd_acceptance as A
import wd_check as C
import wd_wake as K

class MutationControls(unittest.TestCase):
    def rejects(self, case):
        result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([case]))
        self.assertFalse(result.wasSuccessful(), 'control passed a broken implementation')

    def test_noop_check_handler_is_detected(self):
        with patch.object(C, 'main', return_value=0):
            self.rejects(controls_a.AcceptanceContract('test_file_positive'))

    def test_noop_wake_handler_is_detected(self):
        with patch.object(K, 'main', return_value=0):
            self.rejects(controls_a.AcceptanceContract('test_file_positive'))

    def test_noop_owed_handler_is_detected(self):
        original = C.main
        import sys
        def broken():
            return 0 if 'owed' in sys.argv else original()
        with patch.object(C, 'main', side_effect=broken):
            self.rejects(controls_a.AcceptanceContract('test_file_positive'))

    def test_noop_finding_sent_handler_is_detected(self):
        original = K.main
        import sys
        def broken():
            return 0 if '--sent' in sys.argv else original()
        with patch.object(K, 'main', side_effect=broken):
            self.rejects(controls_s.SendContract('test_verified_finding_records_delivery'))

    def test_removing_any_kind_is_detected(self):
        for kind in tuple(A.KINDS):
            with self.subTest(kind=kind):
                retained = {k: v for k, v in A.KINDS.items() if k != kind}
                with patch.dict(A.KINDS, retained, clear=True):
                    self.rejects(controls_a.AcceptanceContract('test_%s_positive' % kind.replace('-', '_')))

if __name__ == '__main__': unittest.main(verbosity=2)
