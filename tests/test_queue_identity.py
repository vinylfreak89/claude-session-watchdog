#!/usr/bin/python3
"""Queue identity controls use the real add/drop command handlers."""
import unittest
from send_contract_support import ContractCase, C, K

class QueueIdentity(ContractCase):
    def test_drop_cannot_reuse_a_live_id(self):
        first = self.queue('Queued alpha')
        second = self.queue('Queued beta')
        rc, out = self.cli(K, '--queue-drop', first, '--reason', 'Synthetic withdrawal')
        self.assertEqual(rc, 0, out)
        third = self.queue('Queued gamma')
        self.assertNotEqual(third, second)
        self.assertNotEqual(third, first)
        self.assertEqual(len({q['id'] for q in self.state()['owner_queue']}), 2)
        self.assertEqual(self.state()['owner_queue_dropped'][0]['id'], first)

    def test_dropped_highest_id_is_never_reused(self):
        ids = [self.queue('Queued number %d' % n) for n in range(4)]
        rc, out = self.cli(K, '--queue-drop', ids[-1], '--reason', 'Synthetic withdrawal')
        self.assertEqual(rc, 0, out)
        new = self.queue('New synthetic request')
        self.assertEqual(new, 'Q5')
        self.assertEqual(self.state()['owner_queue_seq'], 5)

    def test_legacy_archive_participates_in_sequence(self):
        state = self.state()
        state['owner_queue_dropped'] = [dict(id='Q400', text='Legacy dropped item')]
        K.save_state(str(self.state_dir), state)
        self.assertEqual(self.queue(), 'Q401')

if __name__ == '__main__': unittest.main(verbosity=2)
