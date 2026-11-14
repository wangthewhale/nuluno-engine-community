"""AI-proposed, deterministic public-text selector. No I/O or model calls.

It preserves verbatim source spans; it does not infer prices or product facts.
This is a sidecar candidate, not a new operation enabled in Engine 0.12.
"""
import hashlib
import json
import re
from bisect import bisect_right

VERSION = 'context-selection-2'
SIGNALS = (
    (re.compile(r'\b(?:free|no[- ]cost|zero(?:[- ]cost)?|nothing|no charge)\b.{0,120}\b(?:first|up to|\d+)\b.{0,80}\b(?:subscribers?|users?|seats?|contacts?|projects?|websites?)\b|\b(?:first|up to|\d+)\b.{0,80}\b(?:subscribers?|users?|seats?|contacts?|projects?|websites?)\b.{0,120}\b(?:free|no[- ]cost|zero(?:[- ]cost)?|nothing|no charge)\b', re.I | re.S), 30),
    (re.compile(r'(?:[$€£¥]|\b(?:USD|EUR|GBP|TWD)\b)\s*\d[\d,.]*', re.I), 20),
    (re.compile(r'\b\d+\s*[-–]?\s*days?\b.{0,40}\b(?:trial|free)\b|\b(?:trial|free)\b.{0,40}\b\d+\s*[-–]?\s*days?\b', re.I | re.S), 20),
    (re.compile(r'\b(?:no|without)\s+credit\s+cards?\b|\bcredit\s+cards?\b.{0,35}\b(?:required|needed)\b|\b(?:cancel|refund)\b.{0,60}\b(?:any\s*time|days?|charges?|fees?)\b', re.I | re.S), 25),
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
    windows = []
    starts = [line[0] for line in lines]
    for i, (start, end, _) in enumerate(lines):
        lo, hi = max(0, i - 2), min(len(lines), i + 4)
        context = text[lines[lo][0]:lines[hi - 1][1]]
        # Generate a window for each signal, rather than letting an earlier
        # currency or zero-price match hide a separate trial condition.
        for pattern, _ in SIGNALS:
            for match in list(pattern.finditer(context))[:64]:
                center = lines[lo][0] + match.start()
                # Detection windows overlap, but their edge must not truncate
                # a matched offer's next text node (for example a trial's
                # credit-card condition). Anchor context at the matched line.
                position = max(0, bisect_right(starts, center) - 1)
                context_lo, context_hi = max(0, position - 2), min(len(lines), position + 6)
                left = max(lines[context_lo][0], center - 140)
                right = min(lines[context_hi - 1][1], center + 320)
                excerpt = text[left:right]
                groups = tuple(index for index, (signal, _) in enumerate(SIGNALS) if signal.search(excerpt))
                score = sum(SIGNALS[index][1] for index in groups)
                windows.append((left, right, score, groups))
        if len(windows) > 10000:
            raise ValueError('source_signal_window_limit')
    # Preserve a short identity/primary-offer lead before adding focused
    # evidence. Add-on prices must not evict the product's opening offer.
    lead = text.encode('utf-8')[:min(600, max_bytes // 3)].decode('utf-8', errors='ignore')
    while lead and len(canonical(envelope(source, lead, []))) > max_bytes:
        lead = lead[:-1]
    chosen = [(0, len(lead))] if lead else []
    remaining = list(set(windows))
    represented = {index for index, (pattern, _) in enumerate(SIGNALS) if pattern.search(lead)}
    while remaining:
        # Prefer evidence categories that are not represented yet, preventing
        # many paid add-ons from crowding out a free tier or trial condition.
        window = max(remaining, key=lambda row: (sum(SIGNALS[g][1] for g in row[3] if g not in represented), row[2], -row[0], -row[1]))
        remaining.remove(window)
        start, end, score, groups = window
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
            represented.update(groups)
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
