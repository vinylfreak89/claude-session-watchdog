#!/usr/bin/python3
"""An owner answer to a request the target is blocked on passes the OWED gate; nothing else does.

Synthesises the 2026-10-17 deadlock: Q1 delivered and not yet accepted, the target ends its turn
asking the owner for something, he answers, and the gate refused the answer because Q1 was owed --
which it could not stop being without the answer. Owner-approved boundary (wd_answers.py).
"""
import unittest
from send_contract_support import ContractCase, C, K, W, TARGET, ts

ASK = 'Can you plug the Shuttle in and quit OBS?'
HIS = 'shuttle is plugged in. bars running for roughly 95 minutes'


class OwnerAnswerPassthrough(ContractCase):
    def blocked_on_owner(self, text=ASK, at=20):
        q1 = self.queue()
        self.deliver('Create artifact')
        self.sent(q1)
        self.records(dict(type='assistant', uuid='a-ask', timestamp=ts(at), message=dict(
            role='assistant', content=[dict(type='text', text='The probe is ready. ' + text)], stop_reason='end_turn')))
        return q1

    def his_message(self, words=HIS, at=30, path=None):
        self.records(dict(type='user', uuid='his-%d' % at, promptId='his-%d' % at, timestamp=ts(at),
                          origin=dict(kind='human'), message=dict(role='user', content=words)),
                     path=path or self.mine)

    def ask(self, stamp=None, quote=ASK):
        self.clock = 25
        return self.cli(K, '--owe-ask', stamp or ts(20), '--quote', quote)

    def answer(self, did, words=HIS):
        self.clock = 32
        return self.cli(K, '--owe-answer', did, '--quote', words)

    def open_and_answer(self):
        q1 = self.blocked_on_owner()
        self.his_message()
        rc, out = self.ask()
        self.assertEqual(rc, 0, out)
        did = out.split()[1].rstrip(':')
        rc, out = self.answer(did)
        self.assertEqual(rc, 0, out)
        return q1, did, out.split()[1].rstrip(':')

    def test_deadlock_answer_passes_owed_gate(self):
        q1, did, qa = self.open_and_answer()
        rc, out = self.cli(C, 'next')
        self.assertEqual(rc, 0, out)
        self.assertIn('SEND EXACTLY THIS ONE ITEM', out)
        self.assertIn('sent1 ' + qa, out)
        self.assertIn('OWNER ANSWER', out)
        # delivering it closes the decision, logs the passthrough, and leaves Q1 owed
        text = next(x for x in self.state()['owner_queue'] if x['id'] == qa)['text']
        self.deliver(text, ident='delivery-2', at=35)
        self.clock = 36
        rc, out = self.cli(C, 'sent1', qa, 'delivery-2')
        self.assertEqual(rc, 0, out)
        self.assertIn('PASSTHROUGH', out)
        st = self.state()
        self.assertTrue(st['owner_decisions'][did]['answered'])
        self.assertTrue(any(x['id'] == q1 and x.get('sent') for x in st['owner_queue']))
        self.assertFalse(any(x['id'] == qa for x in st['owner_queue']))
        self.assertEqual(len(st['answer_passthroughs']), 1)
        self.assertIn('while %s still owed' % q1, st['answer_passthroughs'][0])

    def test_control_ordinary_item_stays_owed(self):
        self.blocked_on_owner()
        self.his_message()
        self.queue(text='A new task, not an answer')
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('SENT WORK IS NOT YET VERIFIED', out)

    def test_later_completed_turn_makes_answer_ineligible(self):
        self.open_and_answer()
        self.records(dict(type='user', uuid='u-later', promptId='later', timestamp=ts(40), origin=dict(kind='human'),
                          message=dict(role='user', content='something else')),
                     dict(type='assistant', uuid='a-later', timestamp=ts(45), message=dict(
                         role='assistant', content=[dict(type='text', text='Moved on.')], stop_reason='end_turn')))
        st = self.state(); st['last_relay_ts'] = ts(599); K.save_state(str(self.state_dir), st)
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn('SEND EXACTLY', out)
        self.assertIn('SENT WORK IS NOT YET VERIFIED', out)

    def test_answer_words_must_be_his_and_after_the_turn(self):
        self.blocked_on_owner()
        rc, out = self.ask()
        self.assertEqual(rc, 0, out)
        did = out.split()[1].rstrip(':')
        # missing entirely
        rc, out = self.answer(did)
        self.assertNotEqual(rc, 0, out); self.assertIn('not in any of his messages', out)
        # only before the turn ended
        self.his_message(at=15)
        rc, out = self.answer(did)
        self.assertNotEqual(rc, 0, out); self.assertIn('not in any of his messages', out)
        # only in the watchdog's own outbound copy (an assistant tool_use), never his record
        self.records(dict(type='assistant', uuid='mine', timestamp=ts(31), message=dict(role='assistant', content=[
            dict(type='tool_use', id='t', name='SendMessage', input=dict(to='x', message=HIS))], stop_reason='tool_use')),
            path=self.mine)
        rc, out = self.answer(did)
        self.assertNotEqual(rc, 0, out); self.assertIn('not in any of his messages', out)
        # a compaction summary (user record, no origin) quoting him, and a peer record carrying the words
        self.records(dict(type='user', uuid='summary', timestamp=ts(33), isCompactSummary=True,
                          message=dict(role='user', content='Summary: he said ' + HIS)), path=self.mine)
        self.records(dict(type='user', uuid='peer', timestamp=ts(34), origin=dict(kind='peer'),
                          message=dict(role='user', content='<cross-session-message>' + HIS + '</cross-session-message>')),
                     path=self.mine)
        rc, out = self.answer(did)
        self.assertNotEqual(rc, 0, out); self.assertIn('not in any of his messages', out)

    def test_ask_refused_without_question_or_quote(self):
        self.blocked_on_owner(text='Done with the probe.')
        rc, out = self.ask(quote='Done with the probe.')
        self.assertNotEqual(rc, 0, out); self.assertIn('nothing was asked', out)
        rc, out = self.ask(quote='words the target never wrote')
        self.assertNotEqual(rc, 0, out); self.assertIn('verbatim', out)

    def test_open_turn_still_busy_for_an_answer(self):
        self.open_and_answer()
        self.records(dict(type='user', uuid='u-open', promptId='open2', timestamp=ts(50), origin=dict(kind='human'),
                          message=dict(role='user', content='more')))
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn('SEND EXACTLY', out)
        self.assertTrue('TARGET BUSY' in out or 'TARGET STATE UNKNOWN' in out, out)

    def test_second_answer_and_unlinked_decision_refused(self):
        _, did, _ = self.open_and_answer()
        rc, out = self.answer(did)
        self.assertNotEqual(rc, 0, out); self.assertIn('already has a queued answer', out)
        rc, out = self.cli(K, '--owe-add', 'an old free-text decision')
        self.assertEqual(rc, 0, out)
        old = out.split()[1]
        rc, out = self.answer(old)
        self.assertNotEqual(rc, 0, out); self.assertIn('not an open ask', out)

    def test_unrelayed_turn_cannot_open_an_ask(self):
        self.blocked_on_owner()
        st = self.state(); st['last_relay_ts'] = ts(5); K.save_state(str(self.state_dir), st)
        rc, out = self.ask()
        self.assertNotEqual(rc, 0, out); self.assertIn('relay the turn', out)

    def test_whole_message_travels_not_just_the_quote(self):
        self.blocked_on_owner()
        self.his_message(words='yes but not until the bars finish')
        rc, out = self.ask(); did = out.split()[1].rstrip(':')
        rc, out = self.answer(did, words='yes')
        self.assertEqual(rc, 0, out)
        item = [x for x in self.state()['owner_queue'] if x.get('kind') == 'answer'][0]
        self.assertIn('yes but not until the bars finish', item['text'])

    def test_held_answer_is_not_sent(self):
        _, _, qa = self.open_and_answer()
        rc, out = self.cli(K, '--queue-hold', qa, '--hold-until', 'he checks the cable')
        self.assertEqual(rc, 0, out)
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('held: he checks the cable', out)

    def test_pending_findings_still_block_an_answer(self):
        self.open_and_answer()
        st = self.state(); st['proposed'] = {'F1': dict(key='x:1', message='a finding', ts=ts(31))}
        K.save_state(str(self.state_dir), st)
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out); self.assertNotIn('SEND EXACTLY', out)

    def test_unrecorded_answer_delivery_is_stuck(self):
        _, _, qa = self.open_and_answer()
        text = next(x for x in self.state()['owner_queue'] if x['id'] == qa)['text']
        self.deliver(text, ident='delivery-2', at=35)
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out); self.assertIn('STUCK', out)

    def test_stale_answer_reason_shown_and_out_of_gate_delivery_logged_as_such(self):
        _, did, qa = self.open_and_answer()
        self.records(dict(type='user', uuid='u-later', promptId='later', timestamp=ts(40), origin=dict(kind='human'),
                          message=dict(role='user', content='something else')),
                     dict(type='assistant', uuid='a-later', timestamp=ts(45), message=dict(
                         role='assistant', content=[dict(type='text', text='Moved on.')], stop_reason='end_turn')))
        rc, out = self.cli(C, 'next')
        self.assertIn('not eligible', out); self.assertIn('later turn', out)
        text = next(x for x in self.state()['owner_queue'] if x['id'] == qa)['text']
        self.deliver(text, ident='delivery-2', at=50)
        self.clock = 51
        rc, out = self.cli(C, 'sent1', qa, 'delivery-2')
        self.assertEqual(rc, 0, out)
        self.assertIn('OUTSIDE the gate', self.state()['answer_passthroughs'][-1])

    def test_unblocked_item_still_settles_after_the_answer(self):
        q1 = self.queue(text='Run the probe', spec='msg-to-watchdog "PROBE RUN"')
        self.deliver('Run the probe')
        self.sent(q1)
        self.records(dict(type='assistant', uuid='a-ask', timestamp=ts(20), message=dict(
            role='assistant', content=[dict(type='text', text='The probe is ready. ' + ASK)], stop_reason='end_turn')))
        self.his_message()
        rc, out = self.ask(); did = out.split()[1].rstrip(':')
        rc, out = self.answer(did); qa = out.split()[1].rstrip(':')
        text = next(x for x in self.state()['owner_queue'] if x['id'] == qa)['text']
        self.deliver(text, ident='delivery-2', at=35)
        self.clock = 36
        rc, out = self.cli(C, 'sent1', qa, 'delivery-2'); self.assertEqual(rc, 0, out)
        self.tool('mcp__ccd_session_mgmt__send_message', dict(session_id='local_control_watchdog',
                  message='Probe done.\nPROBE RUN'), result='delivered', at=40)
        self.records(dict(type='user', uuid='reply-1', timestamp=ts(42), origin=dict(kind='peer', **{'from': TARGET}),
                          message=dict(role='user', content='<cross-session-message from="%s">Probe done.\nPROBE RUN'
                                       '</cross-session-message>' % TARGET)), path=self.mine)
        out = self.poll()
        self.assertIn('closed this poll: %s' % q1, out)


if __name__ == '__main__':
    unittest.main(verbosity=2)
