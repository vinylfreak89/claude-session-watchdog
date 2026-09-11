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
  reconcile --confidence <0-100> --evidence "..."
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
         'restored': [], 'confidence': 0, 'confidence_evidence': None, 'complete': False,
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
    if L.get('confidence', 0) != 100:
        o.append('confidence %s/100' % L.get('confidence', 0))
    return o


def status_line(L):
    done = sum(1 for v in L['hours'].values() if v['status'] == 'done')
    return ('RECONCILE %s..%s  hours %d/%d  snapshots %d  actions %d  restored %d  conf %d%s'
            % (L['window']['start'][:13], L['window']['end'][:13], done, len(L['hours']),
               len(L['snapshots']), len(L['actions']), len(L['restored']),
               L.get('confidence', 0), '  COMPLETE' if L.get('complete') else ''))


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
    ap.add_argument('--restore'); ap.add_argument('--confidence', type=int)
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
        L['restored'].append({'ts': now_iso(), 'what': a.restore, 'evidence': a.evidence})
        changed = True
        print('restored: %s' % a.restore)

    if a.confidence is not None:
        if not a.evidence:
            raise SystemExit('--confidence needs --evidence')
        if a.confidence == 100 and outstanding({**L, 'confidence': 100}):
            raise SystemExit('REFUSED: confidence 100 with work outstanding:\n   '
                             + '\n   '.join(outstanding({**L, 'confidence': 100})))
        L['confidence'] = a.confidence
        L['confidence_evidence'] = {'ts': now_iso(), 'evidence': a.evidence}
        changed = True
        print('confidence %d' % a.confidence)

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
