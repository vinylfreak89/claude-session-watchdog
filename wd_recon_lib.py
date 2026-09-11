#!/usr/bin/python3
"""wd_recon_lib.py -- the chronological replay of MY OWN ACTIONS.

The owner, 2026-09-11, defining what this is:

  "You want to walk this chronologically, along with your own transcript chronologically.
   What you are essentially doing is a replay of your entire set of actions to catch things
   that you steered incorrectly, be they owed info, nudges, queues, owner requests, anything
   you own. The idea of reconciliation is to fix the current state to control your fuck ups"

So the unit of work is ONE ACTION OF MINE, in time order -- not a store count. A store diff
finds dropped rows and is blind to the larger class: a `relayed` that relayed nothing, an
`answered` with no send behind it, a `resolved` with no answer, a `nudged` that re-sent
nothing. Those are steers, they are mine, and only the record beside them shows them.

  ACTION      what I recorded, with its timestamp and argument
  ARTIFACT    the thing that must exist if the action was honest -- a send, my own relay text,
              a target reply. Computed from the transcripts, never attested.
  VERDICT     OK when the artifact is there; MISSTEER when it is not; the operator adjudicates
              anything the instrument cannot decide, and repairs are additive.
              "Do not clear any queues."
"""
import os, json, re, collections

PROJ = os.path.expanduser('~/.claude/projects/-Users-vinylfreak89-Documents-blackmagic-usb-mac')

# Both invocation paths. Measured 2026-09-11: `./wd.sh` alone missed 18 `--owe-add`,
# 5 `--queue-add`, 14 `--queue-clear`, 16 `--owe-clear` written straight to the scripts, and
# the join went NEGATIVE (a store closing more than it opened) -- which is how the gap showed.
WRAP = (r'(?:^|[;&|]\s*|\s)\./wd\.sh\s+'
        r'(queue add|queue clear|queue hold|owe add|owe done|owe ungate|sent1|relayed|'
        r'answered|resolved|closed|nudged|conditional|fired|ask|hold|sent|veto|outcome)\b')
DIRECT = r'--(queue-add|queue-clear|queue-hold|owe-add|owe-clear|owe-ungate|ask|sent|veto)\b'
MUT = re.compile(WRAP + '|' + DIRECT)

# What must exist for each action to have been honest.


def _texts(c):
    if isinstance(c, str):
        return [c]
    if isinstance(c, list):
        return [b['text'] for b in c
                if isinstance(b, dict) and b.get('type') == 'text' and b.get('text')]
    return []


def _arg(cmd, verb):
    m = re.search(re.escape(verb) + r'\s+(?:--urgent\s+)?["\']?([^"\'\s]{1,80})', cmd)
    return m.group(1) if m else None


# ---------------------------------------------------------------- citation

import hashlib
import datetime


def cite(fname, lineno):
    """The exact record at (transcript, line). Certainty means the restored bytes ARE the
    recorded bytes -- not my rendering of them. Owner, 2026-09-11: "you need to be certain of
    what you are reconstructing." So a restore names a location in the record and the
    instrument reads it; there is no path that accepts text I typed."""
    if not isinstance(lineno, int) or lineno < 1:
        # A bad citation must be a NAMED refusal, not a TypeError from a format string.
        # Found by the citation fixture, 2026-09-11: a caller passing None crashed here
        # instead of being told the citation was unusable.
        raise ValueError('citation line must be a positive integer, got %r' % (lineno,))
    p = os.path.join(PROJ, fname)
    if not os.path.exists(p):
        raise ValueError('no such transcript: %s' % fname)
    with open(p, 'rb') as f:
        for i, line in enumerate(f, 1):
            if i == lineno:
                return json.loads(line)
    raise ValueError('%s has no line %d' % (fname, lineno))


def commands_in(rec):
    """Every Bash command in a record, in order."""
    out = []
    for b in ((rec.get('message') or {}).get('content') or []):
        if isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') == 'Bash':
            out.append((b.get('input') or {}).get('command', ''))
    return out


def extract_item(cmd):
    """The item text a command DELIVERED, or None. None means REFUSE, never improvise. Read by
    the shell reader -- the same one my_actions uses, so actions and restores cannot drift apart.
    Text the shell would have expanded is refused: the literal is not what the script stored."""
    for inv in invocations(cmd, None):
        if inv['verb'] in ('queue add', 'owe add'):
            t, ok = inv['text'] or '', inv['text_resolved']
        elif inv['verb'] == 'ask':
            t, ok = ' '.join(inv['args'][1:]), not any(inv['arg_expands'][1:])
        else:
            continue
        return t if (ok and t.strip()) else None
    return None


def restore_payload(fname, lineno, which=0):
    """A citation resolved to verbatim bytes plus a hash, so the restore can be re-verified
    later against the transcript rather than trusted."""
    rec = cite(fname, lineno)
    cmds = commands_in(rec)
    if not cmds:
        raise ValueError('%s:%d carries no Bash command' % (fname, lineno))
    if which >= len(cmds):
        raise ValueError('%s:%d has %d command(s), asked for #%d' % (fname, lineno, len(cmds), which))
    # read by the shell reader: a heredoc's example text, a comment or an echo is data, never an
    # invocation (this path once restored a heredoc's example as the owner's words)
    text = extract_item(cmds[which])
    if text is None or not is_real_item(text):
        raise ValueError('%s:%d command #%d carries no recognisable item argument -- REFUSED '
                         '(no guessing: fix the citation or extend the shell reader with a control)'
                         % (fname, lineno, which))
    return {'source': '%s:%d#%d' % (fname, lineno, which),
            'ts': rec.get('timestamp'),
            'text': text,
            'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'cmd_sha256': hashlib.sha256(cmds[which].encode()).hexdigest()}


def verify_payload(p):
    """Re-read the citation and confirm the stored bytes still match. A restore whose source
    no longer reproduces is not evidence."""
    try:
        fresh = restore_payload(*p['source'].split('#')[0].rsplit(':', 1) and
                                (p['source'].split(':')[0],
                                 int(p['source'].split(':')[1].split('#')[0]),
                                 int(p['source'].split('#')[1])))
    except Exception as e:
        return False, str(e)
    return fresh['sha256'] == p['sha256'], fresh['sha256']


SHELLISH = re.compile(r'^\s*(\$[\*@0-9{]|["\']?\$)')


def is_real_item(text):
    """An item is the owner's words. A shell variable, an empty string or control bytes is not.
    There is NO length floor: a 25-character floor existed for parse artifacts (fragments cut at
    an escaped quote), which the shell reader no longer produces, and the floor then refused a
    genuine landed four-word ask. Length is not provenance."""
    if not text or not text.strip() or SHELLISH.match(text):
        return False
    # Control characters are never the owner's words; a NUL in particular would otherwise have
    # been restored as an item.
    return not any(ord(c) < 32 and c not in '\t\n\r' for c in text)


def selftest():
    """Controls for the contamination that has produced a wrong answer three times, read by the
    shell reader. Each must FAIL if its guard is removed."""
    ok = True
    writes = ("cat > wd.sh <<'EOF'\n"
              'owe)    add) exec $PY --owe-add "$*" ;;\n'
              "EOF\n")
    if extract_item(writes) is not None:
        print('FAIL: a file-writing heredoc still reads as an invocation'); ok = False
    else:
        print('pass: heredoc body ignored')
    real = './wd.sh owe add "DOES POSITION ALONE ESTABLISH IDENTITY? Flagged in the contract itself."'
    got = extract_item(real)
    if got and is_real_item(got):
        print('pass: a real invocation still extracts (%r...)' % got[:34])
    else:
        print('FAIL: a real invocation no longer extracts: %r' % got); ok = False
    if extract_item('./wd.sh queue add "$*"') is not None:
        print('FAIL: text the shell would expand is restored as words'); ok = False
    else:
        print('pass: expanded text refused')
    if is_real_item('$*') or is_real_item('') or is_real_item('   ') or not is_real_item('fix it'):
        print('FAIL: is_real_item takes a shell variable or blank, or refuses a short real item')
        ok = False
    else:
        print('pass: shell variables and blanks refused, a short real item kept')
    print('SELFTEST %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


def live_stores(state_dir):
    """The live stores, read-only. Never the reference for what SHOULD be there -- the store is
    the thing under repair, and an instrument cannot corroborate itself."""
    p = os.path.join(state_dir, 'state.json')
    d = {}
    if os.path.exists(p):
        try:
            d = json.load(open(p))
        except Exception as e:
            # A corrupt store is NOT an empty store. Reading it as empty would make every
            # item look dropped and manufacture a restore for each one.
            raise ValueError('state.json at %s is unreadable (%s) -- refused rather than '
                             'treated as empty' % (p, e))
        if not isinstance(d, dict):
            raise ValueError('state.json at %s is a %s, not an object -- refused'
                             % (p, type(d).__name__))
    return {'owner_queue': d.get('owner_queue') or [],
            'owner_decisions': d.get('owner_decisions') or {},
            'open_questions': d.get('open_questions') or {},
            'resolved_questions': d.get('resolved_questions') or {},
            'owner_queue_sent': d.get('owner_queue_sent') or [],
            'owner_decision_seq': d.get('owner_decision_seq'),
            'all_keys': sorted(d.keys()),
            'raw': d}


# ---------------------------------------------------------------- stage 3

SHA = re.compile(r'\b([0-9a-f]{7,40})\b')


def claimed_commits(my_text):
    """Every sha I asserted in my own words to the owner, with where I said it."""
    seen = []
    for t in my_text:
        for m in SHA.finditer(t['text']):
            s = m.group(1)
            if re.fullmatch(r'\d+', s):
                continue
            seen.append({'ts': t['ts'], 'sha': s})
    return seen


def verify_commits(claims, git_probe):
    """A commit claim is landed only if the object exists AND sits on a ref that matters.
    'It is in my working tree' and 'it is on the branch the other agent reads' are different
    claims, and the second is the one that counts."""
    out = []
    for c in claims:
        exists, refs = git_probe(c['sha'])
        out.append(dict(c, exists=exists, refs=refs,
                        # three FACTS: on a ref; exists but reachable from no ref (rewritten
                        # away or dangling); does not exist. None of them is unknown.
                        verdict='ok' if exists and refs else
                                'MISSTEER' if not exists else 'on_no_ref'))
    return out


# ---------------------------------------------------------------- stage 4

SUPERSEDE_HINT = re.compile(
    r'\b(supersede[sd]?|withdraw[ns]?|withdrawn|retract(?:ed|ion)?|reversed?|'
    r'no longer|instead of|replaced? by|answered|closed|resolved|overrul)\w*', re.I)


def stage4_evidence(candidate, records, key_terms=None):
    """For ONE restore candidate, every record AFTER its drop that could supersede it.

    Owner: "some things might end up superseded, so you need to go and recursively check every
    state restore you did to make sure it's valid in the face of new info."

    This GATHERS; it does not decide. A candidate is reinstated only after a human-read verdict,
    because 'he answered this later' is a judgement the text alone does not always carry."""
    terms = key_terms or _terms(candidate['text'])
    after = [r for r in records if (r.get('ts') or '') > candidate['ts']]
    hits = []
    for r in after:
        txt = r.get('text') or ''
        overlap = sum(1 for t in terms if t in txt.lower())
        if overlap >= max(2, len(terms) // 4) or (overlap and SUPERSEDE_HINT.search(txt)):
            hits.append({'ts': r['ts'], 'overlap': overlap, 'kind': r.get('kind', '?'),
                         'excerpt': txt[:300]})
    return {'candidate': candidate.get('cite'), 'terms': sorted(terms)[:12],
            'records_after': len(after), 'hits': hits}


STOP = set('the a an and or of to in is it that this for with on at by be are was as from '
           'not you your i my he his we our they their if then so but do does did have has '
           'had can will would should could must its into out up down over under again'.split())


def _terms(text):
    w = re.findall(r'[a-z]{4,}', text.lower())
    return {x for x in w if x not in STOP}


# ---------------------------------------------------------------- stage 2

def stage2_snapshots(tm_run, diskutil_run):
    """Every backup that EXISTS, from ground truth -- not from the .timemachine listing.

    Both parsers are written against REAL captured bytes (tests/fixtures/), because a mock of
    output I invented would only prove the parser matches my invention. The real bytes settled
    two things a hand-written mock would have got wrong:

      * `diskutil apfs listSnapshots` names them `com.apple.TimeMachine.<date>.backup` in an
        indented tree, and reports 47 on this drive;
      * `tm ls` returns JSON with `count: 427` and `truncated: true` -- the stale-stub trap in
        live form, and a TRUNCATION FLAG that must be honoured or the listing silently reads 8
        of 427. An unhonoured truncation is indistinguishable from a short list.
    """
    truth = sorted(set(re.findall(r'(\d{4}-\d{2}-\d{2}-\d{6})', diskutil_run() or '')))
    raw = tm_run('ls-backups') or ''
    listed, count, truncated = [], None, False
    try:
        j = json.loads(raw)
        listed = sorted({m.group(1) for e in j.get('entries', [])
                         for m in [re.search(r'(\d{4}-\d{2}-\d{2}-\d{6})', e.get('name', ''))]
                         if m})
        count, truncated = j.get('count'), bool(j.get('truncated'))
    except Exception:
        listed = sorted(set(re.findall(r'(\d{4}-\d{2}-\d{2}-\d{6})', raw)))
    return {'ground_truth': truth, 'listed': listed,
            'listed_count': count, 'truncated': truncated,
            'stale_stubs': [x for x in listed if x not in truth],
            'unlisted_but_real': [x for x in truth if x not in listed],
            'usable': not truncated,
            'why_unusable': ('listing truncated at %d of %s -- page it before concluding '
                             'anything' % (len(listed), count)) if truncated else ''}


def stage2_series(snapshots, read_state):
    """The state dir as it stood at EVERY snapshot. Owner: "reading that state directory since
    it has existed. Do not binary search it. Do not sample it. Everything."

    A snapshot whose state cannot be read is recorded as unreadable, NEVER skipped and never
    treated as unchanged -- missing is not a value, and here it would hide the exact moment a
    store lost a row."""
    series = []
    for s in snapshots:
        d = read_state(s)
        if d is None:
            series.append({'snapshot': s, 'readable': False})
            continue
        series.append({'snapshot': s, 'readable': True,
                       'owner_queue': [x.get('id') for x in (d.get('owner_queue') or [])],
                       'owner_decisions': sorted((d.get('owner_decisions') or {}).keys()),
                       'open_questions': sorted((d.get('open_questions') or {}).keys()),
                       'keys': sorted(d.keys())})
    return series


def stage2_disappearances(series):
    """For each id, the snapshot where it was last seen and the one where it was gone.
    This is the audit source for misses: it dates a drop instead of inferring it."""
    seen, gone = {}, {}
    prev = None
    for row in series:
        if not row.get('readable'):
            continue
        now = set()
        for store in ('owner_queue', 'owner_decisions', 'open_questions'):
            for i in row.get(store) or []:
                if i:
                    now.add((store, i))
                    seen[(store, i)] = row['snapshot']
        if prev is not None:
            for k in prev - now:
                gone.setdefault(k, row['snapshot'])
        prev = now
    return {'last_seen': {'%s/%s' % k: v for k, v in seen.items()},
            'first_absent': {'%s/%s' % k: v for k, v in gone.items()},
            'unreadable': [r['snapshot'] for r in series if not r.get('readable')]}


# ---------------------------------------------------------------- stage 5

def stage5_repair(state_dir, restores, apply=False):
    """ADDITIVE repair. Owner: "Do not clear any queues."

    Appends what survived stage 4 and touches nothing that is already there. Existing rows must
    be byte-identical afterwards, and running it twice must not duplicate -- a repair that is
    not idempotent turns a re-run into corruption."""
    p = os.path.join(state_dir, 'state.json')
    d = json.load(open(p))
    before = json.dumps(d, sort_keys=True)
    added, skipped = [], []
    for r in restores:
        store = r['store']
        if store == 'owner_queue':
            rows = d.setdefault('owner_queue', [])
            if any(x.get('restored_sha256') == r['sha256'] for x in rows):
                skipped.append(r['sha256'][:12]); continue
            rows.append({'id': r.get('id') or ('R%d' % (len(rows) + 1)),
                         'text': r['text'], 'restored_from': r['cite'],
                         'restored_sha256': r['sha256'], 'restored_ts': r.get('now')})
        else:
            rows = d.setdefault(store, {})
            key = r.get('id') or ('R%d' % (len(rows) + 1))
            if any(v.get('restored_sha256') == r['sha256'] for v in rows.values()):
                skipped.append(r['sha256'][:12]); continue
            rows[key] = {'text': r['text'], 'restored_from': r['cite'],
                         'restored_sha256': r['sha256'], 'restored_ts': r.get('now')}
        added.append(r['sha256'][:12])
    # Nothing that existed at the START OF THIS CALL may have changed. Compare against a
    # snapshot taken before mutation -- NOT against "rows without a restore marker", which
    # misclassifies rows an earlier run restored and breaks the second run (found by the
    # idempotency fixture, 2026-09-11).
    orig = json.loads(before)
    oq = orig.get('owner_queue') or []
    if json.dumps(d.get('owner_queue', [])[:len(oq)], sort_keys=True) != json.dumps(oq, sort_keys=True):
        raise AssertionError('repair modified or reordered existing owner_queue rows -- refused')
    for store in ('owner_decisions', 'open_questions'):
        for k, v in (orig.get(store) or {}).items():
            if json.dumps((d.get(store) or {}).get(k), sort_keys=True) != json.dumps(v, sort_keys=True):
                raise AssertionError('repair modified existing %s/%s -- refused' % (store, k))
    for k in orig:
        if k in ('owner_queue', 'owner_decisions', 'open_questions'):
            continue
        if json.dumps(d.get(k), sort_keys=True) != json.dumps(orig[k], sort_keys=True):
            raise AssertionError('repair touched unrelated key %r -- refused' % k)
    if apply:
        tmp = p + '.tmp'
        json.dump(d, open(tmp, 'w'), indent=1, sort_keys=True)
        os.replace(tmp, p)
    return {'added': added, 'skipped_duplicate': skipped, 'applied': bool(apply)}


def tm_read_split(raw):
    """`tm read` returns FILE BYTES with a JSON status trailer appended. Established from real
    captured output (tests/fixtures/tm_read_state_hit.raw), not assumed:

        {... the file ...}{"bytes": 300, "ok": true, "path": "...", "truncated": true, ...}

    So neither `json.loads(out)` nor "the whole output is the file" is correct -- the first
    throws, the second silently appends the trailer to the content. Returns (content, status).

    Three things in that trailer decide whether the read may be used at all, and every one of
    them is invisible to the exit status, which is 0 even for a miss:
      ok=false     -> unreadable
      truncated    -> the content is PARTIAL; parsing it as JSON yields garbage or throws
      bytes        -> what was actually delivered
    """
    # NOT stripped: the file's own leading/trailing whitespace is content. Stripping cost two
    # bytes against a real 1470-byte read (fixture tm_read_complete.raw) and would have broken
    # any later hash comparison silently.
    s = raw
    if not s.strip():
        return '', {'ok': False, 'error': 'empty output'}
    for i in range(len(s) - 1, -1, -1):
        if s[i] != '{':
            continue
        try:
            status = json.loads(s[i:])
        except Exception:
            continue
        if isinstance(status, dict) and ('ok' in status or 'error' in status):
            return s[:i], status
    try:
        return '', json.loads(s)
    except Exception:
        return s, {'ok': None, 'error': 'no status trailer found'}


def tm_state_at(raw):
    """The state dict from one snapshot's state.json, or a NAMED refusal.

    Never returns a partial parse and never reads a miss as an empty state: a store that looks
    empty because the read was truncated is exactly how a reconciliation invents a drop."""
    content, st = tm_read_split(raw)
    if not st.get('ok'):
        return None, 'unreadable: %s' % (st.get('error') or 'ok=false')[:120]
    if st.get('truncated'):
        return None, ('truncated at %s bytes -- re-read whole or extract; a partial state file '
                      'must never be parsed' % st.get('bytes'))
    want = st.get('bytes')
    if isinstance(want, int) and len(content.encode()) != want:
        return None, ('content is %d bytes but the reader reported %d -- refused rather than '
                      'parsed' % (len(content.encode()), want))
    try:
        return json.loads(content), 'ok'
    except Exception as e:
        return None, 'content did not parse: %s' % e


# ---------------------------------------------------------------- decision chains



# ================================================================ record-shape truth
# Established from the REAL transcript on 2026-09-11, not assumed. The first fixture invented
# an `attachment` with message.content -- a shape the real records do not have -- so on real
# data the extractor read NONE of the owner's mid-turn words and the fixture still passed.
#
#   queue-operation  top-level `content` (str); operation enqueue | dequeue | remove.
#                    enqueue carries the owner's words AND <task-notification> events.
#   attachment       `attachment` dict; type queued_command carries the words in `prompt`.
#                    Every queued_command duplicates an enqueue -- the same message, delivered
#                    mid-turn -- so a naive union double-counts every mid-turn message.
#   user             message.content: str, or a list of blocks (text / tool_result).
#   tool_result      content: str, or a list of blocks; is_error True | False | None.

# Everything that arrives on an owner channel but is not his words. Each is COUNTED when
# excluded -- "Missing is not a value": a silent filter is how a ruling vanishes.
NOT_OWNER = [
    ('task_notification', re.compile(r'^\s*<task-notification')),
    ('system_reminder', re.compile(r'^\s*<system-reminder')),
    ('peer_message', re.compile(r'^\s*(<cross-session-message|Another Claude session sent a message)')),
    ('compaction_summary', re.compile(r'^\s*This session is being continued')),
    ('interrupt_marker', re.compile(r'^\s*\[Request interrupted')),
    ('slash_command', re.compile(r'^\s*<(command-name|command-message|command-args|local-command)')),
    ('skill_load', re.compile(r'^\s*Base directory for this skill')),
]
PEER = re.compile(r'<cross-session-message\s+from="([^"]+)"[^>]*>(.*?)(?:</cross-session-message>|$)', re.S)


def _block_text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return '\n'.join(b.get('text', '') for b in c
                         if isinstance(b, dict) and b.get('type') == 'text' and b.get('text'))
    return ''


def channel_texts(rec):
    """(source, text) pairs a record carries on an owner-facing channel, in REAL shapes."""
    t = rec.get('type')
    if t == 'queue-operation':
        if rec.get('operation') == 'enqueue' and isinstance(rec.get('content'), str):
            return [('enqueue', rec['content'])]
        return []
    if t == 'attachment':
        a = rec.get('attachment')
        if isinstance(a, dict) and a.get('type') == 'queued_command' and isinstance(a.get('prompt'), str):
            return [('queued_command', a['prompt'])]
        return []
    if t == 'user':
        c = (rec.get('message') or {}).get('content')
        if isinstance(c, str):
            return [('user', c)]
        if isinstance(c, list):
            txt = _block_text(c)
            return [('user', txt)] if txt else []
    return []


def owner_messages(recs):
    """The owner's words, once per MESSAGE, with provenance and an exclusion ledger.

    A message is keyed by its DELIVERY, never by its text. Every message he types is enqueued
    once; its delivery (a `user` record after a dequeue, a `queued_command` attachment after a
    remove) is paired with the OLDEST UNDELIVERED enqueue of the same text. Text-keyed dedupe
    was found wrong by probe: "yes" at 01:00, 05:00 and 06:00 collapsed into ONE message dated
    01:00, so a decision he answered "yes" at 05:00 read as put-not-answered.

    BATCHED DELIVERY: several queued messages are dequeued together into ONE user record whose
    text is their newline-join; that delivery is paired with the pending enqueues it joins.
    A delivery with no pending enqueue (a transcript that begins mid-way) is its own message.
    Peer messages are paired the same way, so each is recorded once.

    Returns (messages, excluded, peers, accounting). accounting satisfies
        seen == attributed + excluded_total + batch_deliveries
    so no text leaves the corpus uncounted."""
    msgs, excluded, peers = [], collections.Counter(), []
    # Every key present from the start: a corpus with no owner text at all (a subagent's
    # transcript, a window before he spoke) crashed stage 1 with KeyError('seen'). Zero is a
    # measured count here -- every record was examined and none carried his words.
    acct = collections.Counter({'seen': 0, 'attributed': 0, 'batch_deliveries': 0})
    pending = collections.defaultdict(list)       # text -> his undelivered enqueues, oldest first
    peer_pending = collections.defaultdict(list)  # the same, for peer messages
    for r in recs:
        if r.get('isMeta'):
            excluded['meta'] += 1
            acct['seen'] += 1
            continue
        ts = r.get('timestamp') or ''
        for src, txt in channel_texts(r):
            acct['seen'] += 1
            if not txt or not txt.strip():
                excluded['empty'] += 1
                continue
            key = txt.strip()
            cls = next((name for name, rx in NOT_OWNER if rx.match(txt)), None)
            if cls:
                excluded[cls] += 1
                if cls == 'peer_message':
                    if src != 'enqueue' and peer_pending[key]:
                        peer_pending[key].pop(0)      # the delivery of one already recorded
                    else:
                        m = PEER.search(txt)
                        pr = {'ts': ts, 'from': m.group(1) if m else '',
                              'text': (m.group(2) if m else txt).strip()}
                        peers.append(pr)
                        if src == 'enqueue':
                            peer_pending[key].append(pr)
                continue
            if src != 'enqueue' and pending[key]:
                pending[key].pop(0)['sources'].add(src)
                acct['attributed'] += 1
                continue
            parts = [x.strip() for x in key.split('\n') if x.strip()]
            need = collections.Counter(parts)
            if src != 'enqueue' and len(parts) > 1 and all(len(pending[x]) >= n
                                                            for x, n in need.items()):
                for x in parts:
                    pending[x].pop(0)['sources'].add(src + '(batch)')
                acct['batch_deliveries'] += 1
                continue
            m = {'ts': ts, 'text': key, 'sources': {src}, 'uuid': r.get('uuid')}
            msgs.append(m)
            acct['attributed'] += 1
            if src == 'enqueue':
                pending[key].append(m)
    msgs.sort(key=lambda m: m['ts'])
    for m in msgs:
        m['sources'] = sorted(m['sources'])
    acct['excluded_total'] = sum(excluded.values())
    return msgs, dict(excluded), sorted(peers, key=lambda p: p['ts']), dict(acct)


# ================================================================ action outcomes

def result_map(recs):
    """tool_use id -> (result text, is_error). The OUTCOME of every action lives here."""
    out = {}
    for r in recs:
        c = (r.get('message') or {}).get('content')
        if not isinstance(c, list):
            continue
        for b in c:
            if isinstance(b, dict) and b.get('type') == 'tool_result' and b.get('tool_use_id'):
                out[b['tool_use_id']] = (_block_text(b.get('content')), bool(b.get('is_error')))
    return out


def ts_formats(recs):
    """Timestamp formats present, by shape (digits -> 9). Events are ordered by comparing
    timestamps as STRINGS, which is sound only within one format: '...:00Z' sorts AFTER
    '...:00.123Z'. Measured on the real record: one format (milliseconds + Z) on every
    timestamped record. Stage 1 refuses a corpus that mixes them."""
    return collections.Counter(re.sub(r'\d', '9', r['timestamp']) for r in recs
                               if isinstance(r.get('timestamp'), str) and r['timestamp'])


# ================================================================ my actions, read by the shell reader
#
# An outcome is ESTABLISHED only by evidence that names the action. The result of a compound
# command is not the result of one verb: on the real record the verb's own output was routinely
# diverted (`>/dev/null`, `| tail -1`) and a listing printed after it, so "no success line" called
# 30 of 34 queue adds and 11 of 11 owe adds `no_effect` while the very same result showed them
# landed, and `queued (\S+)` matched prose ("queued behind this") into ids. Where no evidence names
# the action yet, its outcome is PENDING: work the reconciliation still owes, never a verdict.

SUCCESS = {  # what each verb prints at the START of a line when it took effect (the scripts' prints)
    'queue add': r'queued (\S+)', 'owe add': r'recorded (D\d+)',
    'ask': r'open question (\S+) registered', 'sent1': r'item (\S+) marked sent at',
    'nudged': r'nudged (\S+) \(', 'resolved': r'resolved (\S+) \(open since',
    'owe done': r'(D\d+) answered and cleared', 'owe ungate': r'(D\d+) is now READY',
    'queue clear': r'queue cleared into message (\S+)',
    'queue hold': r'(\S+) (?:HELD UNTIL:|released)',
    'closed': r'closed (\S+) under the one-line exception',
    'relayed': r'relayed to the owner up to (\S+)', 'answered': r'answered at (\S+)',
    'hold': r'holding (\S+):', 'conditional': r'PARKED (\S+)', 'fired': r'FIRED (\S+)',
    'outcome': r'(\S+) graded \w+',
    'sent': r'recorded sent: \[([^\]]*)\]', 'veto': r'recorded veto: \[([^\]]*)\]',
}
NAMED_FAIL = re.compile(r"^(?:no queued item '?([^\s']+?)'?|no open question (\S+))\s*$", re.M)
UNNAMED_FAIL = re.compile(r'^(?:REFUSED\b.*|usage: .*|Traceback \(most recent call last\):.*|'
                          r'\S+: error: .*|answered takes no arguments.*|.*command not found.*)$', re.M)
OUTCOMES = ('landed', 'failed', 'no_effect', 'not_completed')
OPEN_STORE = {'queue add': 'owner_queue', 'owe add': 'owner_decisions', 'ask': 'open_questions'}
CLOSE_VERBS = {'sent1', 'nudged', 'resolved', 'owe done', 'owe ungate', 'queue clear', 'closed',
               'fired'}
STATE_VERBS = set(SUCCESS)
REQUIRED_ARGS = {'relayed': 1, 'hold': 2, 'closed': 1, 'ask': 2, 'nudged': 1, 'resolved': 1,
                 'sent1': 1, 'owe done': 1, 'owe ungate': 1, 'queue clear': 1, 'queue hold': 1,
                 'outcome': 2, 'sent': 1, 'veto': 1, 'conditional': 1, 'fired': 1,
                 'queue add': 1, 'owe add': 1}


def read_records(path):
    """(lineno, record) for every parseable line, plus the count of unparseable ones."""
    out, bad = [], 0
    with open(path, 'rb') as f:
        for i, line in enumerate(f, 1):
            try:
                out.append((i, json.loads(line)))
            except Exception:
                bad += 1
    return out, bad


def _invalid(inv):
    """A fact the command itself establishes: this invocation could not have succeeded."""
    v, args = inv['verb'], [a for a in inv['args']]
    if inv.get('via') == 'wd_check.py' and '--target' not in inv['argv']:
        return 'wd_check.py requires --target; argparse exits 2 without it'
    if v == 'answered' and inv.get('via') == 'wd.sh' and args and args[0] != '--owner-ack':
        return 'wd.sh answered takes no argument except --owner-ack; it exits 2'
    need = REQUIRED_ARGS.get(v)
    if need and len([a for a in args if a != '']) < need:
        return '%s needs %d argument(s); the script refuses without them' % (v, need)
    return None


def _id_match(verb, ident, printed):
    if ident is None:
        return True
    if verb in ('sent', 'veto'):
        got = set(re.findall(r"'([^']*)'", printed)) or {printed}
        want = {x.strip() for x in ident.split(',')}
        return bool(got & want) or ident in got
    if verb == 'relayed':
        return printed >= ident                  # it records max(previous, this)
    return printed == ident


def my_actions(numbered, fname, live_state, cwd=None):
    """Every state-changing action of mine, in order, read by the shell reader, each with the
    outcome the evidence establishes -- or outcome None and `needs` saying what would establish it.

    live_state is the state directory under reconciliation; actions on any other are recorded with
    state='scratch' and are not reconciliations of the live state. cwd is the working directory a
    command starts in when its record does not say (real records carry `cwd`)."""
    rm = result_map([r for _, r in numbered])
    acts = []
    for lineno, rec in numbered:
        if rec.get('type') != 'assistant':
            continue
        ts = rec.get('timestamp') or ''
        here = rec.get('cwd') or cwd
        blocks = [b for b in ((rec.get('message') or {}).get('content') or [])
                  if isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') == 'Bash']
        for j, b in enumerate(blocks):
            cmd = (b.get('input') or {}).get('command', '')
            invs = [x for x in invocations(cmd, live_state, here) if x['verb'] in STATE_VERBS]
            if invs:
                acts.extend(_command_actions(invs, rm.get(b.get('id')), ts,
                                             '%s:%d#%d' % (fname, lineno, j)))
    return acts


def _set(a, outcome, why):
    a['outcome'], a['why'] = outcome, why
    return a


def _command_actions(invs, res, ts, cite):
    txt, err = res if res else ('', False)
    rows = []
    for inv in invs:
        v, args, aexp = inv['verb'], inv['args'], inv['arg_expands']
        a = {'ts': ts, 'cite': cite, 'verb': v, 'state': inv['state'], 'diverted': inv['diverted'],
             'op_before': inv['op_before'], 'loop': inv['loop'], 'outcome': None, 'why': None}
        if v in OPEN_STORE:
            a.update(kind='open', store=OPEN_STORE[v])
            if v == 'ask':
                key = args[0] if args else None
                a['id'] = None if (not key or (aexp and aexp[0])) else key
                t, resolved = ' '.join(args[1:]), not any(aexp[1:])
            else:
                a['id'], t, resolved = None, inv['text'] or '', bool(inv['text_resolved'])
            a.update(text=t, text_resolved=resolved,
                     sha256=hashlib.sha256(t.encode()).hexdigest())
            if inv.get('gated_on') is not None:
                a['gated_on'] = inv['gated_on']
            if inv.get('urgent'):
                a['urgent'] = True
        else:
            a['kind'] = 'close' if v in CLOSE_VERBS else 'mark'
            ident = args[0] if args else None
            if v == 'answered':
                ident = None
            if ident is not None and aexp and aexp[0]:
                a.update(id=None, id_from=None, unresolved=[ident])
            else:
                a.update(id=ident, id_from='command' if ident else None)
        rows.append((a, inv))
    if res is None:
        return [_set(a, 'not_completed', 'no tool_result recorded -- the command did not complete')
                for a, _ in rows]
    lines = {v: [(m.start(), m.group(1)) for m in re.finditer('^' + SUCCESS[v], txt, re.M)]
             for v in {a['verb'] for a, _ in rows}}
    claimed, extra = set(), []
    # 1. facts: structural failures, && links that never ran, lines that name the action
    for k, (a, inv) in enumerate(rows):
        prev = rows[k - 1] if k else None
        if (inv['op_before'] == '&&' and prev and prev[1]['cmd_index'] == inv['cmd_index'] - 1
                and prev[0]['outcome'] in ('failed', 'not_completed')):
            _set(a, 'not_completed', 'never ran: the && link before it did not succeed')
            continue
        v, L = a['verb'], lines.get(a['verb'], [])
        free = [(p, i) for p, i in L if (v, p) not in claimed]
        if a.get('unresolved'):
            if free and not inv['to_file']:
                for p, i in free:
                    claimed.add((v, p))
                    extra.append(dict(a, id=i, id_from='result', unresolved=None,
                                      outcome='landed',
                                      why='id %s read from the result; the command bound it '
                                          'through %s' % (i, a['unresolved'][0])))
                a['outcome'] = 'superseded'           # replaced by the per-id rows above
            continue
        if a['kind'] == 'open' and v != 'ask' or v == 'answered' or a.get('id') is None:
            hit = free[0] if (free and not inv['to_file']) else None
        else:
            hit = next(((p, i) for p, i in free if _id_match(v, a['id'], i)), None)
        if hit:
            claimed.add((v, hit[0]))
            if a['kind'] == 'open' and v != 'ask':
                a['id'], a['id_from'] = hit[1], 'result'
            elif a.get('id') is None and a['kind'] == 'close':
                a['id'], a['id_from'] = hit[1], 'result'
            _set(a, 'landed', 'result says: %s' % re.search('^' + SUCCESS[v], txt[hit[0]:], re.M).group(0)[:80])
            continue
        bad = _invalid(inv)
        if bad:
            _set(a, 'failed', bad)
            continue
        nf = next((m for m in NAMED_FAIL.finditer(txt)
                   if a.get('id') and (m.group(1) or m.group(2)) == a['id']
                   and ('F', m.start()) not in claimed), None)
        if nf:
            claimed.add(('F', nf.start()))
            _set(a, 'failed', 'result says: %s' % nf.group(0)[:80])
    # 2. a failure line that names nobody belongs to the one invocation that could have printed it
    unnamed = [m.group(0) for m in UNNAMED_FAIL.finditer(txt)]
    open_rows = [(a, inv) for a, inv in rows if a['outcome'] is None and not inv['to_file']]
    if unnamed and len(open_rows) == 1:
        _set(open_rows[0][0], 'failed', 'result says: %s' % unnamed[0][:80])
    # 2b. a command that exited non-zero, whose LAST command was this invocation -- unconditional
    #     and unpiped, so it ran and its exit status was the command's -- failed
    if err:
        for a, inv in rows:
            if (a['outcome'] is None and inv.get('is_last') and not inv['piped']
                    and inv['op_before'] not in ('&&', '||')):
                _set(a, 'failed', 'the command exited non-zero and this invocation was its last command')
    # 3. silence proves nothing unless the output reached the result
    for a, inv in rows:
        if a['outcome'] is not None:
            continue
        if (not inv['diverted'] and not (unnamed and len(open_rows) > 1)
                and not (err and inv['op_before'] in ('&&', '||'))):
            _set(a, 'no_effect', 'its output reached the result and printed no line of %s' % a['verb'])
        else:
            a['needs'] = ('its output was %s and the result shows no line naming it: establish it '
                          'from project state or a snapshot'
                          % ('diverted' if inv['diverted'] else 'possibly a failed link'))
    return [a for a, _ in rows if a['outcome'] != 'superseded'] + extra


# ---------------------------------------------------------------- evidence outside the result

QLISTING = re.compile(r'^\s*(?:[-*]\s*)?([A-Z][A-Z0-9_-]*\d+)\s+\[(\d{4}-\d\d-\d\dT[\d:.]+Z)\]\s+(.+)$', re.M)
DLISTING = re.compile(r'^\s{2}(D\d+)\s+(.+)$', re.M)
EVIDENCE_WINDOW_S = 300


def _dt(s):
    """An ISO timestamp as an aware datetime. Project state writes some without a zone; the
    watchdog writes UTC throughout, so an unzoned one is UTC (comparing naive with aware raised)."""
    try:
        d = datetime.datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def _in_window(t, act_ts):
    a, b = _dt(t), _dt(act_ts)
    return bool(a and b) and -10 <= (a - b).total_seconds() <= EVIDENCE_WINDOW_S


def _norm(s):
    return ' '.join((s or '').split()).rstrip('…').rstrip('.')


def resolve_from_evidence(acts, recs, state):
    """Settle PENDING actions from evidence that names them: listing lines printed by later
    commands (`Qn [queued-at] text`, `  Dn text`) and project state (queue items with their queued
    and sent times; open and resolved questions). Nothing is settled from the absence of evidence:
    what no source names stays pending, and the reconciliation is not complete while it does."""
    ql, dl = [], []
    for r in recs:
        c = (r.get('message') or {}).get('content')
        if not isinstance(c, list):
            continue
        for b in c:
            if isinstance(b, dict) and b.get('type') == 'tool_result':
                t = _block_text(b.get('content'))
                ql += [(m.group(1), m.group(2), m.group(3)) for m in QLISTING.finditer(t)]
                dl += [(r.get('timestamp') or '', m.group(1), m.group(2)) for m in DLISTING.finditer(t)]
    items = [(x.get('id'), x.get('ts'), x.get('text'), x.get('sent_ts') or x.get('sent'))
             for x in (state.get('owner_queue') or []) + (state.get('owner_queue_sent') or [])]
    qs = dict(state.get('open_questions') or {})
    qs.update(state.get('resolved_questions') or {})
    for a in acts:
        if a['outcome'] is not None or a.get('state') != 'live':
            continue
        head = _norm(a.get('text'))[:50]
        v, hit = a['verb'], None
        if v == 'queue add' and head:
            ids = {i for i, t, x in ql if _in_window(t, a['ts']) and _norm(x).startswith(head)} | \
                  {i for i, t, x, _s in items if _in_window(t, a['ts']) and _norm(x).startswith(head)}
            if len(ids) == 1:
                hit = (ids.pop(), 'listing/project state shows it queued within the window')
        elif v == 'owe add' and head:
            after = sorted((t, i) for t, i, x in dl if t >= a['ts'] and _norm(x).startswith(head))
            if after and len({i for t, i in after if t == after[0][0]}) == 1:
                hit = (after[0][1], 'the first owe listing after it shows it')
        elif v == 'sent1' and a.get('id'):
            if any(i == a['id'] and s and _in_window(s, a['ts']) for i, t, x, s in items):
                hit = (a['id'], 'project state shows %s sent within the window' % a['id'])
        elif v in ('ask', 'resolved') and a.get('id') in qs:
            q = qs[a['id']]
            t = q.get('asked_ts') if v == 'ask' else q.get('resolved_ts')
            if t and _in_window(t, a['ts']):
                hit = (a['id'], 'project state records it at %s' % t)
        if hit:
            a['id'] = a.get('id') or hit[0]
            a['id_from'] = a.get('id_from') or 'evidence'
            a.pop('needs', None)
            _set(a, 'landed', hit[1])
    return acts


def outstanding(acts):
    """Live actions whose outcome no evidence has established yet. The reconciliation OWES these:
    each is a place Time Machine or a later record must be read, and none is a verdict."""
    return [a for a in acts if a.get('state') == 'live' and a['outcome'] is None]


def turn_starts(numbered):
    """A turn opens at every user record with str content that is not a tool result: an owner
    message, a peer message or a task notification delivered as a turn. Mid-turn attachments do
    NOT open a turn. This is structure, not a guess about who was talking."""
    return sorted(r.get('timestamp') or '' for _, r in numbered
                  if r.get('type') == 'user' and not r.get('isMeta')
                  and isinstance((r.get('message') or {}).get('content'), str))


def same_turn(starts, t1, t2):
    lo, hi = min(t1, t2), max(t1, t2)
    return not any(lo < s <= hi for s in starts)


def _spans(text, minlen=20):
    """Verbatim spans of a text: the whole thing if short, else its sentences of minlen+.
    Relays are verbatim BY RULE, so a relay is tested by a verbatim span, not by overlap."""
    t = ' '.join(text.split())
    if len(t) < minlen * 2:
        return [t] if t else []
    return [s.strip() for s in re.split(r'(?<=[.!?\n])\s+', t) if len(s.strip()) >= minlen] or [t[:minlen * 2]]


def carries(message, text):
    m = ' '.join(message.split())
    return any(s in m for s in _spans(text))


# ================================================================ decision chains, structural

CHAIN_VERDICTS = ('complete', 'ask_did_not_land', 'never_put_to_owner', 'put_not_answered',
                  'answered_not_forwarded', 'answered_not_closed', 'closed_without_answer',
                  'close_had_no_effect', 'close_failed', 'orphan_close')


def decision_chains(acts, owner, my_text, sends, target):
    """Every decision's chain from the record's STRUCTURE, never from word overlap.

        ASKED      an `owe add` whose result minted Dn
        PUT        my own text to the owner naming Dn, after the ask
        ANSWERED   his message naming Dn after the put; failing that, his NEXT message after
                   the put -- the reply to the turn that put it to him, however terse
        FORWARDED  a send to the target carrying a VERBATIM span of that answer
        CLOSED     an `owe done`/`owe-clear` of Dn whose result says it took effect

    Every verdict is a fact about the record. Word overlap is gone: it attributed one ruling to
    two decisions and could not match "drop it" at all.

    AN ID CAN BE MINTED MORE THAN ONCE (a store reset restarts the counter). Each minting is
    its own chain -- keyed Dn#1, Dn#2 when that happens -- and every event naming Dn belongs to
    the most recent minting at or before it. Found by probe: a dict keyed by id kept only the
    last ask, so the first chain vanished and its close was credited to the second."""
    if not target:
        raise ValueError('decision_chains needs the target session id: forwarding his answer '
                         'means a send TO THE TARGET, and a send elsewhere is not a forward')
    acts = [a for a in acts if a.get('state', 'live') == 'live']
    decs = sorted((a for a in acts if a['kind'] == 'open' and a['store'] == 'owner_decisions'
                   and a['id']), key=lambda a: a['ts'])
    # an ask with no id whose outcome is PENDING is outstanding work, not a failed ask
    failed_asks = [a for a in acts if a['kind'] == 'open' and a['store'] == 'owner_decisions'
                   and not a['id'] and a['outcome'] is not None]
    mintings = collections.defaultdict(list)
    for a in decs:
        mintings[a['id']].append(a)
    closes = collections.defaultdict(list)
    for a in acts:
        if a['kind'] == 'close' and a['verb'] in ('owe done', 'owe-clear') and a['id']:
            closes[a['id']].append(a)
    # CASE-INSENSITIVE: the owner writes in lowercase. Found by probe: "d4: never" was not
    # read as naming D4, so it did not scope itself and was credited to D5 as well; and my
    # own put written "d4 for you" read as never put.
    ids_rx = [re.compile(r'\b%s\b' % re.escape(d), re.I) for d in mintings]
    puts = [t for t in my_text if any(r.search(t['text']) for r in ids_rx)]
    out = {}
    for did, seq in mintings.items():
        rx = re.compile(r'\b%s\b' % re.escape(did), re.I)
        for k, ask in enumerate(seq):
            until = seq[k + 1]['ts'] if k + 1 < len(seq) else None
            inside = lambda t, lo=ask['ts'], hi=until: t >= lo and (hi is None or t < hi)
            put = next((t for t in my_text if inside(t['ts']) and rx.search(t['text'])), None)
            answer = None
            if put:
                answer = next((o for o in owner if o['ts'] > put['ts'] and inside(o['ts'])
                               and rx.search(o['text'])), None)
                if answer is None:
                    # His NEXT message is the reply to the turn that put it to him -- however
                    # terse -- UNLESS it names a decision id (then it has scoped itself), or
                    # another decision was put to him in between (then it belongs to that later
                    # turn: an unanswered D3 once took D4's terse answer), or it falls after
                    # this id was minted again.
                    nxt = next((o for o in owner if o['ts'] > put['ts']), None)
                    later = [t for t in puts if t['ts'] > put['ts'] and nxt is not None
                             and t['ts'] < nxt['ts'] and not rx.search(t['text'])]
                    if (nxt is not None and inside(nxt['ts']) and not later
                            and not re.search(r'\bD\d+\b', nxt['text'], re.I)):
                        answer = nxt
            fwd = next((s for s in sends if answer and s.get('to') == target
                        and s['ts'] > answer['ts'] and carries(s['msg'], answer['text'])), None)
            cl = [c for c in closes.get(did, []) if inside(c['ts'])]
            landed = [c for c in cl if c['outcome'] == 'landed']
            pend = [c for c in cl if c['outcome'] is None]
            owed = []
            if pend and not landed:
                owed.append('a close of %s whose outcome is not yet established' % did)
            if ask.get('outcome') is None:
                owed.append('whether the ask of %s landed' % did)
            v = []
            if not put:
                v.append('never_put_to_owner')
            elif not answer:
                v.append('put_not_answered')
            if answer and not fwd:
                v.append('answered_not_forwarded')
            if landed and (not answer or landed[0]['ts'] < answer['ts']):
                v.append('closed_without_answer')
            if cl and not landed and not pend:
                v.append('close_failed' if any(c['outcome'] == 'failed' for c in cl)
                         else 'close_had_no_effect')
            if answer and not landed and not pend:
                v.append('answered_not_closed')
            key = did if len(seq) == 1 else '%s#%d' % (did, k + 1)
            out[key] = {'asked': ask['ts'], 'ask_cite': ask['cite'],
                        'put': put['ts'] if put else None,
                        'answered': answer['ts'] if answer else None,
                        'answer_text': answer['text'][:300] if answer else None,
                        'forwarded': fwd['ts'] if fwd else None,
                        'closed': landed[0]['ts'] if landed else None,
                        'close_attempts': [(c['ts'], c['outcome']) for c in cl],
                        'verdicts': v or ([] if owed else ['complete']), 'outstanding': owed,
                        'text': ask['text'][:160]}
    for did, cs in closes.items():
        first = mintings[did][0]['ts'] if did in mintings else None
        for c in cs:
            if first is None or c['ts'] < first:
                key = did if did not in out else did + '@orphan'
                out.setdefault(key, {'verdicts': ['orphan_close'], 'close_attempts': []})
                out[key]['close_attempts'].append((c['ts'], c['outcome']))
    for a in failed_asks:
        out['ASK@' + a['cite']] = {'asked': a['ts'], 'ask_cite': a['cite'],
                                   'verdicts': ['ask_did_not_land'], 'text': a['text'][:160],
                                   'why': a['why']}
    return out



# ================================================================ stage 3, determinate

REPLAY_VERDICTS = ('ok', 'MISSTEER') + OUTCOMES[1:]      # ok, MISSTEER, failed, no_effect, not_completed


def item_texts(state, acts):
    """Qn -> every text the record or project state holds for it. Project state is one of the
    owner's three admissible sources, and `owner_queue_sent` keeps the text of each sent item."""
    out = collections.defaultdict(list)
    for x in (state.get('owner_queue') or []) + (state.get('owner_queue_sent') or []):
        if x.get('id') and x.get('text'):
            out[x['id']].append(x['text'])
    for a in acts:
        if a['kind'] == 'open' and a.get('id') and a.get('text_resolved', True):
            out[a['id']].append(a['text'])
    return out


def landed_replay(acts, starts, my_text, sends, peers, state, target):
    """Did each action of mine ACTUALLY happen? Owner: "go back in the transcript and confirm
    every action landed. Do it as a replay."

    First the action's own outcome (its result). Then, for a verb that CLAIMS something happened
    beyond the state file, the artifact that must exist in the same turn if it was honest:
      sent1 Qn   a send carrying a verbatim span of Qn's text
      nudged K   a send in the same turn
      answered   a send in the same turn, before it
      relayed    my own text to the owner in the same turn
      resolved K a reply from the target after K was asked
    Every verdict is a fact. There is no undecidable: a check the record cannot satisfy is a
    MISSTEER with the reason, which is itself a finding."""
    if not target:
        # sent1, nudged and answered claim a send TO THE TARGET. Measured on the real record:
        # of 393 sends, 4 went to another session, and any of them satisfied these checks.
        raise ValueError('landed_replay needs the target session id: sent1, nudged and answered '
                         'claim a send TO THE TARGET, and a send elsewhere must not satisfy them')
    tsends = [s for s in sends if s.get('to') == target]
    texts = item_texts(state, acts)
    asked = {a['id']: a['ts'] for a in acts if a['kind'] == 'open' and a['verb'] == 'ask' and a['id']}
    out = []
    for a in acts:
        # a PENDING action (no evidence yet) is outstanding work, never a verdict; a scratch-state
        # action did not touch the state under reconciliation
        if a['outcome'] is None or a.get('state', 'live') != 'live':
            continue
        v, why = a['outcome'], a['why']
        if v != 'landed':
            out.append(dict(a, verdict=v, why=why))
            continue
        verb, ident, ts = a['verb'], a.get('id'), a['ts']
        v, why = 'ok', why
        if verb == 'sent1':
            cand = texts.get(ident) or []
            if not cand:
                v, why = 'MISSTEER', 'no text for %s survives in the record or project state' % ident
            else:
                hit = [s for s in tsends if same_turn(starts, s['ts'], ts) and s['ts'] <= ts
                       and any(carries(s['msg'], t) for t in cand)]
                elsewhere = sorted({s.get('to') or '(no destination recorded)' for s in sends
                                    if s.get('to') != target and same_turn(starts, s['ts'], ts)
                                    and s['ts'] <= ts and any(carries(s['msg'], t) for t in cand)})
                v, why = (('ok', 'a send to the target in the same turn carries %s verbatim' % ident)
                          if hit else
                          ('MISSTEER', '%s marked sent, but its text went to %s, not the target'
                           % (ident, ', '.join(elsewhere))) if elsewhere else
                          ('MISSTEER', '%s marked sent, but no send in that turn carries its text' % ident))
        elif verb in ('nudged', 'answered'):
            hit = [s for s in tsends if same_turn(starts, s['ts'], ts) and s['ts'] <= ts]
            v, why = ('ok', 'a send to the target in the same turn precedes it') if hit else \
                     ('MISSTEER', '`%s` recorded with no send to the target in that turn' % verb)
        elif verb == 'relayed':
            hit = [t for t in my_text if same_turn(starts, t['ts'], ts)]
            v, why = ('ok', 'text to the owner in the same turn') if hit else \
                     ('MISSTEER', '`relayed` with nothing said to the owner in that turn')
        elif verb == 'resolved':
            # A reply counts only from a session I SENT TO after the ask and before the reply.
            # Found by probe: chatter from an unrelated session satisfied `resolved`.
            since = asked.get(ident)
            hit = [p for p in peers if (since is None or p['ts'] > since) and p['ts'] <= ts
                   and any(s.get('to') and s['to'] == p['from'] and s['ts'] <= p['ts']
                           and (since is None or s['ts'] >= since) for s in sends)]
            v, why = ('ok', 'the session it was sent to replied before it was resolved') if hit else \
                     ('MISSTEER', '%s resolved with no reply, after it was asked, from a session '
                                  'it was sent to' % ident)
        out.append(dict(a, verdict=v, why=why))
    return out



def artifacts(recs):
    """My visible text to the owner and my sends to the target, in order."""
    my_text, sends = [], []
    for r in recs:
        if r.get('type') != 'assistant':
            continue
        for b in ((r.get('message') or {}).get('content') or []):
            if not isinstance(b, dict):
                continue
            if b.get('type') == 'text' and b.get('text'):
                my_text.append({'ts': r.get('timestamp') or '', 'text': b['text']})
            elif b.get('type') == 'tool_use' and 'send_message' in (b.get('name') or ''):
                inp = b.get('input') or {}
                sends.append({'ts': r.get('timestamp') or '', 'msg': inp.get('message') or '',
                              # where it went: a reply only answers what was sent to its sender
                              'to': inp.get('session_id') or inp.get('to')})
    return my_text, sends


def transcript_for(prefix, proj=None):
    root = proj or PROJ
    hits = [f for f in sorted(os.listdir(root)) if f.startswith(prefix) and f.endswith('.jsonl')]
    if not hits:
        raise ValueError('no transcript for %s under %s' % (prefix, root))
    return os.path.join(root, hits[0])


# ================================================================ reading commands as the shell does
#
# An invocation is recognised by the SHELL'S OWN STRUCTURE, never by a pattern found somewhere in
# the text. Regexes over raw command text produced, on the real record: a `grep "--sent"` counted
# as a `sent`; `answered && git add` giving `answered` the id `&&`; `relayed <ts>;` keeping the
# `;`; a close inside a quoted `python3 -c` probe program counted as real; item text cut at an
# escaped quote; and a loop unrolled INSIDE a quoted string. Every one of those is a question the
# shell answers exactly: which words are a command, which are data, and which never ran.

SH_REDIRS = ('<<<', '<<-', '<<', '>>', '>&', '>|', '<&', '<>', '&>>', '&>', '>', '<')
SH_OPS = ('&&', '||', ';;', '|&', ';', '|', '&', '(', ')')
SH_KEYWORDS = {'if', 'then', 'else', 'elif', 'fi', 'do', 'done', 'while', 'until', 'case', 'esac',
               '{', '}', '!', 'time'}


def _sh_balanced(s, i, open_, close):
    """s[i] is just past `open_`; return the index just past the matching `close`, honouring
    quotes and escapes."""
    depth, q = 1, None
    while i < len(s):
        ch = s[i]
        if q:
            if ch == '\\' and q == '"':
                i += 2
                continue
            if ch == q:
                q = None
        elif ch in '\'"':
            q = ch
        elif ch == '\\':
            i += 2
            continue
        elif ch == open_:
            depth += 1
        elif ch == close:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(s)


def _sh_dollar(s, i, word):
    """s[i] == '$': consume one expansion into `word`; return the new index."""
    if s.startswith('$(', i):
        j = _sh_balanced(s, i + 2, '(', ')')
        word['expands'] = True
        if not s.startswith('$((', i):          # $((...)) is arithmetic, not a command
            word['subs'].append(s[i + 2:j - 1])
        word['buf'].append(s[i:j])
        return j
    if s.startswith('${', i):
        j = _sh_balanced(s, i + 2, '{', '}')
        word['expands'] = True
        word['buf'].append(s[i:j])
        return j
    j = i + 1
    if j < len(s) and (s[j].isalnum() or s[j] == '_'):
        while j < len(s) and (s[j].isalnum() or s[j] == '_'):
            j += 1
    elif j < len(s) and s[j] in '*@#?$!-':
        j += 1
    else:
        word['buf'].append('$')                  # a lone `$` is literal
        return j
    word['expands'] = True
    word['buf'].append(s[i:j])
    return j


def _sh_backtick(s, i, word):
    j = i + 1
    while j < len(s) and s[j] != '`':
        j += 2 if s[j] == '\\' else 1
    word['expands'] = True
    word['subs'].append(s[i + 1:j])
    word['buf'].append(s[i:j + 1])
    return j + 1


def sh_tokens(cmd):
    """Tokenize like bash. Returns a list of
        {'w': value, 'raw': source, 'expands': bool, 'subs': [...], 'quoted': bool}   a word
        {'op': '&&' | '||' | ';' | ';;' | '|' | '|&' | '&' | '\\n' | '(' | ')'}          an operator
        {'redir': op, 'fd': '0'|'1'|'2'|'both'|..., 'target': word, 'heredoc': body}   a redirection
    `w` has quotes removed and escapes applied. `expands` marks an expansion the shell performs
    at run time ($x, ${x}, $(...), backticks) at an unquoted or double-quoted position: the value
    is then NOT the literal. `subs` are command-substitution sources, which bash executes.
    Heredoc bodies are consumed (they are data to their command); comments are dropped; a
    backslash-newline is a line continuation."""
    s, i, out = cmd or '', 0, []
    pending = []                                 # (redirection token, delimiter, strip tabs)
    word = None

    def fresh(pos):
        return {'buf': [], 'expands': False, 'subs': [], 'start': pos, 'quoted': False}

    def emit(w, end):
        return {'w': ''.join(w['buf']), 'raw': s[w['start']:end], 'expands': w['expands'],
                'subs': w['subs'], 'quoted': w['quoted']}

    def scan(k, w):
        while k < len(s):
            ch = s[k]
            if ch in ' \t\n<>' or s.startswith(SH_OPS, k):
                return k
            if ch == "'":
                w['quoted'] = True
                e = s.find("'", k + 1)
                e = len(s) if e < 0 else e
                w['buf'].append(s[k + 1:e])
                k = e + 1
            elif ch == '"':
                w['quoted'] = True
                k += 1
                while k < len(s) and s[k] != '"':
                    if s[k] == '\\' and k + 1 < len(s) and s[k + 1] in '\\$`"\n':
                        if s[k + 1] != '\n':
                            w['buf'].append(s[k + 1])
                        k += 2
                    elif s[k] == '$':
                        k = _sh_dollar(s, k, w)
                    elif s[k] == '`':
                        k = _sh_backtick(s, k, w)
                    else:
                        w['buf'].append(s[k])
                        k += 1
                k += 1
            elif s.startswith("$'", k):
                w['quoted'] = True
                e = k + 2
                while e < len(s) and s[e] != "'":
                    e += 2 if s[e] == '\\' else 1
                w['buf'].append(s[k + 2:e])
                k = e + 1
            elif ch == '$':
                k = _sh_dollar(s, k, w)
            elif ch == '`':
                k = _sh_backtick(s, k, w)
            elif ch == '\\':
                if s.startswith('\\\n', k):
                    k += 2
                else:
                    w['buf'].append(s[k + 1:k + 2])
                    k += 2
            else:
                w['buf'].append(ch)
                k += 1
        return k

    def flush(end):
        nonlocal word
        if word is not None:
            out.append(emit(word, end))
            word = None

    while i < len(s):
        ch = s[i]
        if ch in ' \t':
            flush(i)
            i += 1
        elif s.startswith('\\\n', i):
            i += 2
        elif ch == '\n':
            flush(i)
            out.append({'op': '\n'})
            i += 1
            for tok, delim, strip in pending:     # bodies follow the line that opened them
                body = []
                while i < len(s):
                    e = s.find('\n', i)
                    e = len(s) if e < 0 else e
                    line, i = s[i:e], e + 1
                    if (line.lstrip('\t') if strip else line) == delim:
                        break
                    body.append(line)
                tok['heredoc'] = '\n'.join(body)
            pending = []
        elif ch == '#' and word is None:
            e = s.find('\n', i)
            i = len(s) if e < 0 else e
        elif ch in '<>' or s.startswith('&>', i):
            fd = None
            if word is not None and not word['quoted'] and not word['expands']:
                b = ''.join(word['buf'])
                if b.isdigit() and word['start'] + len(b) == i:
                    fd, word = b, None
            flush(i)
            op = next(o for o in SH_REDIRS if s.startswith(o, i))
            i += len(op)
            tok = {'redir': op, 'fd': fd or ('0' if op[0] == '<' else ('both' if op[0] == '&' else '1')),
                   'target': None, 'heredoc': None}
            if op in ('>&', '<&') and i < len(s) and (s[i].isdigit() or s[i] == '-'):
                tok['target'] = {'w': s[i], 'raw': s[i], 'expands': False, 'subs': [], 'quoted': False}
                i += 1
            else:
                while i < len(s) and s[i] in ' \t':
                    i += 1
                w = fresh(i)
                j = scan(i, w)
                tok['target'] = emit(w, j)
                i = j
                if op in ('<<', '<<-'):
                    pending.append((tok, tok['target']['w'], op == '<<-'))
            out.append(tok)
        elif s.startswith(SH_OPS, i):
            flush(i)
            op = next(o for o in SH_OPS if s.startswith(o, i))
            out.append({'op': op})
            i += len(op)
        else:
            if word is None:
                word = fresh(i)
            i = scan(i, word)
    flush(i)
    return out


def sh_commands(cmd):
    """Group sh_tokens into simple commands, in order:
        {'words': [...], 'redirs': [...], 'keywords': [...], 'op_before': op, 'op_after': op,
         'loop': None | 'for' | 'while'}
    Leading keywords (do, then, if, ...) are peeled into `keywords`. A `for VAR in ITEMS` loop is
    unrolled: its body is emitted once per literal item with $VAR replaced; an item list carrying
    an expansion (`$(cat ids)`) is NOT unrolled -- word-splitting the substitution text invented
    `ids.txt` as an id -- so its body is emitted once with $VAR unresolved. `while`/`until` bodies
    are emitted once, marked; how many times they ran is not in the command."""
    cmds, cur, before = [], None, None
    for t in sh_tokens(cmd):
        if 'op' in t:
            if cur is not None:
                cur['op_after'] = t['op']
                cmds.append(cur)
                cur = None
            before = t['op']
            continue
        if cur is None:
            cur = {'words': [], 'redirs': [], 'keywords': [], 'op_before': before, 'op_after': None,
                   'loop': None}
        if 'redir' in t:
            cur['redirs'].append(t)
        elif not cur['words'] and not t['quoted'] and not t['expands'] and t['w'] in SH_KEYWORDS:
            cur['keywords'].append(t['w'])
        else:
            cur['words'].append(t)
    if cur is not None:
        cmds.append(cur)
    return _sh_unroll(cmds)


def _sh_unroll(cmds):
    out, i = [], 0
    while i < len(cmds):
        c = cmds[i]
        w = [x['w'] for x in c['words']]
        is_for = w[:1] == ['for'] and len(w) >= 3 and w[2] == 'in' and not c['words'][0]['quoted']
        is_while = c['keywords'][-1:] in (['while'], ['until'])
        if is_for or is_while:
            body, j = _sh_body(cmds, i + 1)
            if is_for:
                var, items = w[1], c['words'][3:]
                literal = bool(items) and all(not it['expands'] for it in items)
                for it in (items if literal else [None]):
                    for bc in _sh_unroll([dict(x) for x in body]):
                        bc = dict(bc, loop=bc.get('loop') or 'for')
                        if it is not None:
                            bc['words'] = [_sh_subst(x, var, it['w']) for x in bc['words']]
                        out.append(bc)
            else:
                out.append(dict(c, loop='while'))          # the condition runs too
                for bc in _sh_unroll([dict(x) for x in body]):
                    out.append(dict(bc, loop='while'))
            i = j + 1
            continue
        out.append(c)
        i += 1
    return [c for c in out if c['words'] or c['redirs']]


def _sh_body(cmds, j):
    """The commands from j to the `done` closing the loop opened just before j, honouring
    nesting. Returns (body, index of that done)."""
    body, depth = [], 1
    while j < len(cmds):
        c = cmds[j]
        ww, kw = [x['w'] for x in c['words']], c['keywords']
        if (ww[:1] == ['for'] and 'in' in ww[2:3]) or kw[-1:] in (['while'], ['until']):
            depth += 1
        if 'done' in kw:
            depth -= kw.count('done')
            if depth <= 0:
                return body, j
        body.append(c)
        j += 1
    return body, j


def _sh_subst(word, var, value):
    raw = word['raw']
    if '$' + var not in raw and '${%s}' % var not in raw:
        return word
    w = word['w'].replace('${%s}' % var, value).replace('$' + var, value)
    still = bool(re.search(r'\$[{(A-Za-z_*@#?!-]|`', w)) and word['expands']
    return dict(word, w=w, expands=still)


WD_SCRIPTS = {'wd_wake.py', 'wd_check.py', 'wd_reconcile.py'}
WD_WAKE_FLAGS = {'--owe-add': 'owe add', '--owe-clear': 'owe done', '--owe-ungate': 'owe ungate',
                 '--queue-add': 'queue add', '--queue-clear': 'queue clear',
                 '--queue-hold': 'queue hold', '--sent': 'sent', '--veto': 'veto',
                 '--outcome': 'outcome', '--owe-list': 'owe list', '--queue-list': 'queue list'}


def _path_join(cwd, p):
    if p.startswith('/'):
        return os.path.normpath(p)
    if p.startswith('~'):
        return os.path.normpath(os.path.expanduser(p))
    return os.path.normpath(os.path.join(cwd, p)) if cwd else None


def invocations(cmd, live_state, cwd=None, _depth=0):
    """Every watchdog invocation the shell would EXECUTE in `cmd`, from its own structure:
        {'verb', 'args': [values], 'arg_expands': [bools], 'text', 'urgent', 'gated_on',
         'state': 'live' | 'scratch', 'state_dir', 'diverted', 'op_before', 'op_after',
         'loop', 'in_substitution', 'argv'}
    `live_state` is the state directory under reconciliation; an invocation that touched any other
    (WD_STATE exported or given as a prefix, --state-dir, another watchdog instance) is scratch.
    `diverted` is True when the invocation's stdout never reached the result (redirected, piped,
    or captured by a substitution): its silence then proves nothing."""
    live = os.path.normpath(live_state) if live_state else None
    out, env, shvars = [], {}, {}
    cmds_ = sh_commands(cmd)
    for ci, c in enumerate(cmds_):
        words = c['words']
        k, prefix = 0, {}
        while k < len(words) and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', words[k]['w']) and not words[k]['quoted']:
            name, _, val = words[k]['w'].partition('=')
            prefix[name] = (val, words[k]['expands'])
            k += 1
        argv = words[k:]
        av = [x['w'] for x in argv]
        # the shell's own bookkeeping, before looking for invocations
        if not argv:
            shvars.update(prefix)                  # plain assignment: NOT exported
        elif av[0] == 'export':
            for x in argv[1:]:
                name, eq, val = x['w'].partition('=')
                env[name] = (val, x['expands']) if eq else shvars.get(name, ('', True))
        elif av[0] == 'cd' and len(av) > 1 and not argv[1]['expands']:
            cwd = _path_join(cwd, av[1])
        # substitutions execute wherever they sit
        for x in words + [r['target'] for r in c['redirs'] if r.get('target')]:
            for sub in x.get('subs') or []:
                if _depth < 4:
                    for inv in invocations(sub, live_state, cwd, _depth + 1):
                        out.append(dict(inv, in_substitution=True, diverted=True, to_file=True,
                                        piped=False, cmd_index=None))
        if not argv:
            continue
        # a script given to a shell as a string, or on stdin, runs too
        if av[0] in ('bash', 'sh', 'zsh', 'eval') or av[0].endswith(('/bash', '/sh', '/zsh')):
            src = None
            if av[0] == 'eval':
                src = ' '.join(av[1:])
            elif '-c' in av[1:]:
                j = av.index('-c', 1)
                src = av[j + 1] if j + 1 < len(av) else None
            else:
                src = next((r['heredoc'] for r in c['redirs'] if r.get('heredoc') is not None), None)
            if src and _depth < 4:
                out.extend(dict(x, cmd_index=None) for x in invocations(src, live_state, cwd, _depth + 1))
            continue
        env_here = dict(env)
        env_here.update(prefix)
        inv = _wd_invocation(av, argv, env_here, cwd, live)
        if inv is None:
            continue
        to_file = any(r['redir'] in ('>', '>>', '>|', '&>', '&>>') and r['fd'] in ('1', 'both')
                      or (r['redir'] == '>&' and r['fd'] == '1') for r in c['redirs'])
        piped = c['op_after'] in ('|', '|&')
        inv.update(diverted=to_file or piped, to_file=to_file, piped=piped, cmd_index=ci,
                   is_last=(ci == len(cmds_) - 1),
                   op_before=c['op_before'], op_after=c['op_after'], loop=c['loop'],
                   in_substitution=False)
        out.append(inv)
    return out


def _wd_invocation(av, argv, env, cwd, live):
    """argv -> one watchdog invocation, read by wd.sh's own dispatch, or None."""
    base = os.path.basename(av[0])
    exp = [x['expands'] for x in argv]
    if base == 'wd.sh' and '/' in av[0] and not argv[0]['expands']:
        wd_dir = _path_join(cwd, os.path.dirname(av[0]))
        st = env.get('WD_STATE')
        if st is not None:
            state_dir = None if st[1] else _path_join(cwd, st[0])
            state_literal = st[0]
        else:
            state_dir = os.path.join(wd_dir, 'state') if wd_dir else None
            state_literal = state_dir
        rest, rexp, via = av[1:], exp[1:], 'wd.sh'
        verb, args, aexp, urgent, gated = (rest[0] if rest else ''), rest[1:], rexp[1:], False, None
        if verb in ('queue', 'owe') and rest[1:]:
            verb = '%s %s' % (rest[0], rest[1])
            args, aexp = rest[2:], rexp[2:]
            if verb == 'queue add' and args[:1] == ['--urgent']:
                urgent, args, aexp = True, args[1:], aexp[1:]
            if verb == 'owe add' and args[:1] == ['--gated-on']:
                gated, args, aexp = (args[1] if len(args) > 1 else ''), args[2:], aexp[2:]
    elif base.startswith('python') and not argv[0]['expands']:
        script = next((j for j in range(1, len(av)) if not av[j].startswith('-')), None)
        if script is None or os.path.basename(av[script]) not in WD_SCRIPTS or argv[script]['expands']:
            return None
        flags = av[script + 1:]
        fexp = exp[script + 1:]
        st = None
        if '--state-dir' in flags:
            j = flags.index('--state-dir')
            st = (flags[j + 1], fexp[j + 1]) if j + 1 < len(flags) else ('', True)
        script_path = _path_join(cwd, av[script])
        if st:
            state_dir, state_literal = (None if st[1] else _path_join(cwd, st[0])), st[0]
        else:                                    # the scripts default to their own ./state
            state_dir = os.path.join(os.path.dirname(script_path), 'state') if script_path else None
            state_literal = state_dir or '(script path unresolved)'
        via = os.path.basename(av[script])
        verb, args, aexp, urgent, gated = None, [], [], False, None
        if os.path.basename(av[script]) == 'wd_wake.py':
            for j, f in enumerate(flags):
                if f in WD_WAKE_FLAGS:
                    verb = WD_WAKE_FLAGS[f]
                    args, aexp = flags[j + 1:j + 2], fexp[j + 1:j + 2]
                    if f == '--outcome':
                        args, aexp = flags[j + 1:j + 3], fexp[j + 1:j + 3]
                    break
            urgent = '--queue-urgent' in flags
            if '--gated-on' in flags:
                j = flags.index('--gated-on')
                gated = flags[j + 1] if j + 1 < len(flags) else ''
        else:
            pos, j = [], 0
            while j < len(flags):
                if flags[j].startswith('--'):
                    j += 2 if flags[j] in ('--target', '--state-dir', '--self', '--repo', '--ledger',
                                           '--quiet-min', '--row-pattern') else 1
                    continue
                pos.append(j)
                j += 1
            if not pos:
                return None
            verb = flags[pos[0]]
            args, aexp = flags[pos[0] + 1:], fexp[pos[0] + 1:]
        if verb is None:
            return None
    else:
        return None
    state = 'live' if (live and state_dir == live) else 'scratch'
    text = ' '.join(args) if verb in ('queue add', 'owe add') else None
    return {'verb': verb, 'args': args, 'arg_expands': aexp, 'text': text,
            'text_resolved': (not any(aexp)) if text is not None else None,
            'urgent': urgent, 'gated_on': gated, 'state': state,
            'state_dir': state_dir or state_literal, 'argv': av, 'via': via}


# At the END of the module: selftest() uses the shell reader, defined above. Mid-module it ran
# before its dependencies existed and the direct entry point died with NameError.
if __name__ == '__main__':
    import sys as _s
    _s.exit(selftest())
