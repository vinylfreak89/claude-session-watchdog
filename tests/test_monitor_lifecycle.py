#!/usr/bin/python3
"""The real monitor command must not quiet an unknown lifecycle as completed."""
import contextlib
import io
import json
import os
import sys
import unittest
from unittest.mock import patch
from send_contract_support import ContractCase, K, W, ts
import wd_wait as H
from test_lifecycle_handlers import THREAD

class MonitorLifecycle(ContractCase):
    def test_wake_retains_unknown_completion(self):
        for known in (False, True):
            state = self.state()
            state['in_flight'] = [dict(id='dispatch-control', kind='codex', thread=THREAD, launched_ts=ts(5))]
            K.save_state(str(self.state_dir), state)
            with patch.object(W, 'codex_thread_state', return_value=dict(found=True, lifecycle_known=known,
                              last_complete=ts(20), in_flight=False)):
                rc, out = self.cli(K, '--bootstrap')
            self.assertEqual(rc, 0, out)
            self.assertEqual(bool(self.state()['in_flight']), not known, out)

    def monitor(self, records):
        root = self.root / 'rollouts'
        directory = root / '2026' / '09' / '16'; directory.mkdir(parents=True)
        rollout = directory / ('rollout-2026-09-16T00-00-00-' + THREAD + '.jsonl')
        rollout.write_text(records)
        self.sess['state_path'] = str(self.root / 'session.json')
        state = self.state()
        state['in_flight'] = [dict(kind='codex', thread=THREAD, launched_ts=ts(5))]
        K.save_state(str(self.state_dir), state)
        clock = [W.epoch_from_iso(ts(500))]
        class QueueIO:
            def control(self, *args):
                clock[0] += 1
                return []
        output = io.StringIO()
        with patch.object(sys, 'argv', [H.__file__, '--target', self.sess['sessionId'], '--state-dir', str(self.state_dir),
                                       '--stale-after', '1', '--stall-min', '0', '--max-wait', '1', '--backstop', '0']), \
             patch.object(W, 'CODEX_SESSIONS', str(root)), patch.object(H.select, 'kqueue', return_value=QueueIO()), \
             patch.object(H.time, 'time', side_effect=lambda: clock[0]), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            rc = H.main()
        return rc, output.getvalue()

    def test_unknown_lifecycle_still_nags(self):
        rc, out = self.monitor('{"payload":{"type":"task_started"}\n')
        self.assertEqual(rc, 0, out)
        self.assertIn('STALL kind=codex', out)
        self.assertIn('unknown', out.lower())
        self.assertNotIn('turn_finished', out)

    def test_known_completed_lifecycle_is_quiet(self):
        records = ''.join(json.dumps(dict(type='event_msg', timestamp=ts(t), payload=dict(type=k))) + '\n'
                          for t, k in [(10, 'task_started'), (20, 'task_complete')])
        rc, out = self.monitor(records)
        self.assertEqual(rc, 3, out)
        self.assertIn('HEARTBEAT', out)
        self.assertNotIn('STALL kind=codex', out)

if __name__ == '__main__': unittest.main(verbosity=2)
