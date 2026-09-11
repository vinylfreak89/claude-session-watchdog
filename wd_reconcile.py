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

PARTS = ('tm', 'diff', 'replay', 'verdict')
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


def rival_hooks():
    """Every live watchdog process that is not this one. Read-only; no signals are sent."""
    me, parent = os.getpid(), os.getppid()
    try:
        out = subprocess.run(['ps', '-axo', 'pid=,ppid=,command='],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception as e:
        # A check that cannot run must NOT read as "no rivals" -- that is the missing-is-not-a-
        # value defect, and here it would licence exactly the concurrency this guard forbids.
        return [('?', 'cannot enumerate processes: %s' % e)]
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
            hits.append((pid, cmd[:110]))
    return hits


# ---------------------------------------------------------------- ledger

def path_of(state_dir):
    return os.path.join(state_dir, 'reconcile.json')


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
    if not L['snapshots']:
        o.append('no Time Machine snapshots enumerated yet')
    stale = [i for i, r in enumerate(L.get('restored', []))
             if r.get('validated_pass') != L.get('pass', 0)]
    if stale:
        o.append('%d restore(s) unvalidated at pass %d (next #%d) -- supersession check owed'
                 % (len(stale), L.get('pass', 0), stale[0]))
    c, parts = confidence(L)
    if c != 100:
        o.append('confidence %d/100 (%s)' % (c, '; '.join('%s %d%%' % (k, v) for k, v in parts)))
    return o


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
    for name, key in (('actions', 'actions'), ('state-keys', 'state_keys'),
                      ('snapshots', 'snapshots'), ('records', 'records')):
        seen, total = a.get(key, [0, 0])[0], a.get(key, [0, 0])[1]
        cov.append((name, 100 * seen // total if total else 0))
    return (min(v for _, v in cov) if cov else 0), cov


def status_line(L):
    done = sum(1 for v in L['hours'].values() if v['status'] == 'done')
    return ('RECONCILE %s..%s  hours %d/%d  snapshots %d  actions %d  restored %d  conf %d%s'
            % (L['window']['start'][:13], L['window']['end'][:13], done, len(L['hours']),
               len(L['snapshots']), len(L['actions']), len(L['restored']),
               confidence(L)[0], '  COMPLETE' if L.get('complete') else ''))



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

    def _world():
        path = RL.transcript_for(a.self_prefix, a.proj)
        numbered, bad = RL.read_records(path)
        recs = [r for _, r in numbered]
        acts = RL.my_actions(numbered, os.path.basename(path))
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
        opens = [x for x in acts if x['kind'] == 'open']
        live = RL.live_stores(S)
        inv = acct['seen'] == acct['attributed'] + acct['excluded_total'] + acct.get('batch_deliveries', 0)
        L['stage1'] = {'ts': now_iso(), 'transcript': os.path.basename(path),
                       'records': len(numbered), 'unparseable': bad, 'ts_formats': dict(fmts),
                       'actions': len(acts), 'opens': len(opens),
                       'by_outcome': dict(_c.Counter(x['outcome'] for x in acts)),
                       'owner_messages': len(owner), 'owner_accounting': acct,
                       'owner_accounting_holds': inv, 'excluded': exc, 'peer_replies': len(peers),
                       'items': [{'cite': o['cite'], 'ts': o['ts'], 'store': o['store'],
                                  'id': o['id'], 'sha256': o['sha256'], 'text': o['text'],
                                  'outcome': o['outcome']} for o in opens]}
        cov['state_keys'] = [len(live['all_keys']), len(live['all_keys'])]
        # an unparseable line is unreconciled content, so it holds record coverage below 100
        cov['records'] = [len(numbered), len(numbered) + bad]
        cov['actions'] = [0, len(acts)]
        print('stage 1: %d records (%d unparseable), %d actions of mine, %d opens, '
              '%d owner messages, %d state keys'
              % (len(numbered), bad, len(acts), len(opens), len(owner), len(live['all_keys'])))
        print('   outcomes: %s' % dict(_c.Counter(x['outcome'] for x in acts)))
        print('   owner corpus accounting %s: %s' % ('HOLDS' if inv else 'BROKEN', acct))
        print('   excluded from the owner corpus, counted: %s' % exc)
        return True
    if n == 3:
        if 'stage1' not in L:
            raise SystemExit('run --stage 1 first: stage 3 replays the actions it found')
        tid = _target_id(a)
        path, numbered, bad, recs, acts, owner, exc, peers, acct, my_text, sends = _world()
        starts = RL.turn_starts(numbered)
        rep = RL.landed_replay(acts, starts, my_text, sends, peers, RL.live_stores(S)['raw'], tid)
        tally = _c.Counter(x['verdict'] for x in rep)
        ch = RL.decision_chains(acts, owner, my_text, sends, tid)
        broken = {k: v for k, v in ch.items() if v['verdicts'] != ['complete']}
        import subprocess as _sp
        repos = []
        for r in ([a.proj] if a.proj else []) + [os.path.dirname(os.path.abspath(__file__))] \
                + list(L.get('repos') or []):
            if isinstance(r, str) and r not in repos:
                repos.append(r)

        def _probe(sha):
            for repo in repos:
                try:
                    if _sp.run(['git', 'cat-file', '-e', sha + '^{commit}'], cwd=repo,
                               capture_output=True).returncode != 0:
                        continue
                    r = _sp.run(['git', 'branch', '-a', '--contains', sha], cwd=repo,
                                capture_output=True, text=True)
                    refs = [x.strip('* ').strip() for x in r.stdout.splitlines() if x.strip()]
                    return True, ['%s:%s' % (os.path.basename(repo), x) for x in refs]
                except Exception:
                    continue
            return False, []

        commits = RL.verify_commits(RL.claimed_commits(my_text), _probe)
        ctally = _c.Counter(x['verdict'] for x in commits)
        L['stage3'] = {'ts': now_iso(), 'target': tid, 'tally': dict(tally),
                       'findings': [{'ts': x['ts'], 'cite': x['cite'], 'verb': x['verb'],
                                     'id': x.get('id'), 'verdict': x['verdict'], 'why': x['why']}
                                    for x in rep if x['verdict'] != 'ok'],
                       'chains': ch,
                       'commits': {'tally': dict(ctally),
                                   'not_ok': [{'ts': x['ts'], 'sha': x['sha'], 'verdict': x['verdict']}
                                              for x in commits if x['verdict'] != 'ok']}}
        cov['actions'] = [len(rep), len(rep)]
        print('stage 3: %d actions replayed -- %s' % (len(rep), dict(tally)))
        print('         %d decision chain(s): %d complete, %d broken'
              % (len(ch), len(ch) - len(broken), len(broken)))
        for did, v in sorted(broken.items()):
            print('   CHAIN %-8s %s' % (did, '+'.join(v['verdicts'])))
        print('         %d commit claim(s) -- %s' % (len(commits), dict(ctally)))
        for f in L['stage3']['findings'][:30]:
            print('   %-13s %s %-10s %s' % (f['verdict'], f['ts'][:19], f['verb'], f['why']))
        return True
    if n == 2:
        raise SystemExit('stage 2 needs the Time Machine wrapper; drive it with --stage 2 once '
                         'the overlay\'s tm path is confirmed by `tm status` (not wired to a '
                         'live drive from here by design -- the reader is fixture-tested)')
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
    ap.add_argument('dates', nargs='*', help='<start> <end> (only with --init)')
    ap.add_argument('--init', action='store_true')
    ap.add_argument('--next', action='store_true')
    ap.add_argument('--hour'); ap.add_argument('--part', choices=PARTS)
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
    ap.add_argument('--repo', action='append', default=[],
                    help='a repo whose commits my claims may refer to (repeatable)')
    ap.add_argument('--validate', type=int)
    ap.add_argument('--superseded', action='store_true')
    ap.add_argument('--evidence'); ap.add_argument('--complete', action='store_true')
    a = ap.parse_args()

    # 1. MUTUAL EXCLUSION -- before reading or writing anything at all.
    rivals = rival_hooks()
    if rivals:
        print('RECONCILE ABORTED -- another hook is running; no action taken:')
        for pid, cmd in rivals[:6]:
            print('   pid %s  %s' % (pid, cmd))
        return 0

    S = a.state_dir
    # A second RECONCILE is also a rival: two of them over one ledger interleave
    # read-modify-write and lose whichever finished first. The rival scan deliberately skips
    # reconcile processes (so it does not see itself), so exclusion between them is a lock.
    os.makedirs(S, exist_ok=True)
    # Only a WRITING run takes the lock. The minute nagger calls this for status every 60 s;
    # if a read took the lock it would abort the very stage runs it exists to nag about.
    writes = bool(a.stage or a.hour or a.snapshot or a.thinned or a.action or a.restore
                  or a.validate is not None or a.complete or a.init or a.repo)
    if not writes:
        return _main(a, S)
    lock = os.path.join(S, 'reconcile.lock')
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
        return _main(a, S)
    finally:
        try:
            if open(lock).read().strip() == str(os.getpid()):
                os.unlink(lock)
        except OSError:
            pass


def _main(a, S):
    if a.repo:
        L0 = load(S)
        if L0 is not None:
            L0['repos'] = sorted(set((L0.get('repos') or []) + a.repo))
            save(S, L0)
            print('repos for commit verification: %s' % ', '.join(L0['repos']))
    if a.init:
        if len(a.dates) != 2:
            raise SystemExit('--init needs exactly two dates: reconcile <start> <end> --init')
        L = init(S, a.dates[0], a.dates[1])
        print(status_line(L))
        print('ledger: %s' % path_of(S))
        return 0

    L = load(S)
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
                             % '|'.join(PARTS))
        h = L['hours'][a.hour]
        h['parts'][a.part] = {'ts': now_iso(), 'evidence': a.evidence}
        if all(h['parts'][p] for p in PARTS):
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
                save(S, L)
            return 1
        L['complete'] = True
        L['log'].append({'ts': now_iso(), 'what': 'complete'})
        save(S, L)
        print(status_line(L))
        print('RECONCILED. The hook may stop.')
        return 0

    if changed:
        L['log'].append({'ts': now_iso(), 'what': ' '.join(sys.argv[1:])[:200]})
        save(S, L)

    o = outstanding(L)
    print(status_line(L))
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
