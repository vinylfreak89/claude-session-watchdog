"""Every agent says what it owes, and must account for it before it may speak again.

There is deliberately NO mechanical correspondence check between a promise and its
account. That was tried in review and it fails both ways: exact comparison flags an
honest paraphrase, and similarity matching lets a narrower task be substituted for
the one promised. The comparison a machine can do here is not the comparison that
matters.

What is mechanical is narrow and it is the part that bites:

  - you cannot open a new list while your last one is unaccounted for;
  - an unaccounted list is OWED and is nagged until it is answered;
  - nothing is overwritten, so an abandonment made while the owner was away is
    still there when he gets back.

The accountability is that the words are retained and put in front of a person,
side by side, in the agent's own voice. Abandoning a promise is allowed and is a
complete account -- but it has to be SAID, and it stays said. Making avoidance
visible is the mechanism, not a hole in it.

Symmetric by the owner's ruling (2026-09-18, "it should apply symmetrically"):
every agent keeps its own chain, and no agent audits another's while keeping its
own private.
"""
import wd_lib as W


def _log(state):
    return state.setdefault('promise_log', [])


def chain(state, agent):
    return [e for e in _log(state) if e.get('agent') == agent]


def unaccounted(state, agent=None):
    """Entries still owing an account. The nagger's question, and the gate's."""
    return [e for e in _log(state)
            if (agent is None or e.get('agent') == agent) and e.get('account') is None]


def _lines(text):
    out = [l.strip().lstrip('-').strip() for l in (text or '').splitlines()]
    return [l for l in out if l]


def open_list(state, agent, text):
    """Record what this agent currently owes. Refused while the last list is unanswered."""
    if not agent:
        raise ValueError('an agent name is required; a promise nobody made is not a promise')
    owing = unaccounted(state, agent)
    if owing:
        raise ValueError('account for the previous list first (opened %s, %d line(s))'
                         % (owing[0]['ts'], len(owing[0]['open'])))
    lines = _lines(text)
    entry = dict(ts=W.now_iso(), agent=agent, open=lines, account=None, account_ts=None)
    _log(state).append(entry)
    return entry


def account(state, agent, text):
    """Answer the open list in the agent's own words. Never overwrites an earlier one."""
    owing = unaccounted(state, agent)
    if not owing:
        raise ValueError('nothing open for %s to account for' % agent)
    lines = _lines(text)
    if not lines:
        raise ValueError('an empty account is not an account')
    entry = owing[0]
    entry['account'] = lines
    entry['account_ts'] = W.now_iso()
    return entry


def render(state, agent=None, limit=5):
    """Promise beside account, for a person to read. The judging happens here, by them."""
    rows = [e for e in _log(state) if agent is None or e.get('agent') == agent]
    if not rows:
        return 'no promises recorded'
    out = []
    for e in rows[-limit:]:
        out.append('%s  %s' % (e['ts'], e['agent']))
        for line in e['open']:
            out.append('    SAID: %s' % line)
        if e['account'] is None:
            out.append('    ---- NOT YET ACCOUNTED FOR ----')
        else:
            for line in e['account']:
                out.append('    THEN: %s' % line)
        out.append('')
    return '\n'.join(out).rstrip()
