"""Synthetic records in the REAL transcript shapes. Every builder here mirrors a shape measured
on the real record (see wd_recon_lib 'record-shape truth'); none is invented. The first fixture
invented an attachment shape and passed while the extractor read nothing on real data."""
import json

N = [0]


def _uid():
    N[0] += 1
    return 'toolu_%06d' % N[0]


def enqueue(ts, text):
    return {'type': 'queue-operation', 'operation': 'enqueue', 'timestamp': ts,
            'sessionId': 's', 'content': text}


def dequeue(ts):
    return {'type': 'queue-operation', 'operation': 'dequeue', 'timestamp': ts, 'sessionId': 's'}


def remove(ts):
    return {'type': 'queue-operation', 'operation': 'remove', 'timestamp': ts, 'sessionId': 's'}


def queued_command(ts, text):
    """A mid-turn delivery. Real ones DUPLICATE an earlier enqueue of the same text."""
    return {'type': 'attachment', 'timestamp': ts, 'uuid': 'u-' + ts,
            'attachment': {'type': 'queued_command', 'prompt': text, 'commandMode': 'prompt',
                           'timestamp': ts},
            'rendered': [{'content': '<system-reminder>\nThe user sent a new message</system-reminder>'}]}


def user_str(ts, text, meta=False):
    r = {'type': 'user', 'timestamp': ts, 'uuid': 'u-' + ts,
         'message': {'role': 'user', 'content': text}}
    if meta:
        r['isMeta'] = True
    return r


def owner_turn(ts, text):
    """A message typed while idle: enqueue, dequeue, then the user record that opens the turn."""
    return [enqueue(ts, text), dequeue(ts), user_str(ts, text)]


def owner_midturn(ts, text):
    """A message typed mid-turn: enqueue, remove, then the queued_command attachment."""
    return [enqueue(ts, text), remove(ts), queued_command(ts, text)]


def bash(ts, command, result, is_error=False):
    """An assistant Bash call AND the user record carrying its tool_result -- the outcome."""
    uid = _uid()
    return [{'type': 'assistant', 'timestamp': ts,
             'message': {'role': 'assistant', 'content': [
                 {'type': 'tool_use', 'id': uid, 'name': 'Bash', 'input': {'command': command}}]}},
            {'type': 'user', 'timestamp': ts,
             'message': {'role': 'user', 'content': [
                 {'type': 'tool_result', 'tool_use_id': uid, 'content': result,
                  'is_error': is_error}]}}]


def bash_no_result(ts, command):
    """A command whose result never arrived -- interrupted, or the session died."""
    return [{'type': 'assistant', 'timestamp': ts,
             'message': {'role': 'assistant', 'content': [
                 {'type': 'tool_use', 'id': _uid(), 'name': 'Bash', 'input': {'command': command}}]}}]


def say(ts, text):
    """My visible text to the owner."""
    return [{'type': 'assistant', 'timestamp': ts,
             'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}}]


def send(ts, message, result='Message delivered (delivery: delivered; message_id: m)',
         to='local_target'):
    """A send_message call. Its destination is `session_id`, the key every real send carries."""
    uid = _uid()
    return [{'type': 'assistant', 'timestamp': ts,
             'message': {'role': 'assistant', 'content': [
                 {'type': 'tool_use', 'id': uid, 'name': 'mcp__ccd_session_mgmt__send_message',
                  'input': {'session_id': to, 'message': message}}]}},
            {'type': 'user', 'timestamp': ts,
             'message': {'role': 'user', 'content': [
                 {'type': 'tool_result', 'tool_use_id': uid, 'content': result}]}}]


def peer_reply(ts, text, frm='local_target'):
    """The target answering me: it arrives as a cross-session message, NOT the owner."""
    body = 'Another Claude session sent a message: <cross-session-message from="%s" name="t">%s</cross-session-message>' % (frm, text)
    return [enqueue(ts, body), dequeue(ts), user_str(ts, body)]


def task_note(ts):
    body = '<task-notification>\n<task-id>b1</task-id>\n<summary>Monitor event</summary>\n</task-notification>'
    return [enqueue(ts, body), dequeue(ts), user_str(ts, body)]


def write(path, recs):
    with open(path, 'w') as f:
        for r in recs:
            f.write(json.dumps(r) + '\n')


# ---------------------------------------------------------------- the TARGET's side, in its real shapes
# Measured on the real target transcript: my messages arrive wrapped in a cross-session-message
# from my session, as a `user` record after a dequeue (target idle) or as a queued_command
# attachment whose `rendered` carries the wrapper (target mid-turn); an enqueue precedes both.

def _wrap(frm, body):
    return '<cross-session-message from="%s" name="Watchdog">\n%s\n</cross-session-message>' % (frm, body)


def target_receives(ts, frm, body, midturn=False):
    w = _wrap(frm, body)
    if midturn:
        return [enqueue(ts, w), remove(ts),
                {'type': 'attachment', 'timestamp': ts, 'uuid': 'a-' + ts,
                 'attachment': {'type': 'queued_command', 'prompt': w, 'commandMode': 'prompt'},
                 'rendered': [{'content': 'Another Claude session sent a message while you were working: ' + w}]}]
    return [enqueue(ts, w), dequeue(ts), user_str(ts, 'Another Claude session sent a message: ' + w)]


def target_receives_batch(ts, frm, bodies):
    """Several queued messages delivered in ONE user record (measured: 17 such records)."""
    ws = [_wrap(frm, b) for b in bodies]
    return [enqueue(ts, w) for w in ws] + [dequeue(ts) for _ in ws] + \
           [user_str(ts, '\n'.join('Another Claude session sent a message: ' + w for w in ws))]


def target_queued_only(ts, frm, body):
    """Queued in the target and never delivered: an enqueue is NOT delivery."""
    return [enqueue(ts, _wrap(frm, body))]
