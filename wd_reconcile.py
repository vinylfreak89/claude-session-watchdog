#!/usr/bin/python3
"""wd_reconcile.py -- `wd.sh reconcile <start> <end>`: rebuild the watchdog's state from the
record, hour by hour, and restore what was dropped.

The owner, 2026-09-11: "a detailed accounting and replay from every hour by reading that state
directory since it has existed. Do not binary search it. Do not sample it. Everything."

Three properties the code ENFORCES rather than describes, because a reconciliation the model
merely intends to do completely is the failure this exists to repair:

  1. MUTUAL EXCLUSION. If any other watchdog hook is alive, this ends IMMEDIATELY with no
     action and no state change. Two hooks writing one state dir is how the dropping started.
  2. EVERY HOUR. The ledger enumerates every hour of the window at --init. An hour leaves
     `pending` only by being marked with evidence for all four of its parts. There is no verb
     that skips one, no "representative sample", and no way to mark a range at once.
  3. 100% IS THE ONLY EXIT. `--complete` REFUSES unless every hour is done, every snapshot is
     visited, no action is left unverified, and confidence is exactly 100. The minute hook
     keeps firing until then, which is the point: it cannot be ended by deciding it is done.

The Time Machine procedure lives in local/reconcile.md, an overlay that is NOT committed.
This file carries no TM knowledge; it reads the overlay and prints it.

  reconcile <start> <end> --init         build the ledger (refuses to clobber a live one)
  reconcile                              STATUS: one line for the hook, then what is owed next
  reconcile --next                       the next hour to work, with the overlay's instructions
  reconcile --hour <ISO-hour> --part tm|diff|replay|verdict --evidence "<what was measured>"
  reconcile --snapshot <name> --evidence "..."      | --thinned <name> --evidence "..."
  reconcile --action <id> --landed yes|no --evidence "..."
  reconcile --restore "<what>" --evidence "..."     an additive restoration, never a removal
  (confidence is COMPUTED, never typed -- see below)
  reconcile --complete                   refuses unless everything above is satisfied
"""
import os, sys, json, argparse, subprocess, datetime, re

PARTS = ('diff', 'replay', 'verdict')          # every hour owes these, from the record alone
OPTIONAL_PARTS = ('tm',)                       # included ONLY where a snapshot for that hour exists
# Owner, 2026-09-12: "it just may be an optional part. do not mark it as complete if it didn't
# exist. just don't include it." An hour with no surviving backup is not an hour with a passed tm
# part and not an hour held open by a missing one: the part is simply not among its parts. Time
# Machine is an ADDITIONAL check (owner, same day: "time machine is an additional check in case
# your scan happens to miss something but it is not necessary").
HERE = os.path.dirname(os.path.abspath(__file__))
OVERLAY = os.path.join(HERE, 'local', 'reconcile.md')


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(s):
    s = s.strip().replace('Z', '+00:00')
    try:
        d = datetime.datetime.fromisoformat(s)
    except ValueError:
        raise SystemExit('not an ISO date/time: %r (want 2026-09-09T10:00:00Z)' % s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.astimezone(datetime.timezone.utc)


def hour_key(d):
    return d.strftime('%Y-%m-%dT%H')


# ---------------------------------------------------------------- exclusivity

# The other hooks, by the command line they run under. `wd.sh reconcile` itself and this
# process are excluded by pid, not by name, so a reconcile never sees itself as a rival.
RIVAL = re.compile(r'wd_wait\.py|wd_wake\.py|wd_check\.py|wd\.sh\s+(wait|wake|owed|due|status)\b')


def rival_hooks(state_dir=None, ps_out=None):
    """Every live watchdog process that is not this one and could touch THIS state. Read-only; no
    signals are sent. A hook whose command line names ANOTHER state directory cannot interleave
    with this ledger or this state, so it is not a rival -- measured: the test suite failed four
    CLI tests whenever the live watchdog's own hooks happened to run, over a different state. A
    hook whose command line names no state directory is still a rival: it cannot be told apart.
    `ps_out` replaces the process table, so the scoping is testable without live processes."""
    me, parent = os.getpid(), os.getppid()
    if ps_out is None:
        try:
            # -ww: never truncate the command line, or its --state-dir is cut off and every hook
            # reads as a rival of every state
            out = subprocess.run(['ps', '-axww', '-o', 'pid=,ppid=,command='],
                                 capture_output=True, text=True, timeout=20).stdout
        except Exception as e:
            # A check that cannot run must NOT read as "no rivals" -- that is the missing-is-not-a-
            # value defect, and here it would licence exactly the concurrency this guard forbids.
            return [('?', 'cannot enumerate processes: %s' % e)]
    else:
        out = ps_out
    mine = os.path.realpath(os.path.abspath(state_dir)) if state_dir else None
    hits = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        bits = line.split(None, 2)
        if len(bits) < 3:
            continue
        pid, ppid, cmd = bits[0], bits[1], bits[2]
        try:
            pid_i, ppid_i = int(pid), int(ppid)
        except ValueError:
            continue
        if pid_i in (me, parent) or ppid_i == me:
            continue
        if 'wd_reconcile.py' in cmd or 'reconcile' in cmd.split('wd.sh ')[-1][:12]:
            continue
        if RIVAL.search(cmd):
            m = re.search(r'--state-dir[= ](\S+)', cmd)
            if (mine and m and os.path.isabs(m.group(1))
                    and os.path.realpath(m.group(1)) != mine):
                continue
            hits.append((pid, cmd[:110]))
    return hits


# ---------------------------------------------------------------- ledger

def path_of(state_dir):
    return os.path.join(state_dir, 'reconcile.json')


TM_WRAPPER = '~/Documents/time-machine-explorer/scripts/tm'
TM_UUID = '290ABBD4-32C6-4EAE-898E-99C6CAAF97E5'
TM_STATE = ('/Volumes/.timemachine/%s/{s}.backup/{s}.backup/Data/Users/vinylfreak89/Documents/'
            'claude-session-watchdog/state' % TM_UUID)


def _local_zone():
    """The zone Time Machine names its backups in -- the machine's own, read not assumed. TM names
    are LOCAL time and the transcript is UTC, and resolve_from_snapshots refuses a row whose time
    was defaulted rather than established."""
    return datetime.datetime.now().astimezone().tzname() or 'local'


def _snap_utc(name):
    """`2026-09-11-052150` (local) -> an aware UTC ISO string. strptime gives a naive local time;
    astimezone() applies this machine's rules FOR THAT DATE, so a zone with DST converts correctly
    rather than by a fixed offset typed in here."""
    naive = datetime.datetime.strptime(name, '%Y-%m-%d-%H%M%S')
    return naive.astimezone(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')


def _shift(iso, hours):
    d = datetime.datetime.fromisoformat(iso.replace('Z', '+00:00')) + datetime.timedelta(hours=hours)
    return d.isoformat().replace('+00:00', 'Z')


def tm_snapshots():
    """Every backup on the destination, by the overlay's GROUND TRUTH: the snapshot list, never the
    .timemachine directory (which keeps an empty stub for every backup that ever existed). The disk
    id is re-derived each time because it changes between attachments."""
    mnt = subprocess.run(['mount'], capture_output=True, text=True).stdout
    disk = None
    for line in mnt.splitlines():
        if '/Volumes/T7' in line and line.startswith('/dev/'):
            disk = line.split()[0].replace('/dev/', '')
    if not disk:
        return None, 'the backup drive is not mounted (no /Volumes/T7 in `mount`)'
    out = subprocess.run(['diskutil', 'apfs', 'listSnapshots', disk],
                         capture_output=True, text=True).stdout
    names = re.findall(r'com\.apple\.TimeMachine\.(\d{4}-\d{2}-\d{2}-\d{6})\.backup', out)
    if not names:
        return None, 'no snapshots listed on %s -- refused rather than read as "none survive"' % disk
    return sorted(set(names)), 'ok'


def tm_state_dir(name):
    """The state DIRECTORY inside one backup -- what `tm ls` takes."""
    return TM_STATE.format(s=name)


def tm_state_path(name):
    """The state FILE inside one backup -- what `tm read` takes. Kept distinct from the directory
    because passing the directory to `read` is exactly the defect this stage shipped with: the
    wrapper refused every backup with `cannot open for reading: .../state`, the reader reported it
    honestly, and the stage read it as "0 of 24 readable"."""
    return tm_state_dir(name) + '/state.json'


def tm_state_size(tm, name):
    """The size the LISTING reports for that backup's state.json -- the independent length a
    trailer-less read is checked against. Returns (None, why) when the backup holds no state dir."""
    out = subprocess.run([tm, 'ls', tm_state_dir(name)], capture_output=True, text=True).stdout
    try:
        d = json.loads(out)
    except Exception:
        return None, 'ls did not return JSON: %s' % out[:120]
    if not d.get('ok'):
        return None, (d.get('error') or 'ls failed')[:160]
    for e in d.get('entries') or []:
        if e.get('name') == 'state.json':
            return e.get('size'), 'ok'
    return None, 'the backup holds the state dir but no state.json'


def ledger_dir(a):
    """Where the ledger and its lock live -- NEVER the state directory by default.

    Owner, 2026-09-12: "you should not be writing to the live state. absolutely not until the
    reconciliation is complete", and "temporary scratch but durable temporary scratch". A
    reconciler that writes into its own subject perturbs what it is measuring: every ledger write
    becomes another state change a later pass has to explain. local/ is gitignored, sits beside
    the overlay, and survives a session -- which /tmp and a session scratchpad do not."""
    d = getattr(a, 'ledger_dir', None) or os.path.join(HERE, 'local', 'reconcile')
    os.makedirs(d, exist_ok=True)
    return d


def load(state_dir):
    p = path_of(state_dir)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def save(state_dir, L):
    os.makedirs(state_dir, exist_ok=True)
    p = path_of(state_dir)
    tmp = p + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(L, f, indent=1, sort_keys=True)
    os.replace(tmp, p)


def init(state_dir, start, end):
    old = load(state_dir)
    if old and not old.get('complete'):
        raise SystemExit('a reconciliation is already open (%s -> %s, %d/%d hours done).\n'
                         'Finish it or move %s aside; --init will not clobber it.'
                         % (old['window']['start'], old['window']['end'],
                            sum(1 for h in old['hours'].values() if h['status'] == 'done'),
                            len(old['hours']), path_of(state_dir)))
    a, b = parse_iso(start), parse_iso(end)
    if b <= a:
        raise SystemExit('end must be after start')
    hours, cur = {}, a.replace(minute=0, second=0, microsecond=0)
    while cur <= b:
        hours[hour_key(cur)] = {'status': 'pending', 'parts': {p: None for p in PARTS}}
        cur += datetime.timedelta(hours=1)
    L = {'window': {'start': a.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'end': b.strftime('%Y-%m-%dT%H:%M:%SZ')},
         'created': now_iso(), 'hours': hours, 'snapshots': {}, 'actions': {},
         'restored': [], 'coverage': {}, 'complete': False,
         'log': [{'ts': now_iso(), 'what': 'init %d hours' % len(hours)}]}
    save(state_dir, L)
    return L


def outstanding(L):
    """Everything still owed, as a list of short strings. Empty == reconciled."""
    o = []
    pend = [k for k, v in sorted(L['hours'].items()) if v['status'] != 'done']
    if pend:
        o.append('%d/%d hours unreconciled (next %s)' % (len(pend), len(L['hours']), pend[0]))
    snap_pend = [k for k, v in sorted(L['snapshots'].items()) if v.get('status') == 'pending']
    if snap_pend:
        o.append('%d snapshots unvisited (next %s)' % (len(snap_pend), snap_pend[0]))
    unver = [k for k, v in sorted(L['actions'].items()) if v.get('landed') not in ('yes', 'no')]
    if unver:
        o.append('%d actions unverified (next %s)' % (len(unver), unver[0]))
    # Time Machine is an ADDITIONAL check and never a requirement (owner, 2026-09-12: "time
    # machine is an additional check in case your scan happens to miss something but it is not
    # necessary"), so a drive that was never consulted cannot hold the reconciliation open -- the
    # confidence already exempts it and this line contradicted that, refusing --complete forever.
    # It is not silently dropped either: `tm_note` says so wherever the result is reported, because
    # "the drive was never read" and "the drive was read and held nothing" are different facts.
    stale = [i for i, r in enumerate(L.get('restored', []))
             if r.get('validated_pass') != L.get('pass', 0)]
    if stale:
        o.append('%d restore(s) unvalidated at pass %d (next #%d) -- supersession check owed'
                 % (len(stale), L.get('pass', 0), stale[0]))
    hs = ((L.get('stage3') or {}).get('hashes') or {}).get('needs_reading') or []
    if hs:
        o.append('%d hex token(s) to read -- meant as a commit or not? (next %s)' % (len(hs), hs[0]['sha']))
    ar = (L.get('stage3') or {}).get('action_readings_owed') or []
    if ar:
        o.append('%d action(s) to read -- carried? answered? (next %s: %s)'
                 % (len(ar), ar[0]['key'], ar[0]['needs'][:140]))
    chs = (L.get('stage3') or {}).get('chains') or {}
    owed = [k for k, v in sorted(chs.items()) if v.get('outstanding')]
    if owed:
        o.append('%d decision chain(s) with work outstanding (next %s: %s)'
                 % (len(owed), owed[0], chs[owed[0]]['outstanding'][0]))
    c, parts = confidence(L)
    if c != 100:
        o.append('confidence %d/100 (%s)' % (c, '; '.join('%s %d%%' % (k, v) for k, v in parts)))
    return o


def tm_note(L):
    """What Time Machine contributed, in one line. Never owed -- it is the additional check --
    but never silent either: a run that reached 100 without the drive says so."""
    s2 = L.get('stage2') or {}
    if s2:
        # stage 2 records some of these as COUNTS and some as collections, and a note that assumes
        # one shape crashes on the other -- which is how this printed a traceback where a summary
        # belonged. `_n` takes either, and the test exercises a ledger shaped like the real one.
        def _n(x):
            return x if isinstance(x, int) else len(x or ())
        pend = [k for k, v in (L.get('snapshots') or {}).items()
                if isinstance(v, dict) and v.get('status') == 'pending']
        return ('Time Machine: %d backup(s), %d readable, %d unreadable, %d unvisited; %d dated '
                'drop(s) (the additional check -- reported, never scored)'
                % (_n(L.get('snapshots')), _n(s2.get('readable')), _n(s2.get('unreadable')),
                   len(pend), _n(s2.get('disappearances'))))
    return 'Time Machine: never consulted -- the additional check was not run'


def confidence(L):
    """COMPUTED, never typed. The owner: "Everything is answerable from transcripts because
    they are all your actions, project state or things I owe you." So confidence is how much of
    that record is accounted for, and it is the WEAKEST of the coverages -- one unexamined
    store cannot be averaged away by a hundred finished hours."""
    cov = []
    hrs = L['hours']
    cov.append(('hours', 100 * sum(1 for v in hrs.values() if v['status'] == 'done') // max(1, len(hrs))))
    a = L.get('coverage', {})
    rs = L.get('restored', [])
    if rs:
        okn = sum(1 for r in rs if r.get('validated_pass') == L.get('pass', 0))
        cov.append(('restores', 100 * okn // len(rs)))
    # Time Machine is an ADDITIONAL check, never a requirement (owner, 2026-09-12: "time machine
    # is an additional check in case your scan happens to miss something but it is not necessary"),
    # so snapshot coverage cannot hold the reconciliation below 100. What the RECORD must answer --
    # every action of mine -- has no such exemption and never will: "every single one of those
    # pendings has an answer somewhere. figure it out. until it gets to 100."
    # Whether the drive was never consulted or was read and found nothing is a real difference, and
    # it is recorded in stage2 rather than smuggled into a score that cannot express it.
    OPTIONAL = ('chains', 'deliveries', 'hashes')
    for name, key in (('actions', 'actions'), ('state-keys', 'state_keys'),
                      ('snapshots', 'snapshots'), ('records', 'records'), ('chains', 'chains'),
                      ('deliveries', 'deliveries'), ('hashes', 'hashes')):
        seen, total = a.get(key, [0, 0])[0], a.get(key, [0, 0])[1]
        # a MEASURED zero is nothing owed; an unmeasured count is owed unless its source is optional
        v = 100 * seen // total if total else (100 if key in OPTIONAL else 0)
        # SNAPSHOTS ARE REPORTED, NEVER SCORED. A thinned or unreadable backup is a property of
        # the drive, not a gap in the reconciliation, and the owner's ruling is that Time Machine
        # is the additional check: "it is not necessary". Scoring it capped a run whose record was
        # wholly answered at 84 because four backups in the window no longer exist -- a number no
        # amount of reading could ever raise. What it FOUND is in stage 2 and in `tm_note`.
        cov.append((name, 100 if key == 'snapshots' else v))
    return (min(v for _, v in cov) if cov else 0), cov


def status_line(L):
    done = sum(1 for v in L['hours'].values() if v['status'] == 'done')
    return ('RECONCILE %s..%s  hours %d/%d  snapshots %d  actions %d  restored %d  conf %d%s'
            % (L['window']['start'][:13], L['window']['end'][:13], done, len(L['hours']),
               len(L['snapshots']), len(L['actions']), len(L['restored']),
               confidence(L)[0], '  COMPLETE' if L.get('complete') else ''))



def _target_transcript(a):
    """The target's transcript path: given directly, or resolved from the selector the way every
    other verb resolves it (local session metadata; the target is never contacted)."""
    if getattr(a, 'target_transcript', None):
        return a.target_transcript
    if getattr(a, 'target', None):
        import wd_lib as W
        return W.transcript_path(W.find_session(a.target))
    return None


def _target_id(a):
    """The target's session id. sent1, nudged and answered claim a send TO THE TARGET, so the
    replay must know which session that is; it is resolved from the configured selector the way
    every other verb resolves it (local session metadata -- the target is never contacted)."""
    if getattr(a, 'target_id', None):
        return a.target_id
    if getattr(a, 'target', None):
        import wd_lib as W
        return W.find_session(a.target)['sessionId']
    raise SystemExit('stage 3 needs the target: sent1, nudged and answered claim a send TO THE '
                     'TARGET, and a send elsewhere must not satisfy them. Run it through wd.sh '
                     '(which passes --target) or give --target-id.')


def run_stage(n, L, S, a):
    """RUN a stage and write what it measured into the ledger. Coverage is computed from these
    numbers, never typed -- finishing cannot be asserted."""
    import wd_recon_lib as RL
    import collections as _c
    cov = L.setdefault('coverage', {})

    _meta = {}

    def _surface(name):
        """One evidence surface, and whether it was THERE. An absent store read as an empty one
        would make everything it could have answered look unanswerable, and the run would report
        those actions as pending rather than as unread. Absent is recorded, never silently ''."""
        p = os.path.join(S, name)
        if not os.path.exists(p):
            return '', False
        try:
            return open(p).read(), True
        except Exception:
            return '', False

    def _world():
        path = RL.transcript_for(a.self_prefix, a.proj)
        numbered, bad = RL.read_records(path)
        recs = [r for _, r in numbered]
        log_text, have_log = _surface('wake.log')
        find_text, have_find = _surface('findings.md')
        # The RECURSION, wired where every stage sees it. The owner, 2026-09-12: "every single one
        # of those pendings has an answer somewhere. figure it out. until it gets to 100. thats the
        # recursive loop... you answer the earliest ones. every time you answer a new one you go
        # back and check the previous ones you answered and see if the new one changes the answer."
        # It was written, tested, and NOT CALLED: the CLI ran the one-shot presence settler alone,
        # which is why the real window left 290 actions pending with one reason between them. The
        # fixed point runs resolve_from_evidence itself, so replacing that call loses nothing.
        acts, passes = RL.reconcile_to_fixed_point(
            RL.my_actions(numbered, os.path.basename(path), S, getattr(a, 'cwd', None)),
            recs, RL.live_stores(S)['raw'], log_text, find_text)
        _meta['passes'] = passes
        _meta['surfaces'] = {'wake.log': have_log, 'findings.md': have_find}
        owner, exc, peers, acct = RL.owner_messages(recs)
        my_text, sends = RL.artifacts(recs)
        return path, numbered, bad, recs, acts, owner, exc, peers, acct, my_text, sends

    if n == 1:
        path, numbered, bad, recs, acts, owner, exc, peers, acct, my_text, sends = _world()
        fmts = RL.ts_formats(recs)
        if len(fmts) > 1:
            raise SystemExit('stage 1 REFUSED: the transcript mixes timestamp formats %s. Events '
                             'are ordered by comparing timestamps as strings, and mixed formats '
                             'misorder them; normalise the reader before reconciling.' % dict(fmts))
        opens = [x for x in acts if x['kind'] == 'open' and x.get('state') == 'live']
        live = RL.live_stores(S)
        inv = acct['seen'] == acct['attributed'] + acct['excluded_total'] + acct.get('batch_deliveries', 0)
        # The census is the durable evidence each hour's `replay` part is checked against. Kept in
        # the ledger rather than printed: a number that exists only in a terminal cannot be
        # re-read, and every figure this instrument produced before this was exactly that.
        w0, w1 = L['window']['start'][:13], L['window']['end'][:13]
        inwin = [x for x in acts if x.get('state') == 'live' and w0 <= x['ts'][:13] <= w1]
        by_hour = {}
        for x in inwin:
            by_hour.setdefault(x['ts'][:13], {}).setdefault(x['outcome'] or 'pending', 0)
            by_hour[x['ts'][:13]][x['outcome'] or 'pending'] += 1
        L['stage1'] = {'ts': now_iso(), 'transcript': os.path.basename(path),
                       'records': len(numbered), 'unparseable': bad, 'ts_formats': dict(fmts),
                       'actions': len(acts), 'opens': len(opens),
                       'live_actions': sum(1 for x in acts if x.get('state') == 'live'),
                       'outstanding': len(RL.outstanding(acts)),
                       'by_outcome': dict(_c.Counter(x['outcome'] or 'pending' for x in acts)),
                       'owner_messages': len(owner), 'owner_accounting': acct,
                       'owner_accounting_holds': inv, 'excluded': exc, 'peer_replies': len(peers),
                       'items': [{'cite': o['cite'], 'ts': o['ts'], 'store': o['store'],
                                  'id': o['id'], 'sha256': o['sha256'], 'text': o['text'],
                                  'outcome': o['outcome']} for o in opens],
                       'passes': _meta.get('passes'), 'surfaces': _meta.get('surfaces'),
                       'by_hour': by_hour, 'in_window': len(inwin),
                       'hours_with_actions': len(by_hour),
                       'actions_all': [{'ts': x['ts'], 'verb': x['verb'], 'id': x.get('id'),
                                        'outcome': x['outcome'], 'cite': x['cite'],
                                        'needs': x.get('needs')} for x in inwin]}
        cov['state_keys'] = [len(live['all_keys']), len(live['all_keys'])]
        # an unparseable line is unreconciled content, so it holds record coverage below 100
        cov['records'] = [len(numbered), len(numbered) + bad]
        cov['actions'] = [0, sum(1 for x in acts if x.get('state') == 'live')]
        print('stage 1: %d records (%d unparseable), %d actions of mine, %d opens, '
              '%d owner messages, %d state keys'
              % (len(numbered), bad, len(acts), len(opens), len(owner), len(live['all_keys'])))
        print('   outcomes: %s (settled in %d passes)'
              % (dict(_c.Counter(x['outcome'] or 'pending' for x in acts)), _meta.get('passes') or 0))
        _unread = [n for n, h in sorted((_meta.get('surfaces') or {}).items()) if not h]
        if _unread:
            print('   NOT READ: %s -- absent, so nothing they would have answered is settled'
                  % ', '.join(_unread))
        print('   in the window: %d live actions across %d of %d hours (census stored in the ledger)'
              % (len(inwin), len(by_hour), len(L['hours'])))
        print('   owner corpus accounting %s: %s' % ('HOLDS' if inv else 'BROKEN', acct))
        print('   excluded from the owner corpus, counted: %s' % exc)
        return True
    if n == 3:
        if 'stage1' not in L:
            raise SystemExit('run --stage 1 first: stage 3 replays the actions it found')
        tid = _target_id(a)
        tpath = _target_transcript(a)
        if not tpath or not os.path.exists(tpath):
            raise SystemExit("stage 3 needs the TARGET'S transcript: a send has landed only if the "
                             "target received it. Give --target-transcript, or run through wd.sh.")
        if not getattr(a, 'self_id', None):
            raise SystemExit('stage 3 needs --self-id: my session id, which marks my messages in '
                             'the target transcript')
        if not getattr(a, 'target_repo', None):
            raise SystemExit("stage 3 needs --target-repo: the target's commits are checked in its "
                             "own repository")
        path, numbered, bad, recs, acts, owner, exc, peers, acct, my_text, sends = _world()
        tnum, tbad = RL.read_records(tpath)
        trecs = [r for _, r in tnum]
        view = RL.target_view(trecs, a.self_id)
        RL.annotate_delivery(sends, tid, view)
        RL.annotate_peers(peers, tid, view)
        starts = RL.turn_starts(numbered)
        rep = RL.landed_replay(acts, starts, my_text, sends, peers, RL.live_stores(S)['raw'], tid,
                               ttexts=view['texts'], owner=owner, readings=L.get('action_readings'))
        tally = _c.Counter(x['verdict'] for x in rep)
        ch = RL.decision_chains(acts, owner, my_text, sends, tid, L.get('adjudications') or {})
        cov['chains'] = [sum(1 for x in ch.values() if not x.get('outstanding')), len(ch)]
        broken = {k: v for k, v in ch.items() if v['verdicts'] != ['complete']}
        repos = []
        for r in [os.path.dirname(os.path.abspath(__file__)), a.target_repo] + list(L.get('repos') or []):
            if isinstance(r, str) and r not in repos:
                repos.append(r)
        probe = RL.git_probe(repos)
        tsends = [s for s in sends if s.get('to') == tid]
        known = {tid, a.self_id} | {p.get('from') for p in peers if p.get('from')} | \
                {s.get('to') for s in sends if s.get('to')}
        items = (RL.hex_items(my_text, 'my text to the owner') + RL.hex_items(tsends, 'my send to the target')
                 + RL.hex_items(view['sends_to_me'], "the target's send to me"))
        hashes = RL.apply_hash_readings(
            RL.classify_hashes(items, probe, RL.file_hashes_in(recs) | RL.file_hashes_in(trecs), known),
            L.get('hash_readings'))
        pushes = RL.check_pushes(view['pushes'], RL.git_probe([a.target_repo]))
        undelivered = [s for s in tsends if not s.get('delivered')]
        htally = _c.Counter(h['cls'] for h in hashes)
        ptally = _c.Counter(p['cls'] for p in pushes)
        owed_readings = [{'key': RL.action_key(x), 'ts': x['ts'], 'verb': x['verb'], 'id': x.get('id'),
                          'needs': x['needs']} for x in acts
                         if x.get('state') == 'live' and (x.get('needs') or '').startswith('a reading')]
        L['stage3'] = {'ts': now_iso(), 'target': tid, 'tally': dict(tally),
                       'findings': [{'ts': x['ts'], 'cite': x['cite'], 'verb': x['verb'],
                                     'id': x.get('id'), 'verdict': x['verdict'], 'why': x['why']}
                                    for x in rep if x['verdict'] != 'ok'],
                       'action_readings_owed': owed_readings,
                       'chains': ch,
                       'delivery': {'to_target': len(tsends), 'delivered': len(tsends) - len(undelivered),
                                    'never_delivered': [{'ts': s['ts'], 'msg': s['msg'][:160]} for s in undelivered],
                                    'target_records': len(tnum), 'target_unparseable': tbad},
                       'hashes': {'tally': dict(htally),
                                  'needs_reading': [{'ts': h['ts'], 'sha': h['sha'], 'where': h['where']}
                                                    for h in hashes if h['cls'] == 'not_a_commit_here'],
                                  'findings': [{'ts': h['ts'], 'sha': h['sha'], 'where': h['where'], 'cls': h['cls']}
                                               for h in hashes if h['cls'] in ('on_no_ref', 'claimed_commit_missing')]},
                       'pushes': {'tally': dict(ptally),
                                  'not_ok': [p for p in pushes if p['cls'] != 'on_its_branch']}}
        # an action counts as reconciled only once evidence ESTABLISHED its outcome; pending ones
        # hold confidence below 100 until Time Machine or a later record settles them
        cov['actions'] = [len(rep), sum(1 for x in acts if x.get('state') == 'live')]
        cov['deliveries'] = [len(tsends), len(tsends)]
        cov['hashes'] = [sum(1 for h in hashes if h['cls'] != 'not_a_commit_here'), len(hashes)]
        print('stage 3: %d actions replayed -- %s' % (len(rep), dict(tally)))
        print('         %d live action(s) still OUTSTANDING (no evidence yet)'
              % sum(1 for x in acts if x.get('state') == 'live' and (x['outcome'] is None or x.get('needs'))))
        print('         %d action(s) owe a READING (--read-action <key> --as yes|no)' % len(owed_readings))
        print('         %d send(s) to the target: %d received in its transcript, %d never received'
              % (len(tsends), len(tsends) - len(undelivered), len(undelivered)))
        print('         %d decision chain(s): %d complete, %d broken'
              % (len(ch), len(ch) - len(broken), len(broken)))
        for did, v in sorted(broken.items()):
            print('   CHAIN %-8s %s' % (did, '+'.join(v['verdicts'])))
        print('         %d hex token(s) -- %s' % (len(hashes), dict(htally)))
        print('         %d push(es) by the target -- %s' % (len(pushes), dict(ptally)))
        for f in L['stage3']['findings'][:30]:
            print('   %-13s %s %-10s %s' % (f['verdict'], f['ts'][:19], f['verb'], f['why']))
        return True
    if n == 2:
        # The Time Machine half. The overlay carries ONE thing -- how to reach the drive; what is
        # read, what it is compared against and what counts as evidence live here (owner,
        # 2026-09-11). Every backup in the window is read: no sampling, no binary search.
        path, numbered, bad, recs, acts, owner, exc, peers, acct, my_text, sends = _world()
        tm = os.path.expanduser(getattr(a, 'tm_wrapper', None) or TM_WRAPPER)
        if not os.path.exists(tm):
            raise SystemExit('stage 2 REFUSED: the tm wrapper named by the overlay is not at %s' % tm)
        st_out = subprocess.run([tm, 'status'], capture_output=True, text=True).stdout
        try:
            status = json.loads(st_out)
        except Exception:
            raise SystemExit('stage 2 REFUSED: `tm status` did not return JSON: %s' % st_out[:200])
        if not status.get('ok') or not all(r.get('readable') for r in status.get('roots') or []):
            raise SystemExit('stage 2 REFUSED: the backup roots are not readable -- %s. The drive '
                             'may be detached, or the broker may have lost Full Disk Access; '
                             'neither is something to work around.' % st_out[:200])

        snaps, why = tm_snapshots()
        if snaps is None:
            raise SystemExit('stage 2 REFUSED: %s' % why)
        w0, w1 = L['window']['start'], L['window']['end']
        # a backup OUTSIDE the window still brackets it: resolve_from_snapshots settles an action
        # only between a readable snapshot before it and one after, so the neighbours are read too
        keep = [s for s in snaps if _snap_utc(s) >= _shift(w0, -48) and _snap_utc(s) <= _shift(w1, 48)]
        rows, seen = [], {}
        for name in keep:
            at = _snap_utc(name)
            size, lserr = tm_state_size(tm, name)
            if size is None:
                seen[name] = ('thinned' if 'does not exist' in (lserr or '') else 'unreadable', lserr)
                rows.append({'snapshot': name, 'at': at, 'readable': False, 'why': lserr})
                continue
            raw = subprocess.run([tm, 'read', tm_state_path(name)], capture_output=True, text=True).stdout
            d, rwhy = RL.tm_state_at(raw, expect_bytes=size)
            seen[name] = (('visited' if d is not None else 'unreadable'),
                          'state.json %d bytes; %s' % (size, rwhy))
            rows.append({'snapshot': name, 'at': at, 'readable': d is not None, 'state': d, 'why': rwhy})
        series = RL.stage2_series([r['snapshot'] for r in rows],
                                  lambda s: next(r for r in rows if r['snapshot'] == s).get('state'),
                                  at_of=lambda s: next(r for r in rows if r['snapshot'] == s)['at'])
        for name, (statusword, ev) in sorted(seen.items()):
            L['snapshots'][name] = {'status': statusword, 'ts': now_iso(), 'evidence': ev}

        pend_before = sum(1 for x in acts if x.get('state') == 'live' and x['outcome'] is None)
        RL.resolve_from_snapshots(acts, series)
        pend_after = sum(1 for x in acts if x.get('state') == 'live' and x['outcome'] is None)
        dis = RL.stage2_disappearances(series)
        readable = [r for r in rows if r['readable']]
        L['stage2'] = {'ts': now_iso(), 'wrapper': tm, 'zone': _local_zone(),
                       'snapshots_considered': len(keep), 'readable': len(readable),
                       'unreadable': [r['snapshot'] for r in rows if not r['readable']],
                       'settled_from_snapshots': pend_before - pend_after,
                       'still_pending': pend_after,
                       'disappearances': {str(k): v for k, v in (dis.get('first_absent') or {}).items()},
                       'readable_at': [r['at'] for r in readable]}
        cov['snapshots'] = [len(readable), max(1, len(keep))]
        print('stage 2: %d backup(s) in and around the window, %d readable, %d unreadable'
              % (len(keep), len(readable), len(keep) - len(readable)))
        print('         snapshot times (UTC): %s' % ', '.join(r['at'][:19] for r in readable))
        print('         %d pending action(s) settled from the snapshots; %d still pending'
              % (pend_before - pend_after, pend_after))
        if dis.get('first_absent'):
            print('         DATED DROPS: %d' % len(dis['first_absent']))
            for k, v in list((dis.get('first_absent') or {}).items())[:10]:
                print('   %s last seen %s, gone by %s' % (k, (dis.get('last_seen') or {}).get(k), v))
        return True
    if n == 4:
        rs = L.get('restored') or []
        if not rs:
            print('stage 4: nothing restored yet, so nothing to check for supersession')
            return False
        path, numbered, bad, recs, acts, owner, exc, peers, acct, my_text, sends = _world()
        later = [{'ts': o['ts'], 'text': o['text'], 'kind': 'owner'} for o in owner]
        for i, r in enumerate(rs):
            if r.get('validated_pass') == L.get('pass', 0):
                continue
            ev = RL.stage4_evidence({'cite': r.get('evidence'), 'ts': r.get('ts'),
                                     'text': r['what']}, later)
            r['supersession_evidence'] = ev
            print('restore #%d: %d owner message(s) after it, %d touching it'
                  % (i, ev['records_after'], len(ev['hits'])))
            for h in ev['hits'][:3]:
                print('     %s  %s' % (h['ts'][:19], h['excerpt'][:110].replace(chr(10), ' ')))
        return True
    if n == 5:
        # The only stage that writes the state under reconciliation. Owner, 2026-09-12: nothing
        # is written to the live state until the reconciliation is COMPLETE -- a repair applied
        # mid-pass changes the very stores the remaining hours are being compared against.
        if a.apply and not L.get('complete'):
            raise SystemExit('stage 5 --apply REFUSED: the reconciliation is not complete, and '
                             'the repair writes the live state. Finish it (reconcile --complete) '
                             'first; a dry run is always allowed.')
        rs = [r for r in (L.get('restored') or [])
              if r.get('validated_pass') == L.get('pass', 0) and not r.get('superseded')]
        if not rs:
            print('stage 5: nothing validated-and-not-superseded to repair')
            return False
        payload = [{'store': r.get('store', 'owner_queue'), 'id': r.get('id'),
                    'text': r['what'], 'cite': r.get('evidence', ''),
                    'sha256': r.get('sha256', ''), 'now': now_iso()} for r in rs]
        res = RL.stage5_repair(S, payload, apply=a.apply)
        print('stage 5: %s -- added %d, skipped %d duplicate(s)'
              % ('APPLIED' if a.apply else 'dry run', len(res['added']),
                 len(res['skipped_duplicate'])))
        return bool(a.apply)
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--state-dir', required=True)
    ap.add_argument('--tm-wrapper', dest='tm_wrapper',
                    help='the tm wrapper the overlay names (default %s); a test points it '
                         'somewhere absent to exercise the refusal, which otherwise depends '
                         'on whether a drive happens to be attached' % TM_WRAPPER)
    ap.add_argument('--ledger-dir', dest='ledger_dir',
                    help='where the ledger and its lock live (default local/reconcile; never '
                         'the state dir -- a reconciliation must not write into its subject)')
    ap.add_argument('dates', nargs='*', help='<start> <end> (only with --init)')
    ap.add_argument('--init', action='store_true')
    ap.add_argument('--next', action='store_true')
    ap.add_argument('--hour'); ap.add_argument('--part', choices=PARTS + OPTIONAL_PARTS)   # an optional part is still
    #                                                         RECORDABLE -- it is simply not
    #                                                         owed by an hour that has none
    ap.add_argument('--snapshot'); ap.add_argument('--thinned')
    ap.add_argument('--action'); ap.add_argument('--landed', choices=('yes', 'no'))
    ap.add_argument('--restore')
    ap.add_argument('--stage', type=int, choices=(1, 2, 3, 4, 5),
                    help='RUN a stage: 1 replay my actions, 2 Time Machine, 3 did it land, '
                         '4 supersession evidence, 5 additive repair')
    ap.add_argument('--apply', action='store_true', help='stage 5 only: write the repair')
    ap.add_argument('--proj', help='transcript corpus (testing)')
    ap.add_argument('--self-prefix', default='80f99b89')
    ap.add_argument('--target', help='the target session selector, as wd.sh passes it from config')
    ap.add_argument('--target-id', help='the target session id directly (testing)')
    ap.add_argument('--cwd', help='starting directory for records that do not carry one (real records do)')
    ap.add_argument('--target-transcript', help="the target's transcript (wd.sh resolves it from --target)")
    ap.add_argument('--target-repo', help="the target's repository, for its commits")
    ap.add_argument('--self-id', help='my session id, which marks my messages in the target transcript')
    ap.add_argument('--read-hash', help='a hex token stage 3 could not classify')
    ap.add_argument('--read-action', help='an action stage 3 owes a reading of (the key it lists)')
    ap.add_argument('--as', dest='read_as', choices=('commit', 'not-a-commit', 'yes', 'no'),
                    help='what the token was meant as, read from the text around it')
    ap.add_argument('--repo', action='append', default=[],
                    help='a repo whose commits my claims may refer to (repeatable)')
    ap.add_argument('--validate', type=int)
    ap.add_argument('--superseded', action='store_true')
    ap.add_argument('--evidence'); ap.add_argument('--complete', action='store_true')
    ap.add_argument('--adjudicate', help='a decision chain key from stage 3')
    ap.add_argument('--put', help='the timestamp of my text that put it to him, or none')
    ap.add_argument('--answer', help='the timestamp of his message that answered it, or none')
    a = ap.parse_args()

    # 1. MUTUAL EXCLUSION -- before reading or writing anything at all.
    rivals = rival_hooks(a.state_dir)
    if rivals:
        print('RECONCILE ABORTED -- another hook is running; no action taken:')
        for pid, cmd in rivals[:6]:
            print('   pid %s  %s' % (pid, cmd))
        return 0

    S = a.state_dir
    LD = ledger_dir(a)          # the ledger's home; S stays the state UNDER reconciliation
    # A second RECONCILE is also a rival: two of them over one ledger interleave
    # read-modify-write and lose whichever finished first. The rival scan deliberately skips
    # reconcile processes (so it does not see itself), so exclusion between them is a lock.
    # Only a WRITING run takes the lock. The minute nagger calls this for status every 60 s;
    # if a read took the lock it would abort the very stage runs it exists to nag about.
    writes = bool(a.read_hash or a.read_action or a.adjudicate or a.stage or a.hour or a.snapshot or a.thinned or a.action or a.restore
                  or a.validate is not None or a.complete or a.init or a.repo)
    if not writes:
        return _main(a, S, LD)
    lock = os.path.join(LD, 'reconcile.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        try:
            holder = open(lock).read().strip()
        except OSError:
            holder = '?'
        alive = False
        try:
            os.kill(int(holder), 0)
            alive = True
        except Exception:
            alive = False
        if alive:
            print('RECONCILE ABORTED -- another reconcile (pid %s) holds the ledger lock; '
                  'no action taken' % holder)
            return 0
        # A stale lock from a killed run must not wedge the instrument forever, but it is
        # REPORTED rather than silently reclaimed.
        print('note: reclaiming a stale lock from pid %s (no such process)' % holder)
        os.unlink(lock)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    try:
        return _main(a, S, LD)
    finally:
        try:
            if open(lock).read().strip() == str(os.getpid()):
                os.unlink(lock)
        except OSError:
            pass


def _main(a, S, LD):
    if a.repo:
        L0 = load(LD)
        if L0 is not None:
            L0['repos'] = sorted(set((L0.get('repos') or []) + a.repo))
            save(LD, L0)
            print('repos for commit verification: %s' % ', '.join(L0['repos']))
    if a.init:
        if len(a.dates) != 2:
            raise SystemExit('--init needs exactly two dates: reconcile <start> <end> --init')
        L = init(LD, a.dates[0], a.dates[1])
        print(status_line(L))
        print('ledger: %s' % path_of(LD))
        return 0

    L = load(LD)
    if L is None:
        print('no reconciliation open. start one:  wd.sh reconcile <start> <end> --init')
        return 1

    changed = False
    if a.stage:
        import sys as _s, os as _o
        _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
        changed = run_stage(a.stage, L, S, a) or changed

    if a.hour:
        if a.hour not in L['hours']:
            raise SystemExit('%s is not an hour in the window (%s..%s)'
                             % (a.hour, L['window']['start'], L['window']['end']))
        if not a.part or not a.evidence:
            raise SystemExit('--hour needs --part %s and --evidence "<what was measured>"'
                             % '|'.join(PARTS + OPTIONAL_PARTS))
        h = L['hours'][a.hour]
        if a.part in OPTIONAL_PARTS and a.part not in h['parts']:
            # recording an optional part ADDS it: it exists for this hour because evidence for it
            # exists, never because the grid said every hour must have one
            h['parts'][a.part] = None
        h['parts'][a.part] = {'ts': now_iso(), 'evidence': a.evidence}
        if all(h['parts'].get(p) for p in h['parts']):
            h['status'] = 'done'
        changed = True
        print('%s %s recorded; %s' % (a.hour, a.part,
              'HOUR DONE' if h['status'] == 'done' else
              'still owed: ' + ', '.join(p for p in PARTS if not h['parts'][p])))

    for name, st in ((a.snapshot, 'visited'), (a.thinned, 'thinned')):
        if name:
            if not a.evidence:
                raise SystemExit('--snapshot/--thinned needs --evidence')
            L['snapshots'][name] = {'status': st, 'ts': now_iso(), 'evidence': a.evidence}
            changed = True
            print('snapshot %s -> %s' % (name, st))

    if a.action:
        if not a.landed or not a.evidence:
            raise SystemExit('--action needs --landed yes|no and --evidence')
        L['actions'][a.action] = {'landed': a.landed, 'ts': now_iso(), 'evidence': a.evidence}
        changed = True
        print('action %s landed=%s' % (a.action, a.landed))

    if a.adjudicate:
        # PUT and ANSWERED are MEANING: whether he answered is read from his words, not matched.
        # The reading is recorded with the words it rests on, and stage 3 refuses one that cites a
        # message the record does not contain for that chain.
        if a.put is None or a.answer is None or not a.evidence:
            raise SystemExit('--adjudicate needs --put <ts|none>, --answer <ts|none> and --evidence '
                             '"<what his words say, quoted>"')
        chains = (L.get('stage3') or {}).get('chains') or {}
        if a.adjudicate not in chains:
            raise SystemExit('no chain %s in the last stage 3 (have: %s)'
                             % (a.adjudicate, ', '.join(sorted(chains)) or 'none -- run --stage 3'))
        L.setdefault('adjudications', {})[a.adjudicate] = {'put': a.put, 'answer': a.answer,
                                                           'evidence': a.evidence, 'ts': now_iso()}
        changed = True
        print('adjudication recorded for %s (put %s, answer %s); stage 3 applies it'
              % (a.adjudicate, a.put, a.answer))

    if a.read_hash:
        # whether a hex token was MEANT as a commit is read from the words around it, never matched
        if a.read_as not in ('commit', 'not-a-commit') or not a.evidence:
            raise SystemExit('--read-hash needs --as commit|not-a-commit and --evidence "<the words around it>"')
        L.setdefault('hash_readings', {})[a.read_hash] = {'as': a.read_as, 'evidence': a.evidence,
                                                          'ts': now_iso()}
        changed = True
        print('reading recorded for %s (%s); stage 3 applies it' % (a.read_hash, a.read_as))

    if a.read_action:
        # whether a send CARRIED an item or question, and whether anything ANSWERED a question, is
        # read from the words -- never matched. Only an action stage 3 listed can be read.
        if a.read_as not in ('yes', 'no') or not a.evidence:
            raise SystemExit('--read-action needs --as yes|no and --evidence "<what was read, and where>"')
        owed = {x['key'] for x in ((L.get('stage3') or {}).get('action_readings_owed') or [])}
        if a.read_action not in owed and a.read_action not in (L.get('action_readings') or {}):
            raise SystemExit('--read-action %s: stage 3 owes no reading of that action -- run '
                             '--stage 3 and use a key it lists' % a.read_action)
        L.setdefault('action_readings', {})[a.read_action] = {'as': a.read_as, 'evidence': a.evidence,
                                                              'ts': now_iso()}
        changed = True
        print('reading recorded for %s (%s); stage 3 applies it' % (a.read_action, a.read_as))

    if a.restore:
        if not a.evidence:
            raise SystemExit('--restore needs --evidence naming where it was recovered from')
        # Every restore lands UNVALIDATED and, per the owner 2026-09-11, invalidates the
        # supersession check of every restore already made: "some things might end up
        # superseded, so you need to go and recursively check every state restore you did to
        # make sure it's valid in the face of new info." Reinstating a row changes what the
        # record says about the others, so the set is re-checked to a FIXED POINT rather than
        # once. The recursion is enforced here, not remembered: this loop cannot be skipped.
        L['pass'] = L.get('pass', 0) + 1
        for r in L['restored']:
            r['validated_pass'] = None
        L['restored'].append({'ts': now_iso(), 'what': a.restore, 'evidence': a.evidence,
                              'validated_pass': None, 'superseded': None})
        changed = True
        print('restored (UNVALIDATED): %s' % a.restore)
        print('   pass %d -- every restore now needs its supersession check re-run'
              % L['pass'])

    if a.validate is not None:
        if not a.evidence:
            raise SystemExit('--validate needs --evidence: what was scanned FORWARD of the drop')
        try:
            r = L['restored'][a.validate]
        except IndexError:
            raise SystemExit('no restore #%d (have %d)' % (a.validate, len(L['restored'])))
        r['validated_pass'] = L.get('pass', 1)
        r['superseded'] = bool(a.superseded)
        r['validation'] = {'ts': now_iso(), 'evidence': a.evidence}
        changed = True
        print('restore #%d %s at pass %d'
              % (a.validate, 'SUPERSEDED (do not reinstate)' if a.superseded else 'stands',
                 r['validated_pass']))

    if a.complete:
        o = outstanding(L)
        if o:
            print('REFUSED -- not reconciled. Outstanding:')
            for x in o:
                print('   %s' % x)
            if changed:
                save(LD, L)
            return 1
        L['complete'] = True
        L['log'].append({'ts': now_iso(), 'what': 'complete'})
        save(LD, L)
        print(status_line(L))
        print('   %s' % tm_note(L))
        print('RECONCILED. The hook may stop.')
        return 0

    if changed:
        L['log'].append({'ts': now_iso(), 'what': ' '.join(sys.argv[1:])[:200]})
        save(LD, L)

    o = outstanding(L)
    print(status_line(L))
    print('   %s' % tm_note(L))
    if not o:
        print('nothing outstanding -- run: wd.sh reconcile --complete')
        return 0
    print('OUTSTANDING:')
    for x in o:
        print('   %s' % x)

    if a.next or not changed:
        pend = [k for k, v in sorted(L['hours'].items()) if v['status'] != 'done']
        if pend:
            h = L['hours'][pend[0]]
            print('NEXT HOUR %s -- owes: %s'
                  % (pend[0], ', '.join(p for p in PARTS if not h['parts'][p])))
    if a.next and os.path.exists(OVERLAY):
        print('\n--- local/reconcile.md (overlay, not committed) ---')
        print(open(OVERLAY).read())
    return 1


if __name__ == '__main__':
    sys.exit(main())
