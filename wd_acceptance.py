"""Typed acceptance with pre-delivery baselines and target-attributed action evidence."""
import base64
import csv
from dataclasses import dataclass
from enum import Enum
import hashlib
import io
import json
import math
import os
import re
import shlex
from typing import Optional

import wd_lib as W
import wd_receipts as D

# Above this, a baseline keeps its sha256 and size but not the bytes. Chosen to cover the
# text subjects that genuinely need content -- the ledger runs to 150 KB under the artifact
# guard, sidecar CSVs are tens of megabytes only as renders -- while excluding media.
CONTENT_BASELINE_MAX = 4 * 1024 * 1024


class Status(str, Enum):
    PASS = 'pass'
    NOT_YET = 'not-yet'
    UNDECIDED = 'undecided'

@dataclass(frozen=True)
class Result:
    status: Status
    evidence: str
    # Computed from the transcript, never persisted as an exemption marker.
    reply_turn_ts: Optional[str] = None

@dataclass(frozen=True)
class Kind:
    minimum: int
    maximum: int
    evaluate: object


def result(status, evidence):
    return Result(status, evidence)


def path_for(a, sess, path):
    repo = a.repo or sess['cwd']
    return os.path.realpath(os.path.expanduser(path if os.path.isabs(path) else os.path.join(repo, path)))


def file_snapshot(path):
    if os.path.isdir(path):
        # A directory has no content to hash. Its children are recorded by size and
        # mtime so a later write inside it is visible without reading every byte --
        # a render directory can hold hundreds of megabytes.
        children = {}
        for root, _dirs, files in os.walk(path):
            for name in files:
                full = os.path.join(root, name)
                try: st = os.stat(full)
                except FileNotFoundError: continue
                children[os.path.relpath(full, path)] = [st.st_size, st.st_mtime]
        return dict(path=path, exists=True, is_dir=True, sha256=None, content=None,
                    mtime=os.stat(path).st_mtime, children=children)
    try:
        with open(path, 'rb') as f:
            data = f.read()
            stat = os.fstat(f.fileno())
        # The sha256 is what detects change. The CONTENT is kept only so a later
        # Write/Edit call can be replayed against it, and to read as text for grep/csv/row.
        # Neither is possible for a large binary, so storing one buys nothing and costs
        # everything: a `file` acceptance on cap4.mp4 put 38 MB of base64 into state.json,
        # seven such items took it to 358 MB, and every wd.sh call then loaded and rewrote
        # that -- which is why `owed` came to take minutes. The consumers already treat an
        # absent content as "cannot prove by replay", which is the honest answer here.
        content = base64.b64encode(data).decode() if len(data) <= CONTENT_BASELINE_MAX else None
        return dict(path=path, exists=True, sha256=hashlib.sha256(data).hexdigest(),
                    content=content, size=len(data), mtime=stat.st_mtime)
    except FileNotFoundError:
        return dict(path=path, exists=False, sha256=None, content=None, mtime=None)


def parse(spec):
    args = shlex.split(spec or '')
    if not args or args[0] not in KINDS:
        raise D.EvidenceError('unknown or situational acceptance kind')
    kind, args = args[0], args[1:]
    definition = KINDS[kind]
    if not definition.minimum <= len(args) <= definition.maximum:
        raise D.EvidenceError('%s acceptance needs %d..%d argument(s)' % (kind, definition.minimum, definition.maximum))
    if kind == 'grep': re.compile(args[1], re.I)
    if kind == 'csv' and not re.fullmatch(r'\s*[\w.]+\s*(?:==|!=|>=|<=|>|<)\s*.+\s*', args[1]):
        raise D.EvidenceError('csv acceptance needs a column comparison')
    if kind == 'commit' and not re.fullmatch(r'[0-9a-fA-F]{40}', args[0]):
        raise D.EvidenceError('commit acceptance requires a full immutable commit hash')
    if kind == 'task' and not re.fullmatch(r'[A-Za-z0-9_-]+', args[0]):
        raise D.EvidenceError('task acceptance requires a task id, not a path')
    if kind == 'file' and len(args) == 2: D.epoch(args[1])
    if any(not arg.strip() for arg in args):
        raise D.EvidenceError('acceptance arguments cannot be blank')
    return kind, args


def remote_contains(repo, sha):
    rc, refs, error = W.git(repo, 'ls-remote', '--heads', 'origin')
    if rc:
        raise D.EvidenceError('cannot query origin: %s' % error.strip())
    unknown = False
    for line in refs.splitlines():
        tip = line.split()[0]
        if tip == sha: return True
        rc, _, _ = W.git(repo, 'merge-base', '--is-ancestor', sha, tip)
        if rc == 0: return True
        if rc != 1: unknown = True
    if unknown:
        raise D.EvidenceError('live remote tips are absent from local history; ancestry is undecided')
    return False


def baseline(a, sess, spec):
    kind, args = parse(spec)
    snap = dict(kind=kind, args=args, captured_at=W.now_iso())
    if kind in ('file', 'grep', 'csv', 'row'):
        if kind == 'row' and not a.ledger:
            raise D.EvidenceError('row acceptance requires a configured ledger')
        snap['subject'] = file_snapshot(path_for(a, sess, a.ledger if kind == 'row' else args[0]))
    elif kind == 'commit':
        snap['remote_contains'] = remote_contains(a.repo or sess['cwd'], args[0])
    elif kind == 'task':
        snap['subject'] = file_snapshot(os.path.join(W.tasks_dir(sess), args[0] + '.output'))
        snap['exit'] = W.task_output_status(snap['subject']['path']).get('exit_code')
    return snap


_TOOL_RECORDS_CACHE = {}


def _tool_records(path, records):
    """Every record carrying tool blocks, parsed once per transcript version.

    `target_calls` is called once per archived item on every `owed` poll, and each
    call used to walk the WHOLE transcript and re-parse every record's content blocks.
    That is O(archived items x transcript length), and both only grow: measured
    2026-09-21, 28 archived items against ~71,000 records, and `owed` stopped
    completing at all -- over 400 s with no output, which also stalled the settle path,
    so a delivered item whose acceptance had come true could not close.

    The scan is hoisted here and keyed by transcript version. It changes nothing about
    what is inspected: the same records, the same block parsing, in the same order.
    The per-call work that REMAINS is the part that depends on the boundary -- the
    window filter, the duplicate-id check and the success tests -- which is why those
    stay in `target_calls` rather than being precomputed into this index.
    """
    # KEYED ON RECORD IDENTITY, not on the list object. The first version compared
    # `hit[0] is records`, and `read_records` returns a FRESH list over the same record
    # dicts on every call, so the comparison could never be true: it rebuilt 28 times for
    # 28 calls and the optimisation did nothing. Caught by an independent evaluation, not
    # by its own test, which compared outputs and so passed while the cache never served.
    key = (path, len(records))
    hit = _TOOL_RECORDS_CACHE.get(key)
    if hit is not None and (not records or (hit[0] is records[0] and hit[1] is records[-1])):
        return hit[2]
    parsed = []
    for record in records:
        role = record.get('type')
        block_kind = {'assistant': 'tool_use', 'user': 'tool_result'}.get(role)
        if not block_kind:
            continue
        blocks = W._blocks((record.get('message') or {}).get('content'), block_kind)
        if not blocks:
            continue
        parsed.append((role, record.get('timestamp'), blocks))
    _TOOL_RECORDS_CACHE.clear()
    _TOOL_RECORDS_CACHE[key] = (records[0] if records else None,
                                records[-1] if records else None, parsed)
    return parsed


def target_calls(sess, after):
    """Successful post-receipt calls, checking duplicate IDs only in that window.

    Inspect every record: replayed old records can appear later in the file, so
    transcript order is not a time boundary. Unknown tool timestamps cannot be
    assumed historical. The boundary is inclusive for integrity checks; action
    credit still requires the call to be strictly later than the receipt.
    """
    boundary = D.epoch(after)
    path = W.transcript_path(sess)
    records = D.read_records(path)
    uses = {}
    complete = []
    for role, stamp, blocks in _tool_records(path, records):
        if D.epoch(stamp) < boundary:
            continue
        if role == 'assistant':
            for b in blocks:
                ident = b.get('id')
                if ident in uses:
                    raise D.EvidenceError('duplicate target tool-use id %r in receipt window at or after %s' % (ident, after))
                uses[ident] = dict(id=ident, name=b.get('name'), input=b.get('input') or {}, ts=stamp)
        elif role == 'user':
            for b in blocks:
                use = uses.get(b.get('tool_use_id'))
                if not use: continue
                if use['name'] in W.MESSAGE_TOOL_NAMES:
                    if b.get('is_error', False) is not False:
                        continue
                    if use['name'] == 'SendMessage':
                        if not W.message_success(W._result_text(b)): continue
                    elif b.get('is_error') is not False and not W.legacy_message_success(use, W._result_text(b)):
                        continue
                elif b.get('is_error', False) is not False:
                    # The host OMITS is_error on successful Edit/Write/Read results and marks
                    # failures with an explicit true (measured 2026-09-18 on the target: 140 Edit
                    # and 98 Write results with no key, every failure is_error=true). Requiring
                    # an explicit False discarded every real Edit and Write, so no file/grep/csv
                    # acceptance could ever be proved by the replay path. Absent is success;
                    # only an explicit non-False value is failure.
                    continue
                if D.epoch(use['ts']) <= boundary or D.epoch(stamp) < D.epoch(use['ts']):
                    continue
                complete.append(dict(use, result=W._result_text(b), result_ts=stamp))
    return complete


def exact_git_call(call, repo, verb, expected_sha=None):
    """Only direct git argv, never a shell string containing a quoted mention."""
    if call['name'] != 'Bash': return False
    command = call['input'].get('command') or ''
    if re.search(r'[\n;&|<>`$]', command): return False
    try: args = shlex.split(command)
    except ValueError: return False
    if not args or os.path.basename(args.pop(0)) != 'git': return False
    if args[:1] == ['-C']:
        if len(args) < 3 or os.path.realpath(args[1]) != os.path.realpath(repo): return False
        args = args[2:]
    if not args or args[0] != verb: return False
    if verb == 'push':
        # Reject dry-run/no-op and alternate repositories/remotes; require explicit origin.
        return (len(args) >= 3 and args[1] == 'origin' and all(not x.startswith('-') for x in args[2:])
                and expected_sha is not None
                and any(re.fullmatch(re.escape(expected_sha) + r':refs/heads/[^\s:]+', x, re.I) for x in args[2:]))
    return True


def attributed_file(a, sess, item, args, calls):
    before = item['acceptance_baseline']['subject']
    path = path_for(a, sess, a.ledger if item['acceptance_baseline']['kind'] == 'row' else args[0])
    if path != before['path']:
        raise D.EvidenceError('acceptance path now resolves to a different subject')
    current = file_snapshot(path)
    if not current['exists']:
        return False, current
    if current.get('is_dir'):
        return attributed_directory(a, sess, item, before, current, calls)
    if current['mtime'] <= D.epoch(item['sent']):
        return False, current
    if current['sha256'] == before['sha256']:
        return False, current
    if script_wrote(sess, path, current['mtime'], calls):
        return True, current
    content = base64.b64decode(before['content']) if before['content'] is not None else None
    proved = False
    for call in calls:
        inp = call['input']
        if call['name'] not in ('Write', 'Edit') or not isinstance(inp.get('file_path'), str): continue
        if path_for(a, sess, inp['file_path']) != path: continue
        if call['name'] == 'Write' and isinstance(inp.get('content'), str):
            content = inp['content'].encode('utf-8')
            proved = True
        elif call['name'] == 'Edit' and content is not None:
            old, new = inp.get('old_string'), inp.get('new_string')
            if not isinstance(old, str) or not old or not isinstance(new, str): continue
            old, new = old.encode(), new.encode()
            replace_all = inp.get('replace_all', False)
            if type(replace_all) is not bool: continue
            if (replace_all and old in content) or content.count(old) == 1:
                content = content.replace(old, new, -1 if replace_all else 1)
                proved = True
    return proved and content is not None and hashlib.sha256(content).hexdigest() == current['sha256'], current


def require_bool(facts, name):
    if type(facts.get(name)) is not bool:
        raise D.EvidenceError('missing or mistyped boolean fact: %s' % name)
    return facts[name]


def require_count(facts, name):
    if type(facts.get(name)) is not int or facts[name] < 0:
        raise D.EvidenceError('missing or mistyped count fact: %s' % name)
    return facts[name]


def evaluate_file(a, sess, item, args, facts, calls):
    if not require_bool(facts, 'exists'): return Result(Status.NOT_YET, 'file does not exist')
    if not require_bool(facts, 'modified_since'): return Result(Status.NOT_YET, 'file predates required time')
    proved, current = attributed_file(a, sess, item, args, calls)
    return Result(Status.PASS if proved else Status.NOT_YET,
                  'target write verified: %s sha256=%s' % (current['path'], current['sha256']) if proved else 'no new target-attributed file contents')


def background_completions(sess):
    """task id -> earliest completion timestamp, from the host's own task notifications."""
    done = {}
    for record in D.read_records(W.transcript_path(sess)):
        text = json.dumps(record, ensure_ascii=False)
        if '<task-notification' not in text or '<status>completed</status>' not in text:
            continue
        for task in re.findall(r'<task-id>([^<]+)</task-id>', text):
            stamp = record.get('timestamp')
            if stamp and (task not in done or D.epoch(stamp) < D.epoch(done[task])):
                done[task] = stamp
    return done


BACKGROUND_RE = re.compile(r'Command running in background with ID: (\S+?)\.')
SCRIPT_SLACK_S = 1.0


def script_wrote(sess, path, mtime, calls, completions=None):
    """A file the target produced with a command rather than Write/Edit.

    Write/Edit attribution replays the exact bytes; a script's output cannot be
    replayed, so this is weaker and is stated as such. It requires ALL of: a
    successful target Bash call after delivery whose command names both the
    file's directory and its file name as literal text, and the file's mtime
    inside that call's own execution window -- from the call to its result, or
    for a backgrounded command to the host's completion notice for that task.
    A mention alone never credits: `ls` of the path outside the write window
    does not satisfy the timing half. SCRIPT_SLACK_S covers filesystem mtime
    granularity at the close of the window, never its start.
    """
    names = {os.path.basename(path)}
    dirs = {os.path.dirname(path), os.path.dirname(os.path.realpath(path))}
    for d in list(dirs):
        if d.startswith('/private/'): dirs.add(d[len('/private'):])
    for call in calls:
        if call['name'] != 'Bash': continue
        command = call['input'].get('command') or ''
        if not any(n in command for n in names) or not any(d in command for d in dirs): continue
        start = D.epoch(call['ts'])
        background = BACKGROUND_RE.search(call.get('result') or '')
        if background:
            if completions is None: completions = background_completions(sess)
            end_ts = completions.get(background.group(1))
            if not end_ts: continue
            end = D.epoch(end_ts)
        else:
            end = D.epoch(call['result_ts'])
        if start <= mtime <= end + SCRIPT_SLACK_S:
            return True
    return False


def attributed_directory(a, sess, item, before, current, calls):
    """A directory subject passes when a file inside it is new or changed since the
    pre-delivery baseline, written after delivery, and attributed to the target --
    by Write/Edit replay or by the script rule above. Untouched files never credit."""
    old = before.get('children') or {}
    sent = D.epoch(item['sent'])
    completions = None
    for rel, (size, mtime) in sorted(current['children'].items()):
        if old.get(rel) == [size, mtime] or mtime <= sent: continue
        full = os.path.join(current['path'], rel)
        if any(c['name'] in ('Write', 'Edit') and isinstance(c['input'].get('file_path'), str)
               and path_for(a, sess, c['input']['file_path']) == os.path.realpath(full) for c in calls):
            return True, dict(current, path=full, sha256=file_digest(full))
        if completions is None: completions = background_completions(sess)
        if script_wrote(sess, full, mtime, calls, completions):
            return True, dict(current, path=full, sha256=file_digest(full))
    return False, current


def file_digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''): h.update(chunk)
    return h.hexdigest()


def previous_text(item):
    raw = item['acceptance_baseline']['subject']['content']
    return base64.b64decode(raw).decode('utf-8') if raw is not None else ''


def evaluate_grep(a, sess, item, args, facts, calls):
    if facts.get('exists') is False: return Result(Status.NOT_YET, 'file does not exist')
    count = require_count(facts, 'hits')
    proved, current = attributed_file(a, sess, item, args, calls)
    now = base64.b64decode(current['content']).decode() if current['content'] is not None else ''
    hits = lambda text: {line for line in text.splitlines() if re.search(args[1], line, re.I)}
    changed_matches = hits(now) - hits(previous_text(item))
    return Result(Status.PASS if count and proved and changed_matches else Status.NOT_YET,
                  '%s; %d matching line(s), %d new matching line(s)' % (current['path'], count, len(changed_matches)))


def csv_matches(text, expr):
    match = re.fullmatch(r'\s*([\w.]+)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*', expr)
    col, op, expected = match.groups()
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or col not in reader.fieldnames:
        raise D.EvidenceError('CSV comparison column is absent')
    hits = set()
    for row in reader:
        if row.get(col) is None or None in row:
            raise D.EvidenceError('malformed CSV row')
        value = row[col]
        try: left, right = float(value), float(expected)
        except ValueError: left, right = value, expected
        if isinstance(left, float) and not (math.isfinite(left) and math.isfinite(right)):
            raise D.EvidenceError('CSV numeric comparison is non-finite')
        if op not in ('==', '!=') and isinstance(left, str):
            raise D.EvidenceError('ordered CSV comparison requires numbers')
        matched = {'==': left == right, '!=': left != right, '>': left > right,
                   '<': left < right, '>=': left >= right, '<=': left <= right}[op]
        if matched: hits.add(json.dumps(row, sort_keys=True))
    return hits


def evaluate_csv(a, sess, item, args, facts, calls):
    if facts.get('exists') is False: return Result(Status.NOT_YET, 'CSV does not exist')
    count = require_count(facts, 'matched')
    require_count(facts, 'total')
    proved, current = attributed_file(a, sess, item, args, calls)
    now = base64.b64decode(current['content']).decode() if current['content'] is not None else ''
    before = previous_text(item)
    added = csv_matches(now, args[1]) - (csv_matches(before, args[1]) if before else set())
    return Result(Status.PASS if count and proved and added else Status.NOT_YET,
                  '%s; %d matching row(s), %d newly matching row(s)' % (current['path'], count, len(added)))


def evaluate_row(a, sess, item, args, facts, calls):
    if not require_bool(facts, 'present'): return Result(Status.NOT_YET, 'required ledger row is absent')
    if not isinstance(facts.get('hash'), str) or not facts['hash']:
        raise D.EvidenceError('row hash is missing')
    proved, current = attributed_file(a, sess, item, args, calls)
    old_lines = previous_text(item).splitlines()
    now_lines = base64.b64decode(current['content']).decode().splitlines() if current['content'] else []
    row = lambda lines: [line.strip() for line in lines if re.match(r'^\|\s*' + re.escape(args[0]) + r'\s*\|', line)]
    return Result(Status.PASS if proved and row(now_lines) and row(now_lines) != row(old_lines) else Status.NOT_YET,
                  '%s row %s must be created or changed by the target' % (current['path'], args[0]))


def evaluate_commit(a, sess, item, args, facts, calls):
    if not require_bool(facts, 'exists'): return Result(Status.NOT_YET, 'commit object is absent')
    before = item['acceptance_baseline'].get('remote_contains')
    if type(before) is not bool: raise D.EvidenceError('remote baseline is missing')
    if before: return Result(Status.NOT_YET, 'commit was already on origin before the request')
    repo = a.repo or sess['cwd']
    pushed = any(exact_git_call(c, repo, 'push', args[0]) for c in calls)
    on_remote = remote_contains(repo, args[0])
    return Result(Status.PASS if pushed and on_remote else Status.NOT_YET,
                  'target push after delivery=%s; live origin contains %s=%s' % (pushed, args[0], on_remote))


def evaluate_task(a, sess, item, args, facts, calls):
    if 'exit' not in facts or (facts['exit'] is not None and type(facts['exit']) is not int):
        raise D.EvidenceError('task exit fact is missing or mistyped')
    if facts['exit'] != 0: return Result(Status.NOT_YET, 'task has not completed successfully')
    baseline = item['acceptance_baseline']
    if 'exit' not in baseline or (baseline['exit'] is not None and type(baseline['exit']) is not int):
        raise D.EvidenceError('task baseline exit is missing or mistyped')
    if baseline['exit'] is not None:
        return Result(Status.NOT_YET, 'task was already finished before the request')
    records = D.read_records(W.transcript_path(sess))
    launches = [x for turn in W.split_turns(records) for x in turn.background_launches(sess.get('cli'))]
    expected = os.path.realpath(os.path.join(W.tasks_dir(sess), args[0] + '.output'))
    launched = any(x['task_id'] == args[0] and os.path.realpath(x['output_file']) == expected for x in launches)
    notified = False
    for r in records:
        if (r.get('type') != 'user' or (r.get('origin') or {}).get('kind') != 'task-notification'
                or D.epoch(r.get('timestamp')) <= D.epoch(item['sent'])): continue
        text = W._text_of((r.get('message') or {}).get('content'))
        if (W._grab(r'<task-id>([^<]+)</task-id>', text) == args[0]
                and W._grab(r'<status>([^<]+)</status>', text) == 'completed'):
            notified = True
    return Result(Status.PASS if launched and notified else Status.NOT_YET,
                  'session-scoped launch=%s; post-delivery harness completion=%s' % (launched, notified))


def unique_reply_turn(sess, calls):
    """Credit only one completed turn containing every qualifying candidate call.

    If multiple calls could explain a delivery, choosing one would be a guess.
    Old uses of an ID outside the call's timestamp do not identify this call.
    A still-open turn or an ambiguous end timestamp earns no turn exemption.
    """
    turns = W.split_turns(D.read_records(W.transcript_path(sess)))
    matched = []
    for call in calls:
        candidates = [t for t in turns if any(u.get('id') == call['id'] and u.get('ts') == call['ts']
                                             for u in t.tool_uses)]
        if len(candidates) != 1 or candidates[0].end_state != 'end_turn': return None
        matched.append(candidates[0])
    if not matched or any(t is not matched[0] for t in matched): return None
    stamp = matched[0].end_ts
    if sum(t.end_ts == stamp for t in turns) != 1: return None
    if any(D.epoch(stamp) < D.epoch(c['result_ts']) for c in calls): return None
    return stamp


def evaluate_message(a, sess, item, args, facts, calls):
    require_count(facts, 'count')
    sender = W.find_session(a.self_sel)
    expected = args[0]
    sent = [c for c in calls if W.message_to_session(c, sender['sessionId'])
            and expected in W.message_input(c)['message'].splitlines()]
    if not sent: return Result(Status.NOT_YET, 'no successful matching target reply call after delivery')
    # A match requires rec['ts'] >= c['ts'] for some candidate call, so a record older than
    # the EARLIEST candidate cannot match whatever it contains. Skipping those before the
    # expensive parse is a pure speedup: same records, same order, same result -- only the
    # provably-hopeless ones stop paying for delivery(). Measured 2026-09-21: `owed` spent
    # 120 s of its 171 s inside 1,280,091 delivery() calls, 27 acceptances each walking the
    # whole 118 MB sender transcript. Records with no readable timestamp are NOT skipped;
    # absorbed deliveries are read by delivery() itself and must still reach it.
    earliest = min(D.epoch(c['ts']) for c in sent)
    for record in D.read_records(W.transcript_path(sender)):
        stamp = record.get('timestamp')
        if isinstance(stamp, str) and stamp:
            try:
                if D.epoch(stamp) < earliest: continue
            except D.EvidenceError: pass
        try: rec = D.delivery(record, sess['sessionId'])
        except D.EvidenceError: continue
        matching = [c for c in sent if rec['body'] == W.message_input(c)['message'].strip()
                    and D.epoch(rec['ts']) >= D.epoch(c['ts'])]
        if expected in rec['body'].splitlines() and matching:
            turn = unique_reply_turn(sess, matching)
            detail = ('; qualifying reply turn %s' % turn if turn else
                      '; no unique completed reply turn: relay remains blocking')
            return Result(Status.PASS, 'matching reply delivered to watchdog in record %s%s' % (rec['id'], detail), turn)
    return Result(Status.NOT_YET, 'target reply has not been delivered to the watchdog')


KINDS = {
    'file': Kind(1, 2, evaluate_file),
    'grep': Kind(2, 2, evaluate_grep),
    'csv': Kind(2, 3, evaluate_csv),
    'row': Kind(1, 1, evaluate_row),
    'commit': Kind(1, 1, evaluate_commit),
    'task': Kind(1, 1, evaluate_task),
    'msg-to-watchdog': Kind(1, 1, evaluate_message),
}


def evaluate(a, sess, state, item):
    try:
        kind, args = parse(item.get('acted_when'))
        snap = item.get('acceptance_baseline')
        if not isinstance(snap, dict) or snap.get('kind') != kind or snap.get('args') != args:
            raise D.EvidenceError('missing or inconsistent pre-delivery acceptance baseline')
        if D.epoch(snap.get('captured_at')) >= D.epoch(item.get('sent')):
            raise D.EvidenceError('acceptance baseline does not precede delivery')
        rec = D.receipt(W.transcript_path(sess), a.self_sel, item.get('message_id'))
        binding = (state.get('send_receipts') or {}).get(rec['id'])
        if not binding or binding.get('queue_id') != item.get('id') or rec['ts'] != item['sent']:
            raise D.EvidenceError('item lacks a matching registered delivery receipt')
        from wd_check import check
        check_args = list(args)
        if kind == 'file':
            since = max([rec['ts']] + args[1:], key=D.epoch)
            check_args = [args[0], since]
        elif kind == 'msg-to-watchdog':
            check_args = [rec['ts']]
        checked, description, facts = check(a, sess, kind, check_args, state)
        if not isinstance(facts, dict): raise D.EvidenceError('check did not return a fact object')
        calls = target_calls(sess, rec['ts'])
        value = KINDS[kind].evaluate(a, sess, item, args, facts, calls)
        if not isinstance(value, Result) or not isinstance(value.status, Status):
            raise D.EvidenceError('acceptance did not return a typed decision')
        return Result(value.status, '%s: %s; %s' % (checked, description, value.evidence), value.reply_turn_ts)
    except (Exception, SystemExit) as exc:
        return Result(Status.UNDECIDED, '%s: %s' % (type(exc).__name__, exc))
