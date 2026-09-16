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

import wd_lib as W
import wd_receipts as D

class Status(str, Enum):
    PASS = 'pass'
    NOT_YET = 'not-yet'
    UNDECIDED = 'undecided'

@dataclass(frozen=True)
class Result:
    status: Status
    evidence: str

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
    try:
        with open(path, 'rb') as f:
            data = f.read()
            stat = os.fstat(f.fileno())
        return dict(path=path, exists=True, sha256=hashlib.sha256(data).hexdigest(),
                    content=base64.b64encode(data).decode(), mtime=stat.st_mtime)
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


def target_calls(sess, after):
    """Successful tool calls with both call and result after the receipt, from the target."""
    records = D.read_records(W.transcript_path(sess))
    uses = {}
    complete = []
    for record in records:
        content = (record.get('message') or {}).get('content')
        if record.get('type') == 'assistant':
            for b in W._blocks(content, 'tool_use'):
                ident = b.get('id')
                if ident in uses:
                    raise D.EvidenceError('duplicate target tool-use id')
                uses[ident] = dict(id=ident, name=b.get('name'), input=b.get('input') or {}, ts=record.get('timestamp'))
        elif record.get('type') == 'user':
            for b in W._blocks(content, 'tool_result'):
                use = uses.get(b.get('tool_use_id'))
                if not use or b.get('is_error') is not False:
                    continue
                stamp = record.get('timestamp')
                if D.epoch(use['ts']) <= D.epoch(after) or D.epoch(stamp) < D.epoch(use['ts']):
                    continue
                complete.append(dict(use, result=W._result_text(b), result_ts=stamp))
    return complete


def exact_git_call(call, repo, verb):
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
        return len(args) >= 3 and args[1] == 'origin' and all(not x.startswith('-') for x in args[2:])
    return True


def attributed_file(a, sess, item, args, calls):
    before = item['acceptance_baseline']['subject']
    path = path_for(a, sess, a.ledger if item['acceptance_baseline']['kind'] == 'row' else args[0])
    if path != before['path']:
        raise D.EvidenceError('acceptance path now resolves to a different subject')
    current = file_snapshot(path)
    if not current['exists']:
        return False, current
    if current['mtime'] <= D.epoch(item['sent']):
        return False, current
    if current['sha256'] == before['sha256']:
        return False, current
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
    pushed = any(exact_git_call(c, repo, 'push') for c in calls)
    on_remote = remote_contains(repo, args[0])
    return Result(Status.PASS if pushed and on_remote else Status.NOT_YET,
                  'target push after delivery=%s; live origin contains %s=%s' % (pushed, args[0], on_remote))


def evaluate_task(a, sess, item, args, facts, calls):
    if 'exit' not in facts or (facts['exit'] is not None and type(facts['exit']) is not int):
        raise D.EvidenceError('task exit fact is missing or mistyped')
    if facts['exit'] != 0: return Result(Status.NOT_YET, 'task has not completed successfully')
    if item['acceptance_baseline'].get('exit') is not None:
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


def evaluate_message(a, sess, item, args, facts, calls):
    require_count(facts, 'count')
    sender = W.find_session(a.self_sel)
    expected = args[0]
    sent = [c for c in calls if c['name'] == 'mcp__ccd_session_mgmt__send_message'
            and c['input'].get('session_id') == sender['sessionId'] and c['input'].get('message') == expected]
    if not sent: return Result(Status.NOT_YET, 'no successful matching target reply call after delivery')
    for record in D.read_records(W.transcript_path(sender)):
        try: rec = D.delivery(record, sess['sessionId'])
        except D.EvidenceError: continue
        if rec['body'] == expected and any(D.epoch(rec['ts']) >= D.epoch(c['ts']) for c in sent):
            return Result(Status.PASS, 'matching reply delivered to watchdog in record %s' % rec['id'])
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
        return Result(value.status, '%s: %s; %s' % (checked, description, value.evidence))
    except (Exception, SystemExit) as exc:
        return Result(Status.UNDECIDED, '%s: %s' % (type(exc).__name__, exc))
