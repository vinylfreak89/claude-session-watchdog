#!/usr/bin/python3
"""A real standalone reply can answer exactly one named target turn."""
import unittest
from send_contract_support import ContractCase, C, K, SELF, ts


class AcknowledgementContract(ContractCase):
    def setUp(self):
        super().setUp()
        self.records(dict(type='assistant', timestamp=ts(2), message=dict(
            content=[dict(type='text', text='Which option should I use?')], stop_reason='end_turn')))
        self.clock = 30

    def answer(self, stamp=None, ident='ack', prose='Acknowledged the request for an option'):
        return self.cli(C, 'answered', stamp or ts(2), '--acknowledged', ident, prose)

    def test_real_later_reply_answers_question_without_queue_or_finding(self):
        self.deliver('I acknowledge your request for an option.', ident='ack')
        self.assertIn(ts(2) + '  [not answered or held]', self.poll())
        rc, out = self.answer()
        self.assertEqual(rc, 0, out)
        out = self.poll()
        self.assertIn('OWED completed turns: 0', out)
        self.assertIn('Acknowledged the request for an option', out)
        entry = self.state()['acknowledged_turns'][0]
        self.assertEqual(entry['actor'], SELF)
        for key in ('at', 'turn_ts', 'turn_hash', 'message_id', 'delivery_ts', 'body_hash', 'reason'):
            self.assertTrue(entry.get(key), key)

    def test_predating_or_simultaneous_delivery_refused(self):
        for at in (1, 2):
            with self.subTest(at=at):
                ident = self.deliver('Acknowledgement', ident='early-%s' % at, at=at)
                before = self.state()
                rc, out = self.answer(ident=ident)
                self.assertNotEqual(rc, 0, out)
                self.assertEqual(self.state(), before)

    def test_non_delivery_and_wrong_sender_refused(self):
        self.deliver('Ordinary text', ident='not-peer', origin=False)
        self.deliver('Wrong peer', ident='wrong-peer')
        self.tx.write_text(self.tx.read_text().replace(SELF, 'local_other_sender'))
        for ident in ('missing', 'not-peer', 'wrong-peer'):
            rc, out = self.answer(ident=ident)
            self.assertNotEqual(rc, 0, out)
        self.assertIn(ts(2) + '  [not answered or held]', self.poll())

    def test_empty_prose_refused(self):
        self.deliver('Acknowledgement', ident='ack')
        rc, out = self.answer(prose='   ')
        self.assertNotEqual(rc, 0, out)
        self.assertIn(ts(2) + '  [not answered or held]', self.poll())

    def test_without_acknowledgement_question_remains_owed(self):
        self.assertIn(ts(2) + '  [not answered or held]', self.poll())

    def test_question_still_cannot_be_held_or_closed(self):
        for mode in ('hold', 'closed'):
            rc, out = self.cli(C, mode, ts(2), 'Blocked on owner')
            self.assertNotEqual(rc, 0, out)
            self.assertIn('question', out)

    def test_exact_turn_only_and_relay_is_preserved(self):
        self.turn('later', 3, 4)
        self.deliver('Acknowledgement', ident='ack')
        state = self.state(); state['last_relay_ts'] = ''
        K.save_state(str(self.state_dir), state)
        rc, out = self.answer()
        self.assertEqual(rc, 0, out)
        out = self.poll()
        self.assertIn(ts(2) + '  [not relayed]', out)
        self.assertIn(ts(4) + '  [not relayed + not answered or held]', out)

    def test_history_is_appended_and_delivery_cannot_answer_another_turn(self):
        self.turn('later', 3, 4)
        self.deliver('Acknowledgement', ident='ack')
        self.assertEqual(self.answer()[0], 0)
        first = self.state()['acknowledged_turns'][0]
        self.assertNotEqual(self.answer(stamp=ts(4))[0], 0)
        self.deliver('More detail', ident='ack-2', at=11)
        self.assertEqual(self.answer(ident='ack-2', prose='Further explanation')[0], 0)
        self.assertEqual(self.state()['acknowledged_turns'][0], first)
        self.assertEqual(len(self.state()['acknowledged_turns']), 2)

    def test_changed_delivery_invalidates_credit(self):
        self.deliver('Acknowledgement', ident='ack')
        self.assertEqual(self.answer()[0], 0)
        self.tx.write_text(self.tx.read_text().replace('Acknowledgement', 'Altered delivery'))
        out = self.poll()
        self.assertIn(ts(2) + '  [not answered or held]', out)
        self.assertIn('INVALID ACKNOWLEDGEMENT', out)

    def test_acknowledgement_does_not_settle_delivered_queue_work(self):
        self.clock = 5
        qid = self.queue()
        self.deliver('Create artifact')
        self.sent(qid)
        self.deliver('Acknowledgement', ident='ack', at=16)
        self.clock = 30
        before = self.state()
        rc, out = self.answer()
        self.assertEqual(rc, 0, out)
        self.assertIn('SENT, NOT YET ACTED ON: 1', self.poll())
        for key in ('owner_queue', 'send_receipts', 'last_send_ts'):
            self.assertEqual(self.state().get(key), before.get(key), key)

    def test_ambiguous_delivery_and_nonexistent_turn_refused(self):
        self.deliver('Acknowledgement', ident='ack')
        rc, out = self.answer(stamp=ts(999))
        self.assertNotEqual(rc, 0, out)
        self.assertIn('exactly one completed target turn', out)
        self.deliver('Acknowledgement', ident='ack', at=11)
        rc, out = self.answer()
        self.assertNotEqual(rc, 0, out)
        self.assertIn('exactly one transcript record', out)

    def test_one_delivery_cannot_answer_two_turns_by_switching_routes(self):
        self.clock = 5
        self.queue()
        self.turn('later', 6, 7)
        self.deliver('Create artifact', ident='ack')
        before = self.state()
        self.clock = 30
        rc, out = self.answer()
        self.assertEqual(rc, 0, out)
        rc, out = self.cli(C, 'answered', 'ack')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('different turn', out)
        # Reverse the ordering in the same isolated fixture.
        K.save_state(str(self.state_dir), before)
        rc, out = self.cli(C, 'answered', 'ack')
        self.assertEqual(rc, 0, out)
        rc, out = self.answer()
        self.assertNotEqual(rc, 0, out)
        self.assertIn('another turn', out)
        rc, out = self.answer(stamp=ts(7))
        self.assertEqual(rc, 0, out)


if __name__ == '__main__':
    unittest.main()
