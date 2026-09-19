#!/usr/bin/env python3
"""SessionStart hook: tell the WATCHDOG session (only) to reload its skill via the Skill tool.

Skills load on invocation and live in that context alone, so a compaction drops the
one document defining this session's role -- and a compacted session resumes on a
monitor event, where nobody is there to say "reload the skill". Measured 2026-09-17/18:
the loop ran for over a day without it, composing messages by hand and redesigning
constraint-layer code without knowing that territory was off limits.

Identity is DERIVED, never stored: the hook asks this repo's own config which transcript
belongs to `self`, and emits only when that is the session firing. Every other session --
the target included -- gets nothing. A marker file would go stale the moment the watchdog
session changed; this cannot.

WHAT THIS DOES NOT COVER. A brand-new watchdog session is not yet `self`: config.json
still names the previous one until the bootstrap step writes it. So the first load is
still the bootstrap's job (`get_session self` -> config.json, then invoke the skill).
This hook keeps it loaded from then on. `startup` is accepted so a plain restart of an
already-configured watchdog is covered too.

Fails silent and open by design: a hook must never be able to break a session start.
Do NOT rewrite this as `python3 - <<'PY'`. That form reads the PROGRAM from stdin, so
the hook's own JSON is consumed as source and the check silently never fires -- it tests
as a clean exit 0 with no output, indistinguishable from "not the watchdog".
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, 'skills', 'watchdog', 'SKILL.md')
SOURCES = ('compact', 'resume', 'startup')


def main():
    try:
        hook = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(hook, dict) or hook.get('source') not in SOURCES:
        return 0
    here = os.path.realpath(hook.get('transcript_path') or '')
    if not here:
        return 0
    try:
        sys.path.insert(0, ROOT)
        import wd_lib as W
        cfg = json.load(open(os.path.join(ROOT, 'config.json'), encoding='utf-8'))
        mine = os.path.realpath(W.transcript_path(W.find_session(cfg['self'])))
        body = open(SKILL, encoding='utf-8').read()  # existence check only
    except Exception:
        return 0
    if here != mine or not body.strip():
        return 0
    # The skill body is NOT emitted. Measured 2026-09-19 on the first live compact: the hook
    # fired and wrote ~49.6 KB, the harness persisted anything over its output cap to a file
    # and put only a 2 KB preview in context -- so the "re-injected IN FULL" skill never
    # loaded. The Skill tool has no such cap, so the hook's whole job is a short directive
    # to call it, kept far under the cap (asserted by tests/test_skill_reload_hook.py).
    sys.stdout.write(
        'WATCHDOG SKILL NOT LOADED. This session is the orchestration watchdog for the session '
        'named in %s, and it was just %s, which dropped the watchdog skill from context. Your '
        'FIRST action, before acting on any monitor event, running wd.sh or sending anything to '
        'the target, is to invoke the Skill tool with skill "watchdog". Do this even mid-task and '
        'even if a summary says the skill was loaded earlier: an earlier load is gone.\n'
        % (os.path.join(ROOT, 'config.json'),
           {'compact': 'compacted', 'resume': 'resumed', 'startup': 'started'}[hook['source']]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
