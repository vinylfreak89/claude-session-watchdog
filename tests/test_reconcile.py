#!/usr/bin/python3
"""Known-answer harness for the reconciliation search.

The fixture is SYNTHETIC and its truth is fixed here, so the search can be wrong and be seen to
be wrong. It is built out of the exact forms that have each produced a WRONG ANSWER against the
real record, so every guard has a case that fails without it:

  1. a heredoc WRITING a file that contains `--owe-add "$*"`   -> read as a real state change
                                                                  (three separate times)
  2. `for d in D1 D3; do ... --owe-clear $d; done`             -> D3's close invisible, so a
                                                                  closed decision read as DROPPED
  3. a bare shell variable as the item text                    -> `$*` restored as an owner item
  4. a fragment shorter than a sentence                        -> parse artifact restored as words
  5. the owner's mid-turn message as `attachment`/
     `queue-operation` rather than `user`                      -> most of what he said unread

Run: python3 tests/test_reconcile.py
"""
import os, sys, json, tempfile, shutil

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wd_recon_lib as R


def rec(ts, cmds=None, text=None, kind='assistant'):
    if cmds is not None:
        content = [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': c}} for c in cmds]
    else:
        content = [{'type': 'text', 'text': text or ''}]
    return {'type': kind, 'timestamp': ts, 'message': {'role': 'assistant', 'content': content}}


# ---- the synthetic world, and its TRUTH -------------------------------------------------
REAL_ITEMS = [
    'The fitted thresholds in the harness need to go. Owner: yeah those need to go.',
    'DOES POSITION ALONE ESTABLISH IDENTITY? Flagged in the contract itself at :64-65.',
    'THE HEAD-SWITCH BAND CORRECTION, owed from you and never delivered. Contract :186.',
]
TRUTH = {
    'opens': 3,                    # exactly the three above
    'closed_ids': {'D1', 'D3', 'Q1'},   # D3 closed only inside a for-loop
    'never_closed': ['D2'],        # issued (seq=3) and genuinely never closed
}


def build(dirpath):
    lines = [
        # 1. a file being WRITTEN that contains the mutation syntax -- not a state change
        rec('2026-09-09T10:00:00Z', cmds=[
            "cat > doc.md <<'EOF'\n"
            "Example of the form, for the docs -- NOT a real invocation:\n"
            "  ./wd.sh queue add \"THIS SENTENCE IS ONLY AN EXAMPLE IN A DOCUMENT and must never "
            "be restored as one of the owner\'s items.\"\n"
            "  exec $PY \"$D/wd_wake.py\" --owe-add \"$*\" ;;\n"
            "EOF\n"]),
        # 2. three genuine opens
        rec('2026-09-09T11:00:00Z', cmds=['cd /x && ./wd.sh queue add "%s"' % REAL_ITEMS[0]]),
        rec('2026-09-11T01:13:00Z', cmds=['cd /x && ./wd.sh owe add "%s"' % REAL_ITEMS[1]]),
        rec('2026-09-11T01:19:00Z', cmds=['cd /x && python3 wd_wake.py --owe-add "%s"' % REAL_ITEMS[2]]),
        # 3. a close whose id exists ONLY as a loop variable
        rec('2026-09-11T02:00:00Z', cmds=[
            'cd /x && for d in D1 D3; do python3 wd_wake.py --owe-clear $d; done']),
        # 4. an ordinary literal close
        rec('2026-09-11T03:00:00Z', cmds=['cd /x && ./wd.sh sent1 Q1']),
        # 5. the owner's words arriving mid-turn, NOT as a `user` record
        {'type': 'attachment', 'timestamp': '2026-09-11T04:00:00Z',
         'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'stop all your hooks now'}]}},
        {'type': 'queue-operation', 'timestamp': '2026-09-11T04:01:00Z',
         'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'do not clear any queues'}]}},
        # 6. a record with no timestamp at all
        {'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'x'}]}},
    ]
    p = os.path.join(dirpath, '80f99b89-fixture.jsonl')
    with open(p, 'w') as f:
        for r in lines:
            f.write(json.dumps(r) + '\n')
    st = os.path.join(dirpath, 'state')
    os.makedirs(st, exist_ok=True)
    json.dump({'owner_queue': [], 'owner_decisions': {}, 'open_questions': {},
               'owner_decision_seq': 3}, open(os.path.join(st, 'state.json'), 'w'))
    return p, st


def main():
    d = tempfile.mkdtemp(prefix='recon-fixture-')
    fails = []
    try:
        _, st = build(d)
        opens, closes = R.stage1(self_prefix='80f99b89', proj=d)
        join = R.stage1_join(opens, closes, st)

        def ck(name, got, want):
            ok = got == want
            print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                                  '' if ok else '  got=%r want=%r' % (got, want)))
            if not ok:
                fails.append(name)

        ck('opens: only the genuine items (heredoc ignored)', join['opens'], TRUTH['opens'])
        ck('no shell variable was taken as an item',
           any(o['text'].strip() in ('$*', '"$*"') for o in opens), False)
        ck('every open carries a citation', all(':' in o['cite'] and '#' in o['cite'] for o in opens), True)
        ck('every open carries a verbatim hash', all(len(o['sha256']) == 64 for o in opens), True)
        closed = {c['id'] for c in closes if c['id']}
        ck('close ids include the loop-bound D3', 'D3' in closed, True)
        ck('close ids include the loop-bound D1', 'D1' in closed, True)
        ck('the literal `$d` is never recorded as an id', '$d' in closed, False)
        ck('literal close still found', 'Q1' in closed, True)
        ck('decision issued but never closed is found', join['decision_ids_never_closed'],
           TRUTH['never_closed'])

        # the owner's mid-turn words must be readable where they actually live
        mine, tgt = [], []
        for line in open(os.path.join(d, '80f99b89-fixture.jsonl')):
            mine.append(json.loads(line))
        mine = [m for m in mine if m.get('timestamp')]
        acts, art = R.timeline(sorted(mine, key=lambda r: r['timestamp']), tgt)
        ck('owner words read from attachment/queue-operation', len(art['owner']), 2)

        # --- mutation controls: each guard must be LOAD-BEARING -------------------------
        print('\n--- mutation controls (each must break the result) ---')
        save = R.normalize
        R.normalize = R.strip_heredocs          # drop loop unrolling only
        o2, c2 = R.stage1(self_prefix='80f99b89', proj=d)
        got = 'D3' in {c['id'] for c in c2 if c['id']}
        print('%-56s %s' % ('without expand_loops, D3 goes missing', 'PASS' if not got else 'FAIL'))
        if got:
            fails.append('expand_loops not load-bearing')
        R.normalize = lambda c: R.expand_loops(c)   # drop heredoc stripping only
        o3, _ = R.stage1(self_prefix='80f99b89', proj=d)
        print('%-56s %s' % ('without strip_heredocs, a written file scores as opens',
                            'PASS' if len(o3) > TRUTH['opens'] else 'FAIL'))
        if len(o3) <= TRUTH['opens']:
            fails.append('strip_heredocs not load-bearing')
        R.normalize = save

        print('\nRESULT: %s' % ('all controls pass' if not fails else '%d FAILED: %s'
                                % (len(fails), ', '.join(fails))))
        return 1 if fails else 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
