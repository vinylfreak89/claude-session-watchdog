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
NEEDS_SEND = {'sent1', 'nudged', 'answered', 'queue clear', 'queue-clear'}
NEEDS_RELAY_TEXT = {'relayed'}
NEEDS_TARGET_REPLY = {'resolved', 'closed'}


def _texts(c):
    if isinstance(c, str):
        return [c]
    if isinstance(c, list):
        return [b['text'] for b in c
                if isinstance(b, dict) and b.get('type') == 'text' and b.get('text')]
    return []


def load(self_prefix='80f99b89'):
    """Every record of every transcript, once, in time order. No sampling: 'Everything.'"""
    mine, target, = [], []
    for f in sorted(os.listdir(PROJ)):
        if not f.endswith('.jsonl'):
            continue
        is_mine = f.startswith(self_prefix)
        for line in open(os.path.join(PROJ, f), 'rb'):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if not r.get('timestamp'):
                continue
            (mine if is_mine else target).append(r)
    mine.sort(key=lambda r: r['timestamp'])
    target.sort(key=lambda r: r['timestamp'])
    return mine, target


def timeline(mine, target):
    """MY actions in chronological order, each with the artifacts available to corroborate it."""
    actions, sends, my_text, owner_msgs = [], [], [], []
    for r in mine:
        ts, t = r['timestamp'], r.get('type')
        m = r.get('message') or {}
        if t in ('user', 'attachment', 'queue-operation'):
            for x in _texts(m.get('content') if isinstance(m, dict) else None):
                if x.startswith('<') or 'system-reminder' in x[:200] or 'tool_result' in x[:40]:
                    continue
                owner_msgs.append({'ts': ts, 'kind': t, 'text': x})
        if t != 'assistant':
            continue
        for b in (m.get('content') or []):
            if not isinstance(b, dict):
                continue
            if b.get('type') == 'text' and b.get('text'):
                my_text.append({'ts': ts, 'text': b['text']})
            if b.get('type') != 'tool_use':
                continue
            nm, inp = b.get('name'), (b.get('input') or {})
            if nm and 'send_message' in nm:
                sends.append({'ts': ts, 'msg': inp.get('message') or ''})
            if nm == 'Bash':
                # normalize FIRST. Without it a test fixture's example text, a heredoc body or
                # a commented example is replayed as a real action -- measured 2026-09-11 on
                # the live record, it produced four false MISSTEERs dated to the hour the
                # fixtures were written. Same bypass restore_payload had.
                c = normalize(inp.get('command', ''))
                for mm in re.finditer(MUT, c):
                    verb = mm.group(1) or mm.group(2)
                    actions.append({'ts': ts, 'verb': verb, 'hour': ts[:13],
                                    'arg': _arg(c, verb), 'cmd': c[:500]})
    tgt_text = [{'ts': r['timestamp'], 'text': x}
                for r in target if r.get('type') == 'assistant'
                for x in _texts((r.get('message') or {}).get('content'))]
    return actions, {'sends': sends, 'my_text': my_text, 'target': tgt_text,
                     'owner': owner_msgs}


def _arg(cmd, verb):
    m = re.search(re.escape(verb) + r'\s+(?:--urgent\s+)?["\']?([^"\'\s]{1,80})', cmd)
    return m.group(1) if m else None


def _near(items, ts, before_s=900, after_s=900):
    """Artifacts within a window of the action. A relay or a send belongs to the same turn."""
    import datetime
    def p(s):
        return datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
    t0 = p(ts)
    return [x for x in items
            if -before_s <= (p(x['ts']) - t0).total_seconds() <= after_s]


# ---------------------------------------------------------------- citation

import hashlib


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


# The argument forms that carry the owner's words into a store. A restore extracts the text
# with these and NEVER by hand; if none matches, it refuses rather than storing a guess.
ARGFORMS = [
    re.compile(r"--queue-add\s+(?P<q>['\"])(?P<t>.*?)(?P=q)", re.S),
    re.compile(r"--owe-add\s+(?P<q>['\"])(?P<t>.*?)(?P=q)", re.S),
    re.compile(r"\./wd\.sh\s+queue\s+add\s+(?:--urgent\s+)?(?P<q>['\"])(?P<t>.*?)(?P=q)", re.S),
    re.compile(r"\./wd\.sh\s+owe\s+add\s+(?:--gated-on\s+['\"].*?['\"]\s+)?(?P<q>['\"])(?P<t>.*?)(?P=q)", re.S),
]


def extract_item(cmd):
    """The verbatim item text a command carried, or None. None means REFUSE, never improvise."""
    for rx in ARGFORMS:
        m = rx.search(cmd)
        if m and m.group('t').strip():
            return m.group('t')
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
    # normalize() FIRST. Without it this path restored a heredoc's example text as the
    # owner's words -- the same defect that once produced the literal `$*` as an item, still
    # live here after every other caller was fixed. Found by the citation fixture, 2026-09-11.
    cmd = normalize(cmds[which])
    text = extract_item(cmd)
    if text is None or not is_real_item(text):
        raise ValueError('%s:%d command #%d carries no recognisable item argument -- REFUSED '
                         '(no guessing: fix the citation or widen ARGFORMS with a control)'
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


HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1(.*?)^\2\s*$",
                     re.S | re.M)


def strip_heredocs(cmd):
    """Remove every heredoc BODY before matching. A command that WRITES a file containing
    `--owe-add "$*"` is not an invocation of it, and three separate false results came from
    reading wd.sh's own source as state changes (2026-09-11: 66 matches vs 63 real; four
    bogus MISSTEERs; a citation that resolved to the literal shell variable `$*`)."""
    return HEREDOC.sub(lambda m: '<<' + m.group(2) + '\n', cmd)


SHELLISH = re.compile(r'^\s*(\$[\*@0-9{]|["\']?\$)')


def is_real_item(text):
    """An item is the owner's words. A shell variable, an empty string, or a fragment shorter
    than a sentence is a parse artifact -- REFUSE rather than reinstate it."""
    if not text or SHELLISH.match(text):
        return False
    # Control characters are never the owner's words; a NUL in particular sailed through the
    # length test and would have been restored as an item.
    if any(ord(c) < 32 and c not in '\t\n\r' for c in text):
        return False
    return len(text.strip()) >= 25


def selftest():
    """Controls for the contamination that has produced a wrong answer three times.
    Each must FAIL if the guard is removed."""
    ok = True
    writes = ('cat > wd.sh <<\'EOF\'\n'
              'owe)    add) exec $PY --owe-add "$*" ;;\n'
              'EOF\n')
    if extract_item(strip_heredocs(writes)) is not None:
        print('FAIL: a file-writing heredoc still reads as an invocation'); ok = False
    else:
        print('pass: heredoc body ignored')
    real = './wd.sh owe add "DOES POSITION ALONE ESTABLISH IDENTITY? Flagged in the contract itself."'
    got = extract_item(strip_heredocs(real))
    if got and is_real_item(got):
        print('pass: a real invocation still extracts (%r...)' % got[:34])
    else:
        print('FAIL: a real invocation no longer extracts: %r' % got); ok = False
    if is_real_item('$*') or is_real_item('') or is_real_item('short'):
        print('FAIL: a shell variable or fragment passes is_real_item'); ok = False
    else:
        print('pass: shell variables and fragments refused')
    print('SELFTEST %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    import sys as _s
    _s.exit(selftest())


FORLOOP = re.compile(r'for\s+(\w+)\s+in\s+([^;\n]+?)\s*;\s*do\b(.*?)(?:^|[;&\n])\s*done\b',
                     re.S | re.M)


def expand_loops(cmd):
    """Unroll `for d in D1 D3; do ... $d ...; done` so ids bound to a loop variable are visible.

    Found 2026-09-11 by hunting an id rather than trusting the join: D3 read as
    'issued but never closed' -- a DROP that would have been reported to the owner -- because
    its close ran inside a loop and the extractor captured the literal `$d`. An identifier that
    never appears as a literal is invisible to any pattern over the command text."""
    def sub(m):
        var, items, body = m.group(1), m.group(2).split(), m.group(3)
        out = []
        for it in items:
            it = it.strip('"\'')
            b = body.replace('${%s}' % var, it).replace('$%s' % var, it)
            out.append(b)
        return ';'.join(out)
    prev = None
    while prev != cmd:
        prev, cmd = cmd, FORLOOP.sub(sub, cmd)
    return cmd


COMMENT = re.compile(r'^\s*#.*$', re.M)
ECHOED = re.compile(r"""\b(?:echo|printf)\s+(?:-\w+\s+)*(['"])(?:\\.|(?!\1).)*\1""", re.S)


def strip_comments(cmd):
    """A commented-out example is not an invocation. Found by fixture 2026-09-11: a line
    beginning `# ./wd.sh queue add "..."` registered as a genuine owner item."""
    return COMMENT.sub('', cmd)


def strip_echoes(cmd):
    """Text that only ever reached a terminal is not an invocation. Same fixture: an
    `echo './wd.sh owe add "..."'` registered as a genuine decision."""
    return ECHOED.sub('echo', cmd)


WHILELOOP = re.compile(r'while\s+.*?;\s*do\b(.*?)(?:^|[;&\n])\s*done\b', re.S | re.M)
UNRESOLVED = re.compile(r'\$\(|\$\{?\w+\}?')


def unresolved_ids(cmd):
    """Ids this pass could not resolve to a literal -- a `while read` variable, a `$(...)`
    substitution, a leftover `$var`. These must be REPORTED, never silently recorded as an id
    whose name is the substitution text. A `for` loop is unrolled; these cannot be."""
    out = []
    for m in CLOSE_RX.finditer(cmd):
        arg = m.group(3)
        if arg and UNRESOLVED.search(arg):
            out.append({'verb': m.group(1) or m.group(2), 'literal': arg})
    for m in WHILELOOP.finditer(cmd):
        body = m.group(1)
        for mm in CLOSE_RX.finditer(body):
            if mm.group(3) and UNRESOLVED.search(mm.group(3)):
                out.append({'verb': mm.group(1) or mm.group(2), 'literal': mm.group(3),
                            'form': 'while-loop'})
    return out


def normalize(cmd):
    """The ONE preprocessing path every matcher must use. Each step exists because its absence
    produced a false item against a known-answer corpus: heredoc bodies (a document being
    written), comments (an example), echoes (terminal output), and loops (an id that never
    appears as a literal)."""
    return expand_loops(strip_echoes(strip_comments(strip_heredocs(cmd))))


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


# ---------------------------------------------------------------- stage 1

OPEN_RX = [
    (re.compile(r"--queue-add\s+(?P<q>['\"])(?P<t>.*?)(?P=q)", re.S), 'owner_queue'),
    (re.compile(r"--owe-add\s+(?:--gated-on\s+['\"].*?['\"]\s+)?(?P<q>['\"])(?P<t>.*?)(?P=q)", re.S), 'owner_decisions'),
    (re.compile(r"\./wd\.sh\s+queue\s+add\s+(?:--urgent\s+)?(?P<q>['\"])(?P<t>.*?)(?P=q)", re.S), 'owner_queue'),
    (re.compile(r"\./wd\.sh\s+owe\s+add\s+(?:--gated-on\s+['\"].*?['\"]\s+)?(?P<q>['\"])(?P<t>.*?)(?P=q)", re.S), 'owner_decisions'),
    (re.compile(r"\./wd\.sh\s+ask\s+(?P<q>['\"])(?P<t>.*?)(?P=q)", re.S), 'open_questions'),
]
CLOSE_RX = re.compile(
    r'(?:^|[;&|]\s*|\s)(?:\./wd\.sh\s+(queue clear|sent1|owe done|owe ungate|resolved|closed|nudged)'
    r'|--(queue-clear|owe-clear|owe-ungate))\b(?:\s+([^\s;&|]+))?')


def stage1(self_prefix='80f99b89', proj=None):
    """CHRONOLOGICAL REPLAY OF MY OWN ACTIONS.

    Owner: "a replay of your entire set of actions to catch things that you steered
    incorrectly, be they owed info, nudges, queues, owner requests, anything you own."

    Every command passes through normalize() -- heredoc bodies removed, shell loops unrolled --
    because each of those produced a WRONG ANSWER when it was missing: heredocs read a file
    being written as state changes (three times), and a loop hid D3's close so it read as a
    dropped decision. Returns opens, closes and the per-id join. Decides nothing."""
    root = proj or PROJ
    p = None
    for f in sorted(os.listdir(root)):
        if f.startswith(self_prefix) and f.endswith('.jsonl'):
            p = os.path.join(root, f)
    if not p:
        raise ValueError('no transcript for %s' % self_prefix)
    opens, closes, unresolved = [], [], []
    for i, line in enumerate(open(p, 'rb'), 1):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        ts = rec.get('timestamp') or ''
        for j, raw in enumerate(commands_in(rec)):
            c = normalize(raw)
            for rx, store in OPEN_RX:
                for m in rx.finditer(c):
                    t = m.group('t')
                    if is_real_item(t):
                        opens.append({'ts': ts, 'cite': '%s:%d#%d' % (os.path.basename(p), i, j),
                                      'store': store, 'text': t,
                                      'sha256': hashlib.sha256(t.encode()).hexdigest()})
            for u in unresolved_ids(c):
                unresolved.append(dict(u, ts=ts,
                                       cite='%s:%d#%d' % (os.path.basename(p), i, j)))
            for m in CLOSE_RX.finditer(c):
                verb, arg = (m.group(1) or m.group(2)), m.group(3)
                aid = (arg or '').strip('"\'') or None
                if aid and UNRESOLVED.search(aid):
                    aid = None        # never record a substitution as if it were an id
                closes.append({'ts': ts, 'cite': '%s:%d#%d' % (os.path.basename(p), i, j),
                               'verb': verb, 'id': aid})
    return opens, closes, unresolved


def stage1_join(opens, closes, state_dir, unresolved=()):
    """Which created items are accounted for, and which are candidates for restore."""
    live = live_stores(state_dir)
    closed_ids = collections.Counter(c['id'] for c in closes if c['id'])
    seq = live.get('owner_decision_seq') or 0
    ids = ['D%d' % n for n in range(1, seq + 1)]
    never = [i for i in ids if i not in closed_ids]
    dupes = {i: n for i, n in closed_ids.items() if n > 1 and re.fullmatch(r'D\d+|Q\d+', i or '')}
    return {'opens': len(opens), 'closes': len(closes),
            'ids_unresolvable': len(unresolved),
            'unresolvable_detail': [dict(u) for u in unresolved][:20],
            'decision_ids_issued': seq, 'decision_ids_closed': len([i for i in ids if i in closed_ids]),
            'decision_ids_never_closed': never,
            'closed_more_than_once': dupes,
            'live_owner_queue': len(live['owner_queue']),
            'live_owner_decisions': len(live['owner_decisions'])}


# ---------------------------------------------------------------- stage 3

def stage3(actions, art, git_probe=None):
    """LANDED-REPLAY: did each action I claimed actually happen?

    Owner: "Then go back in the transcript and confirm every action landed. Do it as a replay."
    Distinct from stage 1, which finds the ITEMS I lost; this finds the ACTIONS I claimed.

    A verdict is OK / MISSTEER / UNDECIDABLE. Undecidable is a real outcome and is never
    rounded to OK -- silence is not success. `git_probe` is injected so the corpus can be
    synthetic; it answers "does this sha exist and on which refs"."""
    out = []
    for a in actions:
        v, ident = a['verb'], a.get('arg')
        verdict, why = 'undecidable', 'no artifact rule for this verb'
        if v in ('sent1', 'nudged'):
            near = _near(art['sends'], a['ts'])
            if not near:
                verdict, why = 'MISSTEER', 'no message to the target within 15 min'
            elif ident and any(ident in s['msg'][:6000] for s in near):
                verdict, why = 'ok', 'a send names %s' % ident
            else:
                verdict, why = 'undecidable', '%d send(s) near, none names %s' % (len(near), ident)
        elif v == 'answered':
            near = _near(art['sends'], a['ts'])
            verdict, why = ('ok', '%d send(s) near' % len(near)) if near else \
                           ('MISSTEER', '`answered` recorded with no send behind it')
        elif v == 'relayed':
            near = [x for x in _near(art['my_text'], a['ts'], 1800, 300) if len(x['text']) > 400]
            verdict, why = ('ok', '%d substantial reply/ies to the owner' % len(near)) if near else \
                           ('MISSTEER', '`relayed` with nothing said to the owner around it')
        elif v in ('resolved', 'closed'):
            near = _near(art['target'], a['ts'], 3600, 0)
            verdict, why = ('ok', '%d target turn(s) precede it' % len(near)) if near else \
                           ('MISSTEER', 'resolved with no target output before it')
        out.append(dict(a, verdict=verdict, why=why))
    return out


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
                        verdict='ok' if exists and refs else
                                'MISSTEER' if not exists else 'undecidable'))
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
