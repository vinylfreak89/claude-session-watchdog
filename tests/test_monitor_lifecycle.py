#!/usr/bin/python3
"""Exercise completion and stall decisions through the real monitor command."""
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
        return self.run_monitor()

    def run_monitor(self, stall_min=0):
        self.sess['state_path'] = str(self.root / 'session.json')
        clock = [W.epoch_from_iso(ts(2000))]
        class QueueIO:
            def control(self, *args):
                clock[0] += 1
                return []
        output = io.StringIO()
        with patch.object(sys, 'argv', [H.__file__, '--target', self.sess['sessionId'], '--state-dir', str(self.state_dir),
                                       '--stale-after', '1', '--stall-min', str(stall_min), '--max-wait', '1', '--backstop', '0']), \
             patch.object(W, 'CODEX_SESSIONS', str(self.root / 'rollouts')), patch.object(H.select, 'kqueue', return_value=QueueIO()), \
             patch.object(H.time, 'time', side_effect=lambda: clock[0]), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            rc = H.main()
        return rc, output.getvalue()

    def background(self, contents):
        output = self.tasks / 'background-control.output'
        output.write_text(contents)
        when = W.epoch_from_iso(ts(20))
        os.utime(output, (when, when))
        item = dict(kind='bg', id='background-control', output_file=str(output),
                    command='synthetic-background-command', launched_ts=ts(5))
        state = self.state()
        state['in_flight'] = [item]
        K.save_state(str(self.state_dir), state)
        return item

    def test_bg_exit_marker_finishes_and_evicts(self):
        for code in (0, 1):
            with self.subTest(exit_code=code):
                self.background('Synthetic output\n[exited with code %d]\n' % code)
                with patch.object(W, 'task_output_status', wraps=W.task_output_status) as probe:
                    rc, out = self.run_monitor(stall_min=20)
                self.assertEqual(rc, 3, out)
                self.assertNotIn('STALL kind=bg', out)
                self.assertIn('FINISHED kind=bg', out)
                self.assertIn('exit_code=%d' % code, out)
                self.assertEqual(self.state()['in_flight'], [])
                self.assertEqual(probe.call_count, 1, 'finished work was interrogated again')

    def test_bg_missing_exit_marker_still_stalls(self):
        item = self.background('Synthetic output with no completion marker\n')
        rc, out = self.run_monitor(stall_min=20)
        self.assertEqual(rc, 0, out)
        self.assertIn('STALL kind=bg', out)
        self.assertIn('missing_exit_marker', out)
        self.assertIn('live_processes_0', out)
        self.assertEqual(self.state()['in_flight'], [item])

    def test_bg_completion_clears_persisted_stall(self):
        item = self.background('[exited with code 0]\n')
        status = W.task_output_status(item['output_file'])
        memory = self.state_dir / 'wait_memory.json'
        memory.write_text(json.dumps(dict(stalled={item['id']: [status['size'], status['mtime'], 0]})))
        rc, out = self.run_monitor(stall_min=20)
        self.assertEqual(rc, 3, out)
        self.assertEqual(self.state()['in_flight'], [])
        self.assertNotIn(item['id'], json.loads(memory.read_text())['stalled'])

    def test_completion_preserves_concurrent_state_update(self):
        item = self.background('[exited with code 0]\n')
        replacement = dict(item, launched_ts=ts(1999))
        real_probe = W.task_output_status
        def changed_during_probe(path):
            status = real_probe(path)
            state = self.state()
            state['in_flight'] = [replacement]
            state['unrelated_control'] = 'preserve this write'
            K.save_state(str(self.state_dir), state)
            return status
        # Only the first read sees completion. Later reads see an unfinished replacement.
        calls = [0]
        def probe(path):
            calls[0] += 1
            return changed_during_probe(path) if calls[0] == 1 else dict(exists=False)
        with patch.object(W, 'task_output_status', side_effect=probe):
            rc, out = self.run_monitor(stall_min=20)
        self.assertEqual(rc, 3, out)
        self.assertEqual(self.state()['in_flight'], [replacement])
        self.assertEqual(self.state()['unrelated_control'], 'preserve this write')

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
