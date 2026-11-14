"""AI-proposed, deterministic public-text selector. No I/O or model calls.

It preserves verbatim source spans; it does not infer prices or product facts.
This is a sidecar candidate, not a new operation enabled in Engine 0.12.
"""
import hashlib
import json
import re

VERSION = 'context-selection-1'
SIGNALS = (
    (re.compile(r'(?:[$€£¥]|\b(?:USD|EUR|GBP|TWD)\b)\s*\d[\d,.]*', re.I), 20),
    (re.compile(r'\b\d+\s*[-–]?\s*days?\b.{0,40}\b(?:trial|free)\b|\b(?:trial|free)\b.{0,40}\b\d+\s*[-–]?\s*days?\b', re.I | re.S), 20),
    (re.compile(r'\b(?:pricing|per month|per year|monthly|annually|trial|refund|cancel|billing|delivery)\b', re.I), 4),
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def envelope(source, text, spans):
    result = {k: source[k] for k in ('url', 'status', 'content_type', 'body_sha256', 'bytes', 'observed_at') if k in source}
    result.update(representation=VERSION, text=text, text_truncated=True,
                  representation_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())
    return result


def select(source, max_bytes=2500):
    if type(max_bytes) is not int or not 512 <= max_bytes <= 65536:
        raise ValueError('explicit_utf8_budget_required')
    text = source.get('text', '')
    if not isinstance(text, str) or len(text.encode('utf-8')) > 512 * 1024:
        raise ValueError('bounded_source_text_required')
    if len(canonical(envelope(source, '', []))) > max_bytes:
        raise ValueError('metadata_exceeds_budget')
    lines = []
    for match in re.finditer(r'[^\n]+', text):
        if match.group().strip():
            lines.append((match.start(), match.end(), match.group()))
    # A source lead preserves identity/context. Every scored window includes
    # adjacent lines because HTML often splits currency and amount into nodes.
    windows = [(0, min(len(text), 160), 1)] if text else []
    for i, (start, end, _) in enumerate(lines):
        lo, hi = max(0, i - 2), min(len(lines), i + 4)
        context = text[lines[lo][0]:lines[hi - 1][1]]
        score = sum(weight for pattern, weight in SIGNALS if pattern.search(context))
        if score:
            # Limit long prose nodes around an actual signal instead of taking
            # the prefix of that node. This is still an exact source substring.
            first = next((m for pattern, _ in SIGNALS for m in [pattern.search(context)] if m), None)
            center = lines[lo][0] + (first.start() if first else 0)
            left = max(lines[lo][0], center - 140)
            right = min(lines[hi - 1][1], center + 320)
            windows.append((left, right, score))
    chosen = []
    for start, end, score in sorted(set(windows), key=lambda row: (-row[2], row[0], row[1])):
        trial = sorted(chosen + [(start, end)])
        merged = []
        for a, b in trial:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        candidate_text = '\n[…]\n'.join(text[a:b] for a, b in merged)
        if len(canonical(envelope(source, candidate_text, merged))) <= max_bytes:
            chosen = merged
    # Empty/no-signal pages keep their prefix. No facts are manufactured.
    if not chosen:
        raw = text.encode('utf-8')
        for end in range(min(len(raw), max_bytes), -1, -1):
            s = raw[:end].decode('utf-8', errors='ignore')
            if len(canonical(envelope(source, s, []))) <= max_bytes:
                chosen = [(0, len(s))] if s else []
                break
    result = envelope(source, '\n[…]\n'.join(text[a:b] for a, b in chosen), chosen)
    # Spans are returned separately so the representation budget is exactly
    # comparable with the original compact representation's complete envelope.
    return {'view': result, 'source_spans': chosen,
            'view_bytes': len(canonical(result)), 'method_version': VERSION}
