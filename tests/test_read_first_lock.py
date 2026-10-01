#!/usr/bin/python3
"""Control: read-first modes run without the state lock, and NEVER write without it.

Measured 2026-10-01 (Codex, /tmp/wd-perf-oct01): `owed` held the exclusive state lock for
minutes while parsing transcripts, and a 0.38 s `relayed` sat in flock behind it. owed/next/check
(and wd_wake's due/queue-list/owe-list/--no-state) now run first unlocked; the first write aborts
that run and the whole mode re-runs under the lock.

The property worth having is the forbidden half: an unlocked run must not write. Each control
below ATTEMPTS a write by a different route and requires the refusal:
  1. a pure read completes while another process holds the lock (the point of the change);
  2. save_state aborts the run, nothing reaches disk, its printed output is discarded;
  3. a write inside `except Exception` still aborts (acceptance evaluation catches Exception);
  4. a writer called through a SECOND module object (wd_wake run as __main__) is also refused;
  5. through main(): a mode that writes ends up writing INSIDE the transaction, never before;
  6. every function in wd_wake that writes a file is on the refusal list.
"""
import ast, fcntl, io, os, subprocess, sys, tempfile, types, contextlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import wd_check as C, wd_wake as WK, wd_state as S

fails = 0
def check(name, ok, got=''):
    global fails
    fails += not ok
    print('%-64s : %s' % (name, 'PASS' if ok else 'FAIL  got=%r' % (got,)))

d = tempfile.mkdtemp()
WK.save_state(d, {'x': 1})
before = open(os.path.join(d, 'state.json')).read()

# 1. Another process holds the exclusive lock until we tell it to let go. A read-first body must
#    finish while it is held; the holder only releases after we have the result.
holder = subprocess.Popen([sys.executable, '-c',
    'import fcntl,sys; f=open(sys.argv[1],"a"); fcntl.flock(f.fileno(), fcntl.LOCK_EX); '
    'print("held", flush=True); sys.stdin.readline()', os.path.join(d, '.writer.lock')],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
assert holder.stdout.readline().strip() == 'held'
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = C.run_read_first(lambda: (print('report'), 7)[1])
check('1. pure read completes while another process holds the lock', rc == 7 and buf.getvalue() == 'report\n', (rc, buf.getvalue()))
holder.stdin.write('\n'); holder.stdin.flush(); holder.wait()

# 2. a save aborts: NEEDS_LOCK, file untouched, output discarded
def writes():
    print('partial output that must not appear')
    WK.save_state(d, {'x': 2})
    return 0
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = C.run_read_first(writes)
check('2. save_state aborts the unlocked run', rc is C.NEEDS_LOCK, rc)
check('2. ... and nothing reached disk', open(os.path.join(d, 'state.json')).read() == before)
check('2. ... and its printed output was discarded', buf.getvalue() == '', buf.getvalue())
check('2. ... and the real writer is restored afterwards', WK.save_state is not None and WK.save_state.__name__ == 'save_state')

# 3. a broad `except Exception` around the write cannot swallow the abort
def swallowed():
    try:
        WK.append_findings_md(d, [])
    except Exception:
        return 'swallowed'
    return 'returned'
rc = C.run_read_first(swallowed)
check('3. write inside `except Exception` still aborts', rc is C.NEEDS_LOCK, rc)

# 4. a second module object with its OWN save_state global (what `python3 wd_wake.py` is)
main_like = types.ModuleType('main_like')
exec('import wd_wake as _W\nsave_state = _W.save_state\ndef body(d):\n    save_state(d, {"x": 3})\n    return 0\n',
     main_like.__dict__)
rc = C.run_read_first(lambda: main_like.body(d))
check('4. unlisted second module: the write escapes (the defect)', rc == 0 and '"x": 3' in open(os.path.join(d, 'state.json')).read())
WK.save_state(d, {'x': 1})
rc = C.run_read_first(lambda: main_like.body(d), main_like)
check('4. listed second module: the write is refused', rc is C.NEEDS_LOCK, rc)
check('4. ... and nothing reached disk', open(os.path.join(d, 'state.json')).read() == before)
WK.save_state(d, {'x': 1})

# 5. through main(): every write of a read-first mode happens inside the transaction
locked = [False]; writes_seen = []
real_tx, real_run, real_save = S.transaction, C.run, WK.save_state
@contextlib.contextmanager
def tx(directory):
    locked[0] = True
    try:
        yield
    finally:
        locked[0] = False
def recording_save(directory, state):
    writes_seen.append(locked[0]); real_save(directory, state)
def run_that_writes(a, ap):
    WK.save_state(a.state_dir, {'x': 5}); print('wrote'); return 0
S.transaction, C.run, WK.save_state = tx, run_that_writes, recording_save
sys.argv = ['wd_check.py', '--target', 't', '--state-dir', d, 'owed']
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf):
        rc = C.main()
finally:
    S.transaction, C.run, WK.save_state = real_tx, real_run, real_save
check('5. owed that writes: exactly one write, made under the lock', writes_seen == [True], writes_seen)
check('5. ... its output printed once, from the locked run', buf.getvalue() == 'wrote\n' and rc == 0, buf.getvalue())

# 6. every wd_wake function that writes a file is refused in a read-first run
src = open(os.path.join(HERE, '..', 'wd_wake.py')).read()
writers = set()
for node in ast.walk(ast.parse(src)):
    if isinstance(node, ast.FunctionDef) and node.col_offset == 0:
        body = ast.get_source_segment(src, node)
        if "'w'" in body or "'a'" in body or 'os.replace' in body:
            writers.add(node.name)
unlisted = sorted(writers - set(C.STATE_WRITERS))
check('6. every top-level wd_wake file writer is on STATE_WRITERS', not unlisted, unlisted)

print('RESULT: %s' % ('FAIL (%d)' % fails if fails else 'PASS'))
sys.exit(1 if fails else 0)
