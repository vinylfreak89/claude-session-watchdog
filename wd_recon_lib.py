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
                c = inp.get('command', '')
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


def adjudicate(actions, art):
    """Per action: is the artifact that must exist, there? Undecidable stays undecidable."""
    out = []
    for a in actions:
        v, verdict, why = a['verb'], 'unchecked', ''
        if v in NEEDS_SEND:
            near = _near(art['sends'], a['ts'])
            if a.get('arg') and v == 'sent1':
                hit = [s for s in near if a['arg'] in s['msg'][:4000]]
                verdict, why = ('ok', 'send carries %s' % a['arg']) if hit else \
                               ('ok-nearby', '%d send(s) near, id not matched' % len(near)) if near else \
                               ('MISSTEER', 'no send within 15 min of sent1')
            else:
                verdict, why = ('ok', '%d send(s) near' % len(near)) if near else \
                               ('MISSTEER', 'no send within 15 min')
        elif v in NEEDS_RELAY_TEXT:
            near = _near(art['my_text'], a['ts'], before_s=1800, after_s=300)
            big = [x for x in near if len(x['text']) > 400]
            verdict, why = ('ok', '%d relay-sized reply/ies' % len(big)) if big else \
                           ('MISSTEER', 'no substantial reply to the owner around `relayed`')
        elif v in NEEDS_TARGET_REPLY:
            near = _near(art['target'], a['ts'], before_s=3600, after_s=0)
            verdict, why = ('ok', '%d target turn(s) before' % len(near)) if near else \
                           ('MISSTEER', 'no target output before resolving')
        out.append(dict(a, verdict=verdict, why=why))
    return out
