#!/usr/bin/python3
"""Lifecycle evidence must survive chunk boundaries through the check CLI."""
import json
import unittest
import warnings
from unittest.mock import patch
from send_contract_support import ContractCase, C, W, ts

THREAD = '00000000-1111-2222-3333-444444444444'

class LifecycleHandlers(ContractCase):
    def setUp(self):
        super().setUp()
        self.rollouts = self.root / 'rollouts'
        directory = self.rollouts / '2026' / '09' / '16'
        directory.mkdir(parents=True)
        self.rollout = directory / ('rollout-2026-09-16T00-00-00-' + THREAD + '.jsonl')
        patch.object(W, 'CODEX_SESSIONS', str(self.rollouts)).start()

    def check_dispatch(self):
        rc, out = self.cli(C, 'check', 'dispatch', THREAD)
        self.assertEqual(rc, 0, out)
        data = json.loads(out)
        self.assertEqual(data['kind'], 'dispatch')
        return data['evidence']

    def straddle(self, event):
        record = (json.dumps(dict(timestamp=ts(10), type='event_msg', payload=dict(type=event))) + '\n').encode()
        wanted = 4 * 1024 * 1024 - (len(record) - 20)
        empty = (json.dumps(dict(timestamp=ts(20), type='response_item', payload=dict(type='reasoning', text=''))) + '\n').encode()
        filler = empty.replace(b'"text": ""', b'"text": "' + b'x' * (wanted - len(empty)) + b'"')
        self.rollout.write_bytes(record + filler)

    def test_start_across_chunk_boundary_is_in_flight(self):
        self.straddle('task_started')
        self.assertTrue(self.check_dispatch()['in_flight'])

    def test_completion_across_chunk_boundary_is_known(self):
        self.straddle('task_complete')
        state = self.check_dispatch()
        self.assertFalse(state['in_flight'])
        self.assertEqual(state.get('last_complete'), ts(10))
        self.assertTrue(state.get('lifecycle_known'))

    def test_unrelated_marker_cannot_invent_start(self):
        self.rollout.write_text(json.dumps(dict(timestamp=ts(10), type='response_item', payload=dict(text='task_started'))) + '\n')
        state = self.check_dispatch()
        self.assertFalse(state['in_flight'])
        self.assertTrue(state.get('lifecycle_known'))

    def test_invalid_record_is_not_conclusive_absence(self):
        self.rollout.write_text('{"payload":{"type":"task_started"}\n')
        state = self.check_dispatch()
        self.assertFalse(state.get('lifecycle_known', True))

    def test_capped_search_is_unknown(self):
        self.straddle('task_started')
        scan = W._last_marked_line
        with patch.object(W, '_last_marked_line', side_effect=lambda path, size, marker: scan(path, size, marker, cap_bytes=64, chunk=64)) as bounded:
            state = self.check_dispatch()
            self.assertEqual(bounded.call_count, 2, 'both lifecycle scans must reach the capped reader')
        self.assertFalse(state.get('lifecycle_known', True))

    def test_scanner_stderr_does_not_replace_json(self):
        self.straddle('task_started')
        scan = W._last_marked_line
        def bounded(path, size, marker):
            warnings.warn('synthetic scanner diagnostic', RuntimeWarning)
            return scan(path, size, marker, cap_bytes=64, chunk=64)
        with warnings.catch_warnings(), patch.object(W, '_last_marked_line', side_effect=bounded):
            warnings.simplefilter('always')
            state = self.check_dispatch()
        self.assertFalse(state.get('lifecycle_known', True))
        self.assertIn('synthetic scanner diagnostic', self.cli_stderr)

    def test_small_empty_lifecycle_remains_conclusive(self):
        self.rollout.write_text(json.dumps(dict(timestamp=ts(10), type='response_item', payload=dict(type='reasoning'))) + '\n')
        state = self.check_dispatch()
        self.assertFalse(state['in_flight'])
        self.assertTrue(state.get('lifecycle_known'))

if __name__ == '__main__': unittest.main(verbosity=2)
