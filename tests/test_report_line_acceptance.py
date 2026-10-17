#!/usr/bin/python3
"""The exact done-line at the end of the target's own report closes a msg-to-watchdog item.

Owner, 2026-10-17 (watchdog transcript line 136172): "it put it in the report but its the same so
you should act on it the same way". Q318's line sat in the target's report to him, not in a message
to the watchdog, and the item stayed owed while his bars window ran.
"""
import unittest
from send_contract_support import ContractCase, C, K, W, ts

LINE = 'BACKEND READY FOR REVIEW'


class ReportLineAcceptance(ContractCase):
    def sent_item(self):
        q = self.queue(text='Build the backend', spec='msg-to-watchdog "%s"' % LINE)
        self.deliver('Build the backend')
        self.sent(q)
        return q

    def report(self, text, at=20, stop='end_turn'):
        self.records(dict(type='assistant', uuid='a-%d' % at, timestamp=ts(at), message=dict(
            role='assistant', content=[dict(type='text', text=text)], stop_reason=stop)))

    def test_line_alone_in_the_report_closes_the_item(self):
        q = self.sent_item()
        self.report('Built and reviewed.\n\n%s' % LINE)
        out = self.poll()
        self.assertIn('closed this poll: %s' % q, out)

    def test_line_quoted_inside_a_sentence_does_not_close(self):
        q = self.sent_item()
        self.report('I will reply with "%s" once it is reviewed.' % LINE)
        out = self.poll()
        self.assertNotIn('closed this poll', out)
        self.assertIn('%s sent' % q, out)

    def test_line_in_an_open_turn_does_not_close(self):
        self.sent_item()
        self.report('%s\nnow running one more check' % LINE, stop='tool_use')
        out = self.poll()
        self.assertNotIn('closed this poll', out)

    def test_line_before_delivery_does_not_close(self):
        q = self.queue(text='Build the backend', spec='msg-to-watchdog "%s"' % LINE)
        self.report('Old turn.\n%s' % LINE, at=8)
        self.deliver('Build the backend')
        self.sent(q)
        out = self.poll()
        self.assertNotIn('closed this poll', out)


if __name__ == '__main__':
    unittest.main(verbosity=2)
