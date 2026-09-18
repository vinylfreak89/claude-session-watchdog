#!/usr/bin/python3
"""A delivery absorbed into a running turn is still a delivery, and must be citable.

The host writes a message that arrives while the target is mid-turn as a
queue-operation with `reason: absorbed_mid_turn` and NO `uuid`. The receipt layer
addressed deliveries only by uuid and refused queue operations outright, so such a
delivery could never be recorded: `sent1` demanded an id that does not exist for it,
every id offered was refused, and the refusal latched the send gate.

Every fixture here is built by the test. Nothing depends on such a record existing
in a live transcript, so fixing or retiring any real delivery cannot silence it.
"""
import unittest
from send_contract_support import ContractCase, C, SELF, ts
import wd_receipts as D

ENVELOPE = '<cross-session-message from="%s" name="Control">%s</cross-session-message>'


class AbsorbedDelivery(ContractCase):
    def absorbed(self, body, at=10, sender=SELF, operation='remove', reason='absorbed_mid_turn'):
        """Write the host's own shape for a message absorbed into a running turn."""
        record = dict(type='queue-operation', operation=operation, reason=reason,
                      sessionId='target-session', timestamp=ts(at),
                      content=ENVELOPE % (sender, body))
        self.records(record)
        return record

    # --- the defect itself -------------------------------------------------

    def test_absorbed_peer_delivery_is_readable_and_addressable(self):
        record = self.absorbed('Create artifact')
        rec = D.delivery(record, SELF)
        self.assertEqual(rec['body'], 'Create artifact')
        self.assertEqual(rec['ts'], record['timestamp'])
        self.assertTrue(rec['id'].startswith('absorbed:'))
        # The id is derived from the record, so a second reader computes the same one
        # and a caller cannot choose it -- the property uuid was relied on for.
        self.assertEqual(rec['id'], D.absorbed_id(record))
        self.assertEqual(D.receipt(str(self.tx), SELF, rec['id'])['body'], 'Create artifact')

    def test_sent1_records_an_absorbed_delivery_without_latching(self):
        q = self.queue()
        record = self.absorbed('Create artifact')
        rc, out = self.cli(C, 'sent1', q, D.absorbed_id(record))
        self.assertEqual(rc, 0, out)
        self.assertNotIn('receipt_recording_failure', self.state())
        self.assertTrue(self.state()['owner_queue'][0].get('sent'))

    def test_id_follows_content_not_position(self):
        one = self.absorbed('First message', at=10)
        two = self.absorbed('Second message', at=11)
        self.assertNotEqual(D.absorbed_id(one), D.absorbed_id(two))
        # Same content at the same instant in the same session is the same record.
        self.assertEqual(D.absorbed_id(one), D.absorbed_id(dict(one)))

    # --- what must still be refused ---------------------------------------

    def test_enqueue_is_not_a_delivery(self):
        record = self.absorbed('Create artifact', operation='add', reason=None)
        with self.assertRaises(D.EvidenceError):
            D.delivery(record, SELF)

    def test_removal_for_another_reason_is_not_a_delivery(self):
        record = self.absorbed('Create artifact', reason='cancelled_by_user')
        with self.assertRaises(D.EvidenceError):
            D.delivery(record, SELF)

    def test_absorbed_non_message_is_not_a_delivery(self):
        record = dict(type='queue-operation', operation='remove', reason='absorbed_mid_turn',
                      sessionId='target-session', timestamp=ts(10),
                      content='<task-notification><task-id>b1</task-id></task-notification>')
        self.records(record)
        with self.assertRaises(D.EvidenceError):
            D.delivery(record, SELF)

    def test_unattributable_sender_is_refused(self):
        record = self.absorbed('Create artifact', sender='local_someone_else')
        with self.assertRaises(D.EvidenceError):
            D.delivery(record, SELF)

    def test_nested_envelope_is_refused(self):
        record = self.absorbed(ENVELOPE % (SELF, 'smuggled'))
        with self.assertRaises(D.EvidenceError):
            D.delivery(record, SELF)

    def test_unknown_absorbed_id_does_not_resolve(self):
        self.absorbed('Create artifact')
        with self.assertRaises(D.EvidenceError):
            D.receipt(str(self.tx), SELF, 'absorbed:' + '0' * 32)

    # --- the latch this was blamed for stays exactly as strict -------------

    def test_wrong_id_still_latches_and_a_corrected_id_does_not_clear_it(self):
        """Being able to cite the delivery does not make the latch self-clearing.

        A refused recording leaves a send whose record is in an unknown state.
        Recording a DIFFERENT id afterwards is a different delivery, so the gate
        stays stopped for the owner. This is the guard a relaxation removed.
        """
        q = self.queue()
        record = self.absorbed('Create artifact')
        rc, out = self.cli(C, 'sent1', q, 'send-side-msg-id')
        self.assertNotEqual(rc, 0, out)
        failure = self.state()['receipt_recording_failure']
        rc, out = self.cli(C, 'sent1', q, D.absorbed_id(record))
        self.assertEqual(self.state().get('receipt_recording_failure'), failure)
        self.assertIn('STUCK', self.cli(C, 'next')[1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
