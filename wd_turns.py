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
