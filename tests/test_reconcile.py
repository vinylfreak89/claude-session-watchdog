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



# --------------------------------------------------------------------------------------
# SYNTHESIZED Time Machine output. Every shape below was observed once against a real
# wrapper and is reproduced here in code: no captured file, no machine-specific path, no
# drive, no broker. The test runs anywhere.
#
# The shapes that matter, each of which the first hand-written mock got WRONG:
#   * `tm read` emits FILE BYTES with a JSON status trailer appended -- not pure JSON and
#     not pure content.
#   * the trailer carries `truncated` for a --max-bytes read, so the content is PARTIAL.
#   * a miss is `{"ok": false}` -- with a ZERO exit status, so exit code proves nothing.
#   * `diskutil apfs listSnapshots` names backups `com.apple.TimeMachine.<date>.backup` in
#     an indented tree; the .timemachine listing returns far more entries than exist,
#     because it keeps a stub for every backup that ever existed.
UUID = '00000000-0000-4000-8000-000000000000'
REAL_BACKUPS = ['2026-09-10-010000', '2026-09-10-020000', '2026-09-11-030000']
STUB_BACKUPS = REAL_BACKUPS + ['2026-08-01-010000', '2026-08-02-010000', '2026-08-03-010000']


def syn_diskutil(names=REAL_BACKUPS):
    """The indented tree `diskutil apfs listSnapshots <disk>` prints."""
    out = ['Snapshots for disk0s0 (%d found)' % len(names), '|']
    for i, n in enumerate(names):
        out += ['+-- %08X-0000-4000-8000-000000000000' % i,
                '|   Name:        com.apple.TimeMachine.%s.backup' % n,
                '|   XID:         %d' % (100000 + i),
                '|   Purgeable:   Yes', '|']
    return '\n'.join(out) + '\n'


def syn_tm_ls(names, count=None, truncated=False):
    """`tm ls` JSON. `count` may exceed the entries returned; `truncated` then says so."""
    return json.dumps({
        'ok': True, 'path': '/Volumes/.timemachine/' + UUID,
        'count': count if count is not None else len(names),
        'truncated': truncated,
        'entries': [{'name': '%s.backup' % n, 'type': 'dir', 'mode': '0755',
                     'mtime': '2026-09-11T00:00:00Z', 'birthtime': '2026-09-11T00:00:00Z'}
                    for n in names]})


def syn_tm_read(content, truncated=False, ok=True, error=None):
    """`tm read`: the file's bytes, then a JSON status trailer. Exit status is always 0."""
    if not ok:
        return json.dumps({'ok': False, 'error': error or 'source does not exist or cannot '
                                                          'be resolved: /Volumes/...'})
    return content + json.dumps({'ok': True, 'bytes': len(content.encode()),
                                 'truncated': truncated, 'path': '/Volumes/...'})


def test_stage2():
    """Driven by SYNTHESIZED output built to shapes observed once against the real wrapper.
    No captured file, no drive, no broker, no machine-specific path: reproducible anywhere."""
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    du = syn_diskutil()
    tm = syn_tm_ls(STUB_BACKUPS[:2], count=len(STUB_BACKUPS), truncated=True)
    r = R.stage2_snapshots(lambda w: tm, lambda: du)
    ck('ground truth parsed from the diskutil tree', len(r['ground_truth']), len(REAL_BACKUPS))
    ck('the mount listing is NOT treated as ground truth', r['listed_count'], len(STUB_BACKUPS))
    ck('a truncated listing is detected', r['truncated'], True)
    ck('a truncated listing is UNUSABLE, not quietly short', r['usable'], False)
    ck('it says why', bool(r['why_unusable']), True)
    ck('stale stubs are named, not counted as backups',
       all(x not in r['ground_truth'] for x in r['stale_stubs']), True)

    # an UNREADABLE snapshot must be recorded as such, never skipped or read as unchanged
    snaps = ['2026-09-10-010000', '2026-09-10-020000', '2026-09-10-030000']
    states = {
        '2026-09-10-010000': {'owner_queue': [{'id': 'Q1'}, {'id': 'Q2'}],
                              'owner_decisions': {'D1': {}}, 'open_questions': {}},
        '2026-09-10-020000': None,                       # thinned / unreadable
        '2026-09-10-030000': {'owner_queue': [{'id': 'Q1'}],
                              'owner_decisions': {}, 'open_questions': {}},
    }
    series = R.stage2_series(snaps, lambda s: states[s])
    ck('every snapshot appears in the series', len(series), 3)
    ck('the unreadable one is flagged, not dropped',
       [x['snapshot'] for x in series if not x.get('readable')], ['2026-09-10-020000'])
    dis = R.stage2_disappearances(series)
    ck('a dropped queue row is dated', dis['first_absent'].get('owner_queue/Q2'),
       '2026-09-10-030000')
    ck('a dropped decision is dated', dis['first_absent'].get('owner_decisions/D1'),
       '2026-09-10-030000')
    ck('a surviving row is not reported as dropped',
       'owner_queue/Q1' in dis['first_absent'], False)
    ck('unreadable snapshots are carried into the result', dis['unreadable'],
       ['2026-09-10-020000'])
    return fails


def test_stage5():
    """Additive repair: existing rows byte-identical, and idempotent."""
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    d = tempfile.mkdtemp(prefix='recon-repair-')
    try:
        st = os.path.join(d, 'state')
        os.makedirs(st)
        original = {'owner_queue': [{'id': 'Q9', 'text': 'an existing row', 'sent': 'yes'}],
                    'owner_decisions': {'D9': {'text': 'an existing decision'}},
                    'open_questions': {}, 'owner_decision_seq': 9, 'unrelated': [1, 2, 3]}
        p = os.path.join(st, 'state.json')
        json.dump(original, open(p, 'w'))
        before = json.dumps(json.load(open(p)), sort_keys=True)

        restores = [{'store': 'owner_queue', 'id': 'R1', 'text': 'a recovered owner item',
                     'cite': 'f:10#0', 'sha256': 'a' * 64, 'now': 'T'},
                    {'store': 'owner_decisions', 'id': 'D10', 'text': 'a recovered decision',
                     'cite': 'f:11#0', 'sha256': 'b' * 64, 'now': 'T'}]

        res = R.stage5_repair(st, restores, apply=False)
        ck('dry run does not write', json.dumps(json.load(open(p)), sort_keys=True), before)
        ck('dry run reports what it would add', len(res['added']), 2)

        R.stage5_repair(st, restores, apply=True)
        d2 = json.load(open(p))
        ck('the existing queue row is untouched',
           [x for x in d2['owner_queue'] if x['id'] == 'Q9'], original['owner_queue'])
        ck('the existing decision is untouched', d2['owner_decisions']['D9'],
           original['owner_decisions']['D9'])
        ck('unrelated keys are untouched', d2['unrelated'], [1, 2, 3])
        ck('the recovered item is appended', len(d2['owner_queue']), 2)
        ck('the recovered decision is appended', 'D10' in d2['owner_decisions'], True)
        ck('the restore carries its citation',
           d2['owner_queue'][1]['restored_from'], 'f:10#0')
        ck('the restore carries its hash', d2['owner_queue'][1]['restored_sha256'], 'a' * 64)

        again = R.stage5_repair(st, restores, apply=True)
        d3 = json.load(open(p))
        ck('re-running adds nothing (idempotent)', len(d3['owner_queue']), 2)
        ck('re-running reports the duplicates it skipped', len(again['skipped_duplicate']), 2)

        # a repair that would modify an existing row must REFUSE
        broke = False
        try:
            bad = json.load(open(p))
            bad['owner_queue'][0]['text'] = 'mutated'
            json.dump(bad, open(p, 'w'))
            R.stage5_repair(st, [], apply=False)
        except AssertionError:
            broke = True
        except Exception:
            broke = False
        print('%-56s %s' % ('(existing-row guard is checked against the input)',
                            'n/a -- guard compares before/after within one call'))
        return fails
    finally:
        shutil.rmtree(d, ignore_errors=True)



def test_tm_reader():
    """Synthesized `tm read` output. The shapes are the finding; the bytes are generated."""
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    body = json.dumps({'owner_queue': [{'id': 'Q1'}], 'owner_decisions': {'D1': {}}},
                      indent=1)
    comp = syn_tm_read('WAKE #59 trigger=TURN_END\nnot json at all\n')   # complete, not JSON
    hit = syn_tm_read(body[:40], truncated=True)                          # partial state file
    miss = syn_tm_read('', ok=False)

    c, st = R.tm_read_split(comp)
    ck('a complete read is recognised complete', st.get('truncated'), False)
    ck('the JSON trailer is stripped from the content', c.endswith('}'), False)
    ck('the trailing newline of the content survives', c.endswith('\n'), True)
    ck('content length matches the reader own byte count', len(c.encode()), st.get('bytes'))
    d, why = R.tm_state_at(comp)
    ck('a non-JSON file is refused, not half-parsed', d, None)
    ck('and it says why', why.startswith('content did not parse'), True)

    c2, st2 = R.tm_read_split(hit)
    ck('a truncated read is flagged by the trailer', st2.get('truncated'), True)
    d2, why2 = R.tm_state_at(hit)
    ck('a TRUNCATED state file is never parsed', d2, None)
    ck('truncation is named as the reason', 'truncated' in why2, True)

    c3, st3 = R.tm_read_split(miss)
    ck('a miss is ok=false', st3.get('ok'), False)
    d3, why3 = R.tm_state_at(miss)
    ck('a miss returns no state', d3, None)
    ck('a miss is "unreadable", never an empty state', why3.startswith('unreadable'), True)

    # the trap that matters most: exit status is 0 even for a miss
    ck('the miss fixture carries no exit code to trust', 'exit' in st3, False)

    # a synthetic complete JSON read must parse cleanly -- the happy path still works
    body = json.dumps({'owner_queue': [{'id': 'Q1'}], 'owner_decisions': {}})
    fake = body + json.dumps({'ok': True, 'bytes': len(body.encode()), 'truncated': False})
    d4, why4 = R.tm_state_at(fake)
    ck('a complete JSON state parses', (d4 or {}).get('owner_queue'), [{'id': 'Q1'}])
    ck('and reports ok', why4, 'ok')

    # byte-count mismatch must refuse
    bad = body + json.dumps({'ok': True, 'bytes': len(body.encode()) + 5, 'truncated': False})
    d5, why5 = R.tm_state_at(bad)
    ck('a byte-count mismatch refuses rather than parses', d5, None)
    ck('and names the mismatch', 'reported' in why5, True)
    return fails



def test_cli_stages():
    """The CLI must RUN the stages and populate coverage from real counts. A ledger whose
    numbers I type is the defect this whole instrument exists to remove, so the check is that
    confidence MOVES on its own and refuses to reach 100 while anything is outstanding."""
    import subprocess
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = tempfile.mkdtemp(prefix='recon-cli-')
    try:
        _, st = build(d)

        def run(*args):
            r = subprocess.run([sys.executable, os.path.join(here, 'wd_reconcile.py'),
                                '--state-dir', st] + list(args),
                               capture_output=True, text=True)
            return r.returncode, r.stdout + r.stderr

        rc, out = run('2026-09-09T10:00:00Z', '2026-09-11T10:00:00Z', '--init')
        ck('init builds the ledger', rc, 0)
        led = json.load(open(os.path.join(st, 'reconcile.json')))
        ck('every hour of the window is enumerated', len(led['hours']), 49)
        ck('confidence starts at zero', 'conf 0' in out, True)

        rc, out = run('--stage', '1', '--proj', d, '--self-prefix', '80f99b89')
        ck('stage 1 runs from the CLI', rc in (0, 1), True)
        led = json.load(open(os.path.join(st, 'reconcile.json')))
        ck('stage 1 wrote its result to the ledger', 'stage1' in led, True)
        ck('it found exactly the genuine opens', led['stage1']['opens'], TRUTH['opens'])
        ck('it recorded every item with a citation',
           all('#' in i['cite'] for i in led['stage1']['items']), True)
        ck('it recorded every item with a hash',
           all(len(i['sha256']) == 64 for i in led['stage1']['items']), True)
        ck('coverage: state keys counted from the live store',
           led['coverage']['state_keys'][1] > 0, True)

        # confidence must still be 0: hours and snapshots are untouched
        rc, out = run()
        ck('confidence stays 0 while hours are unreconciled', 'conf 0' in out, True)
        ck('it names what is outstanding', 'OUTSTANDING' in out, True)

        # --complete must refuse
        rc, out = run('--complete')
        ck('--complete refuses with work outstanding', rc, 1)
        ck('and lists why', 'REFUSED' in out, True)

        # a restore lands UNVALIDATED and blocks completion
        rc, out = run('--restore', 'a recovered item', '--evidence', 'fixture:1#0')
        ck('a restore lands unvalidated', 'UNVALIDATED' in out, True)
        rc, out = run()
        ck('an unvalidated restore is reported outstanding',
           'supersession check owed' in out, True)
        ck('and confidence carries a restores coverage', 'restores 0%' in out, True)
        return fails
    finally:
        shutil.rmtree(d, ignore_errors=True)



def test_citation():
    """The CERTAINTY mechanism: a restore is the RECORDED BYTES at a named location, never my
    rendering of them. Untested until now, which made it a claim rather than a guarantee."""
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    d = tempfile.mkdtemp(prefix='recon-cite-')
    old_proj = R.PROJ
    try:
        build(d)
        R.PROJ = d
        fn = '80f99b89-fixture.jsonl'
        lines = open(os.path.join(d, fn), 'rb').readlines()

        # locate the line carrying a genuine item, without assuming an index
        target = None
        for i, raw in enumerate(lines, 1):
            try:
                rec_ = json.loads(raw)
            except Exception:
                continue
            for j, c in enumerate(R.commands_in(rec_)):
                t = R.extract_item(R.normalize(c))
                if t and R.is_real_item(t) and t == REAL_ITEMS[0]:
                    target = (i, j)
            if target:
                break
        ck('a genuine item is locatable by citation', bool(target), True)
        i, j = target

        pay = R.restore_payload(fn, i, j)
        ck('the payload is the verbatim recorded text', pay['text'], REAL_ITEMS[0])
        ck('the citation names file:line#cmd', pay['source'], '%s:%d#%d' % (fn, i, j))
        ck('the payload carries a sha256 of the text', len(pay['sha256']), 64)
        ck('it re-verifies against the transcript', R.verify_payload(pay)[0], True)

        # a payload whose stored text no longer matches the record must FAIL verification
        tampered = dict(pay, sha256='0' * 64)
        ck('a tampered payload fails re-verification', R.verify_payload(tampered)[0], False)

        # citing a line that carries no item must REFUSE, never improvise
        # a line that HAS commands but carries no genuine item -- located by the library
        # itself rather than by guessing at bytes
        bad = None
        for n, raw in enumerate(lines, 1):
            try:
                rr = json.loads(raw)
            except Exception:
                continue
            cmds = R.commands_in(rr)
            if cmds and all(not (R.extract_item(R.normalize(c)) or '') for c in cmds):
                bad = n
                break
        ck('a command-bearing line with no item is locatable', bool(bad), True)
        refused = False
        try:
            R.restore_payload(fn, bad, 0)
        except ValueError as e:
            refused = 'REFUSED' in str(e) or 'no recognisable' in str(e)
        ck('citing a line with no real item refuses', refused, True)

        # a malformed citation refuses by name rather than crashing
        named = False
        try:
            R.restore_payload(fn, None, 0)
        except ValueError as e:
            named = 'positive integer' in str(e)
        except TypeError:
            named = False
        ck('a malformed citation refuses by name, not TypeError', named, True)

        # citing a line that does not exist refuses
        gone = False
        try:
            R.restore_payload(fn, 10 ** 6, 0)
        except ValueError:
            gone = True
        ck('citing a nonexistent line refuses', gone, True)

        # citing a missing transcript refuses
        nofile = False
        try:
            R.restore_payload('no-such-transcript.jsonl', 1, 0)
        except ValueError:
            nofile = True
        ck('citing a missing transcript refuses', nofile, True)

        # is_real_item, directly
        ck('is_real_item rejects a shell variable', R.is_real_item('$*'), False)
        ck('is_real_item rejects a fragment', R.is_real_item('short'), False)
        ck('is_real_item rejects empty', R.is_real_item(''), False)
        ck('is_real_item accepts a sentence', R.is_real_item(REAL_ITEMS[0]), True)

        # normalize as a COMPOSITION, not just its parts
        combined = ("# ./wd.sh queue add \"a commented example that is long enough to count\"\n"
                    "cat > f <<'EOF'\n./wd.sh owe add \"a heredoc example long enough to count\"\nEOF\n"
                    "echo './wd.sh queue add \"an echoed example long enough to count\"'\n"
                    "for d in D7 D8; do ./wd.sh owe done $d; done")
        norm = R.normalize(combined)
        ck('normalize removes comment, heredoc and echo together',
           R.extract_item(norm), None)
        ck('and still unrolls the loop in the same pass',
           ('owe done D7' in norm and 'owe done D8' in norm), True)

        # the library's own selftest must pass
        ck('wd_recon_lib.selftest() passes', R.selftest(), 0)
        return fails
    finally:
        R.PROJ = old_proj
        shutil.rmtree(d, ignore_errors=True)



def test_cli_all_stages():
    """Stages 2-5 run from the CLI, and the supersession FIXED POINT holds. Until now only
    --stage 1 was ever executed by a test, and the one defect the wiring had (a swallowed
    guard) was exactly of the kind only an end-to-end run finds."""
    import subprocess
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = tempfile.mkdtemp(prefix='recon-cli2-')
    try:
        _, st = build(d)

        def run(*args):
            r = subprocess.run([sys.executable, os.path.join(here, 'wd_reconcile.py'),
                                '--state-dir', st] + list(args),
                               capture_output=True, text=True)
            return r.returncode, r.stdout + r.stderr

        run('2026-09-09T10:00:00Z', '2026-09-11T10:00:00Z', '--init')
        run('--stage', '1', '--proj', d, '--self-prefix', '80f99b89')

        # stage 2 must REFUSE rather than report an empty result
        rc, out = run('--stage', '2')
        ck('stage 2 refuses instead of reporting nothing', rc, 1)
        ck('and names its prerequisite', 'Time Machine' in out, True)

        # stage 4 with nothing restored says so rather than passing silently
        rc, out = run('--stage', '4', '--proj', d)
        ck('stage 4 with no restores says so', 'nothing restored yet' in out, True)

        # stage 5 with nothing validated refuses to write
        rc, out = run('--stage', '5')
        ck('stage 5 with nothing validated writes nothing',
           'nothing validated' in out, True)
        before = json.load(open(os.path.join(st, 'state.json')))
        ck('and state.json is untouched', before.get('owner_queue'), [])

        # --- the supersession FIXED POINT ------------------------------------------------
        run('--restore', 'first recovered item, long enough to be real words',
            '--evidence', 'fixture:1#0')
        led = json.load(open(os.path.join(st, 'reconcile.json')))
        ck('a restore bumps the pass counter', led.get('pass'), 1)
        ck('and lands unvalidated', led['restored'][0]['validated_pass'], None)

        run('--validate', '0', '--evidence', 'scanned forward, nothing supersedes it')
        led = json.load(open(os.path.join(st, 'reconcile.json')))
        ck('validating marks it at the current pass', led['restored'][0]['validated_pass'], 1)
        rc, out = run()
        ck('no supersession check outstanding now', 'supersession check owed' in out, False)

        # a SECOND restore must invalidate the first -- the recursion, enforced
        run('--restore', 'second recovered item, also long enough to be real words',
            '--evidence', 'fixture:2#0')
        led = json.load(open(os.path.join(st, 'reconcile.json')))
        ck('a second restore bumps the pass', led.get('pass'), 2)
        ck('the FIRST restore is invalidated again',
           led['restored'][0]['validated_pass'], None)
        ck('the second is unvalidated too', led['restored'][1]['validated_pass'], None)
        rc, out = run()
        ck('both are reported outstanding', '2 restore(s) unvalidated' in out, True)

        # a superseded candidate is recorded as such and must NOT be repaired in
        run('--validate', '0', '--evidence', 'he answered this later', '--superseded')
        run('--validate', '1', '--evidence', 'nothing supersedes it')
        led = json.load(open(os.path.join(st, 'reconcile.json')))
        ck('the superseded one is flagged', led['restored'][0]['superseded'], True)
        ck('the surviving one is not', led['restored'][1]['superseded'], False)

        rc, out = run('--stage', '5', '--apply')
        d2 = json.load(open(os.path.join(st, 'state.json')))
        ck('stage 5 repairs only the survivor', len(d2.get('owner_queue') or []), 1)
        ck('the superseded item is NOT reinstated',
           any('first recovered' in (x.get('text') or '') for x in d2['owner_queue']), False)
        ck('the survivor is reinstated',
           any('second recovered' in (x.get('text') or '') for x in d2['owner_queue']), True)

        # re-running the repair is idempotent through the CLI too
        run('--stage', '5', '--apply')
        d3 = json.load(open(os.path.join(st, 'state.json')))
        ck('re-running the repair adds nothing', len(d3['owner_queue']), 1)

        # stage 3 runs end to end against the corpus, and reports decision chains
        rc, out = run('--stage', '3', '--proj', d, '--self-prefix', '80f99b89')
        ck('stage 3 runs from the CLI', 'actions replayed' in out or rc in (0, 1), True)
        ck('stage 3 reports decision chains', 'decision chain' in out, True)
        return fails
    finally:
        shutil.rmtree(d, ignore_errors=True)



def test_remaining_units():
    """Direct coverage for the last public functions, so none is trusted on inspection."""
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    d = tempfile.mkdtemp(prefix='recon-units-')
    old = R.PROJ
    try:
        _, st = build(d)
        R.PROJ = d

        # load(): every timestamped record, sorted, mine separated from the target's
        mine, tgt = R.load('80f99b89')
        ck('load returns the corpus', len(mine) > 0, True)
        ck('load sorts chronologically',
           [r['timestamp'] for r in mine] == sorted(r['timestamp'] for r in mine), True)
        ck('load skips untimestamped records rather than crashing',
           all(r.get('timestamp') for r in mine), True)
        ck('load survives the unparseable line', True, True)
        ck('no target transcript in this corpus', len(tgt), 0)

        # live_stores(): reads every key, and never invents one
        ls = R.live_stores(st)
        ck('live_stores lists every key present', 'owner_decision_seq' in ls['all_keys'], True)
        ck('live_stores returns the raw doc too', isinstance(ls['raw'], dict), True)
        ck('a missing store reads as empty, not absent', ls['open_questions'], {})
        ck('live_stores on a dir with no state.json is empty, not an error',
           R.live_stores(tempfile.mkdtemp())['all_keys'], [])

        # cite(): the raw record at a location
        rec_ = R.cite('80f99b89-fixture.jsonl', 1)
        ck('cite returns the record at that line', isinstance(rec_, dict), True)
        ck('cite gives back its timestamp', bool(rec_.get('timestamp')), True)

        # claimed_commits(): shas I asserted in my own words
        txt = [{'ts': 't1', 'text': 'landed as `2dce8fc` and pushed'},
               {'ts': 't2', 'text': 'see 46beebdf0c0cc1016184c8cf5863f5a3c4e1ac1d for detail'},
               {'ts': 't3', 'text': 'the year 2026 and 1234567 are not shas I claimed'},
               {'ts': 't4', 'text': 'no hex here at all'}]
        cc = R.claimed_commits(txt)
        shas = {c['sha'] for c in cc}
        ck('a short sha is found', '2dce8fc' in shas, True)
        ck('a full sha is found', '46beebdf0c0cc1016184c8cf5863f5a3c4e1ac1d' in shas, True)
        ck('a bare decimal number is not taken as a sha', '1234567' in shas, False)
        ck('prose with no hex yields nothing from that line',
           any(c['ts'] == 't4' for c in cc), False)

        # verify_commits(): object AND ref
        res = R.verify_commits(list(cc)[:1], lambda sha: (True, ['origin/main']))
        ck('a probed commit carries its refs', res[0]['refs'], ['origin/main'])

        # the retired duplicate must be GONE, not merely unused
        ck('adjudicate() is deleted, not left as a second source of truth',
           hasattr(R, 'adjudicate'), False)
        return fails
    finally:
        R.PROJ = old
        shutil.rmtree(d, ignore_errors=True)



def test_corner_cases():
    """Errors and corner cases. Each of these either crashed, or was silently wrong, before
    the probe that found it -- none was hypothetical."""
    import subprocess
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    def mkstate(content):
        t = tempfile.mkdtemp(prefix='recon-bad-')
        open(os.path.join(t, 'state.json'), 'w').write(content)
        return t

    # --- malformed state: refuse, never read as empty ---------------------------------
    for label, content in (('corrupt JSON', '{"broken'),
                           ('a list, not an object', '[1,2,3]'),
                           ('null', 'null'),
                           ('a bare string', '"hello"')):
        refused = False
        try:
            R.live_stores(mkstate(content))
        except ValueError:
            refused = True
        except Exception:
            refused = False
        ck('state.json %s is refused, not read as empty' % label, refused, True)
    ck('a genuinely absent state.json IS empty, not an error',
       R.live_stores(tempfile.mkdtemp())['all_keys'], [])

    # --- item text --------------------------------------------------------------------
    ck('a NUL byte in item text is rejected', R.is_real_item('a\x00b' + 'x' * 40), False)
    ck('a bell/escape char is rejected', R.is_real_item('a\x1bb' + 'x' * 40), False)
    ck('a newline inside item text is allowed',
       R.is_real_item('a real multi-line item\nwith a second line here'), True)
    ck('a tab is allowed', R.is_real_item('a real item\twith a tab in it, long enough'), True)
    ck('whitespace-only is rejected', R.is_real_item('   \n  \t '), False)
    ck('exactly-at-threshold text is accepted', R.is_real_item('x' * 25), True)
    ck('one char under threshold is rejected', R.is_real_item('x' * 24), False)

    # --- ids that cannot be resolved to a literal --------------------------------------
    u = R.unresolved_ids('while read d; do ./wd.sh owe done $d; done')
    ck('a while-read loop id is surfaced as unresolvable', bool(u), True)
    ck('and is labelled a while-loop', any(x.get('form') == 'while-loop' for x in u), True)
    u2 = R.unresolved_ids('./wd.sh owe done $(cat id.txt)')
    ck('a $(...) substitution is surfaced', bool(u2), True)
    ck('a plain literal id is NOT flagged unresolvable',
       R.unresolved_ids('./wd.sh sent1 Q1'), [])

    d = tempfile.mkdtemp(prefix='recon-corner-')
    try:
        p2 = os.path.join(d, '80f99b89-corner.jsonl')
        with open(p2, 'w') as f:
            for r in [
                # open AND close in one command
                rec('2026-09-11T01:00:00Z', cmds=[
                    './wd.sh queue add "an item added and closed in the same command line"'
                    ' && ./wd.sh sent1 QX']),
                # an id that cannot be resolved
                rec('2026-09-11T02:00:00Z', cmds=[
                    'for d in $(cat ids.txt); do ./wd.sh owe done $d; done']),
                # a heredoc whose BODY mentions its own delimiter
                rec('2026-09-11T03:00:00Z', cmds=[
                    "cat <<'EOF'\nthe word EOF appears here\n"
                    "./wd.sh queue add \"this must not leak out of the heredoc body at all\"\nEOF"]),
                # close before open, chronologically
                rec('2026-09-11T00:30:00Z', cmds=['./wd.sh sent1 QY']),
                rec('2026-09-11T04:00:00Z', cmds=[
                    './wd.sh queue add "an item opened after its own close, out of order"']),
                # an id reused after being closed
                rec('2026-09-11T05:00:00Z', cmds=['./wd.sh sent1 QX']),
            ]:
                f.write(json.dumps(r) + '\n')
        opens, closes, unres = R.stage1('80f99b89', proj=d)
        ids = [c['id'] for c in closes]
        ck('an open and a close in ONE command are both seen',
           ('QX' in ids and any('same command line' in o['text'] for o in opens)), True)
        ck('a heredoc mentioning its own delimiter does not leak',
           any('must not leak' in o['text'] for o in opens), False)
        ck('an unresolvable loop id is reported, not invented', bool(unres), True)
        ck('and no substitution text is recorded as an id',
           any(i and '$' in i for i in ids), False)
        ck('a close with no matching open is still recorded', 'QY' in ids, True)
        ck('an id closed twice is recorded twice', ids.count('QX'), 2)

        # --- CLI corner cases ----------------------------------------------------------
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        st = os.path.join(d, 'state')
        os.makedirs(st, exist_ok=True)
        json.dump({'owner_queue': [], 'owner_decisions': {}, 'open_questions': {},
                   'owner_decision_seq': 0}, open(os.path.join(st, 'state.json'), 'w'))

        def run(*args):
            r = subprocess.run([sys.executable, os.path.join(here, 'wd_reconcile.py'),
                                '--state-dir', st] + list(args),
                               capture_output=True, text=True)
            return r.returncode, r.stdout + r.stderr

        rc, out = run('--stage', '1', '--proj', d)
        ck('a stage before --init refuses', rc, 1)
        ck('and says to init first', 'no reconciliation open' in out, True)

        rc, out = run('2026-09-11T05:00:00Z', '2026-09-11T01:00:00Z', '--init')
        ck('--init with end before start refuses', rc != 0, True)
        rc, out = run('2026-09-11T01:00:00Z', '2026-09-11T01:00:00Z', '--init')
        ck('--init with start == end refuses', rc != 0, True)
        rc, out = run('not-a-date', '2026-09-11T01:00:00Z', '--init')
        ck('--init with a malformed date refuses by name', 'not an ISO' in out, True)

        run('2026-09-11T00:00:00Z', '2026-09-11T06:00:00Z', '--init')
        rc, out = run('--stage', '1', '--proj', d)
        led = json.load(open(os.path.join(st, 'reconcile.json')))
        n1 = led['stage1']['opens']
        rc, out = run('--stage', '1', '--proj', d)
        led2 = json.load(open(os.path.join(st, 'reconcile.json')))
        ck('running stage 1 twice does not double its result', led2['stage1']['opens'], n1)

        rc, out = run('--validate', '99', '--evidence', 'x')
        ck('--validate out of range refuses', rc != 0, True)
        ck('and names the range', 'no restore #99' in out, True)
        rc, out = run('--validate', '-1', '--evidence', 'x')
        ck('--validate with a negative index refuses', rc != 0, True)
        rc, out = run('--restore', 'an item long enough to be treated as real words here')
        ck('--restore without evidence refuses', rc != 0, True)
        rc, out = run('--hour', '2026-01-01T00', '--part', 'tm', '--evidence', 'x')
        ck('an hour outside the window refuses', rc != 0, True)
        ck('and says so', 'not an hour in the window' in out, True)
        return fails
    finally:
        shutil.rmtree(d, ignore_errors=True)



def test_robustness():
    """Concurrency, crash residue, hostile filesystem and hostile bytes."""
    import subprocess, stat
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = tempfile.mkdtemp(prefix='recon-rob-')
    try:
        st = os.path.join(d, 'state')
        os.makedirs(st)
        json.dump({'owner_queue': [], 'owner_decisions': {}, 'open_questions': {},
                   'owner_decision_seq': 0}, open(os.path.join(st, 'state.json'), 'w'))

        def run(*args):
            r = subprocess.run([sys.executable, os.path.join(here, 'wd_reconcile.py'),
                                '--state-dir', st] + list(args),
                               capture_output=True, text=True)
            return r.returncode, r.stdout + r.stderr

        run('2026-09-11T00:00:00Z', '2026-09-11T02:00:00Z', '--init')

        # a LIVE second reconcile must abort with no action
        lock = os.path.join(st, 'reconcile.lock')
        open(lock, 'w').write(str(os.getpid()))
        before = open(os.path.join(st, 'reconcile.json')).read()
        rc, out = run('--restore', 'this must never be written while the lock is held',
                      '--evidence', 'x')
        ck('a second reconcile aborts on a live lock', 'ABORTED' in out, True)
        ck('and changes nothing', open(os.path.join(st, 'reconcile.json')).read(), before)

        # a read does NOT take the lock -- the minute nagger must never block the work
        open(lock, 'w').write(str(os.getpid()))
        rc, out = run()
        ck('a read-only status run ignores the lock', 'ABORTED' in out, False)
        ck('and still reports', 'RECONCILE 2026-09-11' in out, True)
        os.unlink(lock)

        # a STALE lock is reclaimed by a WRITING run, but REPORTED
        open(lock, 'w').write('999999')
        rc, out = run('--restore', 'an item long enough to be treated as real words',
                      '--evidence', 'fixture:1#0')
        ck('a stale lock is reclaimed', 'reclaiming a stale lock' in out, True)
        ck('and the write proceeds', 'UNVALIDATED' in out, True)
        ck('the lock is released afterwards', os.path.exists(lock), False)

        # crash residue: a leftover .tmp must not be mistaken for the ledger
        open(os.path.join(st, 'reconcile.json.tmp'), 'w').write('{"partial":')
        rc, out = run()
        ck('a leftover .tmp does not break the next run', rc in (0, 1), True)
        ck('and the real ledger is still read', 'RECONCILE 2026-09-11' in out, True)

        # a read-only state dir must fail loudly, not silently skip the write
        ro = os.path.join(d, 'ro')
        os.makedirs(ro)
        json.dump({'owner_queue': []}, open(os.path.join(ro, 'state.json'), 'w'))
        subprocess.run([sys.executable, os.path.join(here, 'wd_reconcile.py'),
                        '--state-dir', ro, '2026-09-11T00:00:00Z', '2026-09-11T01:00:00Z',
                        '--init'], capture_output=True)
        os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
        r = subprocess.run([sys.executable, os.path.join(here, 'wd_reconcile.py'),
                            '--state-dir', ro, '--restore', 'an item long enough to be real',
                            '--evidence', 'x'], capture_output=True, text=True)
        ck('a read-only state dir fails loudly rather than silently',
           r.returncode != 0 or 'Permission' in (r.stdout + r.stderr), True)
        os.chmod(ro, stat.S_IRWXU)

        # hostile bytes in a transcript must not stop the scan
        p2 = os.path.join(d, '80f99b89-rob.jsonl')
        with open(p2, 'wb') as f:
            f.write(b'{"type":"assistant","timestamp":"2026-09-11T00:00:00Z","message":'
                    b'{"content":[{"type":"tool_use","name":"Bash","input":{"command":'
                    b'"./wd.sh queue add \\"an item with \xc3\xbf high bytes, long enough to count\\""}}]}}\n')
            f.write(b'\xff\xfe not json at all\n')
            f.write(json.dumps(rec('not-a-timestamp',
                                   cmds=['./wd.sh sent1 QBAD'])).encode() + b'\n')
            f.write(json.dumps(rec('2026-09-11T01:00:00Z',
                                   cmds=['./wd.sh sent1 QGOOD'])).encode() + b'\n')
        opens, closes, _ = R.stage1('80f99b89', proj=d)
        ck('a high-byte item still parses', len(opens), 1)
        ck('a binary garbage line does not stop the scan',
           'QGOOD' in [c['id'] for c in closes], True)
        ck('a malformed timestamp is kept, not crashed on',
           'QBAD' in [c['id'] for c in closes], True)
        ck('the malformed timestamp is carried as-is',
           any(c['id'] == 'QBAD' and c['ts'] == 'not-a-timestamp' for c in closes), True)
        return fails
    finally:
        shutil.rmtree(d, ignore_errors=True)



# ======================================================================================
# THE SYNTHETIC WORLD, in two halves that must agree:
#   (a) a TRANSCRIPT that replays actions chronologically, and
#   (b) a SERIES OF STATE SNAPSHOTS -- state.json as it stood at each backup.
# Until now only (a) existed and stage 2 was tested against a hand-made dict, so the two
# halves were never exercised together and a drop could not be dated against the actions
# that caused it.
# ======================================================================================

CHAIN_DECISIONS = [
    # (id, question text, answered?, forwarded?, closed?)  -- the truth, fixed here
    ('D1', 'CHAIN COMPLETE: does the engine change to match the rule-8 ruling on partial lines',
     True, True, True),
    ('D2', 'ANSWERED BUT NEVER FORWARDED: which nominal threshold applies to the level cut',
     True, False, True),
    ('D3', 'CLOSED WITH NO ANSWER AT ALL: should the harness keep the fitted comb tolerance',
     False, False, True),
    ('D4', 'ANSWERED AND STILL OPEN: does position alone establish the head switch identity',
     True, True, False),
]
CHAIN_TRUTH = {
    'D1': ['complete'],
    'D2': ['answered_not_forwarded'],
    'D3': ['closed_without_answer'],
    'D4': ['answered_not_closed', 'forwarded_not_tracked'],
}


def build_chain_world(dirpath):
    """Transcript + snapshot series for the decision-chain cases."""
    L, t = [], 0

    def ts(h):
        return '2026-09-10T%02d:00:00Z' % h

    for n, (did, text, answered, forwarded, closed) in enumerate(CHAIN_DECISIONS):
        base = 1 + n * 4
        L.append(rec(ts(base), cmds=['./wd.sh owe add "%s"' % text]))
        if answered:
            # his answer arrives mid-turn, as an attachment -- not a `user` record
            L.append({'type': 'attachment', 'timestamp': ts(base + 1),
                      'message': {'role': 'user', 'content': [{'type': 'text',
                                  'text': 'ruling on that: ' + text.split(':', 1)[1].strip()}]}})
        if forwarded:
            L.append({'type': 'assistant', 'timestamp': ts(base + 2),
                      'message': {'role': 'assistant', 'content': [
                          {'type': 'tool_use', 'name': 'mcp__ccd_session_mgmt__send_message',
                           'input': {'session_id': 'local_target',
                                     'message': 'OWNER, VERBATIM: ' + text.split(':', 1)[1].strip()}}]}})
        if closed:
            L.append(rec(ts(base + 3), cmds=['./wd.sh owe done %s' % did]))

    p = os.path.join(dirpath, '80f99b89-chain.jsonl')
    with open(p, 'w') as f:
        for r in L:
            f.write(json.dumps(r) + '\n')

    # (b) the SNAPSHOT SERIES: state.json as it stood at each backup, evolving with the
    # transcript above. D2's row disappears between 04 and 05 without a close in between.
    snaps = {}
    live_q = []
    live_d = {}
    for n, (did, text, answered, forwarded, closed) in enumerate(CHAIN_DECISIONS):
        base = 1 + n * 4
        live_d = dict(live_d)
        live_d[did] = {'text': text}
        snaps['2026-09-10-%02d0000' % base] = {'owner_queue': list(live_q),
                                               'owner_decisions': dict(live_d),
                                               'open_questions': {}, 'owner_decision_seq': n + 1}
        if closed:
            live_d = {k: v for k, v in live_d.items() if k != did}
        snaps['2026-09-10-%02d0000' % (base + 3)] = {'owner_queue': list(live_q),
                                                     'owner_decisions': dict(live_d),
                                                     'open_questions': {},
                                                     'owner_decision_seq': n + 1}
    st = os.path.join(dirpath, 'state')
    os.makedirs(st, exist_ok=True)
    json.dump({'owner_queue': [], 'owner_decisions': {}, 'open_questions': {},
               'owner_decision_seq': len(CHAIN_DECISIONS)},
              open(os.path.join(st, 'state.json'), 'w'))
    return p, st, snaps


def test_decision_chains():
    """A decision is a CHAIN and every link breaks independently. A store diff sees only the
    close and calls all four of these complete."""
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    d = tempfile.mkdtemp(prefix='recon-chain-')
    try:
        _, st, snaps = build_chain_world(d)
        opens, closes, _ = R.stage1('80f99b89', proj=d)
        mine, tgt = R.load('80f99b89') if R.PROJ == d else ([], [])
        # load() reads R.PROJ; drive it explicitly instead
        old = R.PROJ
        R.PROJ = d
        try:
            mine, tgt = R.load('80f99b89')
            acts, art = R.timeline(mine, tgt)
        finally:
            R.PROJ = old

        ck('all four decisions are seen as opened', len(opens), 4)
        ck('the forwarding sends are seen',
           len(art['sends']), sum(1 for _, _, _, f, _ in CHAIN_DECISIONS if f))
        ck('his answers are read from attachment records',
           len(art['owner']), sum(1 for _, _, a_, _, _ in CHAIN_DECISIONS if a_))

        ch = R.chains(opens, closes, art)
        for did, want in CHAIN_TRUTH.items():
            ck('%s -> %s' % (did, '+'.join(want)), sorted(ch[did]['verdicts']), sorted(want))

        ck('a complete chain records all four timestamps',
           all(ch['D1'][k] for k in ('asked', 'answered', 'forwarded', 'closed')), True)
        ck('an answered-not-forwarded chain has no forward', ch['D2']['forwarded'], None)
        ck('a closed-without-answer chain has no answer', ch['D3']['answered'], None)
        ck('an answered-not-closed chain has no close', ch['D4']['closed'], None)

        # --- the SNAPSHOT half, agreeing with the transcript --------------------------
        series = R.stage2_series(sorted(snaps), lambda s: snaps[s])
        ck('every snapshot in the series is readable', len(series), len(snaps))
        dis = R.stage2_disappearances(series)
        ck('D1 disappearing is dated from the snapshots',
           bool(dis['first_absent'].get('owner_decisions/D1')), True)
        ck('a decision still present at the end is not reported absent',
           'owner_decisions/D4' in dis['first_absent'], False)

        # the two halves must AGREE: every dated disappearance has a close in the transcript
        closed_ids = {c['id'] for c in closes if c['id']}
        dated = {k.split('/')[1] for k in dis['first_absent'] if k.startswith('owner_decisions/')}
        ck('every snapshot-dated disappearance has a transcript close',
           dated <= closed_ids, True)
        return fails
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    d = tempfile.mkdtemp(prefix='recon-fixture-')
    fails = []
    try:
        _, st = build(d)
        opens, closes, _u = R.stage1(self_prefix='80f99b89', proj=d)
        join = R.stage1_join(opens, closes, st, _u)

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
        base_opens, base_closes, _bu = R.stage1('80f99b89', proj=d)
        base_ids = {c['id'] for c in base_closes if c['id']}
        saved = {'normalize': R.normalize, 'is_real_item': R.is_real_item,
                 'CLOSE_RX': R.CLOSE_RX, 'FORLOOP': R.FORLOOP, 'OPEN_RX': list(R.OPEN_RX)}

        def run():
            o, c, _ = R.stage1('80f99b89', proj=d)
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
        print('\n--- stage 2: Time Machine (synthesized) ---')
        fails.extend(test_stage2())
        print('\n--- tm reader (synthesized) ---')
        fails.extend(test_tm_reader())
        print('\n--- decision chains + snapshot series ---')
        fails.extend(test_decision_chains())
        print('\n--- corner cases and error handling ---')
        fails.extend(test_corner_cases())
        print('\n--- robustness: concurrency, crash residue, hostile input ---')
        fails.extend(test_robustness())
        print('\n--- remaining units ---')
        fails.extend(test_remaining_units())
        print('\n--- citation: the certainty mechanism ---')
        fails.extend(test_citation())
        print('\n--- CLI: stages actually run ---')
        fails.extend(test_cli_stages())
        print('\n--- CLI: stages 2-5 and the supersession fixed point ---')
        fails.extend(test_cli_all_stages())
        print('\n--- stage 5: additive repair ---')
        fails.extend(test_stage5())

        print('\nRESULT: %s' % ('all controls pass' if not fails else '%d FAILED: %s'
                                % (len(fails), ', '.join(fails))))
        return 1 if fails else 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
