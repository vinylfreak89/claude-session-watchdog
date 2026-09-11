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
    "Leave the quoted profanity in the contract -- his words: 'no keep it LOL it is fine.'",
    'An item whose text runs over several lines,\nwith a second line that must survive intact,\nand a third.',
    'An item carrying unicode and punctuation: the peak drifts 340 -> 660 -> 160, +-2 lines, 99% of units.',
]
TRUTH = {
    'opens': len(REAL_ITEMS),
    'closed_ids': {'D1', 'D3', 'D5', 'Q1', 'Q2', 'Q3'},
    'never_closed': ['D2', 'D4'],
}


def build(dirpath):
    """A corpus of every form that could make the search wrong. Each entry is annotated with
    what it is there to defeat; the count of genuine opens is fixed in TRUTH above."""
    L = [
        # --- forms that must NOT register as state changes -------------------------------
        # 1. heredoc writing a document that contains real-looking invocation text
        rec('2026-09-09T10:00:00Z', cmds=[
            "cat > doc.md <<'EOF'\n"
            "Example for the docs, NOT a real invocation:\n"
            "  ./wd.sh queue add \"THIS SENTENCE IS ONLY AN EXAMPLE IN A DOCUMENT and must "
            "never be restored as one of the owner's items.\"\n"
            "  exec $PY \"$D/wd_wake.py\" --owe-add \"$*\" ;;\n"
            "EOF\n"]),
        # 2. heredoc with an UNQUOTED delimiter
        rec('2026-09-09T10:01:00Z', cmds=[
            "cat > other.md <<EOF\n"
            "./wd.sh owe add \"ANOTHER DOCUMENT EXAMPLE that must never be taken as a real "
            "decision the owner owes.\"\nEOF\n"]),
        # 3. a python heredoc whose body writes the syntax
        rec('2026-09-09T10:02:00Z', cmds=[
            "python3 - <<'PY'\ns='./wd.sh queue add \"NOT AN ITEM, this lives inside a python "
            "string literal and is only ever written to a file.\"'\nopen('f','w').write(s)\nPY\n"]),
        # 4. a shell COMMENT containing the syntax
        rec('2026-09-09T10:03:00Z', cmds=[
            "# ./wd.sh queue add \"A COMMENTED-OUT EXAMPLE that was never executed at all.\"\n"
            "echo done"]),
        # 5. echo of the syntax
        rec('2026-09-09T10:04:00Z', cmds=[
            "echo './wd.sh owe add \"AN ECHOED EXAMPLE that only ever reached a terminal.\"'"]),
        # 6. item text that is a bare shell variable
        rec('2026-09-09T10:05:00Z', cmds=['./wd.sh queue add "$*"']),
        # 7. item text too short to be words
        rec('2026-09-09T10:06:00Z', cmds=['./wd.sh queue add "short"']),
        # 8. an empty command, and whitespace only
        rec('2026-09-09T10:07:00Z', cmds=['', '   \n  ']),
        # 9. a non-Bash tool call
        {'type': 'assistant', 'timestamp': '2026-09-09T10:08:00Z',
         'message': {'role': 'assistant', 'content': [
             {'type': 'tool_use', 'name': 'Read',
              'input': {'file_path': './wd.sh queue add "NOT A COMMAND AT ALL, a file path."'}}]}},
        # 10. a tool_RESULT that echoes the syntax back
        {'type': 'user', 'timestamp': '2026-09-09T10:09:00Z',
         'message': {'role': 'user', 'content': [
             {'type': 'tool_result',
              'content': './wd.sh queue add "OUTPUT ECHOED BACK BY A TOOL, never an invocation."'}]}},

        # --- genuine opens ---------------------------------------------------------------
        rec('2026-09-09T11:00:00Z', cmds=['cd /x && ./wd.sh queue add "%s"' % REAL_ITEMS[0]]),
        rec('2026-09-11T01:13:00Z', cmds=['cd /x && ./wd.sh owe add "%s"' % REAL_ITEMS[1]]),
        rec('2026-09-11T01:19:00Z', cmds=['cd /x && python3 wd_wake.py --owe-add "%s"' % REAL_ITEMS[2]]),
        # an item whose text contains an apostrophe, inside a double-quoted argument
        rec('2026-09-11T01:20:00Z', cmds=['./wd.sh queue add "%s"' % REAL_ITEMS[3]]),
        # a multi-line item
        rec('2026-09-11T01:21:00Z', cmds=['./wd.sh queue add "%s"' % REAL_ITEMS[4]]),
        # --urgent, and unicode
        rec('2026-09-11T01:22:00Z', cmds=['./wd.sh queue add --urgent "%s"' % REAL_ITEMS[5]]),

        # --- closes in every form --------------------------------------------------------
        # 11. id bound by a for-loop, $d form
        rec('2026-09-11T02:00:00Z', cmds=[
            'cd /x && for d in D1 D3; do python3 wd_wake.py --owe-clear $d; done']),
        # 12. ${d} form, quoted items, inside a pipeline
        rec('2026-09-11T02:01:00Z', cmds=[
            'for d in "D5"; do ./wd.sh owe done ${d} | tail -1; done']),
        # 13. plain literal close
        rec('2026-09-11T03:00:00Z', cmds=['cd /x && ./wd.sh sent1 Q1']),
        # 14. close whose id is followed by shell punctuation
        rec('2026-09-11T03:01:00Z', cmds=['./wd.sh sent1 Q2; echo ok']),
        # 15. the SAME id closed twice
        rec('2026-09-11T03:02:00Z', cmds=['./wd.sh sent1 Q3']),
        rec('2026-09-11T03:03:00Z', cmds=['./wd.sh sent1 Q3']),
        # 16. a close with no id at all
        rec('2026-09-11T03:04:00Z', cmds=['./wd.sh queue clear']),

        # --- record shapes that must not be silently dropped -----------------------------
        # 17. the owner mid-turn, as attachment and as queue-operation, not `user`
        {'type': 'attachment', 'timestamp': '2026-09-11T04:00:00Z',
         'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'stop all your hooks now'}]}},
        {'type': 'queue-operation', 'timestamp': '2026-09-11T04:01:00Z',
         'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'do not clear any queues'}]}},
        # 18. the owner as an ordinary user record too
        {'type': 'user', 'timestamp': '2026-09-11T04:02:00Z',
         'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'keep going until it is built'}]}},
        # 19. a record with NO timestamp
        {'type': 'assistant', 'message': {'role': 'assistant',
                                          'content': [{'type': 'text', 'text': 'no timestamp here'}]}},
    ]
    p = os.path.join(dirpath, '80f99b89-fixture.jsonl')
    with open(p, 'w') as f:
        for r in L:
            f.write(json.dumps(r) + '\n')
        # 20. an unparseable line, which must not stop the scan
        f.write('{ this is not json\n')
        # 21. a genuine open AFTER the bad line, to prove the scan continued
        f.write(json.dumps(rec('2026-09-11T05:00:00Z',
                               cmds=['./wd.sh sent1 Q4'])) + '\n')
    st = os.path.join(dirpath, 'state')
    os.makedirs(st, exist_ok=True)
    json.dump({'owner_queue': [], 'owner_decisions': {}, 'open_questions': {},
               'owner_decision_seq': 5}, open(os.path.join(st, 'state.json'), 'w'))
    return p, st


# ---- stage 3: did the action LAND? ------------------------------------------------------
def test_stage3():
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    art = {'sends': [{'ts': '2026-09-11T05:00:30Z', 'msg': 'here is item Q7 verbatim'}],
           'my_text': [{'ts': '2026-09-11T06:00:10Z', 'text': 'x' * 500}],
           'target': [{'ts': '2026-09-11T06:59:00Z', 'text': 'the target answered'}],
           'owner': []}
    acts = [
        {'ts': '2026-09-11T05:00:00Z', 'verb': 'sent1', 'arg': 'Q7'},      # send names it -> ok
        {'ts': '2026-09-11T05:00:00Z', 'verb': 'sent1', 'arg': 'Q9'},      # send exists, wrong id
        {'ts': '2026-09-11T09:00:00Z', 'verb': 'sent1', 'arg': 'Q8'},      # no send at all
        {'ts': '2026-09-11T06:00:00Z', 'verb': 'relayed', 'arg': None},    # substantial reply -> ok
        {'ts': '2026-09-11T20:00:00Z', 'verb': 'relayed', 'arg': None},    # nothing said
        {'ts': '2026-09-11T07:00:00Z', 'verb': 'resolved', 'arg': 'K1'},   # target spoke first
        {'ts': '2026-09-11T01:00:00Z', 'verb': 'resolved', 'arg': 'K2'},   # nothing from target
        {'ts': '2026-09-11T05:00:00Z', 'verb': 'answered', 'arg': None},   # a send exists
    ]
    v = [x['verdict'] for x in R.stage3(acts, art)]
    ck('sent1 with a send naming the id', v[0], 'ok')
    ck('sent1 with a send that names a DIFFERENT id -> undecidable', v[1], 'undecidable')
    ck('sent1 with no send at all -> MISSTEER', v[2], 'MISSTEER')
    ck('relayed with a substantial reply', v[3], 'ok')
    ck('relayed with nothing said -> MISSTEER', v[4], 'MISSTEER')
    ck('resolved after the target spoke', v[5], 'ok')
    ck('resolved with no target output -> MISSTEER', v[6], 'MISSTEER')
    ck('answered with a send behind it', v[7], 'ok')
    ck('undecidable is never rounded to ok',
       any(x == 'undecidable' for x in v), True)

    # a commit claim is landed only if it exists AND sits on a ref
    probe = {'aaaaaaa': (True, ['origin/main']), 'bbbbbbb': (True, []), 'ccccccc': (False, [])}
    res = R.verify_commits([{'ts': 't', 'sha': k} for k in ('aaaaaaa', 'bbbbbbb', 'ccccccc')],
                           lambda sha: probe[sha])
    ck('commit on a ref -> ok', res[0]['verdict'], 'ok')
    ck('commit that exists on NO ref -> undecidable', res[1]['verdict'], 'undecidable')
    ck('commit that does not exist -> MISSTEER', res[2]['verdict'], 'MISSTEER')
    return fails


# ---- stage 4: supersession evidence ------------------------------------------------------
def test_stage4():
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    cand = {'cite': 'f:1#0', 'ts': '2026-09-10T00:00:00Z',
            'text': 'DOES POSITION ALONE ESTABLISH IDENTITY? blanking excursion horizontal skew'}
    records = [
        {'ts': '2026-09-09T00:00:00Z', 'kind': 'user',
         'text': 'blanking excursion horizontal skew position identity'},          # BEFORE: ignore
        {'ts': '2026-09-11T00:00:00Z', 'kind': 'attachment',
         'text': 'no blanking alone can not establish identity. excursion can, '
                 'but there still needs to be measureable horizontal skew'},        # supersedes
        {'ts': '2026-09-11T02:00:00Z', 'kind': 'user', 'text': 'unrelated chatter about lunch'},
    ]
    ev = R.stage4_evidence(cand, records)
    ck('records before the drop are not scanned', ev['records_after'], 2)
    ck('the later answer is surfaced', len(ev['hits']), 1)
    ck('the unrelated record is not surfaced',
       any('lunch' in h['excerpt'] for h in ev['hits']), False)
    ck('it gathers rather than decides', 'verdict' in ev, False)
    return fails


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
            try:
                mine.append(json.loads(line))
            except Exception:
                continue
        mine = [m for m in mine if m.get('timestamp')]
        acts, art = R.timeline(sorted(mine, key=lambda r: r['timestamp']), tgt)
        ck('owner words read from attachment + queue-operation + user', len(art['owner']), 3)
        kinds = {o['kind'] for o in art['owner']}
        ck('all three owner record kinds are read', kinds,
           {'attachment', 'queue-operation', 'user'})

        # --- MUTATION BATTERY: every guard must be LOAD-BEARING ------------------------
        # Each entry disables ONE guard and states how the result must break. A mutation that
        # changes nothing means the guard protects nothing, and that FAILS the suite.
        print('\n--- mutation battery (each must break the result) ---')
        import copy as _copy
        base_opens, base_closes = R.stage1('80f99b89', proj=d)
        base_ids = {c['id'] for c in base_closes if c['id']}
        saved = {'normalize': R.normalize, 'is_real_item': R.is_real_item,
                 'CLOSE_RX': R.CLOSE_RX, 'FORLOOP': R.FORLOOP, 'OPEN_RX': list(R.OPEN_RX)}

        def run():
            o, c = R.stage1('80f99b89', proj=d)
            return o, {x['id'] for x in c if x['id']}

        def mutate(name, apply_fn, broke_fn):
            apply_fn()
            try:
                o, ids = run()
                broke = broke_fn(o, ids)
            except Exception as e:
                broke = True
            finally:
                R.normalize = saved['normalize']; R.is_real_item = saved['is_real_item']
                R.CLOSE_RX = saved['CLOSE_RX']; R.FORLOOP = saved['FORLOOP']
                R.OPEN_RX = list(saved['OPEN_RX'])
            print('%-56s %s' % (name, 'PASS' if broke else 'FAIL (guard protects nothing)'))
            if not broke:
                fails.append('mutation: ' + name)

        import re as _re
        mutate('drop strip_heredocs -> a written document scores as items',
               lambda: setattr(R, 'normalize',
                               lambda c: R.expand_loops(R.strip_echoes(R.strip_comments(c)))),
               lambda o, i: len(o) > TRUTH['opens'])
        mutate('drop strip_comments -> a commented example scores as an item',
               lambda: setattr(R, 'normalize',
                               lambda c: R.expand_loops(R.strip_echoes(R.strip_heredocs(c)))),
               lambda o, i: len(o) > TRUTH['opens'])
        mutate('drop strip_echoes -> echoed text scores as an item',
               lambda: setattr(R, 'normalize',
                               lambda c: R.expand_loops(R.strip_comments(R.strip_heredocs(c)))),
               lambda o, i: len(o) > TRUTH['opens'])
        mutate('drop expand_loops -> loop-bound ids vanish',
               lambda: setattr(R, 'normalize',
                               lambda c: R.strip_echoes(R.strip_comments(R.strip_heredocs(c)))),
               lambda o, i: not {'D1', 'D3', 'D5'} <= i)
        mutate('loop terminator matches `done` inside `owe done` -> D5 vanishes',
               lambda: setattr(R, 'FORLOOP', _re.compile(
                   r'for\s+(\w+)\s+in\s+([^;\n]+?)\s*;\s*do\b(.*?)\bdone\b', _re.S)),
               lambda o, i: 'D5' not in i)
        mutate('drop is_real_item -> `$*` and fragments become owner items',
               lambda: setattr(R, 'is_real_item', lambda t: bool(t)),
               lambda o, i: len(o) > TRUTH['opens'])
        mutate('close id allows shell punctuation -> `Q2;` never matches `Q2`',
               lambda: setattr(R, 'CLOSE_RX', _re.compile(
                   r'(?:^|[;&|]\s*|\s)(?:\./wd\.sh\s+(queue clear|sent1|owe done|owe ungate|'
                   r'resolved|closed|nudged)|--(queue-clear|owe-clear|owe-ungate))\b(?:\s+(\S+))?')),
               lambda o, i: 'Q2' not in i)
        mutate('OPEN_RX without --urgent -> the urgent item is lost',
               lambda: setattr(R, 'OPEN_RX', [(rx, st) for rx, st in saved['OPEN_RX']
                                              if '--urgent' not in rx.pattern]),
               lambda o, i: len(o) < TRUTH['opens'])

        # reading only `user` records loses most of what he said
        allrecs = []
        for line in open(os.path.join(d, '80f99b89-fixture.jsonl')):
            try:
                allrecs.append(json.loads(line))
            except Exception:
                continue
        only_user = sum(1 for r in allrecs if r.get('type') == 'user'
                        and any(b.get('type') == 'text'
                                for b in ((r.get('message') or {}).get('content') or [])
                                if isinstance(b, dict)))
        print('%-56s %s' % ('reading only `user` loses owner words',
                            'PASS' if only_user < 3 else 'FAIL'))
        if only_user >= 3:
            fails.append('mutation: user-only read loses nothing')

        # records without a timestamp must be COUNTED, never silently dropped
        nots = sum(1 for r in allrecs if not r.get('timestamp'))
        print('%-56s %s' % ('corpus contains untimestamped records to account for',
                            'PASS' if nots else 'FAIL'))
        if not nots:
            fails.append('fixture has no untimestamped record')

        # the scan must survive an unparseable line and keep going
        print('%-56s %s' % ('scan continues past an unparseable line',
                            'PASS' if 'Q4' in base_ids else 'FAIL'))
        if 'Q4' not in base_ids:
            fails.append('scan stopped at the bad line')

        print('\n--- stage 3: landed-replay ---')
        fails.extend(test_stage3())
        print('\n--- stage 4: supersession evidence ---')
        fails.extend(test_stage4())

        print('\nRESULT: %s' % ('all controls pass' if not fails else '%d FAILED: %s'
                                % (len(fails), ', '.join(fails))))
        return 1 if fails else 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
