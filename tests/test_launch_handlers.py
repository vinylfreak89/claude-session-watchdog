#!/usr/bin/python3
"""Launch facts through wake's actual command handler."""
import unittest
from send_contract_support import ContractCase, K, ts
from test_lifecycle_handlers import THREAD

class LaunchHandlers(ContractCase):
    def wake_launch(self, command, background=None, name='Bash', error=False, foreign=False):
        path = self.tasks / 'task-control.output'
        if foreign: path = self.tasks / 'foreign' / 'task-control.output'
        inp = dict(command=command)
        if background is not None: inp['run_in_background'] = background
        result = 'Command running in background with ID: task-control. Output is being written to: %s.' % path
        self.tool(name, inp, result, error=error)
        self.records(dict(type='assistant', timestamp=ts(25), message=dict(role='assistant',
                          content=[dict(type='text', text='Synthetic result')], stop_reason='end_turn')))
        rc, out = self.cli(K, '--bootstrap')
        self.assertEqual(rc, 0, out)
        self.assertIn('bootstrapped:', out)
        return self.state()['in_flight']

    def test_direct_dispatch_background_is_recorded(self):
        items = self.wake_launch('codex-run task ' + THREAD + ' /tmp/control.md', True)
        self.assertTrue(any(x.get('thread') == THREAD for x in items), items)
        self.assertTrue(any(x.get('id') == 'task-control' for x in items), items)

    def test_quoted_command_is_not_dispatch(self):
        items = self.wake_launch("printf '%s' 'codex-run task " + THREAD + " /tmp/control.md'")
        self.assertEqual(items, [])
        self.assertEqual(self.state()['dispatch_log'], [])

    def test_foreground_output_cannot_supply_background(self):
        items = self.wake_launch('codex-run task ' + THREAD + ' /tmp/control.md')
        self.assertFalse(any(x.get('id') == 'task-control' or x.get('output_file') for x in items), items)

    def test_non_bash_tool_cannot_launch(self):
        self.assertEqual(self.wake_launch('example', True, name='Read'), [])

    def test_string_background_flag_cannot_launch(self):
        self.assertEqual(self.wake_launch('example', 'false'), [])

    def test_error_result_cannot_launch(self):
        self.assertEqual(self.wake_launch('example', True, error=True), [])

    def test_nested_path_cannot_impersonate_session(self):
        self.assertEqual(self.wake_launch('example', True, foreign=True), [])

if __name__ == '__main__': unittest.main(verbosity=2)
