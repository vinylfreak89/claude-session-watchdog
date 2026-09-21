"""A reply addressed by NAME counts, and only because it is found in the receiver's transcript.

2026-09-21: the target answered an item by sending to "Watchdog brief builder" instead of the
uds: socket. `message_to_session` accepted only an id or a `uds:` address, so the reply was
invisible, the item stayed OWED, and the send gate kept re-offering something already answered.

The guard that matters is NOT the recipient string -- it is that the message is located in the
RECEIVER'S own records by msg_id and body. This test pins that: a name-addressed call is
credited when the receiver really holds it, and refused when it does not, when the body differs,
and when the send itself failed.

Fixtures are synthesised; nothing outside this file can silence it.
"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wd_lib as W

ME = 'local_aaaaaaaa-0000-0000-0000-000000000001'
BODY = 'the report\n\nCOHERENCE TEST MEASURED'
MID = 'msg-abc123'


def call(recipient, body=BODY, mid=MID, ok=True):
    return {'name': 'SendMessage', 'ts': '2026-09-21T04:26:30.000Z',
            'id': 'tool-1', 'is_error': False if ok else True,
            'input': {'to': recipient, 'message': body},
            'result': json.dumps({'success': True, 'msg_id': mid}) if ok else 'error'}


def receiver_records(tmp, mid=MID, body=BODY):
    """A receiver transcript holding one delivery with that msg_id."""
    path = os.path.join(tmp, 'receiver.jsonl')
    with open(path, 'w') as fh:
        fh.write(json.dumps({'type': 'user', 'uuid': 'u1', 'isMeta': False,
                             'timestamp': '2026-09-21T04:26:31.000Z',
                             'origin': {'msg_id': mid, 'from': 'target-1', 'body': body,
                                        'kind': 'peer', 'name': 'target'},
                             'message': {'role': 'user', 'content':
                                         '<cross-session-message from="target-1" from-name="target" '
                                         'from-mode="prompting">\n%s\n</cross-session-message>' % body}}) + '\n')
    return path


def main():
    tmp = tempfile.mkdtemp()
    path = receiver_records(tmp)
    real_find, real_transcript = W.find_session, W.transcript_path
    W.find_session = lambda sel: {'sessionId': ME}
    W.transcript_path = lambda sess: path
    try:
        # 1. A NAME-addressed reply the receiver really holds is credited.
        assert W.message_to_session(call('Watchdog brief builder'), ME), \
            'name-addressed reply not credited'

        # 2. The socket form still works -- the old path is not regressed.
        assert W.message_to_session(call('uds:/tmp/cc-socks/48630.sock'), ME)

        # 3. An exact id and the bare uuid still short-circuit without needing the transcript.
        assert W.message_to_session(call(ME), ME)
        assert W.message_to_session(call(ME[6:]), ME)

        # 4. THE GUARD: a name whose msg_id the receiver does NOT hold is refused. This is
        #    what stops the widened path from crediting a message that never arrived.
        assert not W.message_to_session(call('Watchdog brief builder', mid='msg-never-sent'), ME), \
            'credited a reply the receiver does not hold'

        # 5. Same msg_id but a DIFFERENT body is refused -- the body must match too.
        assert not W.message_to_session(call('Watchdog brief builder', body='something else'), ME), \
            'credited a reply whose body does not match'

        # 6. A failed send is refused even with a plausible name.
        assert not W.message_to_session(call('Watchdog brief builder', ok=False), ME), \
            'credited a failed send'
        print('ok')
    finally:
        W.find_session, W.transcript_path = real_find, real_transcript


if __name__ == '__main__':
    main()
