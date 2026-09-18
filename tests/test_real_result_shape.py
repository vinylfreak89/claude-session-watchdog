#!/usr/bin/python3
"""The host OMITS is_error on a successful Edit/Write result; only a failure carries it.

Measured 2026-09-18 on the target's transcript: 140 Edit and 98 Write results with no
is_error key, and every failure marked is_error=true. The acceptance layer required an
explicit False, so it discarded every real Edit and Write -- the replay path behind
file, grep and csv had never credited a single real write. The harness hid it: its tool()
helper always writes is_error=False, a shape the host never produces for these tools.

These controls write the host's real shape, key absent, and never the harness default.
"""
import json
import unittest
from send_contract_support import ContractCase, K, ts


class RealResultShape(ContractCase):
    def edit_as_host_writes_it(self, path, old, new, at=20, error=None):
        self.serial += 1
        uid = 'real-%d' % self.serial
        result = dict(type='tool_result', tool_use_id=uid, content='The file has been updated.')
        if error is not None:
            result['is_error'] = error
        self.records(dict(type='assistant', timestamp=ts(at), message=dict(role='assistant',
                          content=[dict(type='tool_use', id=uid, name='Edit', input=dict(
                              file_path=str(path), old_string=old, new_string=new))], stop_reason='tool_use')),
                     dict(type='user', timestamp=ts(at + 1), message=dict(role='user', content=[result])))
        self.assertNotIn('is_error', result) if error is None else None

    def setup_item(self):
        notes = self.root / 'notes.md'
        notes.write_text('alpha\nbeta\n')
        rc, out = self.cli(K, '--queue-add', 'Add the rule', '--acted-when', 'grep notes.md experiments_rule')
        self.assertEqual(rc, 0, out)
        qid = self.state()['owner_queue'][-1]['id']
        self.deliver('Add the rule')
        self.sent(qid)
        return qid, notes

    def apply(self, notes, at=20):
        import os
        from send_contract_support import W
        notes.write_text('alpha\nexperiments_rule\nbeta\n')
        epoch = W.epoch_from_iso(ts(at + 1))
        os.utime(notes, (epoch, epoch))

    def settled(self, qid):
        self.poll()
        return not any(i['id'] == qid for i in self.state().get('owner_queue', []))

    def test_successful_edit_without_is_error_is_credited(self):
        qid, notes = self.setup_item()
        self.apply(notes)
        self.edit_as_host_writes_it(notes, 'alpha\n', 'alpha\nexperiments_rule\n')
        self.assertTrue(self.settled(qid))

    def test_explicit_failure_is_not_credited(self):
        qid, notes = self.setup_item()
        self.apply(notes)
        self.edit_as_host_writes_it(notes, 'alpha\n', 'alpha\nexperiments_rule\n', error=True)
        self.assertFalse(self.settled(qid))


if __name__ == '__main__':
    unittest.main(verbosity=2)
