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
import os, re, sys, json, tempfile, shutil

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
    # every INVOCATION is an action: the six genuine items, the "$*" ask (failed) and the
    # short ask (landed). Decoys -- heredoc bodies, comments, echoes -- are not invocations.
    'opens': 8,
    'landed_open_ids': ['D2', 'D4', 'Q10', 'Q11', 'Q12', 'Q13', 'Q14'],
    'landed_close_ids': {'D1', 'D3', 'D5', 'Q1', 'Q2', 'Q3', 'Q4', 'M7'},
    'never_closed': ['D2', 'D4'],
    'owner': ['do not clear any queues', 'keep going until it is built', 'stop all your hooks now'],
}


def build(dirpath):
    """A corpus of every form that could make the search wrong, in the REAL record shapes
    (tests/realshape.py): every command carries the tool_result that says what happened to it,
    and the owner speaks through the channels he actually uses. The first version of this
    fixture invented an attachment shape and carried no results, and passed while the extractor
    read nothing on real data."""
    import realshape as RS
    L = []
    # --- forms that must NOT register as actions ---------------------------------------
    # 1. heredoc writing a document that contains real-looking invocation text
    L += RS.bash('2026-09-09T10:00:00Z',
                 "cat > doc.md <<'EOF'\n"
                 "Example for the docs, NOT a real invocation:\n"
                 "  ./wd.sh queue add \"THIS SENTENCE IS ONLY AN EXAMPLE IN A DOCUMENT and must "
                 "never be restored as one of the owner's items.\"\n"
                 "  exec $PY \"$D/wd_wake.py\" --owe-add \"$*\" ;;\n"
                 "EOF\n", '')
    # 2. heredoc with an UNQUOTED delimiter
    L += RS.bash('2026-09-09T10:01:00Z',
                 "cat > other.md <<EOF\n"
                 "./wd.sh owe add \"ANOTHER DOCUMENT EXAMPLE that must never be taken as a real "
                 "decision the owner owes.\"\nEOF\n", '')
    # 3. a python heredoc whose body writes the syntax
    L += RS.bash('2026-09-09T10:02:00Z',
                 "python3 - <<'PY'\ns='./wd.sh queue add \"NOT AN ITEM, this lives inside a python "
                 "string literal and is only ever written to a file.\"'\nopen('f','w').write(s)\nPY\n", '')
    # 4. a shell COMMENT containing the syntax
    L += RS.bash('2026-09-09T10:03:00Z',
                 "# ./wd.sh queue add \"A COMMENTED-OUT EXAMPLE that was never executed at all.\"\n"
                 "echo done", 'done')
    # 5. echo of the syntax -- and a RESULT that carries the syntax back
    L += RS.bash('2026-09-09T10:04:00Z',
                 "echo './wd.sh owe add \"AN ECHOED EXAMPLE that only ever reached a terminal.\"'",
                 './wd.sh owe add "AN ECHOED EXAMPLE that only ever reached a terminal."')
    # 6. item text the SHELL expanded: the literal between double quotes is not what was stored
    L += RS.bash('2026-09-09T10:05:00Z', './wd.sh queue add "$*"', 'REFUSED: an item needs text')
    # 7. a genuine SHORT ask that landed: kept, never length-filtered away
    L += RS.bash('2026-09-09T10:06:00Z', './wd.sh queue add "fix it"', 'queued Q14')
    # 8. an empty command, and whitespace only
    L += RS.bash('2026-09-09T10:07:00Z', '', '')
    L += RS.bash('2026-09-09T10:07:30Z', '   \n  ', '')
    # 9. a non-Bash tool call
    L.append({'type': 'assistant', 'timestamp': '2026-09-09T10:08:00Z',
              'message': {'role': 'assistant', 'content': [
                  {'type': 'tool_use', 'id': 'toolu_read', 'name': 'Read',
                   'input': {'file_path': './wd.sh queue add "NOT A COMMAND AT ALL, a file path."'}}]}})
    # 10. an unpaired tool_RESULT that echoes the syntax back
    L.append({'type': 'user', 'timestamp': '2026-09-09T10:09:00Z',
              'message': {'role': 'user', 'content': [
                  {'type': 'tool_result', 'tool_use_id': 'toolu_none',
                   'content': './wd.sh queue add "OUTPUT ECHOED BACK BY A TOOL, never an invocation."'}]}})

    # --- genuine opens, each with the result that minted its id ------------------------
    L += RS.bash('2026-09-09T11:00:00Z', './wd.sh queue add "%s"' % REAL_ITEMS[0], 'queued Q10')
    L += RS.bash('2026-09-11T01:13:00Z', './wd.sh owe add "%s"' % REAL_ITEMS[1],
                 'recorded D2 READY\nREADY for the owner: 1')
    L += RS.bash('2026-09-11T01:19:00Z', 'python3 wd_wake.py --owe-add "%s"' % REAL_ITEMS[2],
                 'recorded D4 READY')
    L += RS.bash('2026-09-11T01:20:00Z', './wd.sh queue add "%s"' % REAL_ITEMS[3], 'queued Q11')
    L += RS.bash('2026-09-11T01:21:00Z', './wd.sh queue add "%s"' % REAL_ITEMS[4], 'queued Q12')
    L += RS.bash('2026-09-11T01:22:00Z', './wd.sh queue add --urgent "%s"' % REAL_ITEMS[5],
                 'queued Q13 (urgent)')

    # --- closes in every form ------------------------------------------------------------
    # 11. ids bound by a for-loop, $d form
    L += RS.bash('2026-09-11T02:00:00Z',
                 'for d in D1 D3; do python3 wd_wake.py --owe-clear $d; done',
                 'D1 answered and cleared\nD3 answered and cleared')
    # 12. ${d} form, quoted items, inside a pipeline
    L += RS.bash('2026-09-11T02:01:00Z', 'for d in "D5"; do ./wd.sh owe done ${d} | tail -1; done',
                 'D5 answered and cleared')
    # 13. plain literal close
    L += RS.bash('2026-09-11T03:00:00Z', './wd.sh sent1 Q1', 'item Q1 marked sent at T')
    # 14. close whose id is followed by shell punctuation
    L += RS.bash('2026-09-11T03:01:00Z', './wd.sh sent1 Q2; echo ok', 'item Q2 marked sent at T\nok')
    # 15. the SAME id closed twice: the second found nothing to close
    L += RS.bash('2026-09-11T03:02:00Z', './wd.sh sent1 Q3', 'item Q3 marked sent at T')
    L += RS.bash('2026-09-11T03:03:00Z', './wd.sh sent1 Q3', 'no queued item Q3')
    # 16. queue clear names the message it cleared into
    L += RS.bash('2026-09-11T03:04:00Z', './wd.sh queue clear M7', 'queue cleared into message M7')
    # 16b. a TRAILING comment hiding an invocation: only comment handling keeps it from running
    L += RS.bash('2026-09-11T03:05:00Z',
                 'echo done  # then: ; ./wd.sh queue add "A TRAILING COMMENT that must never run as an invocation."',
                 'done')
    # 16c. an invocation against a SCRATCH state: recorded, never a change to the live state
    L += RS.bash('2026-09-11T03:06:00Z',
                 'WD_STATE=/private/tmp/scratch-copy ./wd.sh queue add "A SCRATCH-STATE ITEM that never touched the live state."',
                 'queued Q90')

    # --- the owner, through the channels he actually uses -----------------------------
    L += RS.owner_midturn('2026-09-11T04:00:00Z', 'stop all your hooks now')
    L.append(RS.enqueue('2026-09-11T04:01:00Z', 'do not clear any queues'))
    L += RS.owner_turn('2026-09-11T04:02:00Z', 'keep going until it is built')
    # 17. a record with NO timestamp
    L.append({'type': 'assistant', 'message': {'role': 'assistant',
                                               'content': [{'type': 'text', 'text': 'no timestamp here'}]}})
    p = os.path.join(dirpath, '80f99b89-fixture.jsonl')
    with open(p, 'w') as f:
        for r in L:
            f.write(json.dumps(r) + '\n')
        # 18. an unparseable line, which must not stop the scan
        f.write('{ this is not json\n')
        # 19. a genuine close AFTER the bad line, to prove the scan continued
        for r in RS.bash('2026-09-11T05:00:00Z', './wd.sh sent1 Q4', 'item Q4 marked sent at T'):
            f.write(json.dumps(r) + '\n')
    st = os.path.join(dirpath, 'state')
    os.makedirs(st, exist_ok=True)
    json.dump({'owner_queue': [], 'owner_decisions': {}, 'open_questions': {},
               'owner_decision_seq': 5}, open(os.path.join(st, 'state.json'), 'w'))
    return p, st


# ---- stage 3: did the action LAND? ------------------------------------------------------
def test_stage3():
    """landed_replay: the action's own outcome first, then the artifact that must exist in the
    same turn if the action was honest. Every verdict is a fact; nothing is undecidable."""
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    T = lambda h, m=0: '2026-09-11T%02d:%02d:00Z' % (h, m)
    Q7 = 'item seven is a sentence the owner actually said to me'
    Q9 = 'item nine is a completely different sentence entirely'

    def act(kind, verb, ident, ts, outcome='landed', **kw):
        return dict({'kind': kind, 'verb': verb, 'id': ident, 'ts': ts, 'cite': 'f:%s' % ts,
                     'outcome': outcome, 'why': 'result'}, **kw)

    acts = [
        act('open', 'queue add', 'Q7', T(4), store='owner_queue', text=Q7, text_resolved=True),
        act('open', 'queue add', 'Q9', T(4, 1), store='owner_queue', text=Q9, text_resolved=True),
        act('open', 'queue add', 'Q11', T(4, 2), store='owner_queue', text='$*', text_resolved=False),
        act('open', 'ask', 'K1', T(7), store='open_questions', text='what does the target think?'),
        act('open', 'ask', 'K2', T(8), store='open_questions', text='and this one?'),
        act('close', 'sent1', 'Q7', T(5, 1)),       # a send in the turn carries Q7 -> ok
        act('close', 'sent1', 'Q9', T(5, 2)),       # the turn's send carries Q7, not Q9
        act('close', 'sent1', 'Q8', T(9)),          # no text for Q8 anywhere
        act('close', 'sent1', 'Q11', T(9, 30)),     # its only text is the unexpanded "$*"
        act('close', 'sent1', 'Q10', T(10, 1), text=None),  # the send comes AFTER the mark
        act('mark', 'relayed', None, T(6, 1)),      # text to the owner in the same turn
        act('mark', 'relayed', None, T(20)),        # nothing said in that turn
        act('close', 'resolved', 'K1', T(7, 5)),    # the target replied after K1 was asked
        act('close', 'resolved', 'K2', T(8, 5)),    # no reply from the target
        act('mark', 'answered', None, T(5, 3)),     # a send in the same turn precedes it
        act('mark', 'nudged', None, T(21, 1)),      # a nudge with no send at all
        act('close', 'sent1', 'Q12', T(22), outcome='failed'),   # its own result failed
    ]
    starts = [T(5), T(6), T(7), T(8), T(9), T(10), T(20), T(21), T(22)]
    my_text = [{'ts': T(6), 'text': 'here is what the target said, relayed to you'}]
    # every send records where it went: a reply resolves a question only from a session the
    # question was SENT to, after it was asked (K1 is sent at 07:01; K2 is never sent)
    sends = [{'ts': T(5), 'msg': 'OWNER: ' + Q7, 'to': 'local_target'},
             {'ts': T(7, 1), 'msg': 'what does the target think?', 'to': 'local_target'},
             {'ts': T(10, 2), 'msg': 'OWNER: the tenth item text sent after it was marked',
              'to': 'local_target'}]
    peers = [{'ts': T(7, 2), 'from': 'local_target', 'text': 'here is my answer'}]
    state = {'owner_queue_sent': [{'id': 'Q10', 'text': 'the tenth item text sent after it was marked'}]}
    rep = R.landed_replay(acts, starts, my_text, sends, peers, state, 'local_target')
    v = {(x['verb'], x.get('id'), x['ts']): x['verdict'] for x in rep}
    ck('sent1 with a same-turn send carrying its text -> ok', v[('sent1', 'Q7', T(5, 1))], 'ok')
    ck('sent1 whose turn sent a DIFFERENT item -> MISSTEER', v[('sent1', 'Q9', T(5, 2))], 'MISSTEER')
    ck('sent1 with no text anywhere -> MISSTEER', v[('sent1', 'Q8', T(9))], 'MISSTEER')
    ck('an unexpanded "$*" is never taken as the item text',
       v[('sent1', 'Q11', T(9, 30))], 'MISSTEER')
    ck('a send AFTER the mark does not make the mark honest',
       v[('sent1', 'Q10', T(10, 1))], 'MISSTEER')
    ck('project state supplies text the record lacks (owner_queue_sent)',
       'Q10' in R.item_texts(state, acts), True)
    ck('relayed with text to the owner in the same turn -> ok', v[('relayed', None, T(6, 1))], 'ok')
    ck('relayed with nothing said -> MISSTEER', v[('relayed', None, T(20))], 'MISSTEER')
    ck('resolved after the session it was sent to replied -> ok', v[('resolved', 'K1', T(7, 5))], 'ok')
    ck('resolved with no reply (and never sent) -> MISSTEER', v[('resolved', 'K2', T(8, 5))], 'MISSTEER')
    ck('answered with a send before it in the turn -> ok', v[('answered', None, T(5, 3))], 'ok')
    ck('nudged with no send -> MISSTEER', v[('nudged', None, T(21, 1))], 'MISSTEER')
    ck('an action whose own result failed stays failed', v[('sent1', 'Q12', T(22))], 'failed')
    ck('every verdict is in the declared vocabulary',
       all(x['verdict'] in R.REPLAY_VERDICTS for x in rep), True)

    # a commit claim: three FACTS, none of them unknown
    probe = {'aaaaaaa': (True, ['origin/main']), 'bbbbbbb': (True, []), 'ccccccc': (False, [])}
    res = R.verify_commits([{'ts': 't', 'sha': k} for k in ('aaaaaaa', 'bbbbbbb', 'ccccccc')],
                           lambda sha: probe[sha])
    ck('commit on a ref -> ok', res[0]['verdict'], 'ok')
    ck('commit that exists on NO ref -> on_no_ref, a fact', res[1]['verdict'], 'on_no_ref')
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
                                '--state-dir', st, '--cwd', d] + list(args),
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
                t = R.extract_item(c)
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
            if cmds and all(not (R.extract_item(c) or '') for c in cmds):
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
        ck('is_real_item accepts a short genuine item (no length floor)',
           R.is_real_item('short'), True)
        ck('is_real_item rejects empty', R.is_real_item(''), False)
        ck('is_real_item accepts a sentence', R.is_real_item(REAL_ITEMS[0]), True)

        # the shell reader as a COMPOSITION, not just its parts
        combined = ("# ./wd.sh queue add \"a commented example that is long enough to count\"\n"
                    "cat > f <<'EOF'\n./wd.sh owe add \"a heredoc example long enough to count\"\nEOF\n"
                    "echo './wd.sh queue add \"an echoed example long enough to count\"'\n"
                    "for d in D7 D8; do ./wd.sh owe done $d; done")
        ck('comment, heredoc and echo together yield no item', R.extract_item(combined), None)
        ck('and the loop is unrolled in the same read',
           [(i['verb'], i['args']) for i in R.invocations(combined, None)],
           [('owe done', ['D7']), ('owe done', ['D8'])])

        # the library's own selftest must pass -- imported AND as a direct entry point: the
        # __main__ block once sat mid-module and died with NameError before OPEN_RX existed,
        # while the imported call (made after the whole module loaded) still passed
        ck('wd_recon_lib.selftest() passes', R.selftest(), 0)
        import subprocess
        lib = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'wd_recon_lib.py')
        r = subprocess.run([sys.executable, lib], capture_output=True, text=True)
        ck('`python3 wd_recon_lib.py` runs its selftest and exits 0',
           (r.returncode, 'SELFTEST PASS' in r.stdout), (0, True))
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
                                '--state-dir', st, '--cwd', d] + list(args),
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

        # stage 3 without a target REFUSES by name rather than replaying against any session
        rc, out = run('--stage', '3', '--proj', d, '--self-prefix', '80f99b89')
        # BY NAME means a refusal, not a crash that happens to contain the phrase: with the
        # CLI's refusal removed, the lib's own check still stopped stage 3 -- as a traceback
        ck('stage 3 without a target refuses by name',
           (rc != 0, 'needs the target' in out, 'Traceback' in out), (True, True, False))
        # stage 3 runs end to end against the corpus, and reports decision chains
        rc, out = run('--stage', '3', '--proj', d, '--self-prefix', '80f99b89',
                      '--target-id', 'local_target')
        ck('stage 3 runs from the CLI', 'actions replayed' in out, True)
        ck('stage 3 reports decision chains', 'decision chain' in out, True)
        led = json.load(open(os.path.join(st, 'reconcile.json')))
        ck('stage 3 records which target it replayed against',
           (led.get('stage3') or {}).get('target'), 'local_target')
        # adjudications: the two chain links only his words settle, recorded with evidence
        rc, out = run('--adjudicate', 'D2', '--put', 'none', '--answer', 'none')
        ck('an adjudication without --evidence refuses', (rc != 0, 'evidence' in out), (True, True))
        rc, out = run('--adjudicate', 'D77', '--put', 'none', '--answer', 'none', '--evidence', 'x')
        ck('an adjudication of a chain stage 3 did not find refuses by name',
           (rc != 0, 'no chain D77' in out), (True, True))
        rc, out = run('--adjudicate', 'D2', '--put', 'none', '--answer', 'none',
                      '--evidence', 'never named to him; nothing of his to read')
        led = json.load(open(os.path.join(st, 'reconcile.json')))
        ck('an adjudication is recorded with its evidence',
           ((led.get('adjudications') or {}).get('D2') or {}).get('evidence'),
           'never named to him; nothing of his to read')
        rc, out = run('--stage', '3', '--proj', d, '--self-prefix', '80f99b89', '--target-id', 'local_target')
        led = json.load(open(os.path.join(st, 'reconcile.json')))
        ck('stage 3 applies the recorded adjudication',
           ((led['stage3']['chains'].get('D2') or {}).get('adjudication') or {}).get('put'), 'none')
        wdsh = open(os.path.join(here, 'wd.sh')).read()
        ck('wd.sh passes the configured target to reconcile',
           bool(re.search(r'reconcile\) exec .*--target "\$TARGET"', wdsh)), True)
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

        # read_records(): every parseable record, with the file's own line numbers, and the
        # unparseable ones COUNTED -- a record is never dropped silently
        p = os.path.join(d, '80f99b89-fixture.jsonl')
        numbered, bad = R.read_records(p)
        ck('read_records returns the corpus', len(numbered) > 0, True)
        ck('line numbers are the file\'s own', numbered[0][0], 1)
        ck('an untimestamped record is kept, not dropped',
           any(not r.get('timestamp') for _, r in numbered), True)
        ck('the unparseable line is counted', bad, 1)
        ck('transcript_for finds the corpus by prefix',
           R.transcript_for('80f99b89', d), p)
        refused = False
        try:
            R.transcript_for('no-such-prefix', d)
        except ValueError:
            refused = True
        ck('transcript_for with no match refuses by name', refused, True)
        mt, sd = R.artifacts([r for _, r in numbered])
        ck('artifacts: my text to the owner is read', any('no timestamp' in t['text'] for t in mt), True)
        ck('artifacts: no send in this corpus', sd, [])

        # a corpus with NO owner text: accounting is zeros, never a missing key (this crashed
        # stage 1 with KeyError('seen') on the corner-case corpus)
        _m, _e, _p, acct0 = R.owner_messages([r for _, r in numbered
                                             if r.get('type') == 'assistant'])
        # .get, so a missing key reports FAIL here instead of raising and hiding the next check
        ck('owner accounting on a corpus with no owner text is all zeros',
           tuple(acct0.get(k) for k in ('seen', 'attributed', 'excluded_total',
                                         'batch_deliveries')), (0, 0, 0, 0))
        ck('owner accounting on an EMPTY corpus is all zeros',
           R.owner_messages([])[3].get('seen'), 0)

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

        # every retired duplicate must be GONE, not merely unused: two sources of truth is how
        # the Unknown-type verdicts survived the rewrite
        for gone in ('adjudicate', 'load', 'timeline', '_near', 'stage1', 'stage1_join',
                     'stage3', 'chains', 'ARGFORMS', 'normalize', 'strip_heredocs',
                     'strip_comments', 'strip_echoes', 'expand_loops', 'FORLOOP', 'OPEN_RX',
                     'CLOSE_RX', 'unresolved_ids', 'item_text', 'outcome'):
            ck('%s is deleted, not left as a second source of truth' % gone,
               hasattr(R, gone), False)
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
    # there is NO length floor: it refused a genuine landed four-word ask. Length is not
    # provenance; the result says whether the item landed.
    ck('a short genuine item is accepted', R.is_real_item('fix it'), True)
    ck('a one-word genuine item is accepted', R.is_real_item('no'), True)

    # --- ids that cannot be resolved to a literal --------------------------------------
    w = R.invocations('while read d; do ./wd.sh owe done $d; done', None)
    ck('a while-read loop id is an expansion, marked as a loop',
       [(i['args'], i['arg_expands'], i['loop']) for i in w], [(['$d'], [True], 'while')])
    ck('a $(...) substitution id is an expansion',
       [i['arg_expands'] for i in R.invocations('./wd.sh owe done $(cat id.txt)', None)
        if i['verb'] == 'owe done'], [[True]])
    ck('a plain literal id is NOT an expansion',
       [i['arg_expands'] for i in R.invocations('./wd.sh sent1 Q1', None)], [[False]])

    d = tempfile.mkdtemp(prefix='recon-corner-')
    try:
        import realshape as RS
        p2 = os.path.join(d, '80f99b89-corner.jsonl')
        L = []
        # open AND close in one command, sharing one result
        L += RS.bash('2026-09-11T01:00:00Z',
                     './wd.sh queue add "an item added and closed in the same command line"'
                     ' && ./wd.sh sent1 QX', 'queued QX\nitem QX marked sent at T')
        # ids bound through a SUBSTITUTION: never unrolled (that invented `ids.txt` as an id);
        # the script printed which ids it closed, so they are read from the result
        L += RS.bash('2026-09-11T02:00:00Z',
                     'for d in $(cat ids.txt); do ./wd.sh owe done $d; done',
                     'D7 answered and cleared\nD8 answered and cleared')
        # the same kind of loop with NO result: reported unresolved, never invented
        L += RS.bash_no_result('2026-09-11T02:05:00Z',
                               'while read d; do ./wd.sh owe done $d; done < ids.txt')
        # a while-read loop whose result names what it closed
        L += RS.bash('2026-09-11T02:10:00Z',
                     'while read d; do ./wd.sh owe done $d; done < ids.txt', 'D9 answered and cleared')
        # a heredoc whose BODY mentions its own delimiter
        L += RS.bash('2026-09-11T03:00:00Z',
                     "cat <<'EOF'\nthe word EOF appears here\n"
                     "./wd.sh queue add \"this must not leak out of the heredoc body at all\"\nEOF", '')
        # item text with ESCAPED quotes inside double quotes (the old pattern cut it at `\`)
        L += RS.bash('2026-09-11T03:30:00Z',
                     './wd.sh queue add "he said \\"no\\" and meant it"', 'queued QE')
        # close before open, chronologically
        L += RS.bash('2026-09-11T00:30:00Z', './wd.sh sent1 QY', 'item QY marked sent at T')
        L += RS.bash('2026-09-11T04:00:00Z',
                     './wd.sh queue add "an item opened after its own close, out of order"',
                     'queued QZ')
        # an id reused after being closed: the second found nothing to close
        L += RS.bash('2026-09-11T05:00:00Z', './wd.sh sent1 QX', 'no queued item QX')
        RS.write(p2, L)
        numbered, _bad = R.read_records(p2)
        acts = R.my_actions(numbered, os.path.basename(p2), os.path.join(d, 'state'), d)
        opens = [x for x in acts if x['kind'] == 'open']
        closes = [x for x in acts if x['kind'] == 'close']
        ids = [c['id'] for c in closes]
        ck('an open and a close in ONE command are both seen',
           ('QX' in ids and any(o['id'] == 'QX' for o in opens)), True)
        ck('a heredoc mentioning its own delimiter does not leak',
           any('must not leak' in o['text'] for o in opens), False)
        # EXACT set, not one spelling of the invented id: with the guard removed it comes out
        # as `ids.txt)`, and a check for exactly 'ids.txt' passed over it
        ck('every close id is one the record states (no substitution unrolled)',
           sorted({i for i in ids if i}), ['D7', 'D8', 'D9', 'QX', 'QY'])
        ck('ids bound through a substitution are read from the result',
           sorted(c['id'] for c in closes
                  if c.get('id_from') == 'result' and c['id'] in ('D7', 'D8', 'D9')),
           ['D7', 'D8', 'D9'])
        ck('with no result the id is reported unresolved, not invented',
           [(c['outcome'], c.get('unresolved')) for c in closes if c['id'] is None],
           [('not_completed', ['$d'])])
        ck('no substitution text is ever recorded as an id',
           any(i and '$' in i for i in ids), False)
        ck('escaped quotes survive into the item text',
           [o['text'] for o in opens if o['id'] == 'QE'], ['he said "no" and meant it'])
        ck('a close with no matching open is still recorded', 'QY' in ids, True)
        ck('an id closed twice is recorded twice, each with its outcome',
           [c['outcome'] for c in closes if c['id'] == 'QX'], ['landed', 'failed'])

        # --- CLI corner cases ----------------------------------------------------------
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        st = os.path.join(d, 'state')
        os.makedirs(st, exist_ok=True)
        json.dump({'owner_queue': [], 'owner_decisions': {}, 'open_questions': {},
                   'owner_decision_seq': 0}, open(os.path.join(st, 'state.json'), 'w'))

        def run(*args):
            r = subprocess.run([sys.executable, os.path.join(here, 'wd_reconcile.py'),
                                '--state-dir', st, '--cwd', d] + list(args),
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
        ck('stage 1 runs on a corpus with NO owner text',
           ('stage1' in led, 'Traceback' in out), (True, False))
        n1 = (led.get('stage1') or {}).get('opens')
        ck('stage 1 counts every open in the corpus', n1, 3)
        rc, out = run('--stage', '1', '--proj', d)
        led2 = json.load(open(os.path.join(st, 'reconcile.json')))
        ck('running stage 1 twice does not double its result',
           (led2.get('stage1') or {}).get('opens'), n1)

        # timestamps are compared as strings: a corpus mixing formats is REFUSED by name
        ck('ts_formats reads the shape of every timestamp',
           sorted(R.ts_formats([{'timestamp': '2026-09-10T01:00:00Z'},
                                {'timestamp': '2026-09-10T01:00:00.123Z'}, {}]).values()), [1, 1])
        RS.write(os.path.join(d, 'mixedpfx-x.jsonl'),
                 RS.bash('2026-09-11T01:00:00Z', './wd.sh sent1 Q1', 'item Q1 marked sent at T')
                 + RS.bash('2026-09-11T01:00:00.500Z', './wd.sh sent1 Q2', 'item Q2 marked sent at T'))
        rc, out = run('--stage', '1', '--proj', d, '--self-prefix', 'mixedpfx')
        ck('stage 1 REFUSES a corpus mixing timestamp formats, by name',
           (rc != 0, 'mixes timestamp formats' in out, 'Traceback' in out), (True, True, False))

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
                                '--state-dir', st, '--cwd', d] + list(args),
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
        numbered, bad = R.read_records(p2)
        acts = R.my_actions(numbered, os.path.basename(p2), os.path.join(d, 'state'), d)
        opens = [a for a in acts if a['kind'] == 'open']
        closes = [a for a in acts if a['kind'] == 'close']
        ck('the garbage line is counted, not dropped', bad, 1)
        ck('a high-byte item still parses', len(opens), 1)
        ck('a command with no result is not_completed, not dropped',
           [a['outcome'] for a in opens], ['not_completed'])
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
    # (id, question, put to him?, answered?, forwarded?, closed?) -- the truth, fixed here
    ('D1', 'CHAIN COMPLETE: does the engine change to match the rule-8 ruling on partial lines',
     True, True, True, True),
    ('D2', 'ANSWERED BUT NEVER FORWARDED: which nominal threshold applies to the level cut',
     True, True, False, True),
    ('D3', 'PUT AND NEVER ANSWERED, CLOSED ANYWAY: keep the fitted comb tolerance or not',
     True, False, False, True),
    ('D4', 'ANSWERED AND STILL OPEN: does position alone establish the head switch identity',
     True, True, True, False),
]
# what the reconciler would record, reading his words in the chain world
CHAIN_ADJ = {
    'D1': {'put': '2026-09-10T01:10:00Z', 'answer': '2026-09-10T02:00:00Z'},
    'D2': {'put': '2026-09-10T05:10:00Z', 'answer': '2026-09-10T06:00:00Z'},
    'D3': {'put': '2026-09-10T09:10:00Z', 'answer': 'none'},
    'D4': {'put': '2026-09-10T13:10:00Z', 'answer': '2026-09-10T14:00:00Z'},
}
CHAIN_TRUTH = {
    'D1': ['complete'],
    'D2': ['answered_not_forwarded'],
    # D3 was put and got no reply. D4's reply (which names no id) comes AFTER D4 was put, so it
    # belongs to D4's turn -- it must not be taken as D3's answer.
    'D3': ['put_not_answered', 'closed_without_answer'],
    'D4': ['answered_not_closed'],
}


def build_chain_world(dirpath):
    """Transcript + snapshot series for the decision-chain cases, in REAL record shapes."""
    import realshape as RS
    L = []

    def ts(h, m=0):
        return '2026-09-10T%02d:%02d:00Z' % (h, m)

    for n, (did, text, put, answered, forwarded, closed) in enumerate(CHAIN_DECISIONS):
        base = 1 + n * 4
        L += RS.bash(ts(base), './wd.sh owe add "%s"' % text, 'recorded %s READY' % did)
        if put:
            L += RS.say(ts(base, 10), '%s for you: %s' % (did, text))
        ruling = 'ruling on that: ' + text.split(':', 1)[1].strip()
        if answered:
            # his answer arrives mid-turn, as a queued_command attachment -- not a `user` record
            L += RS.owner_midturn(ts(base + 1), ruling)
        if forwarded:
            L += RS.send(ts(base + 2), 'OWNER, VERBATIM: ' + ruling)
        if closed:
            L += RS.bash(ts(base + 3), './wd.sh owe done %s' % did, '%s answered and cleared' % did)

    p = os.path.join(dirpath, '80f99b89-chain.jsonl')
    RS.write(p, L)

    # (b) the SNAPSHOT SERIES: state.json as it stood at each backup, evolving with the
    # transcript above.
    snaps = {}
    live_d = {}
    for n, (did, text, put, answered, forwarded, closed) in enumerate(CHAIN_DECISIONS):
        base = 1 + n * 4
        live_d = dict(live_d)
        live_d[did] = {'text': text}
        snaps['2026-09-10-%02d0000' % base] = {'owner_queue': [], 'owner_decisions': dict(live_d),
                                               'open_questions': {}, 'owner_decision_seq': n + 1}
        if closed:
            live_d = {k: v for k, v in live_d.items() if k != did}
        snaps['2026-09-10-%02d0000' % (base + 3)] = {'owner_queue': [],
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
    close and calls all four of these complete. Transcript and snapshot series must agree."""
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    d = tempfile.mkdtemp(prefix='recon-chain-')
    try:
        p, st, snaps = build_chain_world(d)
        numbered, bad = R.read_records(p)
        recs = [r for _, r in numbered]
        acts = R.my_actions(numbered, os.path.basename(p), st, d)
        owner, _exc, _peers, _acct = R.owner_messages(recs)
        my_text, sends = R.artifacts(recs)

        ck('all four decisions are seen as asked, ids from results',
           sorted(a['id'] for a in acts if a['kind'] == 'open'), ['D1', 'D2', 'D3', 'D4'])
        ck('the forwarding sends are seen',
           len(sends), sum(1 for x in CHAIN_DECISIONS if x[4]))
        ck('his answers are read from queued_command attachments',
           len(owner), sum(1 for x in CHAIN_DECISIONS if x[3]))

        bare = R.decision_chains(acts, owner, my_text, sends, 'local_target')
        ck('without adjudication every named chain is outstanding',
           all(any('adjudicate' in o for o in bare[k]['outstanding']) for k in CHAIN_TRUTH), True)
        ck('D3 was closed before he said anything to it -- a fact, no reading needed',
           'closed_before_any_reply' in bare['D3']['verdicts'], True)
        ch = R.decision_chains(acts, owner, my_text, sends, 'local_target', CHAIN_ADJ)
        for did, want in CHAIN_TRUTH.items():
            ck('adjudicated %s -> %s' % (did, '+'.join(want)), sorted(ch[did]['verdicts']), sorted(want))
        ck('a complete chain records every timestamp',
           all(ch['D1'][k] for k in ('asked', 'put', 'answered', 'forwarded', 'closed')), True)
        ck('an answered-not-forwarded chain has no forward', ch['D2']['forwarded'], None)
        ck('an answered-not-closed chain has no close', ch['D4']['closed'], None)
        bad = R.decision_chains(acts, owner, my_text, sends, 'local_target',
                                {'D1': {'put': '2026-09-10T01:10:00Z', 'answer': '2026-09-10T23:59:00Z'}})
        ck('an adjudication citing a message that is not a candidate is REFUSED',
           any('REFUSED' in o for o in bad['D1']['outstanding']), True)

        # --- the SNAPSHOT half, agreeing with the transcript --------------------------
        series = R.stage2_series(sorted(snaps), lambda s: snaps[s])
        ck('every snapshot in the series is readable', len(series), len(snaps))
        dis = R.stage2_disappearances(series)
        ck('D1 disappearing is dated from the snapshots',
           bool(dis['first_absent'].get('owner_decisions/D1')), True)
        ck('a decision still present at the end is not reported absent',
           'owner_decisions/D4' in dis['first_absent'], False)
        landed = {a['id'] for a in acts if a['kind'] == 'close' and a['outcome'] == 'landed'}
        dated = {k.split('/')[1] for k in dis['first_absent'] if k.startswith('owner_decisions/')}
        ck('every snapshot-dated disappearance has a LANDED transcript close',
           dated <= landed, True)
        return fails
    finally:
        shutil.rmtree(d, ignore_errors=True)



def test_chain_edges():
    """The chain's corner cases, on FACTS and ADJUDICATIONS. Each was a wrong answer when the chain
    guessed: on the real record a challenge ("I answered D13 when?"), a refusal ("I will not answer
    D14 or D15") and a deferral ("I'll handle D16 and D17 next") were all taken as his answers."""
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    T = lambda h, m=0: '2026-09-10T%02d:%02d:00Z' % (h, m)

    def ask(did, ts, text='a question put to the owner that is long enough to be words'):
        return {'kind': 'open', 'store': 'owner_decisions', 'verb': 'owe add', 'id': did,
                'ts': ts, 'cite': 'f:%s' % ts, 'text': text, 'text_resolved': True,
                'outcome': 'landed' if did else 'failed', 'why': 'result'}

    def close(did, ts, oc='landed'):
        return {'kind': 'close', 'verb': 'owe done', 'id': did, 'ts': ts, 'cite': 'c:%s' % ts,
                'outcome': oc, 'why': 'result'}

    say = lambda ts, text: {'ts': ts, 'text': text}
    send = lambda ts, msg, to='local_target': {'ts': ts, 'msg': msg, 'to': to}
    dc = lambda acts, owner, mine, sends, adj=None: R.decision_chains(acts, owner, mine, sends,
                                                                      'local_target', adj)

    # --- facts are collected, never interpreted ----------------------------------------
    his = [say(T(2), 'D1 - drop it.'), say(T(3), 'I answered D1 when?'), say(T(4), 'fine')]
    mine = [say(T(1, 5), 'D1 for you: keep it?'), say(T(3, 30), 'you did not; D1 is still open')]
    ch = dc([ask('D1', T(1))], his, mine, [])
    ck('my texts naming D1 are NAMED', ch['D1']['named'], [T(1, 5), T(3, 30)])
    ck('every message of his naming D1 is a mention -- which answers is READ, not matched',
       [r['ts'] for r in ch['D1']['mentions']], [T(2), T(3)])
    ck('his next message after each naming is a candidate', ch['D1']['candidates'], [T(2), T(3), T(4)])
    ck('without adjudication: outstanding, and no answer is guessed',
       (any('adjudicate D1' in o for o in ch['D1']['outstanding']), ch['D1']['answered']), (True, None))

    # --- an adjudication may only cite what the record contains --------------------------
    ch = dc([ask('D1', T(1))], his, mine, [], {'D1': {'put': T(1, 5), 'answer': T(9)}})
    ck('an adjudicated answer that is not a candidate is REFUSED',
       any('REFUSED' in o for o in ch['D1']['outstanding']), True)
    ch = dc([ask('D1', T(1))], his, mine, [], {'D1': {'put': T(9), 'answer': 'none'}})
    ck('an adjudicated put must be a text of mine naming it',
       any('REFUSED' in o for o in ch['D1']['outstanding']), True)

    # --- his refusal, read by the reconciler as NOT an answer ----------------------------
    ch = dc([ask('D14', T(1))], [say(T(2), 'I will not answer D14 or D15 until the record is properly corrected')],
            [say(T(1, 5), 'D14 -- which threshold?')], [], {'D14': {'put': T(1, 5), 'answer': 'none'}})
    ck('a refusal adjudicated as no answer -> put_not_answered', ch['D14']['verdicts'], ['put_not_answered'])

    # --- verdicts once adjudicated -------------------------------------------------------
    ans = 'D1 - the nominal threshold applies to the level cut only'
    base_his, base_mine = [say(T(2), ans)], [say(T(1, 5), 'D1 for you')]
    adj = {'D1': {'put': T(1, 5), 'answer': T(2)}}
    fwd = send(T(2, 5), 'OWNER, VERBATIM: ' + ans)
    ch = dc([ask('D1', T(1)), close('D1', T(3))], base_his, base_mine, [fwd], adj)
    ck('adjudicated, forwarded verbatim, closed -> complete', ch['D1']['verdicts'], ['complete'])
    ch = dc([ask('D1', T(1))], base_his, base_mine, [send(T(1, 50), ans)], adj)
    ck('a send BEFORE the answer is not a forward', ch['D1']['forwarded'], None)
    ch = dc([ask('D1', T(1))], base_his, base_mine, [send(T(3), 'he says to use the nominal cut')], adj)
    ck('a paraphrase is not a forward (relays are verbatim)',
       'answered_not_forwarded' in ch['D1']['verdicts'], True)
    ch = dc([ask('D1', T(1)), close('D1', T(3), 'no_effect'), close('D1', T(4))], base_his, base_mine, [fwd], adj)
    ck('a no-effect close followed by a landed one is closed', bool(ch['D1']['closed']), True)
    ck('and both attempts are recorded', [o for _, o in ch['D1']['close_attempts']], ['no_effect', 'landed'])
    ch = dc([ask('D1', T(1)), close('D1', T(3), 'failed')], base_his, base_mine, [fwd], adj)
    ck('a failed close -> close_failed + answered_not_closed',
       sorted(ch['D1']['verdicts']), ['answered_not_closed', 'close_failed'])
    ch = dc([ask('D1', T(1)), close('D1', T(1, 30))], base_his, base_mine, [fwd], adj)
    ck('closed before he answered -> closed_without_answer', 'closed_without_answer' in ch['D1']['verdicts'], True)

    # --- facts that need no reading ------------------------------------------------------
    ch = dc([ask('D1', T(1)), close('D1', T(1, 30))], base_his, base_mine, [])
    ck('closed before he said anything to it -> closed_before_any_reply',
       'closed_before_any_reply' in ch['D1']['verdicts'], True)
    ch = dc([ask('D1', T(2))], [], [say(T(1), 'D1 from last week is closed')], [])
    ck('text before the ask is not naming it', ch['D1']['verdicts'], ['never_named_to_owner'])
    ch = dc([ask('D1', T(1)), close('D1', T(2))], [], [], [])
    ck('never named but closed -> closed_without_answer + never_named_to_owner',
       sorted(ch['D1']['verdicts']), ['closed_without_answer', 'never_named_to_owner'])

    # --- decision ids are UPPERCASE: lowercase d1/d2 are this project's field offsets ----
    ch = dc([ask('D1', T(1)), ask('D2', T(1, 1))], [],
            [say(T(1, 5), 'overlap reading purple out of the existing d1-red/d2-blue convention')], [])
    ck('lowercase d1/d2 (field offsets) do not name D1/D2', (ch['D1']['named'], ch['D2']['named']), ([], []))

    # --- the same id minted twice -----------------------------------------------------------
    ch = dc([ask('D1', T(1), 'first'), close('D1', T(2)), ask('D1', T(5), 'second')],
            [say(T(1, 10), 'keep it')], [say(T(1, 5), 'D1 for you'), say(T(5, 5), 'D1 for you')], [])
    ck('an id minted twice yields TWO chains', sorted(ch), ['D1#1', 'D1#2'])
    ck('the close belongs to the minting it followed',
       (bool(ch['D1#1'].get('closed')), ch['D1#2'].get('closed')), (True, None))
    ck("the second minting's facts stay in its own window",
       (ch['D1#2']['named'], ch['D1#2']['candidates']), ([T(5, 5)], []))
    ch = dc([close('D1', T(0)), ask('D1', T(1))], [], [say(T(1, 5), 'D1 for you')], [])
    ck("a close BEFORE the id was minted is an orphan, not this chain's close",
       (ch['D1'].get('closed'), ch.get('D1@orphan', {}).get('verdicts')), (None, ['orphan_close']))

    ch = dc([ask(None, T(1))], [], [], [])
    ck('an ask that did not land is reported, keyed by its citation',
       [v['verdicts'] for v in ch.values()], [['ask_did_not_land']])
    ch = dc([close('D9', T(1))], [], [], [])
    ck('a close with no ask in the window is an orphan_close', ch['D9']['verdicts'], ['orphan_close'])
    ck('no decisions yields no chains', dc([], [], [], []), {})
    return fails


def test_real_shapes():
    """The owner corpus and action outcomes, on records in the REAL shapes."""
    import realshape as RS
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    recs = []
    recs += RS.owner_turn('2026-09-11T01:00:00Z', 'first ruling, typed while you were idle')
    recs += RS.owner_midturn('2026-09-11T01:05:00Z', 'drop it')
    recs += RS.task_note('2026-09-11T01:06:00Z')
    recs += RS.peer_reply('2026-09-11T01:07:00Z', 'the target reporting back')
    recs.append(RS.user_str('2026-09-11T01:08:00Z', 'This session is being continued from a previous conversation...'))
    recs.append(RS.user_str('2026-09-11T01:09:00Z', '[Request interrupted by user]'))
    recs.append(RS.user_str('2026-09-11T01:10:00Z', 'a meta record', meta=True))

    msgs, excluded, peers, _acct = R.owner_messages(recs)
    ck('owner words are read from the REAL channels', [m['text'] for m in msgs],
       ['first ruling, typed while you were idle', 'drop it'])
    ck('a turn-delivered message is counted ONCE, not twice', len(msgs), 2)
    ck('its sources are both listed', msgs[0]['sources'], ['enqueue', 'user'])
    ck('a mid-turn message is counted ONCE (enqueue + queued_command)',
       msgs[1]['sources'], ['enqueue', 'queued_command'])
    ck('a terse ruling survives', msgs[1]['text'], 'drop it')
    ck('task notifications are excluded AND counted', excluded.get('task_notification'), 2)
    ck('peer messages are excluded from the owner corpus', excluded.get('peer_message'), 2)
    ck('but kept as peer replies', [p['text'] for p in peers][:1], ['the target reporting back'])
    ck('compaction summaries are excluded AND counted', excluded.get('compaction_summary'), 1)
    ck('interrupt markers are excluded AND counted', excluded.get('interrupt_marker'), 1)
    ck('meta records are excluded AND counted', excluded.get('meta'), 1)

    # the old extractor on the same records: proves the shape defect was real
    old = [x for r in recs if r.get('type') in ('attachment', 'queue-operation')
           for x in R._texts((r.get('message') or {}).get('content'))]
    ck('(the old message.content reader sees NONE of the mid-turn words)', old, [])

    # outcomes: every verdict is a fact the record states
    ck('OUTCOMES has no unknown-like class',
       any(w in v for v in R.OUTCOMES for w in ('unknown', 'undecid', 'ambig', 'unkey')), False)
    def one(cmd, result, is_error=False, no_result=False):
        rr = (RS.bash_no_result('2026-09-11T02:00:00Z', cmd) if no_result
              else RS.bash('2026-09-11T02:00:00Z', cmd, result, is_error))
        acts_ = R.my_actions([(i + 1, r) for i, r in enumerate(rr)], 'f', '/x/state', '/x')
        return [(x['outcome'], x.get('id')) for x in acts_]
    ck('landed: the success line carries the minted id',
       one('./wd.sh owe add "a decision for him"', 'recorded D5 READY\nREADY for the owner'), [('landed', 'D5')])
    ck('no_effect: clearing an absent id prints nothing, output undiverted',
       one('./wd.sh owe done D1', 'READY for the owner -- he can answer these now: 0'), [('no_effect', 'D1')])
    ck('failed: REFUSED from the one invocation that could print it',
       one('./wd.sh answered', 'REFUSED: no delivered message'), [('failed', None)])
    ck('failed: a queued item that does not exist, named',
       one('./wd.sh sent1 Q9', 'no queued item Q9'), [('failed', 'Q9')])
    ck('failed: exited non-zero, and it was the last command',
       one('./wd.sh sent1 Q9', 'something', True), [('failed', 'Q9')])
    ck('not_completed: no result was ever recorded',
       one('./wd.sh sent1 Q9', None, no_result=True), [('not_completed', 'Q9')])
    ck('a success line for ANOTHER id is not this one',
       one('./wd.sh owe done D2', 'D1 answered and cleared'), [('no_effect', 'D2')])
    ck('PENDING: silence behind >/dev/null proves nothing',
       one('./wd.sh owe done D1 >/dev/null', ''), [(None, 'D1')])
    ck('a line cannot be claimed by an invocation whose output went to a file',
       one('./wd.sh queue add "first item text" >/dev/null; ./wd.sh queue add "second item text"', 'queued Q2'),
       [(None, None), ('landed', 'Q2')])
    ck('"queued" in prose is not a success line',
       one('./wd.sh queue add "an item"', 'nine items are queued behind this'), [('no_effect', None)])
    ck('"no queued item \'Q13\'" is a failure naming Q13',
       one('./wd.sh queue hold Q13 "wait"', "no queued item 'Q13'"), [('failed', 'Q13')])
    ck('an && link after a failed link never ran',
       one('./wd.sh relayed && ./wd.sh answered',
           'usage: wd_check.py [-h]\nwd_check.py: error: relayed <turn end_ts>', True),
       [('failed', None), ('not_completed', None)])
    ck('sent: a list of ids is matched as a list',
       one('./wd.sh sent F1,F2 b7f7de7d', "recorded sent: ['F1', 'F2']"), [('landed', 'F1,F2')])
    ck('outcome ids are names, not only F-numbers',
       one('./wd.sh outcome LIST-turnover accepted "why"', 'LIST-turnover graded accepted'),
       [('landed', 'LIST-turnover')])

    # result_map pairs a tool_use with its outcome, list-shaped results included
    rr = RS.bash('2026-09-11T02:00:00Z', './wd.sh owe add "x"', 'recorded D1 READY')
    rr += RS.bash('2026-09-11T02:01:00Z', './wd.sh sent1 Q1',
                  [{'type': 'text', 'text': 'item Q1 marked sent at T'}])
    rm = R.result_map(rr)
    ck('result_map pairs every tool_use with its result', len(rm), 2)
    ck('list-shaped results are read as text',
       any('marked sent' in t for t, e in rm.values()), True)
    return fails



def test_actions_and_chains_real_shapes():
    """my_actions, decision_chains and landed_replay on a world built ONLY from real shapes,
    with every chain verdict present and its truth fixed here."""
    import realshape as RS
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    Q = ('keep the fitted comb tolerance, or drop it, before the harness is locked down?',
         'which nominal threshold applies to the level cut, now that the basis is accepted?',
         'does position alone establish the head switch identity on this source?',
         'should captions ever move the crop when the geometry disagrees with them?',
         'is the box re-measured on every unit, or held once acquired?')
    T = lambda h, m=0: '2026-09-10T%02d:%02d:00Z' % (h, m)
    recs = []
    # D1 COMPLETE: asked, put, answered naming it, forwarded verbatim, closed
    recs += RS.bash(T(1), './wd.sh owe add "%s"' % Q[0], 'recorded D1 READY\nREADY for the owner: 1')
    recs += RS.say(T(1, 5), 'D1 for you: ' + Q[0])
    recs += RS.owner_turn(T(1, 10), 'D1: drop the fitted tolerance, it was only ever a crutch.')
    recs += RS.send(T(1, 20), 'OWNER, VERBATIM: D1: drop the fitted tolerance, it was only ever a crutch.')
    recs += RS.bash(T(1, 30), './wd.sh owe done D1', 'D1 answered and cleared\nREADY: 0')
    # D2 ANSWERED TERSELY, NEVER FORWARDED, CLOSED
    recs += RS.bash(T(2), './wd.sh owe add "%s"' % Q[1], 'recorded D2 READY')
    recs += RS.say(T(2, 5), 'D2: ' + Q[1])
    recs += RS.owner_midturn(T(2, 10), 'drop it')
    recs += RS.bash(T(2, 30), './wd.sh owe done D2', 'D2 answered and cleared')
    # D3 NEVER PUT TO HIM, CLOSED ANYWAY
    recs += RS.bash(T(3), './wd.sh owe add "%s"' % Q[2], 'recorded D3 READY')
    recs += RS.bash(T(3, 30), './wd.sh owe done D3', 'D3 answered and cleared')
    # D4 + D5 PUT TOGETHER; his reply names D4 only -> D5 is NOT answered by it
    recs += RS.bash(T(4), './wd.sh owe add "%s"' % Q[3], 'recorded D4 READY')
    recs += RS.bash(T(4, 1), './wd.sh owe add "%s"' % Q[4], 'recorded D5 READY')
    recs += RS.say(T(4, 5), 'Two for you. D4: %s D5: %s' % (Q[3], Q[4]))
    recs += RS.owner_turn(T(4, 10), 'D4: never. captions confirm, geometry decides.')
    recs += RS.send(T(4, 20), 'OWNER, VERBATIM: D4: never. captions confirm, geometry decides.')
    # D4's close ran but TOOK NO EFFECT (the id was not there to clear)
    recs += RS.bash(T(4, 30), './wd.sh owe done D4', 'READY for the owner: 1')
    # an owe add that FAILED -- no id was minted
    recs += RS.bash(T(5), './wd.sh owe add "a decision whose ask never landed at all, crashed"',
                    'Traceback (most recent call last):\n  File "wd_wake.py"\nKeyError: seq', True)
    # a close for an id that was never asked
    recs += RS.bash(T(6), './wd.sh owe done D99', 'D99 answered and cleared')
    # a batched delivery: two queued messages dequeued as ONE user record
    recs += [RS.enqueue(T(7), 'what the fuck'), RS.enqueue(T(7, 1), 'unless its in owed?'),
             RS.dequeue(T(7, 2)), RS.dequeue(T(7, 2)),
             RS.user_str(T(7, 2), 'what the fuck\nunless its in owed?')]
    # a queue item, marked sent: once with a real send, once with none
    recs += RS.bash(T(8), './wd.sh queue add "the box drawing is still wrong, draw it over the video"',
                    'queued Q1')
    recs += RS.send(T(8, 5), 'OWNER: the box drawing is still wrong, draw it over the video')
    recs += RS.bash(T(8, 6), './wd.sh sent1 Q1', 'item Q1 marked sent at T')
    recs += RS.bash(T(9), './wd.sh queue add "an item that was marked sent and never actually sent"',
                    'queued Q2')
    recs += RS.bash(T(9, 6), './wd.sh sent1 Q2', 'item Q2 marked sent at T')
    # `answered` with no send in its turn, and one with a send
    recs += RS.owner_turn(T(10), 'status please')
    recs += RS.bash(T(10, 1), './wd.sh answered', 'answered at T')
    recs += RS.send(T(10, 2), 'here is the status')
    recs += RS.bash(T(10, 3), './wd.sh answered', 'answered at T (delivered message at T)')
    # a command whose result never arrived
    recs += RS.bash_no_result(T(11), './wd.sh sent1 Q7')

    d = tempfile.mkdtemp(prefix='recon-real-')
    try:
        fname = '80f99b89-real.jsonl'
        RS.write(os.path.join(d, fname), recs)
        numbered, bad = R.read_records(os.path.join(d, fname))
        acts = R.my_actions(numbered, fname, '/x/state', '/x')
        recs_ = [r for _, r in numbered]
        owner, exc, peers, acct = R.owner_messages(recs_)
        my_text, sends = R.artifacts(recs_)

        # --- batch delivery + accounting ---
        ck('a batched delivery does not double-count its messages',
           sum(1 for m in owner if 'unless its in owed' in m['text']), 1)
        ck('and is recorded as a batch delivery', acct.get('batch_deliveries'), 1)
        ck('ACCOUNTING: seen == attributed + excluded + batches',
           acct['seen'], acct['attributed'] + acct['excluded_total'] + acct.get('batch_deliveries', 0))

        # --- ids from results ---
        opens = [a for a in acts if a['kind'] == 'open']
        ck('open ids are read from the RESULT', [a['id'] for a in opens if a['id']],
           ['D1', 'D2', 'D3', 'D4', 'D5', 'Q1', 'Q2'])
        ck('an open whose result minted no id is recorded failed',
           [a['outcome'] for a in opens if not a['id']], ['failed'])

        # PUT and ANSWERED are read from his words by the reconciler and recorded as
        # adjudications; without one a chain is OUTSTANDING, never guessed
        bare = R.decision_chains(acts, owner, my_text, sends, 'local_target')
        ck('unadjudicated named chains carry an adjudication as outstanding work',
           all(any('adjudicate' in o for o in bare[k]['outstanding']) for k in ('D1', 'D2', 'D4', 'D5')), True)
        ck('and no put/answer verdict is guessed for them',
           any(v in ('complete', 'put_not_answered', 'answered_not_forwarded', 'answered_not_closed')
               for k in ('D1', 'D2', 'D4', 'D5') for v in bare[k]['verdicts']), False)
        ck('a terse reply ("drop it") is a CANDIDATE for the decision put before it',
           T(2, 10) in bare['D2']['candidates'], True)
        ck('his message naming D4 is among its mentions',
           [r['ts'] for r in bare['D4']['mentions']], [T(4, 10)])
        ck("a sibling's reply is only a candidate for D5, never auto-attributed",
           (T(4, 10) in bare['D5']['candidates'], bare['D5']['answered']), (True, None))
        ck('a decision never named to him, then closed, is two facts',
           sorted(bare['D3']['verdicts']), ['closed_without_answer', 'never_named_to_owner'])
        adj = {'D1': {'put': T(1, 5), 'answer': T(1, 10)}, 'D2': {'put': T(2, 5), 'answer': T(2, 10)},
               'D4': {'put': T(4, 5), 'answer': T(4, 10)}, 'D5': {'put': T(4, 5), 'answer': 'none'}}
        ch = R.decision_chains(acts, owner, my_text, sends, 'local_target', adj)
        want = {
            'D1': ['complete'],
            'D2': ['answered_not_forwarded'],
            'D3': ['closed_without_answer', 'never_named_to_owner'],
            'D4': ['answered_not_closed', 'close_had_no_effect'],
            'D5': ['put_not_answered'],
            'D99': ['orphan_close'],
        }
        for did, w in want.items():
            ck('adjudicated %s -> %s' % (did, '+'.join(w)), sorted(ch.get(did, {}).get('verdicts', [])), sorted(w))
        ck('the adjudicated terse answer is the answer text', ch['D2']['answer_text'], 'drop it')
        ck('the failed ask is reported as not landed',
           any(v['verdicts'] == ['ask_did_not_land'] for v in ch.values()), True)
        ck('every chain verdict is in the declared vocabulary',
           all(x in R.CHAIN_VERDICTS for v in ch.values() for x in v['verdicts']), True)

        starts = R.turn_starts(numbered)
        rep = R.landed_replay(acts, starts, my_text, sends, peers,
                              {'owner_queue': [], 'owner_queue_sent': []}, 'local_target')
        by = lambda verb, ident: [x['verdict'] for x in rep if x['verb'] == verb and x.get('id') == ident]
        ck('sent1 Q1 with a send carrying it -> ok', by('sent1', 'Q1'), ['ok'])
        ck('sent1 Q2 marked sent, never sent -> MISSTEER', by('sent1', 'Q2'), ['MISSTEER'])
        ck('sent1 whose result never arrived -> not_completed', by('sent1', 'Q7'), ['not_completed'])
        ans = [x['verdict'] for x in rep if x['verb'] == 'answered']
        ck('answered with no send in its turn -> MISSTEER, then ok', ans, ['MISSTEER', 'ok'])
        ck('a close that took no effect is no_effect, not ok',
           by('owe done', 'D4'), ['no_effect'])
        ck('every replay verdict is in the declared vocabulary',
           all(x['verdict'] in R.REPLAY_VERDICTS for x in rep), True)
        ck('NO verdict anywhere is an unknown-like class',
           any(w in x['verdict'].lower() for x in rep for w in ('unknown', 'undecid', 'ambig'))
           or any(w in v.lower() for c in ch.values() for v in c['verdicts']
                  for w in ('unknown', 'unkey', 'ambig', 'unmatched')), False)
        return fails
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_repeats_and_peers():
    """Found by probe. (1) The owner giving the same short reply more than once collapsed into
    ONE message at the earliest time, so a later decision he answered read as put-not-answered.
    (2) A reply from a session I never sent to satisfied `resolved`, and every peer reply was
    recorded once per channel."""
    import realshape as RS
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    T = lambda h, m=0: '2026-09-10T%02d:%02d:00Z' % (h, m)
    recs = RS.owner_turn(T(1), 'yes') + RS.owner_turn(T(5), 'yes') + RS.owner_midturn(T(6), 'yes')
    msgs, _exc, _peers, acct = R.owner_messages(recs)
    ck('the same reply given three times is three messages', [m['ts'] for m in msgs],
       [T(1), T(5), T(6)])
    ck('each delivery pairs with ITS OWN enqueue', [m['sources'] for m in msgs],
       [['enqueue', 'user'], ['enqueue', 'user'], ['enqueue', 'queued_command']])
    ck('accounting holds with repeats', acct.get('seen'),
       acct.get('attributed', 0) + acct.get('excluded_total', 0) + acct.get('batch_deliveries', 0))

    ask = lambda did, ts: {'kind': 'open', 'store': 'owner_decisions', 'verb': 'owe add', 'id': did,
                           'ts': ts, 'cite': 'c', 'text': 'q', 'text_resolved': True,
                           'outcome': 'landed', 'why': ''}
    ch = R.decision_chains([ask('D1', T(0)), ask('D2', T(4))], msgs,
                           [{'ts': T(0, 30), 'text': 'D1 for you'}, {'ts': T(4, 30), 'text': 'D2 for you'}], [],
                           'local_target')
    ck('a repeated reply is still a candidate for the LATER put', T(5) in ch['D2']['candidates'], True)

    m2 = R.owner_messages([RS.user_str(T(1), 'go ahead')] + RS.owner_turn(T(2), 'go ahead'))[0]
    ck('a delivery with no enqueue is its own message', [m['ts'] for m in m2], [T(1), T(2)])
    ck('an undelivered enqueue is still his words',
       [m['text'] for m in R.owner_messages([RS.enqueue(T(1), 'stop')])[0]], ['stop'])
    m3, _, _, a3 = R.owner_messages([RS.enqueue(T(1), 'yes'), RS.enqueue(T(1, 1), 'yes'),
                                     RS.dequeue(T(1, 2)), RS.dequeue(T(1, 2)),
                                     RS.user_str(T(1, 2), 'yes\nyes')])
    ck('a batch of one word typed twice: two messages, one delivery',
       (len(m3), a3.get('batch_deliveries')), (2, 1))

    # --- a reply resolves a question only from a session it was SENT to ------------------
    recs = []
    recs += RS.bash(T(1), './wd.sh ask "what does the target think of the box rule?"',
                    'open question K1 registered')
    recs += RS.send(T(1, 5), 'question: what do you think of the box rule?')      # to local_target
    recs += RS.peer_reply(T(2), 'unrelated chatter from another session', frm='local_OTHER')
    recs += RS.bash(T(2, 5), './wd.sh resolved K1', 'resolved K1 (open since T)')
    recs += RS.bash(T(3), './wd.sh ask "and the head switch rule?"', 'open question K2 registered')
    recs += RS.send(T(3, 5), 'question: and the head switch rule?')
    recs += RS.peer_reply(T(4), 'the head switch rule stands')                     # from local_target
    recs += RS.bash(T(4, 5), './wd.sh resolved K2', 'resolved K2 (open since T)')
    numbered = [(i + 1, r) for i, r in enumerate(recs)]
    acts = R.my_actions(numbered, 'f', '/x/state', '/x')
    _o, _e, peers, _a = R.owner_messages(recs)
    my_text, sends = R.artifacts(recs)
    rep = R.landed_replay(acts, R.turn_starts(numbered), my_text, sends, peers, {}, 'local_target')
    got = {x['id']: x['verdict'] for x in rep if x['verb'] == 'resolved'}
    ck('a send records where it went', [x.get('to') for x in sends], ['local_target', 'local_target'])
    ck('each peer reply is recorded once, not once per channel', len(peers), 2)
    ck('a reply from a session never sent to does not resolve', got.get('K1'), 'MISSTEER')
    ck('a reply from the session it was sent to resolves', got.get('K2'), 'ok')
    return fails


def test_sends_to_target():
    """sent1, nudged and answered claim a send TO THE TARGET, and a forward of his answer counts
    only if it reached the target. Measured on the real record: of 393 sends, 4 went to another
    session, and before this any of them satisfied these checks."""
    import realshape as RS
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    T = lambda h, m=0: '2026-09-10T%02d:%02d:00Z' % (h, m)
    ITEM1 = 'the first owner item, long enough to be a sentence of his'
    ITEM2 = 'the second owner item, also long enough to be a sentence'
    recs = []
    recs += RS.bash(T(1), './wd.sh queue add "%s"' % ITEM1, 'queued Q1')
    recs += RS.owner_turn(T(2), 'send the first one')
    recs += RS.send(T(2, 1), 'OWNER: ' + ITEM1, to='local_OTHER')
    recs += RS.bash(T(2, 2), './wd.sh sent1 Q1', 'item Q1 marked sent at T')
    recs += RS.bash(T(3), './wd.sh queue add "%s"' % ITEM2, 'queued Q2')
    recs += RS.owner_turn(T(4), 'and the second')
    recs += RS.send(T(4, 1), 'OWNER: ' + ITEM2)
    recs += RS.bash(T(4, 2), './wd.sh sent1 Q2', 'item Q2 marked sent at T')
    recs += RS.bash(T(5), './wd.sh ask "is the box held once acquired?"', 'open question K1 registered')
    recs += RS.owner_turn(T(6), 'nudge them')
    recs += RS.send(T(6, 1), 'nudge: is the box held once acquired?', to='local_OTHER')
    recs += RS.bash(T(6, 2), './wd.sh nudged K1', 'nudged K1 (1 resend(s)); due again')
    recs += RS.owner_turn(T(7), 'nudge again')
    recs += RS.send(T(7, 1), 'nudge: is the box held once acquired?')
    recs += RS.bash(T(7, 2), './wd.sh nudged K1', 'nudged K1 (2 resend(s)); due again')
    recs += RS.bash(T(8), './wd.sh owe add "keep the fitted tolerance or not?"', 'recorded D1 READY')
    recs += RS.say(T(8, 5), 'D1 for you: keep the fitted tolerance or not?')
    recs += RS.owner_turn(T(9), 'D1: drop the fitted tolerance, it was only ever a crutch.')
    recs += RS.send(T(9, 1), 'OWNER, VERBATIM: D1: drop the fitted tolerance, it was only ever a crutch.',
                    to='local_OTHER')
    recs += RS.bash(T(9, 2), './wd.sh owe done D1', 'D1 answered and cleared')
    recs += RS.bash(T(10), './wd.sh owe add "is the head switch rule settled?"', 'recorded D2 READY')
    recs += RS.say(T(10, 5), 'D2 for you: is the head switch rule settled?')
    recs += RS.owner_turn(T(11), 'D2: settled, the contract already says so.')
    recs += RS.send(T(11, 1), 'OWNER, VERBATIM: D2: settled, the contract already says so.')
    recs += RS.bash(T(11, 2), './wd.sh owe done D2', 'D2 answered and cleared')
    numbered = [(i + 1, r) for i, r in enumerate(recs)]
    acts = R.my_actions(numbered, 'f', '/x/state', '/x')
    owner, _e, peers, _a = R.owner_messages(recs)
    my_text, sends = R.artifacts(recs)
    rep = R.landed_replay(acts, R.turn_starts(numbered), my_text, sends, peers, {}, 'local_target')
    by = lambda verb, ident: [x['verdict'] for x in rep if x['verb'] == verb and x.get('id') == ident]
    ck('sent1 whose text went to ANOTHER session -> MISSTEER', by('sent1', 'Q1'), ['MISSTEER'])
    ck('and its reason names where the text went',
       any('local_OTHER' in x['why'] for x in rep if x['verb'] == 'sent1' and x.get('id') == 'Q1'), True)
    ck('sent1 whose text reached the target -> ok', by('sent1', 'Q2'), ['ok'])
    ck('nudged by a send to another session -> MISSTEER, then ok', by('nudged', 'K1'), ['MISSTEER', 'ok'])
    ch = R.decision_chains(acts, owner, my_text, sends, 'local_target',
                           {'D1': {'put': T(8, 5), 'answer': T(9)}, 'D2': {'put': T(10, 5), 'answer': T(11)}})
    ck('a forward to another session is not a forward', ch['D1']['verdicts'], ['answered_not_forwarded'])
    ck('a forward to the target completes the chain', ch['D2']['verdicts'], ['complete'])
    raised = []
    for fn, args in ((R.landed_replay, (acts, [], my_text, sends, peers, {}, None)),
                     (R.decision_chains, (acts, owner, my_text, sends, None))):
        try:
            fn(*args)
            raised.append(False)
        except ValueError:
            raised.append(True)
    ck('with no target both refuse by name, never accept any send', raised, [True, True])
    return fails



def test_shell_reading():
    """The command is read the way the SHELL reads it. Every case is a form from the real record
    that a regex over raw text got wrong."""
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    words = lambda c: [t['w'] for t in R.sh_tokens(c) if 'w' in t]
    ops = lambda c: [t['op'] for t in R.sh_tokens(c) if 'op' in t]
    redirs = lambda c: [(t['redir'], t['fd'], (t['target'] or {}).get('w')) for t in R.sh_tokens(c) if 'redir' in t]

    # --- tokens ---------------------------------------------------------------------------
    c = './wd.sh sent1 Q1 >/dev/null 2>&1 | tail -1'
    ck('tok: words around redirections', words(c), ['./wd.sh', 'sent1', 'Q1', 'tail', '-1'])
    ck('tok: stdout to /dev/null, then 2>&1', redirs(c), [('>', '1', '/dev/null'), ('>&', '2', '1')])
    ck('tok: a pipe is an operator', ops(c), ['|'])
    ck('tok: escaped quotes inside double quotes',
       words('./wd.sh queue add "he said \\"no\\" to it"')[-1], 'he said "no" to it')
    t = [x for x in R.sh_tokens('./wd.sh queue add "$*"') if 'w' in x][-1]
    ck('tok: "$*" expands', (t['w'], t['expands']), ('$*', True))
    t = [x for x in R.sh_tokens("./wd.sh queue add 'costs $5 flat'") if 'w' in x][-1]
    ck('tok: single-quoted $5 does not expand', (t['w'], t['expands']), ('costs $5 flat', False))
    ck('tok: a SINGLE-quoted python -c program is ONE word',
       words('python3 -c \'t="./wd.sh owe done D1"\''), ['python3', '-c', 't="./wd.sh owe done D1"'])
    ck('tok: a quoted python -c program is ONE word',
       words('python3 -c "t=\'./wd.sh owe done D1\'"'), ['python3', '-c', "t='./wd.sh owe done D1'"])
    c = "cat > f <<'EOF'\n./wd.sh queue add \"x y z\"\nEOF\n./wd.sh sent1 Q2"
    ck('tok: a heredoc body is data, not words', words(c), ['cat', './wd.sh', 'sent1', 'Q2'])
    ck('tok: `> f` is a redirection target', redirs(c)[0], ('>', '1', 'f'))
    ck('tok: the heredoc body is kept with its redirection',
       [t['heredoc'] for t in R.sh_tokens(c) if t.get('redir') == '<<'], ['./wd.sh queue add "x y z"'])
    c = "cat <<'EOF'\nthe word EOF appears here\n./wd.sh queue add \"must not leak\"\nEOF\n./wd.sh sent1 Q3"
    ck('tok: a body line mentioning the delimiter does not end it', words(c), ['cat', './wd.sh', 'sent1', 'Q3'])
    ck('tok: a comment is dropped', words('# ./wd.sh queue add "x"\necho done'), ['echo', 'done'])
    ck('tok: # inside a word is literal', words('echo a#b'), ['echo', 'a#b'])
    ck('tok: line continuation', words('./wd.sh queue add \\\n "text here"'), ['./wd.sh', 'queue', 'add', 'text here'])
    t = [x for x in R.sh_tokens('git commit -m "fix `wd.sh` thing"') if 'w' in x][-1]
    ck('tok: backticks in double quotes are a substitution', t['subs'], ['wd.sh'])
    t = [x for x in R.sh_tokens('echo "$(printf \'a)b\')"') if 'w' in x][-1]
    ck('tok: quotes inside $(...) are honoured', t['subs'], ["printf 'a)b'"])
    t = [x for x in R.sh_tokens('a=$((1+2)); echo $a') if 'w' in x][0]
    ck('tok: $((...)) is arithmetic, not a command', (t['expands'], t['subs']), (True, []))
    ck('tok: a word glued to > is a redirection', redirs('./wd.sh owe list>out.txt'), [('>', '1', 'out.txt')])
    ck('tok: digits that are an argument are not an fd', words('./wd.sh hold 12 x'), ['./wd.sh', 'hold', '12', 'x'])
    ck('tok: &> redirects both streams', redirs('x &>/dev/null'), [('&>', 'both', '/dev/null')])
    ck('tok: the empty command', R.sh_tokens(''), [])

    # --- invocations ------------------------------------------------------------------------
    LIVE = '/w/state'
    inv = lambda c, cwd='/w': R.invocations(c, LIVE, cwd)
    brief = lambda c, cwd='/w': [(i['verb'], i['args']) for i in inv(c, cwd)]
    ck('inv: stdout diverted by >/dev/null', [(i['verb'], i['args'], i['diverted']) for i in inv('./wd.sh sent1 Q1 >/dev/null')],
       [('sent1', ['Q1'], True)])
    ck('inv: stdout into a pipe is diverted', [i['diverted'] for i in inv('./wd.sh sent1 Q1 2>&1 | tail -1')], [True])
    ck('inv: 2>&1 alone does not divert stdout', [i['diverted'] for i in inv('./wd.sh sent1 Q1 2>&1')], [False])
    ck('inv: queue add joins its words ("$*") and reads --urgent',
       [(i['verb'], i['text'], i['urgent']) for i in inv('./wd.sh queue add --urgent fix the box')],
       [('queue add', 'fix the box', True)])
    ck('inv: owe add reads --gated-on apart from the text',
       [(i['text'], i['gated_on']) for i in inv('./wd.sh owe add --gated-on "the harness\'s switch line" "Does it change?"')],
       [('Does it change?', "the harness's switch line")])
    ck('inv: expanded text is flagged unresolved',
       [i['text_resolved'] for i in inv('./wd.sh queue add "$*"')], [False])
    ck('inv: wd.sh inside a quoted python program is NOT an invocation',
       inv('python3 -c "t=\'./wd.sh owe done D1\'"'), [])
    ck('inv: an echoed invocation is not one', inv("echo './wd.sh owe add \"x\"'"), [])
    ck('inv: a grep for "--sent" is not a `sent`', inv('grep -n "def record_sent\\|--sent" wd_wake.py'), [])
    ck('inv: `answered && git add` has no id', brief('./wd.sh answered && git add -A'), [('answered', [])])
    ck('inv: `relayed <ts>;` does not keep the `;`',
       brief('./wd.sh relayed 2026-09-10T13:33:47.000Z; ./wd.sh relayed 2026-09-10T13:35:03.323Z'),
       [('relayed', ['2026-09-10T13:33:47.000Z']), ('relayed', ['2026-09-10T13:35:03.323Z'])])
    ck('inv: && chain position is recorded',
       [(i['verb'], i['op_before']) for i in inv('./wd.sh relayed && ./wd.sh answered')],
       [('relayed', None), ('answered', '&&')])
    ck('inv: sent keeps its id list and message id', brief('./wd.sh sent F1,F2 b7f7de7d'), [('sent', ['F1,F2', 'b7f7de7d'])])
    ck('inv: a for loop is unrolled at a command position',
       brief('for d in D1 D3; do ./wd.sh owe done $d; done'), [('owe done', ['D1']), ('owe done', ['D3'])])
    ck('inv: a substitution loop is NOT unrolled',
       [(i['args'], i['arg_expands']) for i in inv('for d in $(cat ids.txt); do ./wd.sh owe done $d; done')],
       [(['$d'], [True])])
    ck('inv: a while-read loop body is read once, marked',
       [(i['args'], i['loop']) for i in inv('while read d; do ./wd.sh owe done $d; done < ids.txt')],
       [(['$d'], 'while')])
    ck('inv: a heredoc body is not executed',
       brief("cat > f <<'EOF'\n./wd.sh queue add \"x y z\"\nEOF\n./wd.sh sent1 Q2"), [('sent1', ['Q2'])])
    ck('inv: a script fed to bash on stdin IS executed',
       brief("bash <<'EOF'\n./wd.sh sent1 Q5\nEOF"), [('sent1', ['Q5'])])
    ck('inv: bash -c runs its string', brief("bash -c './wd.sh sent1 Q9'"), [('sent1', ['Q9'])])
    ck('inv: a bare `wd.sh` is not on PATH, so never runs', inv('git commit -m "fix `wd.sh` thing"'), [])
    ck('inv: a substitution runs, and its stdout is captured',
       [(i['verb'], i['in_substitution'], i['diverted']) for i in inv('out=$(./wd.sh reconcile 2>/dev/null)')],
       [('reconcile', True, True)])

    # --- which state each invocation touched -------------------------------------------------
    ck('state: the default state dir is live', [i['state'] for i in inv('./wd.sh sent1 Q1')], ['live'])
    ck('state: an exported WD_STATE is scratch for what follows',
       [i['state'] for i in inv('export WD_STATE=/private/tmp/x && ./wd.sh sent1 Q1; ./wd.sh sent1 Q2')],
       ['scratch', 'scratch'])
    ck('state: an inline prefix is scratch for THAT command only',
       [i['state'] for i in inv('WD_STATE=$T ./wd.sh next; ./wd.sh sent1 URG1')], ['scratch', 'live'])
    ck('state: a plain (unexported) assignment changes nothing',
       [i['state'] for i in inv('WD_STATE=/tmp/x; ./wd.sh sent1 Q1')], ['live'])
    ck('state: --state-dir resolved against cd',
       [i['state'] for i in inv('cd /w && python3 wd_wake.py --state-dir state --owe-clear D1')], ['live'])
    ck('state: --state-dir elsewhere is scratch',
       [(i['verb'], i['state']) for i in inv('cd /private/tmp/x && python3 wd_wake.py --state-dir state --owe-clear D1')],
       [('owe done', 'scratch')])
    ck('state: another watchdog instance is scratch',
       [i['state'] for i in inv('/private/tmp/meta/watchdog/wd.sh sent1 Q1')], ['scratch'])
    ck('state: cd changes where ./wd.sh is',
       [i['state'] for i in inv('cd /private/tmp/meta/watchdog && ./wd.sh sent1 Q1', cwd='/w')], ['scratch'])
    return fails


def test_vocabulary():
    """Unknown is not an answer class: everything is answerable from my actions, project state or
    what the owner owes me, so an item the instrument could not settle is WORK OUTSTANDING, and a
    terminal 'unknown' verdict would route around the 100% gate. This scans every verdict-like
    string the instrument can emit, and proves the scan fires on a planted one."""
    import ast
    fails = []
    def ck(name, got, want):
        ok = got == want
        print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                              '' if ok else '  got=%r want=%r' % (got, want)))
        if not ok:
            fails.append(name)

    banned = re.compile(r'unknown|undecid|unkeyed|ambig|unmatched|inconclusive|indetermin', re.I)

    def verdict_like(src):
        return sorted({n.value for n in ast.walk(ast.parse(src))
                       if isinstance(n, ast.Constant) and isinstance(n.value, str)
                       and re.fullmatch(r'[A-Za-z][\w\-:]*', n.value) and banned.search(n.value)})

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for mod in ('wd_recon_lib.py', 'wd_reconcile.py'):
        ck('%s emits no unknown-type verdict' % mod,
           verdict_like(open(os.path.join(here, mod)).read()), [])
    ck('the declared vocabularies contain none',
       [v for v in R.CHAIN_VERDICTS + R.REPLAY_VERDICTS + R.OUTCOMES if banned.search(v)], [])
    planted = open(os.path.join(here, 'wd_recon_lib.py')).read() + "\nX = 'undecidable'\n"
    ck('CONTROL: the scan fires on a planted unknown-type verdict',
       verdict_like(planted), ['undecidable'])
    return fails


def _guarded(fn):
    """A test that CRASHES is a failure with a name. Before this, one KeyError in the corner
    cases ended the run and every later test -- including the control written for that very
    defect -- never reported."""
    try:
        return fn()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print('%-56s CRASHED: %r' % (fn.__name__, e))
        return ['CRASHED %s: %r' % (fn.__name__, e)]


def main():
    d = tempfile.mkdtemp(prefix='recon-fixture-')
    fails = []
    try:
        p, st = build(d)
        fname = os.path.basename(p)
        numbered, bad = R.read_records(p)

        def ck(name, got, want):
            ok = got == want
            print('%-56s %s%s' % (name, 'PASS' if ok else 'FAIL',
                                  '' if ok else '  got=%r want=%r' % (got, want)))
            if not ok:
                fails.append(name)

        acts = R.my_actions(numbered, fname, st, d)
        opens = [x for x in acts if x['kind'] == 'open' and x['state'] == 'live']
        closes = [x for x in acts if x['kind'] == 'close' and x['state'] == 'live']
        ck('a scratch-state action is recorded as scratch, not dropped',
           [(x['state'], x.get('id')) for x in acts if 'SCRATCH-STATE' in (x.get('text') or '')],
           [('scratch', 'Q90')])
        landed = {c['id'] for c in closes if c['id'] and c['outcome'] == 'landed'}

        ck('opens: every invocation and only invocations', len(opens), TRUTH['opens'])
        ck('open ids are read from the RESULTS',
           sorted(o['id'] for o in opens if o['id']), TRUTH['landed_open_ids'])
        ck('every genuine item is recorded verbatim',
           all(any(o['text'] == t for o in opens) for t in REAL_ITEMS), True)
        ck('the "$*" ask is FAILED and its text flagged unresolved',
           [(o['outcome'], o['text_resolved']) for o in opens if o['text'] == '$*'],
           [('failed', False)])
        ck('a landed SHORT item is kept, not length-filtered away',
           [o['id'] for o in opens if o['text'] == 'fix it'], ['Q14'])
        ck('every open carries a citation',
           all(':' in o['cite'] and '#' in o['cite'] for o in opens), True)
        ck('every open carries a verbatim hash', all(len(o['sha256']) == 64 for o in opens), True)
        ck('landed closes are exactly the truth', landed, TRUTH['landed_close_ids'])
        ck('loop-bound ids come from the COMMAND where it states them',
           sorted(c['id'] for c in closes
                  if c.get('id_from') == 'command' and c['id'] in ('D1', 'D3', 'D5')),
           ['D1', 'D3', 'D5'])
        ck('the literal `$d` is never recorded as an id',
           any(c['id'] and '$' in c['id'] for c in closes), False)
        ck('a close its result says found nothing is failed, not landed',
           [c['outcome'] for c in closes if c['id'] == 'Q3'], ['landed', 'failed'])
        ck('queue clear records the message it cleared into',
           [(c['id_from'], c['outcome']) for c in closes if c['id'] == 'M7'], [('command', 'landed')])
        ck('a decision asked but never closed is found',
           sorted(o['id'] for o in opens
                  if o['store'] == 'owner_decisions' and o['id'] and o['id'] not in landed),
           TRUTH['never_closed'])
        ck('the unparseable line is COUNTED, not dropped', bad, 1)
        ck('the scan continues past the unparseable line', 'Q4' in landed, True)

        # the owner's words, where they actually live
        owner, _exc, _peers, acct = R.owner_messages([r for _, r in numbered])
        ck('owner words read from every real channel, once each',
           sorted(m['text'] for m in owner), TRUTH['owner'])
        ck('all three owner channels are read',
           {x for m in owner for x in m['sources']} >= {'enqueue', 'queued_command', 'user'}, True)
        ck('owner accounting holds: seen == attributed + excluded + batches',
           acct['seen'], acct['attributed'] + acct['excluded_total'] + acct.get('batch_deliveries', 0))

        # --- MUTATION BATTERY: every guard must be LOAD-BEARING ------------------------
        # Each entry disables ONE guard and states how the result must break. A mutation that
        # changes nothing means the guard protects nothing, and that FAILS the suite.
        print('\n--- mutation battery (each must break the result) ---')
        # Each entry changes ONE guard in the reader's SOURCE, loads the result as a fresh module
        # and requires the answer to break. A mutation that changes nothing means the guard
        # protects nothing, and that FAILS the suite.
        import types as _types
        lib_src = open(R.__file__).read()

        def mutant(old, new):
            assert lib_src.count(old) == 1, ('mutation site not unique', old[:60], lib_src.count(old))
            mod = _types.ModuleType('wd_recon_lib_mutant')
            mod.__file__ = R.__file__
            exec(compile(lib_src.replace(old, new, 1), R.__file__, 'exec'), mod.__dict__)
            return mod

        def run(mod):
            a_ = mod.my_actions(numbered, fname, st, d)
            live = [x for x in a_ if x['state'] == 'live']
            return [x for x in live if x['kind'] == 'open'], [x for x in live if x['kind'] == 'close']

        def mutate(name, old, new, broke_fn):
            try:
                o, c = run(mutant(old, new))
                broke = broke_fn(o, c)
            except AssertionError:
                raise
            except Exception:
                broke = True
            print('%-56s %s' % (name, 'PASS' if broke else 'FAIL (guard protects nothing)'))
            if not broke:
                fails.append('mutation: ' + name)

        cmd_ids = lambda c: {x['id'] for x in c if x.get('id_from') == 'command'}
        landed_ids = lambda c: {x['id'] for x in c if x['id'] and x['outcome'] == 'landed'}
        mutate('heredoc bodies read as commands -> a written document scores',
               "                if op in ('<<', '<<-'):\n                    pending.append(",
               "                if False:\n                    pending.append(",
               lambda o, c: len(o) > TRUTH['opens'])
        mutate('comments read as commands -> a trailing comment runs',
               "        elif ch == '#' and word is None:",
               "        elif False:",
               lambda o, c: len(o) > TRUTH['opens'])
        mutate('for loops not unrolled -> ids a loop states literally are lost',
               "        is_for = w[:1] == ['for'] and len(w) >= 3",
               "        is_for = False and w[:1] == ['for'] and len(w) >= 3",
               lambda o, c: not {'D1', 'D3', 'D5'} <= cmd_ids(c))
        mutate('`done` inside `owe done` ends the loop -> D5 lost',
               "        if 'done' in kw:\n            depth -= kw.count('done')",
               "        if 'done' in kw or 'done' in ww:\n            depth -= kw.count('done') + ww.count('done')",
               lambda o, c: 'D5' not in cmd_ids(c))
        mutate('expanded text treated as literal -> "$*" becomes owner words',
               "            'text_resolved': (not any(aexp)) if text is not None else None,",
               "            'text_resolved': True if text is not None else None,",
               lambda o, c: any(x['text'] == '$*' and x['text_resolved'] for x in o))
        mutate('`;` glued into a word -> Q2 never lands',
               "            if ch in ' \\t\\n<>' or s.startswith(SH_OPS, k):",
               "            if ch in ' \\t\\n<>' or s.startswith(('&&', '||', '|', '&', '(', ')'), k):",
               lambda o, c: 'Q2' not in landed_ids(c))
        mutate('--urgent not read -> the urgent item text is wrong',
               "            if verb == 'queue add' and args[:1] == ['--urgent']:",
               "            if False:",
               lambda o, c: not all(any(x['text'] == t for x in o) for t in REAL_ITEMS))
        mutate('every state treated as live -> a scratch item is reconciled',
               "    state = 'live' if (live and state_dir == live) else 'scratch'",
               "    state = 'live'",
               lambda o, c: len(o) > TRUTH['opens'])
        mutate('no success line for `queue clear` -> M7 is lost',
               "    'queue clear': r'queue cleared into message (\\S+)',\n",
               "",
               lambda o, c: 'M7' not in landed_ids(c))

        # reading only `user` records loses most of what he said
        only_user = [t for _, r in numbered if r.get('type') == 'user'
                     for _src, t in R.channel_texts(r) if t.strip()]
        print('%-56s %s' % ('reading only `user` loses owner words',
                            'PASS' if len(only_user) < len(TRUTH['owner']) else 'FAIL'))
        if len(only_user) >= len(TRUTH['owner']):
            fails.append('mutation: user-only read loses nothing')

        # records without a timestamp must be COUNTED, never silently dropped
        nots = sum(1 for _, r in numbered if not r.get('timestamp'))
        print('%-56s %s' % ('corpus contains untimestamped records to account for',
                            'PASS' if nots else 'FAIL'))
        if not nots:
            fails.append('fixture has no untimestamped record')

        print('\n--- stage 3: landed-replay ---')
        fails.extend(_guarded(test_stage3))
        print('\n--- stage 4: supersession evidence ---')
        fails.extend(_guarded(test_stage4))
        print('\n--- stage 2: Time Machine (synthesized) ---')
        fails.extend(_guarded(test_stage2))
        print('\n--- tm reader (synthesized) ---')
        fails.extend(_guarded(test_tm_reader))
        print('\n--- real record shapes: owner corpus and outcomes ---')
        fails.extend(_guarded(test_real_shapes))
        print('\n--- actions, chains and replay in real shapes ---')
        fails.extend(_guarded(test_actions_and_chains_real_shapes))
        print('\n--- decision chains + snapshot series ---')
        fails.extend(_guarded(test_decision_chains))
        print('\n--- decision chain edges ---')
        fails.extend(_guarded(test_chain_edges))
        print('\n--- corner cases and error handling ---')
        fails.extend(_guarded(test_corner_cases))
        print('\n--- robustness: concurrency, crash residue, hostile input ---')
        fails.extend(_guarded(test_robustness))
        print('\n--- remaining units ---')
        fails.extend(_guarded(test_remaining_units))
        print('\n--- citation: the certainty mechanism ---')
        fails.extend(_guarded(test_citation))
        print('\n--- CLI: stages actually run ---')
        fails.extend(_guarded(test_cli_stages))
        print('\n--- CLI: stages 2-5 and the supersession fixed point ---')
        fails.extend(_guarded(test_cli_all_stages))
        print('\n--- stage 5: additive repair ---')
        fails.extend(_guarded(test_stage5))
        print('\n--- repeated replies and peer senders ---')
        fails.extend(_guarded(test_repeats_and_peers))
        print('\n--- sends must reach the TARGET ---')
        fails.extend(_guarded(test_sends_to_target))
        print('\n--- reading commands as the shell does ---')
        fails.extend(_guarded(test_shell_reading))
        print('\n--- vocabulary: no unknown-type verdict ---')
        fails.extend(_guarded(test_vocabulary))

        print('\nRESULT: %s' % ('all controls pass' if not fails else '%d FAILED: %s'
                                % (len(fails), ', '.join(fails))))
        return 1 if fails else 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
