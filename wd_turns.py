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


REQUEST_RE = re.compile(r'(?im)(?:^|[.!:]\s+)(?:please\s+)?(?:who|what|when|where|why|how|which|can you|could you|would you|will you|do you|are you|is it|should I|shall I|tell me|let me know|confirm|clarify|choose|decide|advise)\b')
QUOTE_CHARS = 40


def _sentences(text):
    return [s.strip() for s in re.split(r'(?<=[.!?？。])\s+|\n+', text) if s.strip()]


def question_candidates(turn):
    """Find sentences that MIGHT be a question, and quote them. It does not adjudicate.

    Owner's ruling, 2026-09-20: "as everything else it should be answerable by
    natural language." The punctuation/keyword proxy could not tell a question put
    to the watchdog from the target narrating its own next measurement, and it was
    holding turns owed that had asked nothing. Deciding what a sentence MEANS is the
    model's job; finding the candidates and keeping the record is the script's.

    So this returns the candidate sentences themselves. Disposing of a turn that has
    any requires quoting each one and saying why it is not a question to the
    watchdog -- that reading is stored forever, so a false dismissal is a specific
    sentence someone can read back, not a silent pass.

    Returns (candidates, hard_refusal). A hard refusal is a MEASUREMENT failure --
    text that could not be read at all -- and no reading disposes of it.
    """
    texts = [text for _, text in turn.assistant_texts]
    for use in turn.tool_uses:
        if use.get('name') not in W.MESSAGE_TOOL_NAMES: continue
        message = W.message_input(use)
        if message is None: return [], 'unreadable request text'
        texts.append(message['message'])
    candidates = []
    for text in texts:
        if not isinstance(text, str):
            return [], 'unreadable request text'
        for sentence in _sentences(text):
            if '?' in sentence or '？' in sentence or REQUEST_RE.search(sentence):
                if sentence not in candidates:
                    candidates.append(sentence)
    return candidates, None


def has_question_candidates(turn):
    """Conservative boolean, for decisions made with NO human reading attached.

    Automatic supersession in `owed` closes a turn with nobody looking at it, so it
    keeps the strict proxy: anything question-shaped blocks it and the turn stays
    owed. The explicit `closed`/`hold` path is the one the owner opened to natural
    language, because there a person writes down what the sentence meant.
    """
    candidates, hard = question_candidates(turn)
    return bool(candidates or hard)


def _normalise(text):
    return re.sub(r'\s+', ' ', text or '').strip().lower()


def unquoted_candidates(candidates, reading):
    """Which candidates the reading fails to quote. Forces literal quotation.

    A blanket "none of these are questions" does not dispose of anything: the
    reading must carry each candidate's own words, so dismissing one is concrete.
    """
    body = _normalise(reading)
    missing = []
    for candidate in candidates:
        norm = _normalise(candidate)
        needle = norm if len(norm) <= QUOTE_CHARS else norm[:QUOTE_CHARS]
        if needle not in body:
            missing.append(candidate)
    return missing


def find_turn(sess, stamp):
    turns = W.split_turns(D.read_records(W.transcript_path(sess)))
    matches = [t for t in turns if t.end_state != 'open' and t.end_ts == stamp]
    if len(matches) != 1:
        raise D.EvidenceError('timestamp must identify exactly one completed target turn')
    return matches[0]


def record_disposition(sess, actor, state, mode, stamp, reason, reading=None):
    reason = (reason or '').strip()
    reading = (reading or '').strip()
    if not reason or not actor:
        raise D.EvidenceError('a disposition requires a nonempty reason and acting session attribution')
    if mode == 'hold' and not re.search(r'\b(owner|you)\b', reason, re.I):
        raise D.EvidenceError('hold is restricted to a turn blocked on the owner')
    turn = find_turn(sess, stamp)
    candidates, hard = question_candidates(turn)
    if hard:
        raise D.EvidenceError('a turn whose text cannot be read is never disposed of (%s)' % hard)
    if candidates:
        if not reading:
            raise D.EvidenceError(
                'this turn has %d sentence(s) that may be a question. Quote each one and say why it is '
                'not a question to the watchdog, with --not-asked "<reading>":\n  %s'
                % (len(candidates), '\n  '.join(repr(c) for c in candidates)))
        missing = unquoted_candidates(candidates, reading)
        if missing:
            raise D.EvidenceError(
                'the reading does not quote %d of the candidate sentence(s); dismissing one means '
                'carrying its own words:\n  %s' % (len(missing), '\n  '.join(repr(c) for c in missing)))
    entry = dict(reason=reason, actor=actor, ruling=RULINGS[mode], at=W.now_iso(),
                 target=sess['sessionId'], turn_hash=fingerprint(turn),
                 question_candidates=candidates, reading=reading)
    store = 'held_turns' if mode == 'hold' else 'closed_turns'
    previous = state.setdefault(store, {}).get(stamp)
    if previous:
        if previous.get('turn_hash') == entry['turn_hash']:
            # Same turn, same content: a differing reason here is a REWRITE of history, and
            # that stays refused. Identical is a no-op.
            if previous['reason'] != reason or previous['actor'] != actor:
                raise D.EvidenceError('an existing audited disposition cannot be rewritten')
            return False
        # Different content = a later state of the turn, so the old decision is STALE, not a
        # lock. Owner's ruling, 2026-09-20: "Nothing should ever be permanently stuck. If the
        # previous decision was overridden, then a stale entry should be superseded and the
        # new decision should close it via roll up."
        #
        # Blocking here made early disposal permanently unfixable, which is the alarm stuck ON
        # -- and the watchdog had argued FOR keeping that, on the reasoning that permanence was
        # the penalty for disposing of a turn before it finished. His design is the opposite
        # and matches the rest of this system (promise, owed): the penalty is VISIBILITY. The
        # superseded entry is kept in full, with its own hash and timestamp, so an early
        # disposal is on the record forever instead of silently vanishing into a fresh one.
        history = list(previous.get('superseded') or [])
        history.append({k: v for k, v in previous.items() if k != 'superseded'})
        entry['superseded'] = history
    state[store][stamp] = entry
    return True


def valid_disposition(sess, turn, entry, mode):
    if not (isinstance(entry, dict) and bool(str(entry.get('reason') or '').strip())
            and isinstance(entry.get('actor'), str) and bool(entry['actor'])
            and entry.get('ruling') == RULINGS[mode] and bool(entry.get('at'))
            and entry.get('target') == sess['sessionId']
            and entry.get('turn_hash') == fingerprint(turn)):
        return False
    candidates, hard = question_candidates(turn)
    if hard:
        return False
    # Candidates are re-derived from the transcript, never trusted from the entry, so a
    # stored reading cannot validate itself. A turn with none needs no reading, which is
    # what makes legacy entries (written when any candidate was refused outright) valid.
    return not unquoted_candidates(candidates, entry.get('reading') or '')


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
