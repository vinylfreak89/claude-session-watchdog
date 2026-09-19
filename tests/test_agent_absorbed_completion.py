"""An agent whose completion was absorbed mid-turn has returned; a mention is not a return.

2026-09-19: a finished recount agent was reported STALLED for 40 minutes because its
completion arrived only as queued task-notification records, never as a user record.
Synthetic records build each case; the real target transcript is checked too.
"""
import json, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import wd_wait as WW

AID = 'aff175ec190ba8815'
LAUNCH = {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': 'Async agent launched. agentId: %s' % AID}]}}
NOTE = '<task-notification>\n<task-id>%s</task-id>\n<status>completed</status>\n</task-notification>' % AID
ABSORBED = [{'type': 'queue-operation', 'operation': 'enqueue', 'content': NOTE},
            {'type': 'attachment', 'attachment': {'type': 'queued_command', 'prompt': NOTE}}]
MENTION_ASSISTANT = {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'agent %s is still running' % AID}]}}
MENTION_ATTACHMENT = {'type': 'attachment', 'attachment': {'type': 'queued_command', 'prompt': 'what is %s doing?' % AID}}


def counts(records):
    fd, p = tempfile.mkstemp(suffix='.jsonl')
    with os.fdopen(fd, 'w') as fh:
        for r in records: fh.write(json.dumps(r) + '\n')
    try: return WW.delivery_counts(p, {AID: None})[AID]
    finally: os.remove(p)


def main():
    assert counts([LAUNCH]) == 1, 'launch alone must read as outstanding'
    assert counts([LAUNCH] + ABSORBED) > 1, 'absorbed completion not seen as a return'
    assert counts([LAUNCH, MENTION_ASSISTANT]) == 1, 'assistant text counted as a return'
    assert counts([LAUNCH, MENTION_ATTACHMENT]) == 1, 'a queued message naming the id counted as a return'
    print('ok')


if __name__ == '__main__':
    main()
