"""Transcript-derived delivery receipts. Caller supplied IDs are never receipt evidence."""
import hashlib
import json
import re

import wd_lib as W

class EvidenceError(ValueError):
    pass


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


def delivery(record, sender):
    """No inferred author: legacy records without origin metadata are undecidable."""
    origin = record.get('origin')
    if not sender or not isinstance(origin, dict) or origin.get('kind') != 'peer' or origin.get('from') != sender:
        raise EvidenceError('delivery has no structural peer provenance for this sender')
    if record.get('type') == 'user':
        body = text_blocks((record.get('message') or {}).get('content'))
    elif record.get('type') == 'attachment':
        attachment = record.get('attachment') or {}
        if attachment.get('type') != 'queued_command' or attachment.get('commandMode') != 'prompt':
            raise EvidenceError('attachment is not a delivered queued command')
        body = text_blocks(attachment.get('prompt'))
    else:
        raise EvidenceError('record is not a delivery')
    wrapper = re.fullmatch(r'(?:Another Claude session sent a message(?: while you were working)?:\s*)?<cross-session-message\s+from="([^"]+)"(?:\s+name="[^"]*")?>\s*([\s\S]*?)\s*</cross-session-message>', body.strip())
    if not wrapper or wrapper.group(1) != sender or '<cross-session-message' in wrapper.group(2):
        raise EvidenceError('delivery must contain one matching peer envelope')
    ident = record.get('uuid')
    if not isinstance(ident, str) or not ident:
        raise EvidenceError('delivery lacks a stable transcript record uuid')
    epoch(record.get('timestamp'))
    return dict(id=ident, ts=record['timestamp'], body=wrapper.group(2))


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
        if item.get('text') and item['text'] in json.dumps(record, ensure_ascii=False):
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
            if rec['turn_ts']:
                answered.add(rec['turn_ts'])
        except EvidenceError:
            continue
    return answered
