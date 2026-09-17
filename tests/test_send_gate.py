#!/usr/bin/python3
"""Exercise the send gate's command handler with real queued requirements and transcripts."""
import unittest
from unittest.mock import patch
from send_contract_support import ContractCase, C, K, W, ts
import wd_receipts as D

class SendGate(ContractCase):
    def finish_peer_turn(self):
        self.records(dict(type='assistant', timestamp=ts(20), message=dict(role='assistant',
            content=[dict(type='text', text='Synthetic completed audit')], stop_reason='end_turn')))

    def assert_stuck(self, reason):
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('STUCK', out)
        self.assertIn(reason, out)
        self.assertNotIn('SEND EXACTLY THIS ONE ITEM', out)
        rc, out = self.cli(K, '--due')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('STUCK', out)
        self.assertIn(reason, out)
        self.assertIn('OWNER ITEMS SENDABLE NOW: 0', out)

    def test_refused_receipt_stops_next_and_due(self):
        q = self.queue()
        rc, out = self.cli(C, 'sent1', q, 'missing-delivery')
        self.assertNotEqual(rc, 0, out)
        self.assert_stuck('delivery uuid')
        # Owner urgency changes pacing, not the ability to keep an honest record.
        rc, out = self.cli(K, '--queue-add', 'Urgent second item', '--queue-urgent', '--acted-when', 'file urgent.txt')
        self.assertEqual(rc, 0, out)
        self.assert_stuck('delivery uuid')

    def test_unavailable_validation_stops_next_and_due(self):
        self.queue()
        self.deliver('Unrelated peer message')
        self.finish_peer_turn()
        with patch.object(D, 'delivery', side_effect=D.EvidenceError('synthetic validator unavailable')):
            self.assert_stuck('synthetic validator unavailable')

    def test_unknown_item_receipt_refusal_stops_gate(self):
        self.queue()
        rc, out = self.cli(C, 'sent1', 'Q999', 'unrecorded-message')
        self.assertNotEqual(rc, 0, out)
        self.assert_stuck('no queued item Q999')

    def test_finding_and_answered_refusals_stop_gate(self):
        self.queue()
        before = self.state()
        for module, args in ((K, ('--sent', 'F1')),
                             (K, ('--sent', 'F1', '--message-id', 'missing')),
                             (C, ('answered', 'missing'))):
            with self.subTest(args=args):
                K.save_state(str(self.state_dir), before)
                rc, out = self.cli(module, *args)
                self.assertNotEqual(rc, 0, out)
                self.assert_stuck('receipt recording refused')

    def test_delivered_unrecorded_item_stops_without_credit_or_backfill(self):
        self.queue()
        self.deliver('Create artifact')
        self.finish_peer_turn()
        before = self.state()
        self.assert_stuck('unrecorded delivery')
        self.assertEqual(self.state(), before)
        self.assertNotIn('send_receipts', self.state())
        self.assertFalse(self.state()['owner_queue'][0].get('sent'))

    def test_recorded_delivery_is_not_renominated(self):
        q = self.queue()
        self.deliver('Create artifact')
        self.sent(q)
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn('SEND EXACTLY THIS ONE ITEM', out)
        rc, out = self.cli(K, '--due')
        self.assertEqual(rc, 0, out)
        self.assertIn('OWNER ITEMS SENDABLE NOW: 0', out)

    def test_working_receipts_leave_unsent_item_sendable_in_due(self):
        q = self.queue()
        self.deliver('Unrelated peer message')
        self.finish_peer_turn()
        rc, out = self.cli(K, '--due')
        self.assertEqual(rc, 0, out)
        self.assertIn('OWNER ITEMS SENDABLE NOW: 1', out)
        self.assertIn(q, out)
        rc, out = self.cli(C, 'next')
        self.assertEqual(rc, 0, out)
        self.assertIn('sent1 ' + q, out)

    def test_later_success_does_not_erase_recording_failure(self):
        q = self.queue()
        rc, out = self.cli(C, 'sent1', q, 'missing-delivery')
        self.assertNotEqual(rc, 0, out)
        failure = self.state()['receipt_recording_failure']
        self.deliver('Create artifact'); self.sent(q)
        self.assertEqual(self.state()['receipt_recording_failure'], failure)
        self.assert_stuck('missing-delivery')

    def test_unreadable_transcript_stops_gate(self):
        self.queue()
        self.tx.write_text('{invalid json\n')
        self.assert_stuck('cannot read complete transcript')

    def test_malformed_failure_record_cannot_reopen_gate(self):
        self.queue()
        for fault in (None, '', [], {}):
            s = self.state(); s['receipt_recording_failure'] = fault
            K.save_state(str(self.state_dir), s)
            self.assert_stuck('failure metadata is unreadable')

    def test_malformed_multiline_possible_delivery_also_stops(self):
        text = 'First synthetic line\nSecond synthetic line'
        self.queue(text)
        self.deliver('Unrelated peer message', ident='other', at=8)
        self.deliver(text + '\n<cross-session-message from="other">nested</cross-session-message>')
        self.finish_peer_turn()
        self.assert_stuck('unrecorded delivery')

    def test_wake_report_uses_same_stuck_gate(self):
        self.queue(); self.deliver('Create artifact'); self.finish_peer_turn()
        rc, out = self.cli(K, '--trigger', 'CONTROL')
        self.assertEqual(rc, 0, out)
        self.assertIn('GATE: STUCK:', out)
        self.assertNotIn('OWNER ITEM SELECTED', out)
        self.assertNotIn('queue clear', out)

    def assert_work_blocks_both(self):
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn('SEND EXACTLY', out)
        rc, out = self.cli(K, '--due')
        self.assertEqual(rc, 0, out)
        self.assertIn('OWNER ITEMS SENDABLE NOW: 0', out)

    def test_shared_gate_preserves_due_work_set(self):
        self.queue()
        before = self.state()
        for field, value in (('proposed', {'F1': dict(message='Pending finding', ts=ts(5))}),
                             ('in_flight', [dict(kind='bg', id='pending-task')])):
            with self.subTest(field=field):
                K.save_state(str(self.state_dir), dict(before, **{field: value}))
                self.assert_work_blocks_both()
        K.save_state(str(self.state_dir), before)
        with patch.object(W, 'live_children', return_value=[dict(pid=999)]):
            self.assert_work_blocks_both()

    def test_shared_gate_preserves_due_recent_activity(self):
        self.queue()
        with patch.object(C.time, 'time', return_value=W.epoch_from_iso(ts(10))):
            self.assert_work_blocks_both()

    def test_unanswered_relayed_turn_allows_one_ready_item(self):
        q = self.queue()
        rc, out = self.cli(C, 'next')
        self.assertEqual(rc, 0, out)
        self.assertIn('SEND EXACTLY THIS ONE ITEM', out)
        self.assertIn('sent1 ' + q, out)

    def test_open_turn_blocks_send(self):
        self.queue()
        self.records(dict(type='user', promptId='open', timestamp=ts(6), message=dict(role='user', content='More work')))
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('TARGET BUSY', out)

    def test_unrelayed_turn_blocks_send(self):
        self.queue()
        state = self.state(); state.pop('last_relay_ts', None)
        K.save_state(str(self.state_dir), state)
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('UNRELAYED', out)

    def test_held_item_is_skipped_for_ready_item(self):
        held = self.queue('Held request')
        rc, out = self.cli(K, '--queue-hold', held, '--hold-until', 'Owner decision')
        self.assertEqual(rc, 0, out)
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('HELD', out)
        ready = self.queue('Ready request', 'file second.txt')
        rc, out = self.cli(C, 'next')
        self.assertEqual(rc, 0, out)
        self.assertIn('sent1 ' + ready, out)
        self.assertNotIn('Held request', out)

    def test_empty_queue_sends_nothing(self):
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn('SEND EXACTLY', out)

    def test_owner_urgent_pacing_exception_remains(self):
        rc, out = self.cli(K, '--queue-add', 'Urgent request', '--queue-urgent', '--acted-when', 'file urgent.txt')
        self.assertEqual(rc, 0, out)
        self.records(dict(type='user', promptId='open', timestamp=ts(6), message=dict(role='user', content='More work')))
        state = self.state(); state.pop('last_relay_ts', None)
        K.save_state(str(self.state_dir), state)
        rc, out = self.cli(C, 'next')
        self.assertEqual(rc, 0, out)
        self.assertIn('URGENT', out)
        self.assertIn('SEND EXACTLY', out)
        self.assertFalse(self.state()['owner_queue'][0].get('sent'))

if __name__ == '__main__': unittest.main(verbosity=2)
