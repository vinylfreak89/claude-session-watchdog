#!/usr/bin/python3
"""Run the real reconciliation CLI against completed, synthetic repair ledgers."""
import sys
import unittest
from send_contract_support import ContractCase, C, K, ts
import wd_reconcile as R

class ReconcileSendGuard(ContractCase):
    def rcli(self, *args):
        from reconcile_cli_support import run_reconcile
        ledger = self.root / 'ledger'
        result = run_reconcile([sys.executable, R.__file__, '--state-dir', str(self.state_dir),
                                '--ledger-dir', str(ledger), '--cwd', str(self.root),
                                '--proj', str(self.root), '--self-prefix', 'archive-control'] + list(args),
                               capture_output=True, text=True)
        return result.returncode, result.stdout + result.stderr

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

class ResolvedArchiveGuard(ReconcileSendGuard):
    def archive_worklist(self):
        import realshape as RS
        import wd_recon_lib as RL
        self.clock = 5
        rc, asked = self.cli(C, 'ask', 'K1', 'Synthetic question')
        self.assertEqual(rc, 0, asked)
        self.clock = 10
        rc, resolved = self.cli(C, 'resolved', 'K1')
        self.assertEqual(rc, 0, resolved)
        source = self.root / 'archive-control.jsonl'
        RS.write(str(source), RS.bash(ts(5), './wd.sh ask K1 "Synthetic question"', asked)
                 + RS.bash(ts(10), './wd.sh resolved K1', resolved))
        state = self.state(); state.pop('resolved_questions')
        K.save_state(str(self.state_dir), state)
        numbered, bad = RL.read_records(str(source))
        self.assertEqual(bad, 0)
        actions = RL.my_actions(numbered, source.name, str(self.state_dir), str(self.root))
        _, _, repair = RL.effect_disposition(actions[0], state, actions)
        self.assertEqual(repair['store'], 'resolved_questions')
        self.prepare(repair)
        ledger = R.load(str(self.root / 'ledger'))
        ledger['owed'][0].update(key=RL.action_key(actions[0]), verb='ask', id='K1', text='Synthetic question')
        R.save(str(self.root / 'ledger'), ledger)
        return source

    def test_recorded_resolution_restores_only_missing_archive(self):
        self.archive_worklist()
        rc, out = self.rcli('--stage', '5', '--apply')
        self.assertEqual(rc, 0, out)
        self.assertIn('APPLIED', out)
        self.assertEqual(self.state()['resolved_questions']['K1']['text'], 'Synthetic question')
        self.assertFalse(self.state().get('open_questions'))

    def test_forged_archived_text_is_refused(self):
        self.archive_worklist()
        ledger = R.load(str(self.root / 'ledger'))
        ledger['owed'][0]['repair']['fields']['text'] = 'Not the recorded question'
        R.save(str(self.root / 'ledger'), ledger)
        before = self.state()
        rc, out = self.rcli('--stage', '5', '--apply')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), before)

    def test_archive_cannot_close_currently_open_question(self):
        self.archive_worklist()
        state = self.state(); state['open_questions'] = {'K1': {'text': 'Asked again'}}
        K.save_state(str(self.state_dir), state)
        rc, out = self.rcli('--stage', '5', '--apply')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), state)

    def test_missing_source_cannot_restore_archive(self):
        source = self.archive_worklist(); source.unlink()
        before = self.state()
        rc, out = self.rcli('--stage', '5', '--apply')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), before)

    def test_old_resolution_cannot_hide_a_later_lost_ask(self):
        import json
        import realshape as RS
        source = self.archive_worklist()
        with source.open('a') as stream:
            for record in RS.bash(ts(15), './wd.sh ask K1 "New question"', 'open question K1 registered at ct 2'):
                stream.write(json.dumps(record) + '\n')
        before = self.state()
        rc, out = self.rcli('--stage', '5', '--apply')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), before)

if __name__ == '__main__': unittest.main(verbosity=2)
