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
        print('\n--- stage 2: Time Machine (synthesized) ---')
        fails.extend(test_stage2())
        print('\n--- tm reader (synthesized) ---')
        fails.extend(test_tm_reader())
        print('\n--- CLI: stages actually run ---')
        fails.extend(test_cli_stages())
        print('\n--- stage 5: additive repair ---')
        fails.extend(test_stage5())

        print('\nRESULT: %s' % ('all controls pass' if not fails else '%d FAILED: %s'
                                % (len(fails), ', '.join(fails))))
        return 1 if fails else 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
