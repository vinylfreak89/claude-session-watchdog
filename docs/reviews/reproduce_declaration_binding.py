"""Executable specification for the declaration-binding proposal.

Run: /usr/bin/python3 docs/reviews/reproduce_declaration_binding.py
Assertions express REQUIRED behavior, not acceptance of the current defects.
This reproduction exits nonzero until the proposed contract is implemented.
It is a review artifact, not an expected-failure exception in tests/run_all.py.
All records/files are synthetic; dispatch commands are DATA and never executed.
"""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from send_contract_support import ContractCase, C, SELF, ts

THREAD = '00000000-1111-2222-3333-444444444444'
ACTION = 'Repair the scheduler fairness test using an event instead of a sleep.'
OTHER = 'Update the documentation navigation links.'
PROMISE = "I'll send the scheduler fairness test repair to Codex once the eight runs finish."


class DeclarationBinding(ContractCase):
    def opener(self, ident, at):
        self.records(dict(type='user', uuid='user-' + ident, promptId=ident, timestamp=ts(at),
                          origin=dict(kind='human'), message=dict(role='user', content='Synthetic request')))

    def finish(self, ident, text, at):
        self.records(dict(type='assistant', uuid='assistant-' + ident, timestamp=ts(at),
                          message=dict(role='assistant', content=[dict(type='text', text=text)],
                                       stop_reason='end_turn')))

    def promise(self):
        self.opener('promise', 10)
        self.finish('promise', PROMISE, 11)

    def dispatch(self, text, at=21, error=False):
        # A fixture containing the complete submitted brief, not just a mutable file path.
        command = "cat > /tmp/synthetic-brief.md <<'BRIEF'\n%s\nBRIEF\ncodex-run task %s /tmp/synthetic-brief.md" % (text, THREAD)
        self.tool('Bash', dict(command=command), result='synthetic accepted dispatch' if not error else 'dispatch refused',
                  at=at, error=error)

    def broken(self):
        rc, out = self.cli(C, 'owed')
        self.assertEqual(rc, 0, out)
        match = re.search(r'^DECLARED an action and made no dispatch: (\d+)$', out, re.M)
        self.assertIsNotNone(match, out)
        return int(match.group(1))

    def test_deferred_matching_dispatch_fulfils_promise(self):
        self.promise()
        self.opener('later', 20)
        self.dispatch(ACTION)
        self.finish('later', 'Submitted the synthetic work request.', 24)
        self.assertEqual(self.broken(), 0, 'a later matching dispatch must fulfil the declaration')

    def test_later_unrelated_dispatch_keeps_promise_owed(self):
        self.promise()
        self.opener('later', 20)
        self.dispatch(OTHER)
        self.finish('later', 'Submitted the unrelated work request.', 24)
        self.assertEqual(self.broken(), 1, 'a later unrelated dispatch must not receive credit')

    def test_message_to_watchdog_is_not_promised_dispatch(self):
        self.opener('promise', 10)
        self.tool('SendMessage', dict(to=SELF, message='The test repair is still pending.'), at=11)
        self.finish('promise', PROMISE, 14)
        self.assertEqual(self.broken(), 1, 'reporting to the watchdog does not dispatch work to Codex')

    def test_same_turn_unrelated_dispatch_keeps_promise_owed(self):
        self.opener('promise', 10)
        self.dispatch(OTHER, at=11)
        self.finish('promise', PROMISE, 14)
        self.assertEqual(self.broken(), 1, 'turn coincidence is not a work binding')

    def test_watchdog_message_alias_is_not_promised_dispatch(self):
        self.opener('promise', 10)
        self.tool('mcp__peer__send_message', dict(to=SELF, message='The test repair is still pending.'), at=11)
        self.finish('promise', PROMISE, 14)
        self.assertEqual(self.broken(), 1, 'recipient and work must be checked across tool aliases')

    def test_failed_dispatch_keeps_promise_owed(self):
        self.opener('promise', 10)
        self.dispatch(ACTION, at=11, error=True)
        self.finish('promise', PROMISE, 14)
        self.assertEqual(self.broken(), 1, 'a refused dispatch cannot fulfil a promise')

    def test_quoted_command_keeps_promise_owed(self):
        self.opener('promise', 10)
        self.tool('Write', dict(file_path=str(self.root / 'notes.md'),
                               content='Example only: codex-run task ' + THREAD + ' /tmp/example.md'), at=11)
        self.finish('promise', PROMISE, 14)
        self.assertEqual(self.broken(), 1, 'writing a command in notes is not dispatching')

    def test_turn_hold_does_not_fulfil_target_promise(self):
        self.promise()
        rc, out = self.cli(C, 'hold', ts(11), 'Waiting for owner input on a separate issue')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.broken(), 1, 'watchdog turn disposition is not target action evidence')

    def test_three_declarations_one_dispatch_leaves_two_owed(self):
        self.opener('promise', 10)
        self.dispatch(ACTION, at=11)
        self.finish('promise', "I'll send the scheduler fairness test repair to Codex.\n"
                    "I'll send the parser review to Codex.\nI'll send the rendering review to Codex.", 14)
        self.assertEqual(self.broken(), 2, 'each declaration needs its own binding')


if __name__ == '__main__':
    unittest.main(verbosity=2)
