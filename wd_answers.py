"""Owner answers to a question the target is blocked on: the one item that may pass the OWED gate.

Owner, 2026-10-17 (watchdog transcript, after a deadlock cost 23 minutes of a time-boxed hardware
window): the target ended its turn asking him to plug in the Shuttle; he answered; the gate refused
the answer because the item the target was working on (Q318) was delivered but not yet accepted --
and it could not be accepted without the answer. He approved this boundary, as designed by an
independent review:

  ONLY an answer to a request the target itself made, while that request is still the target's
  latest completed turn, skips the "sent work not yet verified" clause. Everything else stays
  paced. The binding is two explicit verbs whose provenance the script checks; the model decides
  what words mean, the script never infers an answer.

  owe ask <turn end_ts> "<the target's request, verbatim>"   opens a D entry bound to that turn
  owe answer D<n> "<his words, verbatim>"                     queues a kind=answer item

Every passthrough is logged permanently and printed on every `owed` poll, so using this to skip
the gate is visible to him by default.
"""
import re

import wd_lib as W
import wd_receipts as D
import wd_turns as T


def _norm(text):
    return re.sub(r'\s+', ' ', text or '').strip()


def _turn_texts(turn):
    texts = [text for _, text in turn.assistant_texts]
    for use in turn.tool_uses:
        if use.get('name') in W.MESSAGE_TOOL_NAMES:
            message = W.message_input(use)
            if message is not None:
                texts.append(message['message'])
    return [t for t in texts if isinstance(t, str)]


def latest_completed_end(sess):
    """end_ts of the target's newest completed turn, or None."""
    turns = W.split_turns(D.read_records(W.transcript_path(sess)))
    done = [t for t in turns if t.end_state != 'open']
    return done[-1].end_ts if done else None


def open_ask(sess, state, stamp, quote, allocate):
    """Open a D entry bound to a completed target turn that asked the owner for something."""
    quote = (quote or '').strip()
    if not quote:
        raise D.EvidenceError('owe ask needs the target\'s request, quoted verbatim')
    turn = T.find_turn(sess, stamp)
    if turn.end_state != 'end_turn':
        raise D.EvidenceError('the turn must have completed with an end_turn, not %s' % turn.end_state)
    texts = _turn_texts(turn)
    if not any(_norm(quote) in _norm(t) for t in texts):
        raise D.EvidenceError('the quote does not appear verbatim in that turn\'s text')
    candidates, hard = T.question_candidates(turn)
    hints = [h for t in texts for h in W.owner_gate_hints(t)]
    if hard:
        raise D.EvidenceError('the turn\'s text cannot be read (%s)' % hard)
    if not candidates and not hints:
        raise D.EvidenceError('the turn carries no question or owner-gate declaration; nothing was asked of him')
    relayed = state.get('last_relay_ts')
    if not relayed or D.epoch(stamp) > D.epoch(relayed):
        raise D.EvidenceError('relay the turn to the owner first (wd.sh relayed %s)' % stamp)
    for d in (state.get('owner_decisions') or {}).values():
        if d.get('kind') == 'ask' and d.get('turn_ts') == stamp and not d.get('answered'):
            raise D.EvidenceError('%s is already open for that turn' % d.get('id'))
    return allocate(state, 'ASK from the target (turn %s): %s' % (stamp, W.short(quote, 200)), None,
                    kind='ask', turn_ts=stamp, quote=quote)


def _owner_text(rec):
    """His words in one of HIS records in the watchdog's own transcript, or None.

    A record type is a channel, not an author (SKILL.md): in THIS session his messages arrive as
    `user` records with origin.kind human, and mid-turn as `queue-operation` enqueues and
    `attachment` queued_command records with origin.kind human. The watchdog's own outbound copies
    live in assistant tool_use inputs and are never read here.
    """
    ty = rec.get('type')
    if ty == 'user':
        if rec.get('isMeta') or (rec.get('origin') or {}).get('kind') != 'human':
            return None
        c = (rec.get('message') or {}).get('content')
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return ' '.join(b.get('text', '') for b in c if isinstance(b, dict) and b.get('type') == 'text')
        return None
    if ty == 'attachment':
        att = rec.get('attachment') or {}
        if att.get('type') == 'queued_command' and (att.get('origin') or {}).get('kind') == 'human':
            p = att.get('prompt')
            return p if isinstance(p, str) else None
    return None


def owner_words_record(self_sess, words, after_ts):
    """The uuid/ts of his record carrying `words` verbatim, written after `after_ts`."""
    want = _norm(words)
    if not want:
        raise D.EvidenceError('owe answer needs his words, quoted verbatim')
    floor = D.epoch(after_ts)
    for rec in D.read_records(W.transcript_path(self_sess)):
        text = _owner_text(rec)
        if text is None or want not in _norm(text):
            continue
        stamp = rec.get('timestamp') or (rec.get('attachment') or {}).get('timestamp')
        if stamp and D.epoch(stamp) > floor:
            return rec.get('uuid') or (rec.get('attachment') or {}).get('source_uuid') or '', stamp, text
    raise D.EvidenceError('those words are not in any of his messages after the turn (%s); '
                          'only his own records count, never a copy the watchdog sent' % after_ts)


def queue_answer(sess, self_sess, state, did, words, now):
    """Queue his answer to an open ask as a kind=answer item. Returns the item id."""
    d = (state.get('owner_decisions') or {}).get(did)
    if not d or d.get('kind') != 'ask':
        raise D.EvidenceError('%s is not an open ask bound to a target turn' % did)
    if d.get('answered'):
        raise D.EvidenceError('%s was already answered (%s)' % (did, d['answered']))
    if any(x.get('kind') == 'answer' and x.get('decision') == did for x in state.get('owner_queue') or []):
        raise D.EvidenceError('%s already has a queued answer' % did)
    uuid, stamp, full = owner_words_record(self_sess, words, d['turn_ts'])
    # A substring can verify while meaning less than the whole message ("yes" out of "yes but not
    # until the bars finish"), so the target always receives his WHOLE message, not just the quote.
    full = full.strip()
    q = state.setdefault('owner_queue', [])
    history = q + list(state.get('owner_queue_sent') or []) + list(state.get('owner_queue_dropped') or [])
    used = [int(x['id'][1:]) for x in history if re.fullmatch(r'Q[0-9]+', str(x.get('id', '')))]
    seq = max([int(state.get('owner_queue_seq') or 0)] + used) + 1
    state['owner_queue_seq'] = seq
    text = ('[watchdog] owner: "%s"\n\n(His whole message, verbatim, answering your request "%s" from your turn ending %s. '
            'Watchdog transcript %s, uuid %s, %s, his own record.)'
            % (full, W.short(d['quote'], 300), d['turn_ts'], W.transcript_path(self_sess), uuid, stamp))
    q.append(dict(id='Q%d' % seq, ts=now, urgent=False, kind='answer', decision=did, text=text,
                  owner_record=dict(uuid=uuid, ts=stamp, full_text=full, quoted=words.strip()), acted_when=None, acceptance_baseline=None,
                  review='machine'))
    return 'Q%d' % seq


def eligible(sess, state, item):
    """(ok, why): an answer passes only while its ask is still the target's latest completed turn."""
    d = (state.get('owner_decisions') or {}).get(item.get('decision'))
    if not d or d.get('kind') != 'ask' or not d.get('turn_ts'):
        return False, 'its decision is not an open ask bound to a turn'
    if d.get('answered'):
        return False, 'its decision was already answered'
    latest = latest_completed_end(sess)
    if latest != d['turn_ts']:
        return False, ('the target has completed a later turn (%s) than the one that asked (%s); '
                       'open a fresh ask' % (latest, d['turn_ts']))
    return True, 'answers %s (turn %s)' % (d['id'], d['turn_ts'])


def record_passthrough(state, item, rec_ts, owed_ids, now, outside=False):
    """Close the D entry and log the delivery permanently, for the owner to see on every poll.

    `outside` is True when the delivery did not follow the asking turn, i.e. it was not sent while
    eligible: the log then says so instead of claiming the gate passed it (review, 2026-10-17)."""
    d = state['owner_decisions'][item['decision']]
    d['answered'] = dict(item=item['id'], delivered=rec_ts, outside_gate=bool(outside))
    owner = item.get('owner_record') or {}
    line = ('%s: %s answered %s (target turn %s, "%s") with your message at %s ("%s"); delivered %s %s%s'
            % (now, item['id'], d['id'], d['turn_ts'], W.short(d['quote'], 80), owner.get('ts'),
               W.short(owner.get('full_text') or '', 120), rec_ts,
               'OUTSIDE the gate (not while eligible)' if outside else 'past the OWED gate',
               (' while %s still owed' % ', '.join(owed_ids)) if owed_ids else ''))
    state.setdefault('answer_passthroughs', []).append(line)
    return line
