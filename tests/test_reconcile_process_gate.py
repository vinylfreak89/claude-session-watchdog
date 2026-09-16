#!/usr/bin/python3
"""Real reconciliation CLI, fixture process census, real exclusion and initialization."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from reconcile_cli_support import run_reconcile

class ReconcileProcessGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='recon-gate-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'; self.state.mkdir()
        (self.state / 'state.json').write_text('{}')
        self.ledger = self.root / 'ledger'
        self.argv = [sys.executable, str(Path(__file__).resolve().parents[1] / 'wd_reconcile.py'),
                     '--state-dir', str(self.state), '--ledger-dir', str(self.ledger),
                     '2026-09-11T00:00:00Z', '2026-09-11T02:00:00Z', '--init']

    def invoke(self, process_table='', ps_exit=0):
        return run_reconcile(self.argv, process_table=process_table, ps_exit=ps_exit, capture_output=True, text=True)

    def test_known_empty_census_initializes_real_ledger(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        ledger = json.loads((self.ledger / 'reconcile.json').read_text())
        self.assertTrue(ledger['hours'])
        self.assertIn('conf 0', result.stdout)
        self.assertEqual(list(self.state.iterdir()), [self.state / 'state.json'])

    def test_same_state_rival_aborts_before_initialization(self):
        result = self.invoke('987654 1 python /synthetic/wd_wait.py --state-dir %s\n' % self.state)
        self.assertIn('RECONCILE ABORTED', result.stdout)
        self.assertFalse((self.ledger / 'reconcile.json').exists())

    def test_other_state_rival_does_not_block_this_ledger(self):
        result = self.invoke('987654 1 python /synthetic/wd_wait.py --state-dir %s\n' % (self.root / 'other'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.ledger / 'reconcile.json').exists())

    def test_failed_census_is_not_an_empty_census(self):
        result = self.invoke(ps_exit=3)
        self.assertIn('RECONCILE ABORTED', result.stdout)
        self.assertIn('cannot enumerate processes', result.stdout)
        self.assertFalse((self.ledger / 'reconcile.json').exists())

if __name__ == '__main__': unittest.main(verbosity=2)
