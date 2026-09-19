"""Auditable owner-authorized turn dispositions and the pre-receipt migration boundary."""
import hashlib
import json
import re

import wd_lib as W
import wd_receipts as D

RULINGS = {'hold': 'owner:2026-09-10:relay-and-respond-or-hold',
           'closed': 'owner:D15:one-line-exception-on-probation'}


def initialize_tracking(state):
    """Freeze historical policy once, before any upgraded handler can advance a mark.

    This is a migration of an existing bootstrap, not a new operator-settable cutoff.
    Legacy answers stay historical; later receipts can never move this frontier.
    """
    if 'turn_tracking' in state or not state.get('bootstrap_ts'):
        return False
    since = state['bootstrap_ts']
    D.epoch(since)
    frontier = state.get('last_send_ts')
    if frontier:
        D.epoch(frontier)
    state['turn_tracking'] = dict(version=1, since=since, legacy_answered_through=frontier,
                                  source='pre-receipt bootstrap and send watermark', at=W.now_iso())
    return True


def fingerprint(turn):
    return hashlib.sha256(json.dumps(turn.records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def question_evidence(turn):
    """Conservative explicit-question/request detection, not semantic adjudication.

    False positives keep a turn owed. This cannot prove absence of a question in
    arbitrary prose; that boundary is documented, not disguised as understanding.
    """
    texts = [text for _, text in turn.assistant_texts]
    for use in turn.tool_uses:
        if use.get('name') not in W.MESSAGE_TOOL_NAMES: continue
        message = W.message_input(use)
        if message is None: return 'unreadable request text'
        texts.append(message['message'])
    for text in texts:
        if not isinstance(text, str):
            return 'unreadable request text'
        if '?' in text or '？' in text:
            return 'question punctuation'
        if re.search(r'(?im)(?:^|[.!:]\s+)(?:please\s+)?(?:who|what|when|where|why|how|which|can you|could you|would you|will you|do you|are you|is it|should I|shall I|tell me|let me know|confirm|clarify|choose|decide|advise)\b', text):
            return 'direct question or request language'
    return None


def find_turn(sess, stamp):
    turns = W.split_turns(D.read_records(W.transcript_path(sess)))
    matches = [t for t in turns if t.end_state != 'open' and t.end_ts == stamp]
    if len(matches) != 1:
        raise D.EvidenceError('timestamp must identify exactly one completed target turn')
    return matches[0]


def record_disposition(sess, actor, state, mode, stamp, reason):
    reason = (reason or '').strip()
    if not reason or not actor:
        raise D.EvidenceError('a disposition requires a nonempty reason and acting session attribution')
    if mode == 'hold' and not re.search(r'\b(owner|you)\b', reason, re.I):
        raise D.EvidenceError('hold is restricted to a turn blocked on the owner')
    turn = find_turn(sess, stamp)
    evidence = question_evidence(turn)
    if evidence:
        raise D.EvidenceError('a turn containing a direct question cannot be held or closed (%s)' % evidence)
    entry = dict(reason=reason, actor=actor, ruling=RULINGS[mode], at=W.now_iso(),
                 target=sess['sessionId'], turn_hash=fingerprint(turn))
    store = 'held_turns' if mode == 'hold' else 'closed_turns'
    previous = state.setdefault(store, {}).get(stamp)
    if previous:
        if valid_disposition(sess, turn, previous, mode):
            if previous['reason'] != reason or previous['actor'] != actor:
                raise D.EvidenceError('an existing audited disposition cannot be rewritten')
            return False
        # Do not relabel unverifiable legacy records as if they had new provenance.
        raise D.EvidenceError('legacy disposition lacks auditable provenance; it cannot be rewritten')
    state[store][stamp] = entry
    return True


def valid_disposition(sess, turn, entry, mode):
    return (isinstance(entry, dict) and bool(str(entry.get('reason') or '').strip())
            and isinstance(entry.get('actor'), str) and bool(entry['actor'])
            and entry.get('ruling') == RULINGS[mode] and bool(entry.get('at'))
            and entry.get('target') == sess['sessionId']
            and entry.get('turn_hash') == fingerprint(turn)
            and not question_evidence(turn))


def acknowledgement_evidence(sess, actor, stamp, ident, records):
    """Bind a standalone reply to the explicitly named turn, not a queue payload.

    The operator's retained words explain what was acknowledged; the machine
    proves delivery and ordering, not whether those words answer the question.
    Unlike hold/closed, a question is precisely what this route may answer.
    """
    if not actor:
        raise D.EvidenceError('acknowledgement requires acting session attribution')
    turns = [t for t in W.split_turns(records) if t.end_state != 'open' and t.end_ts == stamp]
    if len(turns) != 1:
        raise D.EvidenceError('timestamp must identify exactly one completed target turn')
    hits = [r for r in records if r.get('uuid') == ident]
    if not ident or len(hits) != 1:
        raise D.EvidenceError('delivery uuid must identify exactly one transcript record')
    rec = D.delivery(hits[0], actor)
    if D.epoch(rec['ts']) <= D.epoch(stamp):
        raise D.EvidenceError('acknowledgement delivery must be later than the named turn')
    return dict(target=sess['sessionId'], actor=actor, turn_ts=stamp,
                turn_hash=fingerprint(turns[0]), message_id=ident, delivery_ts=rec['ts'],
                body_hash=hashlib.sha256(rec['body'].encode()).hexdigest())


def record_acknowledgement(sess, actor, state, stamp, ident, reason):
    reason = reason.strip()
    if not reason:
        raise D.EvidenceError('acknowledgement requires nonempty prose saying what was acknowledged')
    entry = acknowledgement_evidence(sess, actor, stamp, ident, D.read_records(W.transcript_path(sess)))
    existing = (state.get('send_receipts') or {}).get(ident)
    if existing is not None and existing.get('turn_ts') != stamp:
        raise D.AlreadyBound('delivery receipt already bound to another turn')
    history = state.get('acknowledged_turns', [])
    if not isinstance(history, list) or any(not isinstance(e, dict) for e in history):
        raise D.EvidenceError('invalid acknowledgement history; cannot append')
    if any(e.get('message_id') == ident and e.get('turn_ts') != stamp for e in history):
        raise D.AlreadyBound('acknowledgement delivery already bound to another turn')
    entry.update(reason=reason, at=W.now_iso())
    if D.epoch(entry['at']) < D.epoch(entry['delivery_ts']):
        raise D.EvidenceError('acknowledgement delivery is in the future')
    state.setdefault('acknowledged_turns', []).append(entry)
    return entry


def acknowledged_turns(sess, actor, state):
    """Revalidate retained evidence on reads; damaged records never retire debt."""
    valid, problems = [], []
    history = state.get('acknowledged_turns', [])
    if not isinstance(history, list):
        return [], ['invalid acknowledgement history']
    if not history:
        return valid, problems
    try:
        records = D.read_records(W.transcript_path(sess))
        for entry in history:
            try:
                if not isinstance(entry, dict) or not isinstance(entry.get('reason'), str) or not entry['reason'].strip():
                    raise D.EvidenceError('missing acknowledgement prose')
                expected = acknowledgement_evidence(sess, actor, entry.get('turn_ts'), entry.get('message_id'), records)
                if any(entry.get(k) != v for k, v in expected.items()):
                    raise D.EvidenceError('recorded acknowledgement differs from transcript evidence or actor')
                existing = (state.get('send_receipts') or {}).get(entry['message_id'])
                if existing is not None and existing.get('turn_ts') != entry['turn_ts']:
                    raise D.EvidenceError('delivery receipt already bound to another turn')
                if D.epoch(entry.get('at')) < D.epoch(expected['delivery_ts']):
                    raise D.EvidenceError('acknowledgement predates its delivery')
                if any(isinstance(e, dict) and e.get('message_id') == entry['message_id']
                       and e.get('turn_ts') != entry['turn_ts'] for e in history):
                    raise D.EvidenceError('acknowledgement delivery bound to multiple turns')
                valid.append(entry)
            except (D.EvidenceError, OSError, SystemExit) as exc:
                problems.append(str(exc))
    except (D.EvidenceError, OSError, SystemExit) as exc:
        problems.append(str(exc))
    return valid, problems
