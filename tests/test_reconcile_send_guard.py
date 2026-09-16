#!/usr/bin/python3
"""Run the real reconciliation CLI against completed, synthetic repair ledgers."""
import contextlib
import io
import sys
import unittest
from unittest.mock import patch
from send_contract_support import ContractCase, K, ts
import wd_reconcile as R

class ReconcileSendGuard(ContractCase):
    def rcli(self, *args):
        ledger = self.root / 'ledger'
        ledger.mkdir(exist_ok=True)
        output = io.StringIO()
        with patch.object(sys, 'argv', [R.__file__, '--state-dir', str(self.state_dir), '--ledger-dir', str(ledger)] + list(args)), patch.object(R, 'rival_hooks', return_value=[]), contextlib.redirect_stdout(output):
            try:
                rc = R.main()
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 1
                output.write(str(exc))
        return rc, output.getvalue()

    def prepare(self, repair):
        rc, out = self.rcli('--init', ts(0), ts(60))
        self.assertEqual(rc, 0, out)
        ledger = self.root / 'ledger'
        data = R.load(str(ledger))
        for hour in data['hours'].values(): hour['status'] = 'done'
        data['coverage'] = {k: [1, 1] for k in ('actions', 'state_keys', 'records')}
        data['owed'] = [dict(key='synthetic/answered', verb='answered', id=None, text='Synthetic repair',
                            ts=ts(20), why='missing effect', checked_pass=data.get('pass', 0), repair=repair)]
        R.save(str(ledger), data)

    def test_stale_answered_repair_cannot_advance_send(self):
        self.prepare(dict(op='advance', store='last_send_ts', fields=dict(to=ts(30))))
        before = self.state()
        rc, out = self.rcli('--stage', '5', '--apply')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('REFUSED', out)
        self.assertEqual(self.state(), before)

    def test_stale_sent_repair_cannot_mark_item(self):
        self.queue()
        self.prepare(dict(op='amend', store='owner_queue', id='Q1', fields=dict(sent=ts(30))))
        before = self.state()
        rc, out = self.rcli('--stage', '5', '--apply')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('REFUSED', out)
        self.assertEqual(self.state(), before)

    def test_metadata_repair_still_runs(self):
        self.queue()
        self.prepare(dict(op='amend', store='owner_queue', id='Q1', fields=dict(hold_until='Synthetic dependency')))
        rc, out = self.rcli('--stage', '5', '--apply')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.state()['owner_queue'][0]['hold_until'], 'Synthetic dependency')
        self.assertIn('APPLIED', out)

if __name__ == '__main__': unittest.main(verbosity=2)
