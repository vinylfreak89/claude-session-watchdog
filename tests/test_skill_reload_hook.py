"""The reload hook's output must fit in context, and must reach only the watchdog session.

2026-09-19: the hook emitted the whole skill (~49.6 KB); the harness persisted it to a file
and showed a 2 KB preview, so the skill silently never loaded after a compaction.
"""
import json, os, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, 'hooks', 'session_start_skill_reload.py')
sys.path.insert(0, ROOT)
import wd_lib as W

CAP = 2000  # the preview size the harness kept; stay under it with margin


def run(transcript, source='compact'):
    p = subprocess.run([HOOK], input=json.dumps({'source': source, 'transcript_path': transcript}),
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return p.stdout


def main():
    cfg = json.load(open(os.path.join(ROOT, 'config.json')))
    mine = W.transcript_path(W.find_session(cfg['self']))
    for src in ('compact', 'resume', 'startup'):
        out = run(mine, src)
        assert out.strip(), 'no directive for the watchdog on %s' % src
        assert len(out.encode()) < CAP, 'directive is %d bytes, over the cap' % len(out.encode())
        assert 'Skill tool' in out and '"watchdog"' in out, out
    assert run(mine, 'clear') == ''
    other = os.path.join(os.path.dirname(mine), 'not-the-watchdog.jsonl')
    assert run(other) == '', 'hook fired for a session that is not self'
    print('ok: directive %d bytes' % len(run(mine).encode()))


if __name__ == '__main__':
    main()
