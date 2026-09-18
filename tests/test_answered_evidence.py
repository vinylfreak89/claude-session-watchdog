#!/usr/bin/env python3
"""Owner-authorized prose answers on the existing command handler."""
import copy
import unittest
from send_contract_support import ContractCase, C, K, SELF, TARGET, ts


class AnsweredEvidence(ContractCase):
    def delivered_item(self):
        q = self.queue('Create the artifact and report any residue', 'file artifact.txt')
        self.deliver('Create the artifact and report any residue'); self.sent(q)
        self.records(dict(type='assistant', timestamp=ts(20), message=dict(
            content=[dict(type='text', text='Synthetic progress')], stop_reason='end_turn')))
        self.turn('terminal', 21, 22)
        return q

    def answer(self, q, *extra):
        self.clock = 30
        return self.cli(C, 'answered', q, '--evidence', 'I inspected the artifact; the requested result is present.', *extra)

    def require_answer(self, q, *extra):
        rc, out = self.answer(q, *extra)
        self.assertEqual(rc, 0, out)
        return out

    def test_evidence_closes_only_delivered_item_and_retains_original(self):
        q = self.delivered_item()
        original = copy.deepcopy(self.state()['owner_queue'][0])
        self.require_answer(q)
        state = self.state()
        self.assertEqual(state['owner_queue'], [])
        item = state['owner_queue_sent'][0]
        for key, value in original.items(): self.assertEqual(item[key], value)
        report = item['answers'][0]
        self.assertEqual(report['actor'], SELF)
        self.assertEqual(report['target'], TARGET)
        self.assertIn('I inspected', report['evidence'])
        self.assertNotEqual(item.get('acted_status'), 'pass')
        self.assertEqual(state['last_send_ts'], original['sent'])

    def test_undelivered_item_refuses_without_mutation(self):
        q = self.queue()
        before = self.state()
        rc, out = self.answer(q)
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), before)

    def test_empty_evidence_refuses_without_mutation(self):
        q = self.delivered_item()
        before = self.state()
        for evidence in ('', '  '):
            rc, out = self.cli(C, 'answered', q, '--evidence', evidence)
            self.assertNotEqual(rc, 0, out)
            self.assertEqual(self.state(), before)

    def test_reported_residue_is_visible_and_never_owner_obligation(self):
        q = self.delivered_item()
        before = copy.deepcopy(self.state().get('owner_decisions'))
        self.require_answer(q, '--open', 'The separate review is still open.')
        out = self.poll()
        self.assertIn('The separate review is still open.', out)
        self.assertIn('I inspected the artifact', out)
        self.assertIn('Create the artifact and report any residue', out)
        self.assertIn('file artifact.txt', out)
        self.assertEqual(self.state().get('owner_decisions'), before)
        self.assertIn('The separate review is still open.', self.poll())

    def test_repeated_answer_appends_without_overwriting(self):
        q = self.delivered_item()
        self.require_answer(q, '--open', 'First residue')
        first = copy.deepcopy(self.state()['owner_queue_sent'][0]['answers'][0])
        self.require_answer(q, '--open', 'Later report')
        answers = self.state()['owner_queue_sent'][0]['answers']
        self.assertEqual(len(answers), 2)
        self.assertEqual(answers[0], first)
        out = self.poll()
        self.assertIn('First residue', out)
        self.assertIn('Later report', out)

    def test_missing_registered_receipt_refuses(self):
        q = self.delivered_item()
        state = self.state(); state['send_receipts'] = {}
        K.save_state(str(self.state_dir), state)
        before = self.state()
        rc, out = self.answer(q)
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state(), before)

    def test_legacy_single_receipt_route_is_unchanged(self):
        q = self.delivered_item()
        rc, out = self.cli(C, 'answered', 'delivery-1')
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.state()['owner_queue']), 1)
        self.assertNotIn('answers', self.state()['owner_queue'][0])

    def test_conflicting_routes_and_repeated_options_refuse(self):
        q = self.delivered_item()
        before = self.state()
        for extra in (('--owner-ack', 'Synthetic acknowledgement'), ('--evidence', 'Second evidence'),
                      ('--span', ts(20), ts(22))):
            rc, out = self.answer(q, *extra)
            self.assertNotEqual(rc, 0, out)
            self.assertEqual(self.state(), before)

    def test_corrupt_answer_history_cannot_release_gate(self):
        q = self.delivered_item()
        self.require_answer(q)
        self.clock = 35
        self.queue('Independent work', 'file independent.txt')
        state = self.state()
        state['owner_queue_sent'][0]['answers'][0]['evidence'] = ''
        K.save_state(str(self.state_dir), state)
        rc, out = self.cli(C, 'next')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('STUCK', out)
        self.assertNotIn('SEND EXACTLY THIS ONE ITEM', out)
        rc, out = self.cli(K, '--due')
        self.assertIn('OWNER ITEMS SENDABLE NOW: 0', out)
        self.assertIn('STUCK', out)


if __name__ == '__main__': unittest.main(verbosity=2)
