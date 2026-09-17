"""Each agent says what it currently owes. Every later entry supersedes -- and thereby
answers -- the one before it.

There is no open/account pair, because they were the same act described twice: an agent
saying where it stands. There is no refusal either. The mechanism is that the newest list
is printed on every `owed` poll, next to the one it replaced, until the agent writes a new
one saying what became of it. Visibility is the whole of it.

Deliberately NO machine comparison between consecutive lists. Review established that exact
comparison flags an honest paraphrase while similarity matching lets a narrower task be
substituted for the one promised; the comparison a machine can do is not the one that
matters. A person reads them.

Abandonment is a complete answer -- "I said I would do this, I am not going to, because X"
-- but it has to be written, and nothing is ever overwritten, so an abandonment made while
the owner was away is still there when he gets back.

Symmetric by the owner's ruling (2026-09-18, "it should apply symmetrically"): every agent
keeps its own chain and none is exempt.
"""
import wd_lib as W


def _log(state):
    return state.setdefault('promise_log', [])


def say(state, agent, text):
    """Append what this agent owes now. Supersedes its previous entry."""
    if not agent:
        raise ValueError('an agent name is required; a promise nobody made is not a promise')
    lines = [l.strip().lstrip('-').strip() for l in (text or '').splitlines()]
    lines = [l for l in lines if l]
    if not lines:
        raise ValueError('an empty list is not an answer; say "nothing outstanding" if that is true')
    entry = dict(ts=W.now_iso(), agent=agent, open=lines)
    _log(state).append(entry)
    return entry


def current(state):
    """The newest entry per agent -- what each one last said it owed."""
    latest = {}
    for e in _log(state):
        latest[e['agent']] = e
    return [latest[k] for k in sorted(latest)]


def render(state, agent=None, limit=6):
    """Consecutive entries, so a person can see what changed and judge it."""
    rows = [e for e in _log(state) if agent is None or e['agent'] == agent]
    if not rows:
        return 'nothing said yet'
    out = []
    for e in rows[-limit:]:
        out.append('%s  %s' % (e['ts'], e['agent']))
        for line in e['open']:
            out.append('    %s' % line)
        out.append('')
    return '\n'.join(out).rstrip()
