#!/usr/bin/python3
"""State writers must not erase obligations on an unreadable file or overlapping commands."""
import multiprocessing
import time
import unittest
from unittest.mock import patch
from send_contract_support import ContractCase, K

class StateTransactions(ContractCase):
    def test_unreadable_state_is_not_reinitialized(self):
        path = self.state_dir / 'state.json'
        path.write_text('{broken')
        rc, out = self.cli(K, '--queue-add', 'New request')
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(path.read_text(), '{broken')

    def test_distinct_state_directories_do_not_share_defaults(self):
        first = self.state_dir / 'first'; first.mkdir()
        second = self.state_dir / 'second'; second.mkdir()
        original = self.state_dir
        self.state_dir = first
        # A fixture proposal models the existing finding producer's in-memory update.
        state = K.load_state(str(first)); state['proposed']['Fshared'] = dict(message='fixture')
        K.save_state(str(first), state)
        self.state_dir = second
        rc, out = self.cli(K, '--queue-add', 'Independent request')
        self.assertEqual(rc, 0, out)
        self.assertNotIn('Fshared', self.state()['proposed'])
        self.state_dir = original

    def test_parallel_queue_commands_preserve_both_items(self):
        original = K.load_state
        def delayed_load(path):
            state = original(path)
            time.sleep(.15)
            return state
        def add(text):
            rc, out = self.cli(K, '--queue-add', text)
            if rc: raise RuntimeError(out)
        context = multiprocessing.get_context('fork')
        with patch.object(K, 'load_state', side_effect=delayed_load):
            children = [context.Process(target=add, args=(text,)) for text in ('First', 'Second')]
            for child in children: child.start()
            for child in children:
                child.join(10)
                if child.is_alive(): child.terminate(); child.join()
                self.assertEqual(child.exitcode, 0)
        queue = self.state()['owner_queue']
        self.assertEqual({q['text'] for q in queue}, {'First', 'Second'})
        self.assertEqual(len({q['id'] for q in queue}), 2)

if __name__ == '__main__': unittest.main(verbosity=2)
