#!/usr/bin/python3
"""A reply that reaches the WATCHDOG while it is mid-turn is still a reply.

The send-side half of this was repaired in 4c82e7a: a delivery absorbed into the
target's running turn has no uuid and no origin block. The same host behaviour applies
in the other direction. When the target answers over a socket address and the
watchdog is busy, its transcript records an absorbed queue removal with no origin, so
there is no msg_id to join on. The resolver required one, reported "none", and the
item waiting on that reply stayed NOT_YET although the exact line had arrived.
Measured 2026-09-18 on Q15.

Every record here is built by the test.
"""
import json
import unittest
from send_contract_support import ContractCase, C, K, SELF, ts

SOCKET = 'uds:/tmp/cc-socks/control.sock'
LINE = 'RULES RECEIVED: premise, falsifier, verdict on the premise'


class AbsorbedReply(ContractCase):
    def item(self):
        rc, out = self.cli(K, '--queue-add', 'Rules', '--acted-when', 'msg-to-watchdog "%s"' % LINE)
        self.assertEqual(rc, 0, out)
        qid = self.state()['owner_queue'][-1]['id']
        self.deliver('Rules')
        self.sent(qid)
        return qid

    def target_replies(self, text=LINE, at=20, msg_id='m-1'):
        self.tool('SendMessage', dict(to=SOCKET, message=text), at=at,
                  result=json.dumps(dict(success=True, message='queued there', msg_id=msg_id)))

    def watchdog_absorbs(self, text=LINE, at=22, operation='remove', reason='absorbed_mid_turn'):
        body = '<cross-session-message from="uds:/tmp/cc-socks/target.sock" from-name="Target">\n%s\n</cross-session-message>' % text
        self.records(dict(type='queue-operation', operation=operation, reason=reason,
                          sessionId=SELF, timestamp=ts(at), content=body), path=self.mine)

    def settled(self, qid):
        self.poll()
        return not any(i['id'] == qid for i in self.state().get('owner_queue', []))

    def test_reply_absorbed_into_a_busy_watchdog_settles(self):
        qid = self.item()
        self.target_replies()
        self.watchdog_absorbs()
        self.assertTrue(self.settled(qid))

    def test_enqueue_alone_is_not_receipt(self):
        qid = self.item()
        self.target_replies()
        self.watchdog_absorbs(operation='enqueue', reason=None)
        self.assertFalse(self.settled(qid))

    def test_a_different_body_is_not_receipt(self):
        qid = self.item()
        self.target_replies()
        self.watchdog_absorbs(text='RULES RECEIVED')
        self.assertFalse(self.settled(qid))

    def test_receipt_before_the_call_is_not_receipt(self):
        qid = self.item()
        self.watchdog_absorbs(at=18)
        self.target_replies(at=20)
        self.assertFalse(self.settled(qid))

    def test_two_matching_removals_are_ambiguous_and_credit_nothing(self):
        qid = self.item()
        self.target_replies()
        self.watchdog_absorbs(at=22)
        self.watchdog_absorbs(at=23)
        self.assertFalse(self.settled(qid))

    def test_failed_send_is_not_a_reply(self):
        qid = self.item()
        self.tool('SendMessage', dict(to=SOCKET, message=LINE), at=20,
                  result=json.dumps(dict(success=False, message='refused')))
        self.watchdog_absorbs()
        self.assertFalse(self.settled(qid))


if __name__ == '__main__':
    unittest.main(verbosity=2)
