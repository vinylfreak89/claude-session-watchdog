"""Transcript-derived delivery receipts. Caller supplied IDs are never receipt evidence."""
import hashlib
import json
import math
import re

import wd_lib as W

class EvidenceError(ValueError):
    pass


def recording_failed(state, operation, reason, item_id=None, message_id=None):
    """Retain the first recording failure for human review; never credit or reset it."""
    state.setdefault('receipt_recording_failure', dict(operation=operation, reason=str(reason),
                     item_id=item_id, message_id=message_id, at=W.now_iso()))


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


def read_records(path):
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
        records = read_records(W.transcript_path(session))
    except (EvidenceError, OSError, SystemExit):
        return False
    results = []
    for rec in records:
        if rec.get('type') != 'user': continue
        message = rec.get('message')
        if not isinstance(message, dict): continue
        for block in W._blocks(message.get('content'), 'tool_result'):
            if block.get('is_error', False) is not False: continue
            try: value = json.loads(W._result_text(block))
            except (ValueError, TypeError): continue
            if isinstance(value, dict) and value.get('success') is True and value.get('msg_id') == origin['msg_id']:
                results.append(block)
    if len(results) != 1 or not results[0].get('tool_use_id'): return False
    uses = [(rec, block) for rec in records if rec.get('type') == 'assistant' and isinstance(rec.get('message'), dict)
            for block in W._blocks(rec['message'].get('content'), 'tool_use')
            if block.get('id') == results[0]['tool_use_id']]
    if len(uses) != 1: return False
    source, use = uses[0]
    if not isinstance(use.get('input'), dict): return False
    message = use['input'].get('message')
    return (use.get('name') == 'SendMessage' and isinstance(message, str) and
            message.strip() == body and epoch(source.get('timestamp')) <= epoch(record.get('timestamp')))


def delivered_text(record):
    """Read only actual delivery surfaces, never queue operations or tool results."""
    if record.get('type') == 'user':
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


def delivery(record, sender):
    """Read the delivered envelope, excluding the host's appended guidance."""
    origin = record.get('origin')
    if not sender or not isinstance(origin, dict) or origin.get('kind') != 'peer':
        raise EvidenceError('delivery has no structural peer provenance for this sender')
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
    if (len(attrs) != len(pairs) or not attrs.get('from') or attrs['from'] != origin.get('from') or
            '<cross-session-message' in payload.lower() or
            len(re.findall(r'</?cross-session-message\b', body, re.I)) != 2):
        raise EvidenceError('delivery must contain one matching peer envelope')
    tail = wrapper['tail'].strip()
    if tail and not tail.startswith('This came from another Claude session — '):
        raise EvidenceError('unrecognized text after the delivered peer envelope')
    if 'body' in origin and (not isinstance(origin['body'], str) or origin['body'].strip() != payload):
        raise EvidenceError('peer origin body disagrees with the delivered envelope')
    if origin.get('from') != sender and not socket_sender_matches(record, sender, payload):
        raise EvidenceError('delivery has no structural peer provenance for this sender; '
                            'a socket peer needs a matching successful SendMessage result in the sender transcript')
    ident = record.get('uuid')
    if not isinstance(ident, str) or not ident:
        raise EvidenceError('delivery lacks a stable transcript record uuid')
    epoch(record.get('timestamp'))
    return dict(id=ident, ts=record['timestamp'], body=payload)


def receipt(path, sender, ident):
    if not ident:
        raise EvidenceError('a target transcript delivery uuid is required')
    records = read_records(path)
    hits = [(i, r) for i, r in enumerate(records) if r.get('uuid') == ident]
    if len(hits) != 1:
        raise EvidenceError('delivery uuid must identify exactly one transcript record')
    index, record = hits[0]
    result = delivery(record, sender)
    # The target transcript fixes the answered turn; invocation time is irrelevant.
    done = [t for t in W.split_turns(records[:index]) if t.end_state != 'open']
    previous = done[-1] if done else None
    result['turn_ts'] = previous.end_ts if previous else None
    if previous and epoch(previous.end_ts) >= epoch(result['ts']):
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
