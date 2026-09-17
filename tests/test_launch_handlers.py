#!/usr/bin/python3
"""Launch facts through wake's actual command handler."""
import json
import unittest
from send_contract_support import ContractCase, K, C, W, ts
from test_lifecycle_handlers import THREAD

class LaunchHandlers(ContractCase):
    def wake_launch(self, command, background=None, name='Bash', error=False, foreign=False, bootstrap=True):
        path = self.tasks / 'task-control.output'
        if foreign: path = self.tasks / 'foreign' / 'task-control.output'
        inp = dict(command=command)
        if background is not None: inp['run_in_background'] = background
        result = 'Command running in background with ID: task-control. Output is being written to: %s.' % path
        self.tool(name, inp, result, error=error)
        self.records(dict(type='assistant', timestamp=ts(25), message=dict(role='assistant',
                          content=[dict(type='text', text='Synthetic result')], stop_reason='end_turn')))
        rc, out = self.cli(K, *(['--bootstrap'] if bootstrap else ['--trigger', 'manual-control']))
        self.assertEqual(rc, 0, out)
        if bootstrap: self.assertIn('bootstrapped:', out)
        return self.state()['in_flight']

    def test_direct_dispatch_background_is_recorded(self):
        items = self.wake_launch('codex-run task ' + THREAD + ' /tmp/control.md', True)
        self.assertTrue(any(x.get('thread') == THREAD for x in items), items)
        self.assertTrue(any(x.get('id') == 'task-control' for x in items), items)

    def test_heredoc_dispatch_reaches_wake(self):
        items = self.wake_launch("S=/tmp\ncat > $S/control.md <<'EOF'\n"
            "Quoted example: codex-run task 99999999-1111-2222-3333-444444444444 /tmp/fake.md\nEOF\n"
            "codex-run task " + THREAD + " $S/control.md > $S/reply.txt 2>&1; echo done", True, bootstrap=False)
        self.assertTrue(any(x.get('thread') == THREAD for x in items), items)
        log = self.state()['dispatch_log']
        self.assertEqual(len(log), 1, log)
        self.assertEqual(log[0]['brief_path'], '/tmp/control.md')

    def test_heredoc_data_alone_is_not_dispatch(self):
        self.wake_launch("cat > /tmp/notes.md <<'EOF'\ncodex-run task " + THREAD + " /tmp/control.md\nEOF\n")
        self.assertEqual(self.state()['dispatch_log'], [])

    def test_pipe_does_not_turn_data_into_commands(self):
        self.wake_launch('codex-run task ' + THREAD + " /tmp/control.md 2>&1 | tee /tmp/reply.txt", True,
                         bootstrap=False)
        self.assertEqual(len(self.state()['dispatch_log']), 1)

    def test_refused_result_has_no_credit(self):
        self.tool('Bash', dict(command='codex-run task ' + THREAD + ' /tmp/control.md'),
                  result='REFUSED: cannot submit this request')
        self.records(dict(type='assistant', timestamp=ts(25), message=dict(role='assistant',
            content=[dict(type='text', text="I'll send the review to Codex.")], stop_reason='end_turn')))
        rc, out = self.cli(K, '--replay', '1', '--json')
        self.assertEqual(rc, 0, out)
        self.assertIn('dispatch_failed', [f['cls'] for f in json.loads(out)['findings']])

    def test_missing_result_is_unproven_not_credit(self):
        self.records(dict(type='assistant', timestamp=ts(20), message=dict(role='assistant', content=[
            dict(type='tool_use', id='missing-result', name='Bash',
                 input=dict(command='codex-run task ' + THREAD + ' /tmp/control.md'))])))
        self.records(dict(type='assistant', timestamp=ts(25), message=dict(role='assistant',
            content=[dict(type='text', text="I'll send the review to Codex.")], stop_reason='end_turn')))
        rc, out = self.cli(K, '--trigger', 'manual-control')
        self.assertEqual(rc, 0, out)
        self.assertFalse(any(x.get('thread') == THREAD for x in self.state()['in_flight']))

    def claim_findings(self, command):
        self.wake_launch(command)
        self.records(dict(type='assistant', timestamp=ts(26), message=dict(role='assistant',
            content=[dict(type='text', text='Sent it to Codex.')], stop_reason='end_turn')))
        rc, out = self.cli(K, '--replay', '1', '--json')
        self.assertEqual(rc, 0, out)
        return [f['cls'] for f in json.loads(out)['findings']]

    def test_unsupported_shell_is_not_proof_of_no_call(self):
        findings = self.claim_findings('false && codex-run task ' + THREAD + ' /tmp/control.md')
        self.assertNotIn('dispatch_claim_no_call', findings)
        self.assertFalse(any(x.get('thread') == THREAD for x in self.state()['in_flight']))

    def test_data_only_claim_still_produces_no_call_finding(self):
        findings = self.claim_findings("cat > /tmp/notes.md <<'EOF'\ncodex-run task " + THREAD + " /tmp/control.md\nEOF\n")
        self.assertIn('dispatch_claim_no_call', findings)

    def test_literal_variable_and_tab_stripped_heredoc(self):
        self.wake_launch('CR="/tmp/bin/codex-run"\nS=/tmp\ncat > "$S/control.md" <<-\'END\'\n'
                         '\tprintf data\n\tEND\n"$CR" task ' + THREAD + ' "$S/control.md"', True, bootstrap=False)
        self.assertEqual(len(self.state()['dispatch_log']), 1)

    def test_hidden_shell_text_cannot_credit_dispatch(self):
        """Quoted, expanded, defined or piped text naming codex-run is DATA, never a dispatch.

        Observed through the wake handler's dispatch_log. It used to be observed through
        owed's declared/dispatched alarm; that alarm was removed with the guessed-declaration
        path, but this requirement is about the READER and still holds.
        """
        commands = [
            "printf '%s' 'codex-run task " + THREAD + " /tmp/control.md'",
            "cat <<'EOF'\nEOF-not-the-terminator\ncodex-run task " + THREAD + " /tmp/control.md\nEOF\n",
            "echo $(printf 'codex-run task " + THREAD + " /tmp/control.md')",
            "f() { codex-run task " + THREAD + " /tmp/control.md; }",
            "CR='/tmp/space dir/codex-run'; $CR task " + THREAD + ' /tmp/control.md',
            "CR=/tmp/codex-run | cat; $CR task " + THREAD + ' /tmp/control.md',
            "CR=/tmp/codex-run & $CR task " + THREAD + ' /tmp/control.md',
            '| codex-run task ' + THREAD + ' /tmp/control.md',
            'codex-run task ' + THREAD + ' /tmp/control.md |',
        ]
        for command in commands:
            with self.subTest(command=command):
                self.tx.write_text('')
                self.wake_launch(command)
                self.assertEqual(self.state()['dispatch_log'], [], command)
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
