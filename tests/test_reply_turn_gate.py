#!/usr/bin/env python3
"""Only a verified message acceptance's actual reply turn gets a relay exemption."""
import json
import unittest
from send_contract_support import ContractCase, C, K, SELF, ts
import test_acceptance_contract as fixtures
import wd_wait as H


class ReplyTurnGate(ContractCase):
    git = fixtures.AcceptanceContract.git

    def finish_reply(self, at=22):
        self.records(dict(type='assistant', timestamp=ts(at), message=dict(
            content=[dict(type='text', text='Synthetic close-out report')], stop_reason='end_turn')))

    def reply_item(self, by_report=False):
        q = fixtures.AcceptanceContract.message_case(self, 'SendMessage')
        self.finish_reply()
        if by_report:
            self.clock = 30
            rc, out = self.cli(C, 'answered', q, '--evidence', 'I verified the delivered close-out and its detail.')
            self.assertEqual(rc, 0, out)
        else:
            self.poll()
        self.assertEqual(self.state()['owner_queue'], [])
        return q

    def queue_next(self):
        self.clock = 35
        q = self.queue('Independent work', 'file independent.txt')
        state = self.state(); state['last_relay_ts'] = ts(1)
        K.save_state(str(self.state_dir), state)
        return q

    def assert_gate(self, ident, send):
        before = self.state()
        rc, out = self.cli(C, 'next')
        self.assertEqual('SEND EXACTLY THIS ONE ITEM' in out, send, out)
        if send:
            self.assertEqual(rc, 0, out)
            self.assertIn('sent1 ' + ident, out)
        rc, due = self.cli(K, '--due')
        self.assertIn('OWNER ITEMS SENDABLE NOW: %d' % int(send), due)
        if send: self.assertIn(ident, due)
        self.assertEqual(self.state(), before, 'reading the gate must not store exemptions')

    def test_verified_reply_turn_is_answered_but_relay_stays_visible(self):
        self.reply_item()
        q = self.queue_next()
        out = self.poll()
        self.assertIn(ts(22) + '  [not relayed]', out)
        self.assertNotIn(ts(22) + '  [not relayed + not answered', out)
        self.assert_gate(q, True)
        self.assertEqual(self.state()['last_relay_ts'], ts(1))
        lines = H.gate_lines(self.sess, str(self.state_dir), SELF)
        self.assertTrue(any('NEXT: SEND ' + q in line for line in lines), lines)

    def test_operator_report_still_needs_actual_matching_reply_for_exemption(self):
        self.reply_item(by_report=True)
        q = self.queue_next()
        self.assert_gate(q, True)
        self.mine.write_text('')
        self.assert_gate(q, False)

    def test_neither_previous_nor_later_unrelayed_turn_is_exempt(self):
        self.reply_item()
        q = self.queue_next()
        self.assert_gate(q, True)
        state = self.state(); state['last_relay_ts'] = ''
        K.save_state(str(self.state_dir), state)
        self.assert_gate(q, False)  # the receipt's PRE-delivery turn is not the reply turn
        state['last_relay_ts'] = ts(1)
        K.save_state(str(self.state_dir), state)
        self.turn('unrelated-later', 31, 32)
        self.assert_gate(q, False)  # neither the latest turn nor a time watermark qualifies
        out = self.poll()
        self.assertIn(ts(22) + '  [not relayed]', out)
        self.assertIn(ts(32) + '  [not relayed + not answered or held]', out)

    def test_commit_acceptance_grants_no_turn_exemption(self):
        spec = fixtures.AcceptanceContract.spec(self, 'commit')
        q = self.queue('Push the synthetic commit', spec)
        self.deliver('Push the synthetic commit'); self.sent(q)
        fixtures.AcceptanceContract.act(self, 'commit')
        self.finish_reply()
        out = self.poll()
        self.assertEqual(self.state()['owner_queue'], [], out)
        self.assertEqual(self.state()['owner_queue_sent'][0]['acted_status'], 'pass')
        b = self.queue_next()
        self.assert_gate(b, False)
        self.assertIn(ts(22) + '  [not relayed + not answered or held]', self.poll())

    def test_nonmessage_prose_answer_grants_no_turn_exemption(self):
        q = self.queue(); self.deliver('Create artifact'); self.sent(q)
        self.finish_reply()
        self.clock = 30
        rc, out = self.cli(C, 'answered', q, '--evidence', 'I verified the file.')
        self.assertEqual(rc, 0, out)
        self.assert_gate(self.queue_next(), False)

    def test_unanswered_message_item_still_blocks(self):
        fixtures.AcceptanceContract.message_case(self, 'SendMessage', receive=False)
        self.finish_reply()
        self.assert_gate(self.queue_next(), False)

    def test_hold_survives_verified_reply_exemption(self):
        self.reply_item()
        q = self.queue_next()
        rc, out = self.cli(K, '--queue-hold', q, '--hold-until', 'Specific prerequisite')
        self.assertEqual(rc, 0, out)
        self.assert_gate(q, False)

    def test_ambiguous_reply_calls_do_not_exempt_either_turn(self):
        self.reply_item()
        # Replace the one receipt with a later delivery, after two identical
        # successful calls in distinct turns. Neither call uniquely explains it.
        self.turn('second-call', 23, 24)
        self.tool('SendMessage', dict(to=SELF, message='Synthetic result'),
                  result=json.dumps(dict(success=True)), at=25)
        self.finish_reply(27)
        rows = [json.loads(line) for line in self.mine.read_text().splitlines()]
        rows[-1]['timestamp'] = ts(28)
        self.mine.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        q = self.queue_next()
        self.assert_gate(q, False)
        out = self.poll()
        self.assertNotIn('relay exempt', out)


if __name__ == '__main__': unittest.main(verbosity=2)
