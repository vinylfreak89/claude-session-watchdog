#!/usr/bin/env python3
"""Read-only observations for the spoken-promises review, not an implementation.

Uses synthetic text/records and a temporary rollout. Does not read live sessions.
The proposal has no handler to exercise; these are reader probes, not acceptance
controls for the proposed instrument.
"""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import wd_lib as W


def main():
    samples = {
        'deferred': "I'll hand this to Codex once the eight runs finish.",
        'matching_open': 'sending the pool-test brief to Codex once the eight runs finish',
        'past_tense': 'I sent the brief to Codex for review.',
        'quoted': 'The test fixture contains the sentence "I\'ll send it to Codex".',
        'non_dispatch_promise': 'I will recheck whether the truth file regenerates after the Makefile change.',
        'four_promises': "I'll send alpha. I'll send beta. I'll send gamma. I'll send delta.",
    }
    for label, text in samples.items():
        print(label, json.dumps(W.declared_actions(text)))
    previous = samples['matching_open']
    account = 'sending the pool-test brief: done, dispatched at 14:38, commit f504c16'
    print('proposal_example_exact_match', previous == account)
    print('proposal_example_old_text_present', previous in account)

    def turn(stop, interrupt=False):
        t = W.Turn(dict(type='user', timestamp='2026-09-18T00:00:00Z', promptId='synthetic',
                        message=dict(content='Review this')))
        t.add(dict(type='assistant', timestamp='2026-09-18T00:00:01Z', message=dict(
            content=[dict(type='text', text="I'll send the review after checking the results.")], stop_reason=stop)))
        if interrupt:
            t.add(dict(type='user', timestamp='2026-09-18T00:00:02Z',
                       message=dict(content='[Request interrupted by user]')))
        return t.end_state

    print('max_tokens_without_list', turn('max_tokens'))
    print('interrupted_without_list', turn(None, True))
    print('no_final_record', W.Turn(dict(type='user', message=dict(content='Review this'))).end_state)
    print('torn_record_reader', W.parse_lines(b'{"type":"assistant","message":', 0))

    with tempfile.TemporaryDirectory(prefix='spoken-promise-probe-') as tmp:
        directory = Path(tmp) / '2026' / '09' / '18'
        directory.mkdir(parents=True)
        full = ('Synthetic completed work. ' * 20) + '\nOPEN:\n- Send the review when the owner returns.'
        records = [dict(type='event_msg', timestamp='2026-09-18T00:00:00Z', payload=dict(type='task_started')),
                   dict(type='event_msg', timestamp='2026-09-18T00:01:00Z',
                        payload=dict(type='task_complete', last_agent_message=full))]
        (directory / 'rollout-synthetic-review-thread.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
        with patch.object(W, 'CODEX_SESSIONS', tmp):
            result = W.codex_thread_state('review-thread')
        print('codex_completion_known', result['lifecycle_known'])
        print('codex_full_message_contains_open', 'OPEN:' in full)
        print('codex_summary_length', len(result['last_agent_message']))
        print('codex_summary_contains_open', 'OPEN:' in result['last_agent_message'])


if __name__ == '__main__':
    main()
