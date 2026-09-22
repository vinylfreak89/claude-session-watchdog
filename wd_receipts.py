"""Transcript-derived delivery receipts. Caller supplied IDs are never receipt evidence."""
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
import re

import wd_lib as W

class EvidenceError(ValueError):
    pass


class AlreadyBound(EvidenceError):
    """A receipt that is already bound to a different turn. The delivery's state is
    fully known -- only the operator's request was wrong -- so it refuses without
    latching the send gate (2026-09-19: re-using one receipt for a second turn
    stopped every send)."""


def recording_failed(state, operation, reason, item_id=None, message_id=None):
    """Retain the first failure until that exact recording succeeds with evidence."""
    state.setdefault('receipt_recording_failure', dict(operation=operation, reason=str(reason),
                     item_id=item_id, message_id=message_id, at=W.now_iso()))


def recording_succeeded(state, operation, receipt, actor, item_id=None):
    """Called only after a recording handler fully validates and records delivery.

    A healthy unrelated send is not recovery of the failed operation. Preserve
    the failure verbatim before removing its latch, even for idempotent retries.
    There is no command, timeout or owner-ack bypass for this transition.

    The recovering delivery id MUST equal the one that failed. That makes a latch
    caused by a wrong id unreachable by a corrected retry, which was briefly
    treated here as a defect and relaxed. It is not one: a refused recording means
    a send whose record is in an unknown state, and a different id is a different
    delivery, so the stop is correct and the review is the owner's. The recurrence
    it was blamed for had another cause entirely -- a delivery absorbed mid-turn
    carries no uuid, so no correct id existed to retry with (see `absorbed_id`).
    """
    fault = state.get('receipt_recording_failure')
    if (not isinstance(fault, dict) or not fault.get('reason') or not actor
            or fault.get('operation') != operation or fault.get('item_id') != item_id
            or not fault.get('message_id') or fault['message_id'] != receipt['id']):
        return False
    history = state.get('receipt_recording_recoveries', [])
    if not isinstance(history, list):
        print('STUCK: receipt recovery history is unreadable; cannot clear failure')
        return False
    state.setdefault('receipt_recording_recoveries', []).append(dict(
        failure=dict(fault), cleared_at=W.now_iso(), actor=actor,
        operation=operation, item_id=item_id, message_id=receipt['id'], delivery_ts=receipt['ts']))
    del state['receipt_recording_failure']
    print('RECEIPT RECORDING RECOVERED: %s, message %s; prior failure retained' % (operation, receipt['id']))
    return True


def recording_problem(sess, state):
    """Read-only receipt health for the single send gate. Unknown is a stop.

    A delivery left unrecorded is never repaired here. In particular, finding its
    text in the target record is a reason to stop sending, not permission to mark it.
    """
    fault = state.get('receipt_recording_failure')
    if 'receipt_recording_failure' in state:
        if not isinstance(fault, dict) or not fault.get('reason'):
            return 'receipt recording failure metadata is unreadable; human review required'
        return 'receipt recording refused (%s, item %s, message %s): %s; human review required' % (
            fault.get('operation'), fault.get('item_id') or '-', fault.get('message_id') or '-', fault.get('reason'))
    try:
        records = read_records(W.transcript_path(sess))
    except EvidenceError as exc:
        return 'receipt validation unavailable: %s' % exc
    pending = [(str(q.get('id')), q.get('text'), q.get('ts'))
               for q in state.get('owner_queue') or [] if not q.get('sent')]
    pending += [(str(fid), f.get('message'), f.get('ts'))
                for fid, f in (state.get('proposed') or {}).items()]
    peers, validated, errors = 0, 0, []
    for record in records:
        origin = record.get('origin') or {}
        if not isinstance(origin, dict) or origin.get('kind') != 'peer' or record.get('type') not in ('user', 'attachment'):
            continue
        peers += 1
        try:
            # Health checks the observed transport format. This supplies NO sender
            # credit: attribution to configured self still happens at recording.
            rec = delivery(record, origin.get('from'))
            validated += 1
            body = rec['body']
        except EvidenceError as exc:
            errors.append(str(exc))
            # Preserve multiline text even when its envelope is malformed. A
            # possible duplicate stops the gate; it never supplies receipt credit.
            try:
                body = delivered_text(record)
            except EvidenceError as unreadable:
                return 'receipt validation unavailable: %s' % unreadable
        for ident, text, stamp in pending:
            try: after = epoch(record.get('timestamp')) > epoch(stamp)
            except EvidenceError as exc: return 'receipt timing unavailable: %s' % exc
            if after and text and text in body:
                return 'possible unrecorded delivery of %s in target record %s; do not resend or backfill' % (ident, record.get('uuid') or '(no uuid)')
    if peers and not validated:
        return 'receipt validation unavailable: none of %d peer deliveries validate (%s)' % (peers, '; '.join(sorted(set(errors))))
    return None


@dataclass(frozen=True)
class _RecordSnapshot:
    version: object
    records: list
    deliveries: list
    size: int
    digest: bytes
    lines: int
    terminated: bool


_RECORDS_CACHE = {}


def _file_version(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _parse_record_bytes(data, first_line=1):
    records, lines = [], 0
    # Match the uncached text reader's UTF-8 and universal-newline behavior.
    with io.TextIOWrapper(io.BytesIO(data), encoding='utf-8') as stream:
        for number, line in enumerate(stream, first_line):
            lines += 1
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise EvidenceError('record %d is not an object' % number)
            records.append(record)
    return records, lines


def read_records(path, *, deliveries_only=False):
    """Read current evidence, reusing JSON decoding only for a verified prefix.

    A growing transcript used to invalidate the entire parse on every append,
    including when it grew DURING decoding. An active poll could therefore decode
    hundreds of megabytes repeatedly and never finish settling its items.

    On a changed version, read the bytes again and hash the entire previous prefix.
    Only an identical SHA-256 permits reuse of its immutable record dictionaries;
    replacement, truncation and edits within a growing file are not assumed to be
    appends. Decode the suffix, preserving duplicate records and strict JSON errors.
    An unterminated final line is reparsed rather than treated as a record boundary.

    Device/inode/size/mtime/ctime still guard unchanged-file hits. A read that moves
    is saved only as a byte-verified baseline, NEVER as a hit for that file version:
    the next request reads and verifies the bytes again. No delivery or acceptance
    verdict is cached. Callers still receive independent lists over shared records.
    """
    realpath = os.path.realpath(path)
    try:
        version = _file_version(os.stat(path))
        previous = _RECORDS_CACHE.get(realpath)
        if previous is not None and previous.version == version:
            return list(previous.deliveries if deliveries_only else previous.records)
        with open(path, 'rb') as stream:
            before = _file_version(os.fstat(stream.fileno()))
            data = stream.read()
            after = _file_version(os.fstat(stream.fileno()))
        current = _file_version(os.stat(path))
        stable = before == after == current and len(data) == after[2]
        prefix = None
        if previous is not None and previous.terminated and len(data) >= previous.size:
            prefix = hashlib.sha256(memoryview(data)[:previous.size])
        if prefix is not None and prefix.digest() == previous.digest:
            tail = data[previous.size:]
            added, lines = _parse_record_bytes(tail, previous.lines + 1)
            records = previous.records + added
            deliveries = previous.deliveries + [r for r in added if delivery_candidate(r)]
            lines += previous.lines
            prefix.update(tail)
            digest = prefix.digest()
        else:
            records, lines = _parse_record_bytes(data)
            deliveries = [r for r in records if delivery_candidate(r)]
            digest = hashlib.sha256(data).digest()
        _RECORDS_CACHE[realpath] = _RecordSnapshot(
            current if stable else None, records, deliveries, len(data), digest, lines,
            not data or data.endswith(b'\n'))
        return list(deliveries if deliveries_only else records)
    except (OSError, UnicodeError, ValueError) as exc:
        raise EvidenceError('cannot read complete transcript: %s' % exc)


def _read_records_uncached(path):
    """Independent full-scan reference retained for differential controls."""
    records = []
    try:
        with open(path, encoding='utf-8') as f:
            for number, line in enumerate(f, 1):
                if not line.strip():
                    continue
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise EvidenceError('record %d is not an object' % number)
                records.append(record)
    except (OSError, UnicodeError, ValueError) as exc:
        raise EvidenceError('cannot read complete transcript: %s' % exc)
    return records


def epoch(stamp):
    if not isinstance(stamp, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)', stamp):
        raise EvidenceError('missing or invalid timestamp')
    value = W.epoch_from_iso(stamp)
    if value is None:
        raise EvidenceError('invalid timestamp')
    return value


def text_blocks(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content and all(isinstance(b, dict) and b.get('type') == 'text' and isinstance(b.get('text'), str) for b in content):
        return '\n'.join(b['text'] for b in content)
    raise EvidenceError('delivery content is not exclusively text')


_TOOL_USE_INDEX = {}


def _tool_use_index(path, records):
    """tool_use id -> [(record, block), ...], built once per record set.

    `socket_sender_matches` found its result via _msg_id_results and then RE-WALKED every
    assistant record to locate the call that produced it. Measured 2026-09-21: 140 socket
    checks costing 10.7 s, of which 6.55 s was that rescan -- an index over results sitting
    next to a linear scan for the matching use.

    EVERY use of an id is kept, deliberately. The caller requires exactly one and refuses
    otherwise, and the evaluation's warning is the reason a dict-of-one would be wrong:
    "do not let a later duplicated use disappear behind an earlier cached success". A
    duplicated id must still be visible as a duplicate, so the list is the value.

    Keyed on the identity of the records, never a stat of the path -- the same rule the
    append race forced on every other index here.
    """
    key = (os.path.realpath(path), len(records))
    cached = _TOOL_USE_INDEX.get(key)
    if cached is not None and (not records or (cached[0] is records[0] and cached[1] is records[-1])):
        return cached[2]
    index = {}
    for rec in records:
        if rec.get('type') != 'assistant' or not isinstance(rec.get('message'), dict):
            continue
        for block in W._blocks(rec['message'].get('content'), 'tool_use'):
            ident = block.get('id')
            if ident:
                index.setdefault(ident, []).append((rec, block))
    for stale in [k for k in _TOOL_USE_INDEX if k[0] == key[0]]:
        del _TOOL_USE_INDEX[stale]
    _TOOL_USE_INDEX[key] = (records[0] if records else None,
                            records[-1] if records else None, index)
    return index


def socket_sender_matches(record, sender, body):
    """Attribute a socket delivery using the sender's existing SendMessage result.

    Socket paths/PIDs and display names are not durable session identities. The
    transport message ID occurs in both the receiver origin and the successful
    tool result in the configured sender's transcript; no alias registry is kept.
    """
    origin = record.get('origin')
    if (not str(origin.get('from') or '').startswith('uds:') or
            type(origin.get('verifiedPeerPid')) is not int or origin['verifiedPeerPid'] <= 0 or
            not isinstance(origin.get('msg_id'), str) or not origin['msg_id']):
        return False
    try:
        session = W.find_session(sender)
        path = W.transcript_path(session)
        records = read_records(path)
        results = _msg_id_results(path, records).get(origin['msg_id'], [])
    except (EvidenceError, OSError, SystemExit):
        return False
    if len(results) != 1 or not results[0].get('tool_use_id'): return False
    uses = _tool_use_index(path, records).get(results[0]['tool_use_id'], [])
    if len(uses) != 1: return False
    source, use = uses[0]
    if not isinstance(use.get('input'), dict): return False
    message = use['input'].get('message')
    return (use.get('name') == 'SendMessage' and isinstance(message, str) and
            message.strip() == body and epoch(source.get('timestamp')) <= epoch(record.get('timestamp')))


def absorbed_id(record):
    """Address a delivery the host recorded without a uuid, by its own content.

    A message that arrives while the target is mid-turn is written as a
    queue-operation carrying no `uuid`, so it cannot be cited by one. The id is
    derived from the record itself -- session, timestamp and exact content -- so
    it is reproducible by anyone reading the same transcript and cannot be chosen
    by the caller, which is the property `uuid` was relied on for.
    """
    seed = '\x00'.join([str(record.get('sessionId') or ''), str(record.get('timestamp') or ''),
                        str(record.get('content') or '')])
    return 'absorbed:' + hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]


def is_absorbed_delivery(record):
    return (record.get('type') == 'queue-operation' and record.get('operation') == 'remove'
            and record.get('reason') == 'absorbed_mid_turn' and isinstance(record.get('content'), str))


def sender_sent_text(sender, payload, not_after):
    """Attribute an origin-less delivery by the sender's own successful send.

    The absorbed record proves ARRIVAL by existing in the target transcript; this
    establishes only that the configured sender is the author. It requires a
    SendMessage whose message is exactly this payload and whose tool_result did
    not error, at or before the arrival -- two independent transcripts agreeing,
    never this session's assertion.
    """
    try:
        records = read_records(W.transcript_path(W.find_session(sender)))
    except (EvidenceError, OSError, SystemExit):
        return False
    failed = set()
    for rec in records:
        if rec.get('type') != 'user' or not isinstance(rec.get('message'), dict):
            continue
        for block in W._blocks(rec['message'].get('content'), 'tool_result'):
            if block.get('is_error', False) is not False and block.get('tool_use_id'):
                failed.add(block['tool_use_id'])
    for rec in records:
        if rec.get('type') != 'assistant' or not isinstance(rec.get('message'), dict):
            continue
        for use in W._blocks(rec['message'].get('content'), 'tool_use'):
            if use.get('name') not in ('SendMessage', 'send_message') or use.get('id') in failed:
                continue
            message = (use.get('input') or {}).get('message')
            if not isinstance(message, str) or message.strip() != payload:
                continue
            if epoch(rec.get('timestamp')) <= epoch(not_after):
                return True
    return False


def delivered_text(record):
    """Read actual delivery surfaces only, never an enqueue or a tool result.

    `absorbed_mid_turn` IS a delivery surface: the host removes the item from the
    pending queue because it handed it to the running turn. An `add` is not, and
    is still refused -- an enqueued message has not arrived.
    """
    if is_absorbed_delivery(record):
        body = record['content']
    elif record.get('type') == 'queue-operation':
        raise EvidenceError('queue operation %r is not a delivery' % record.get('operation'))
    elif record.get('type') == 'user':
        message = record.get('message')
        if not isinstance(message, dict):
            raise EvidenceError('delivery message is not an object')
        body = text_blocks(message.get('content'))
    elif record.get('type') == 'attachment':
        attachment = record.get('attachment')
        if not isinstance(attachment, dict):
            raise EvidenceError('delivery attachment is not an object')
        if attachment.get('type') != 'queued_command' or attachment.get('commandMode') != 'prompt':
            raise EvidenceError('attachment is not a delivered queued command')
        body = text_blocks(attachment.get('prompt'))
    else:
        raise EvidenceError('record is not a delivery')
    return body


def delivery_candidate(record):
    """Structural eligibility only; never sender attribution or delivery credit.

    Shared by the parser's ordered candidate list and the validator, so the fast
    path cannot grow a separate definition of which record shapes are admissible.
    """
    origin = record.get('origin')
    return is_absorbed_delivery(record) or (isinstance(origin, dict) and origin.get('kind') == 'peer')


def delivery(record, sender):
    """Read the delivered envelope, excluding the host's appended guidance."""
    absorbed = is_absorbed_delivery(record)
    origin = record.get('origin')
    if not sender or not delivery_candidate(record):
        raise EvidenceError('delivery has no structural peer provenance for this sender')
    if absorbed:
        # The host writes no origin block on an absorbed record. Authorship comes
        # from the sender's own transcript instead; every other check is unchanged.
        origin = {}
    body = delivered_text(record)
    # Start at the delivered message, never search inside prose for a quotation.
    # Attributes describe the host format; only `from` is an author identity.
    wrapper = re.fullmatch(r'(?:Another Claude session sent a message(?: while you were working)?:\s*)?'
                          r'<cross-session-message(?P<attrs>(?:\s+[\w:-]+="[^"<>]*")*)\s*>'
                          r'(?P<body>[\s\S]*?)</cross-session-message>(?P<tail>[\s\S]*)', body.strip())
    if not wrapper:
        raise EvidenceError('delivery must contain one matching peer envelope')
    pairs = re.findall(r'([\w:-]+)="([^"<>]*)"', wrapper['attrs'])
    attrs = dict(pairs)
    payload = wrapper['body'].strip()
    if (len(attrs) != len(pairs) or not attrs.get('from') or
            (not absorbed and attrs['from'] != origin.get('from')) or
            '<cross-session-message' in payload.lower() or
            len(re.findall(r'</?cross-session-message\b', body, re.I)) != 2):
        raise EvidenceError('delivery must contain one matching peer envelope')
    tail = wrapper['tail'].strip()
    if tail and not tail.startswith('This came from another Claude session — '):
        raise EvidenceError('unrecognized text after the delivered peer envelope')
    if 'body' in origin and (not isinstance(origin['body'], str) or origin['body'].strip() != payload):
        raise EvidenceError('peer origin body disagrees with the delivered envelope')
    if absorbed:
        epoch(record.get('timestamp'))
        # Same evidence standard as the origin-bearing path: the host-rendered
        # `from` identifies the sender, and a socket alias falls back to the
        # sender's own successful send. Neither is this session's assertion.
        if attrs['from'] != sender and not sender_sent_text(sender, payload, record['timestamp']):
            raise EvidenceError('absorbed delivery is not attributable to this sender; a socket alias '
                                'needs a matching successful send in the sender transcript')
        return dict(id=absorbed_id(record), ts=record['timestamp'], body=payload)
    if origin.get('from') != sender and not socket_sender_matches(record, sender, payload):
        raise EvidenceError('delivery has no structural peer provenance for this sender; '
                            'a socket peer needs a matching successful SendMessage result in the sender transcript')
    ident = record.get('uuid')
    if not isinstance(ident, str) or not ident:
        raise EvidenceError('delivery lacks a stable transcript record uuid')
    epoch(record.get('timestamp'))
    return dict(id=ident, ts=record['timestamp'], body=payload)


_RECEIPT_INDEX = {}


def _receipt_index(path, records):
    """delivery id -> [(record, preceding completed turn end_ts)], in ONE pass.

    Replaces a whole-result memo that was wrong twice over (see `receipt`). This indexes the
    TARGET-SIDE STRUCTURE only: which record carries an id, and what the last completed turn
    was AT THAT POINT. It caches no verdict, so every request still validates provenance.

    WHY THIS IS CORRECT WHERE SLICING WOULD NOT BE. `receipt` needs the last completed turn in
    `records[:index]`, and a turn complete at one prefix can REOPEN after a tool continuation,
    so the final turn list cannot be sliced to answer a prefix question. This walk instead
    records the answer BEFORE consuming each record, observing the state at that prefix, which
    is what makes reopening a non-issue rather than a hazard. It reuses `is_opener`, `Turn` and
    `add` so the turn semantics are shared with `_split_turns_uncached`, never re-derived.

    Measured: 131 receipt requests caused 65 distinct prefix rebuilds visiting 4,316,320
    records; this is one pass over 73,678. An independent evaluation compared a prototype of
    this walk against the uncached production splitter over the original 240 mixed-record
    prefixes plus 4,000 deterministic ones covering API errors, interruptions, tool results,
    meta users and reopening -- all agreed.
    """
    key = (os.path.realpath(path), len(records))
    cached = _RECEIPT_INDEX.get(key)
    if cached is not None and (not records or (cached[0] is records[0] and cached[1] is records[-1])):
        return cached[2]
    index, prev_pid, cur, finalized = {}, None, None, None
    for r in records:
        prior = cur.end_ts if cur is not None and cur.end_state != 'open' else finalized
        # KEYS ARE DEDUPED WITHIN ONE PHYSICAL RECORD, never across records. Two separate
        # rows sharing an id must stay ambiguous -- that is the guard. But one row inserted
        # twice is not ambiguity, and the old code was an OR per record, not two hits.
        # Regression found by recheck: a record whose uuid equals its derived absorbed_id
        # was appended under the same key twice and a VALID receipt then refused as
        # ambiguous. `absorbed_id` excludes uuid, so the collision is easy to construct.
        keys = set()
        ident = r.get('uuid')
        # Only non-empty STRING uuids are indexed -- the ids the delivery validator accepts.
        # Regression found by recheck: an unrelated bookkeeping row carrying a non-hashable
        # uuid (e.g. "uuid": ["metadata"]) raised TypeError and took down the whole index,
        # where the old equality scan simply did not match it. That is a loss of
        # availability for a valid receipt caused by another row's malformed field.
        if isinstance(ident, str) and ident:
            keys.add(ident)
        if is_absorbed_delivery(r):
            keys.add(absorbed_id(r))
        for k in keys:
            index.setdefault(k, []).append((r, prior))
        ty = r.get('type')
        if ty == 'user':
            if W.is_opener(r, prev_pid):
                if cur is not None and cur.end_state != 'open':
                    finalized = cur.end_ts
                cur = W.Turn(r)
            elif cur is not None:
                cur.add(r)
            prev_pid = r.get('promptId')
        elif cur is not None and ty == 'assistant':
            cur.add(r)
        elif cur is not None:
            cur.records.append(r)
    for stale in [k for k in _RECEIPT_INDEX if k[0] == key[0]]:
        del _RECEIPT_INDEX[stale]
    _RECEIPT_INDEX[key] = (records[0] if records else None,
                           records[-1] if records else None, index)
    return index


def receipt(path, sender, ident):
    """Structure is indexed; PROVENANCE IS REVALIDATED ON EVERY CALL.

    An earlier version memoised the whole result on (target records, sender, ident). That was
    wrong twice, and an independent evaluation reproduced the worse half:

    1. A RECEIPT IS NOT A FUNCTION OF THE TARGET'S RECORDS ALONE. A socket delivery also
       depends on the successful SendMessage result and unique call in the SENDER'S
       transcript. The memo keyed only on target identity plus the sender's NAME, and its hit
       returned before consulting that evidence. Appending a duplicate successful result to
       the sender file -- leaving the target untouched -- made the memo answer CREDIT where
       uncached `delivery` REFUSES. That is fail-open, on the path `record_delivery` uses, and
       the docstring's claim that "the answer cannot differ within one parse" was simply false:
       there are two evidence inputs and it keyed on one.

    2. IT DID NOT REMOVE THE WORK IT CLAIMED. With or without it there were still 65 uncached
       prefix rebuilds -- `split_turns`'s own cache was already serving the repeats. What the
       memo actually skipped was the repeated provenance validation, which is the part that
       must never be skipped.

    So the index holds only the target-side structural answer, and `delivery(record, sender)`
    runs for every request, reading the sender transcript each time.
    """
    if not ident:
        raise EvidenceError('a target transcript delivery uuid is required')
    records = read_records(path)
    hits = _receipt_index(path, records).get(ident, [])
    if len(hits) != 1:
        raise EvidenceError('delivery uuid must identify exactly one transcript record')
    record, previous_ts = hits[0]
    result = delivery(record, sender)
    # The target transcript fixes the answered turn; invocation time is irrelevant.
    result['turn_ts'] = previous_ts
    if previous_ts and epoch(previous_ts) >= epoch(result['ts']):
        raise EvidenceError('delivery is not later than the completed turn')
    return result


def possibly_delivered(path, sender, item):
    """Withdrawal needs a conclusive negative, including unmarked/legacy deliveries."""
    for record in read_records(path):
        if record.get('type') not in ('user', 'attachment', 'queue-operation'):
            continue
        stamp = record.get('timestamp')
        if stamp and epoch(stamp) < epoch(item['ts']):
            continue
        # An ambiguous author/channel blocks withdrawal too; it never supplies credit.
        texts = [json.dumps(record, ensure_ascii=False), W._text_of((record.get('message') or {}).get('content')),
                 (record.get('attachment') or {}).get('prompt', ''), record.get('content', '')]
        if item.get('text') and any(isinstance(t, str) and item['text'] in t for t in texts):
            return True
        try:
            if item.get('text') and item['text'] in delivery(record, sender)['body']:
                return True
        except EvidenceError:
            pass
    return False


def components(state, rec):
    queues = list(state.get('owner_queue') or []) + list(state.get('owner_queue_sent') or [])
    findings = dict(state.get('proposed') or {})
    findings.update(state.get('sent_findings') or {})
    candidates = []
    # A complete, canonical body is required; substring mentions are not item delivery.
    for q in [None] + queues:
        rest = rec['body']
        if q is not None:
            text = q.get('text') or ''
            if not text or not (rest == text or rest.startswith(text + '\n\n')):
                continue
            rest = rest[len(text):].removeprefix('\n\n') if hasattr(str, 'removeprefix') else rest[len(text):].lstrip('\n')
        found = []
        if rest:
            for part in rest.split('\n\n'):
                ids = [fid for fid, f in findings.items() if f.get('message') == part]
                if len(ids) != 1:
                    break
                found.append(ids[0])
            else:
                if len(found) == len(set(found)):
                    candidates.append((q, found))
                continue
            continue
        if q is not None:
            candidates.append((q, found))
    if len(candidates) != 1:
        raise EvidenceError('delivery does not uniquely match one queued item and/or complete finding messages')
    q, fids = candidates[0]
    for entry in ([q] if q else []) + [findings[fid] for fid in fids]:
        if epoch(entry.get('ts')) >= epoch(rec['ts']):
            raise EvidenceError('item must have been recorded before delivery')
    return q, fids, findings


def record_delivery(path, sender, state, ident, queue_id=None, finding_ids=None):
    """Validate every component before mutating; either mark order records the same receipt."""
    rec = receipt(path, sender, ident)
    history = state.get('acknowledged_turns', [])
    if not isinstance(history, list) or any(not isinstance(e, dict) for e in history):
        raise EvidenceError('invalid acknowledgement history; cannot bind receipt')
    if any(e.get('message_id') == ident and e.get('turn_ts') != rec['turn_ts'] for e in history):
        raise EvidenceError('delivery already acknowledges a different turn')
    q, fids, findings = components(state, rec)
    if queue_id is not None and (q is None or q.get('id') != queue_id):
        raise EvidenceError('receipt did not deliver this queue item')
    if finding_ids is not None and (not finding_ids or not set(finding_ids).issubset(fids)):
        raise EvidenceError('receipt did not deliver every named finding')
    if q is not None:
        import wd_check as C
        ok, why = C.acceptance_valid(q.get('acted_when'))
        if not ok:
            raise EvidenceError(why)
        if not isinstance(q.get('acceptance_baseline'), dict):
            raise EvidenceError('item lacks a pre-delivery acceptance baseline')
        if q.get('sent') and q.get('message_id') != ident:
            raise EvidenceError('item is already bound to another delivery')
    for fid in fids:
        if findings[fid].get('message_id') not in (None, ident):
            raise EvidenceError('finding is already bound to another delivery')
    value = dict(ts=rec['ts'], turn_ts=rec['turn_ts'], queue_id=q.get('id') if q else None,
                 findings=sorted(fids), body_hash=hashlib.sha256(rec['body'].encode()).hexdigest())
    old = (state.get('send_receipts') or {}).get(ident)
    if old is not None:
        if old != value:
            raise EvidenceError('receipt binding changed')
        return rec, False
    # No rebranding of a previously spent transcript record or legacy credit.
    if rec['ts'] <= (state.get('credited_send_ts') or ''):
        raise EvidenceError('legacy delivery credit cannot be migrated without an independent receipt')
    waiting = dict(state.get('awaiting_reply') or {})
    for fid in fids:
        f = findings[fid]
        if f.get('is_poke') and waiting:
            waiting.update(poked=True, poke_message_id=ident)
        elif f.get('asks_reply'):
            delay = f.get('reply_min', 20)
            if type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0:
                raise EvidenceError('invalid recorded reply interval')
            deadline = W.iso_from_epoch(epoch(rec['ts']) + 60 * delay)
            if not waiting or epoch(deadline) < epoch(waiting.get('deadline')):
                waiting = dict(message_id=ident, sent_ts=rec['ts'], deadline=deadline, findings=[fid], poked=False)
    if q is not None:
        q['sent'] = rec['ts']
        q['message_id'] = ident
    for fid in fids:
        f = dict(findings[fid], sent=rec['ts'], message_id=ident)
        state.setdefault('sent_findings', {})[fid] = f
        state.setdefault('proposed', {}).pop(fid, None)
        state.setdefault('raised', {})[f['key']] = dict(evidence_hash=f['evidence_hash'], finding_id=fid,
                                                       ts=rec['ts'], message_id=ident)
    state.setdefault('send_receipts', {})[ident] = value
    if waiting:
        state['awaiting_reply'] = waiting
    state['last_send_ts'] = max(state.get('last_send_ts') or '', rec['ts'])
    return rec, True


def answered_turns(path, sender, state):
    answered = set()
    for ident, value in (state.get('send_receipts') or {}).items():
        try:
            rec = receipt(path, sender, ident)
            if (value.get('ts') != rec['ts'] or value.get('turn_ts') != rec['turn_ts'] or
                    value.get('body_hash') != hashlib.sha256(rec['body'].encode()).hexdigest()):
                continue
            item_id = value.get('queue_id')
            completed = [q for q in state.get('owner_queue_sent') or []
                         if q.get('id') == item_id and q.get('message_id') == ident
                         and q.get('acted_status') == 'pass' and q.get('acted_evidence')]
            if rec['turn_ts'] and len(completed) == 1 and not value.get('findings'):
                answered.add(rec['turn_ts'])
        except EvidenceError:
            continue
    return answered


def read_records_from_landmark(path, stamp):
    """Records from the one bearing `stamp` to EOF, read by streaming the tail.

    Owner's design, 2026-09-21: "read backwards from the bottom until finding where the
    original question was queued and stop. so don't deserialize the whole thing, stream it
    under looking for the answer."

    Why a LANDMARK and not a timestamp comparison: `target_calls` documents that replayed
    old records can appear later in the file, so transcript order is not a time boundary
    and stopping on "older than X" could cut off records that still matter. Stopping at the
    delivery record itself is structural -- everything after it in FILE ORDER is the
    candidate set, and anything replayed among them is still seen and still rejected on its
    own timestamp by the caller.

    Returns None when the landmark is not present, so the caller falls back to the full
    read rather than guessing. Cost before this: one 340 MB parse, 122 s of CPU per `owed`.
    """
    if not isinstance(stamp, str) or not stamp:
        return None
    needle = ('"timestamp":"%s"' % stamp).encode()
    alt = ('"timestamp": "%s"' % stamp).encode()
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as fh:
            chunk, pos, tail = 1 << 20, size, b''
            while pos > 0:
                step = min(chunk, pos)
                pos -= step
                fh.seek(pos)
                block = fh.read(step) + tail
                index = max(block.rfind(needle), block.rfind(alt))
                if index >= 0:
                    start = block.rfind(b'\n', 0, index) + 1
                    fh.seek(pos + start)
                    return _parse_lines(fh.read().decode('utf-8'))
                newline = block.find(b'\n')
                tail = block[:newline + 1] if newline >= 0 else block
    except (OSError, UnicodeError, ValueError) as exc:
        raise EvidenceError('cannot read complete transcript: %s' % exc)
    return None


def _parse_lines(text):
    records = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise EvidenceError('record %d is not an object' % number)
        records.append(record)
    return records


_MSGID_CACHE = {}


def _msg_id_results(path, records):
    """msg_id -> successful tool_result blocks, built once per transcript version.

    `socket_sender_matches` previously walked the whole sender transcript on EVERY call to
    find one msg_id. Measured 2026-09-21: 947 calls at ~107 ms each, 100 s of a 171 s `owed`
    run. The scan's inputs never change within a file version, so the same answer is built
    once and looked up thereafter.

    KEYED ON THE IDENTITY OF THE RECORDS IT INDEXED, never on a stat of its own. It used to
    stat `path` independently, which is the append race an independent evaluation reproduced
    on 2026-09-21: the caller reads records, a record is appended, this function then stats
    and files an index built from the OLD records under the NEW size/mtime. The next read
    sees the appended record while the lookup returns the index that cannot see it, so a
    duplicate SendMessage result stays hidden and a delivery a fresh index REFUSES gets
    credited. It fails toward credit, which is the wrong direction for the layer that decides
    whether the target acted. The reproduction printed "race second delivery credited despite
    duplicate result: receipt" against `D.delivery` itself, not merely a helper.

    The identity is the one `split_turns` already uses -- length plus the `is` identity of the
    first and last record -- because read_records hands out a fresh list over the SAME record
    dicts, so identical objects mean the same parse. There is no stat here to disagree with it.

    The caller's `len(results) != 1` rule is preserved exactly: every match for an id is kept,
    so an ambiguous id still fails rather than silently picking one.
    """
    key = (os.path.realpath(path), len(records))
    cached = _MSGID_CACHE.get(key)
    if cached is not None and records and cached[0] is records[0] and cached[1] is records[-1]:
        return cached[2]
    if cached is not None and not records:
        return cached[2]
    index = {}
    for rec in records:
        if rec.get('type') != 'user': continue
        message = rec.get('message')
        if not isinstance(message, dict): continue
        for block in W._blocks(message.get('content'), 'tool_result'):
            if block.get('is_error', False) is not False: continue
            try: value = json.loads(W._result_text(block))
            except (ValueError, TypeError): continue
            if isinstance(value, dict) and value.get('success') is True:
                mid = value.get('msg_id')
                if isinstance(mid, str) and mid:
                    index.setdefault(mid, []).append(block)
    for stale in [k for k in _MSGID_CACHE if k[0] == key[0]]:
        del _MSGID_CACHE[stale]
    _MSGID_CACHE[key] = (records[0] if records else None,
                         records[-1] if records else None, index)
    return index

AUDIT_STAMP = 'last_audit.json'

def last_audit_ts(state_dir):
    """When the unconditional audit timer last fired.

    Deliberately its own file rather than a key in state.json: the audit hook and a wake can run
    at the same moment, and a read-modify-write of the shared state would silently drop whichever
    finished first. Nothing else writes this file.
    """
    try:
        with open(os.path.join(state_dir, AUDIT_STAMP)) as fh:
            return (json.load(fh) or {}).get('ts') or ''
    except Exception:
        return ''

def mark_audit(state_dir, ts):
    """Called by the audit timer itself -- the sole release for owner-active quiet."""
    os.makedirs(state_dir, exist_ok=True)
    tmp = os.path.join(state_dir, AUDIT_STAMP + '.tmp')
    with open(tmp, 'w') as fh:
        json.dump(dict(ts=ts), fh)
    os.replace(tmp, os.path.join(state_dir, AUDIT_STAMP))
    return ts


def audit_budget(max_wait, prev_stamp, now):
    """How long the audit backstop should still wait, measured from the LAST STAMP.

    A Monitor expires every 30 minutes and must be re-armed, and the audit used to sleep
    a fixed `max_wait` from its own process start -- so a re-arm landing mid-window
    restarted the 20-minute clock and postponed the release. Measured 2026-09-22: one
    quiet period ran about 28 minutes against the 20 the owner agreed to ("that means
    when I'm active it shuts you up except once every 20 minutes").

    FAILS OPEN, DELIBERATELY. A missing, unparseable or future-dated stamp returns the
    full window rather than zero or a negative: the audit is the backstop that releases
    quiet, and a backstop that declines to fire is a quiet that never ends. Clamped to
    [0, max_wait] so a very old stamp fires immediately and a clock that has gone
    backwards cannot extend the window past its nominal length.
    """
    if not prev_stamp:
        return max_wait
    try:
        prev = epoch(prev_stamp)
    except Exception:
        return max_wait
    return max(0.0, min(float(max_wait), float(max_wait) - (now - prev)))


def quiet_window_start(sess, state, path=None, state_dir=None):
    """The instant the CURRENT owner-active quiet window opened, or None if none is open.

    The window opens at the earliest owner-opened target turn that the last audit did
    not already cover, and closes at the next audit stamp. It is derived from the same
    two facts `owner_active_quiet` uses, so the banner and the refusal cannot disagree.
    """
    turns = [t for t in W.split_turns(read_records(path or W.transcript_path(sess)))
             if t.end_state != 'open']
    audit = (last_audit_ts(state_dir) if state_dir else '') or ''
    starts = [t.end_ts for t in turns
              if getattr(t, 'opener_kind', None) == 'human' and (t.end_ts or '') > audit]
    return min(starts) if starts else None


def quiet_speech(self_sess, since_ts):
    """(watchdog messages, owner messages) in the watchdog's OWN transcript since `since_ts`.

    MEASURED FROM THE RECORD, NEVER ATTESTED, and that is the whole point. The quiet
    rule failed on 2026-09-22 because it was built as a refusal inside `relayed` -- a
    bookkeeping verb -- while nothing at all constrained the watchdog's user-facing
    text. It went quiet in the store and kept narrating the target's conversation to
    the owner every wake, which is the exact behaviour the rule exists to stop. His
    words on why a convention cannot fix this: "hooks are bullshit. they don't have
    teeth. at all. you will find some tool that circumvents them."

    So this counts nothing the watchdog tells it. It reads the harness-written
    transcript, where a message the watchdog emitted is a fact it cannot decline to
    record. Owner messages are counted beside it because answering him IS allowed
    during quiet: the signal is the two numbers together, not either alone.
    """
    if not since_ts:
        return 0, 0
    mine = theirs = 0
    for r in read_records(W.transcript_path(self_sess)):
        ts = r.get('timestamp') or ''
        if not ts or ts <= since_ts or r.get('isMeta'):
            continue
        kind = r.get('type')
        if kind == 'assistant':
            blocks = ((r.get('message') or {}).get('content') or [])
            if any(isinstance(b, dict) and b.get('type') == 'text' and (b.get('text') or '').strip()
                   for b in blocks):
                mine += 1
        elif kind == 'user' and (r.get('origin') or {}).get('kind') == 'human':
            # A RECORD TYPE IS A CHANNEL, NOT AN AUTHOR, and `type == 'user'` is a
            # CROWDED channel: in this session it also carries tool results and
            # task-notifications, and `queue-operation`/`attachment` carry harness
            # traffic with no message at all. Counting the types raw read 193 owner
            # messages in a window where he sent exactly one -- and because the
            # narration warning is suppressed when his count is the larger, the
            # miscount HID the 22 messages it existed to surface. `origin.kind`
            # is the same discriminator `split_turns` uses for an owner-opened
            # turn, so the two readers cannot drift apart.
            theirs += 1
    return mine, theirs


def owner_active_quiet(sess, state, path=None, state_dir=None):
    """Is the owner driving the target right now, with no audit release since?

    Returns a reason string while quiet holds, or None. Deliberately keyed on WHO OPENED the
    newest completed turn rather than on a decaying timer: a timer drifts and would release
    quiet mid-exchange. An AUDIT wake sets `last_audit_ts` and is the only release.
    """
    try:
        turns = [t for t in W.split_turns(read_records(path or W.transcript_path(sess))) if t.end_state != 'open']
    except Exception:
        return None
    if not turns:
        return None
    last = max(turns, key=lambda t: t.end_ts or '')
    if getattr(last, 'opener_kind', None) != 'human':
        return None
    audit = last_audit_ts(state_dir) if state_dir else (state or {}).get('last_audit_ts') or ''
    if audit and audit >= (last.end_ts or ''):
        return None
    return ('newest completed turn %s was owner-opened; last audit %s'
            % (last.end_ts, audit or 'never'))
