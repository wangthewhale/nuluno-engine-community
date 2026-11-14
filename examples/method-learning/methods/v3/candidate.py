"""AI-authored commercial-fact packing; pure, bounded, original source spans.

This revision spends bytes on short fact-bearing units instead of a long
navigation lead and wide overlapping signal windows. No product name, URL,
plan name, numeric answer or evaluator quote is embedded in the method.
"""
import hashlib
import json
import re
from bisect import bisect_right

VERSION = 'commercial-fact-packing-3'
MONEY = re.compile(r'(?:[$€£¥]|\b(?:USD|EUR|GBP|TWD)\b)\s*\d[\d,.]*', re.I)
CAPACITY_UNITS = r'(?:subscribers?|users?|seats?|contacts?|projects?|websites?|sites?|forms?|submissions?|credits?|inboxes?|storage|requests?|events?)'
CAPACITY = re.compile(r'\b(?:free|no[- ]cost|zero(?:[- ]cost)?|nothing|no charge)\b.{0,120}?\b(?:plan|tier|forever|(?:first|up to|\d+|unlimited)\b.{0,55}?\b'+CAPACITY_UNITS+r')\b|\b(?:first|up to|\d+|unlimited)\b.{0,55}?\b'+CAPACITY_UNITS+r'\b.{0,110}?\b(?:free|no[- ]cost|zero(?:[- ]cost)?|nothing|no charge)\b', re.I | re.S)
FREE = re.compile(r'\b(?:free|no[- ]cost|nothing|no charge)\b', re.I)
TRIAL = re.compile(r'\b\d+\s*[-–]?\s*days?\b.{0,45}\b(?:trial|free)\b|\b(?:trial|free)\b.{0,45}\b\d+\s*[-–]?\s*days?\b', re.I | re.S)
TERMS = re.compile(r'\b(?:no|without)\s+credit\s+cards?\b|\bcredit\s+cards?\b.{0,35}\b(?:required|needed)\b|\b(?:cancel|refund)\b.{0,60}\b(?:any\s*time|days?|charges?|fees?)\b', re.I | re.S)
NAV = re.compile(r'^(?:skip to content|menu|close menu|product|products|pricing|features|solutions|resources|company|customers|industries|learn|support|contact|log ?in|sign ?in|sign ?up|book (?:a )?demo|get started|start for free|try for free|start free trial|start your free trial)$', re.I)
CATEGORIES = ((CAPACITY, 50), (MONEY, 35), (TRIAL, 35), (TERMS, 25))
JOIN = '\n[…]\n'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def envelope(source, text):
    result = {k: source[k] for k in ('url', 'status', 'content_type', 'body_sha256', 'bytes', 'observed_at') if k in source}
    result.update(representation=VERSION, text=text, text_truncated=True,
                  representation_sha256=hashlib.sha256(text.encode()).hexdigest())
    return result


def merge(spans):
    result = []
    for a,b in sorted(spans):
        if result and a <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], b))
        else:
            result.append((a,b))
    return result


def select(source, max_bytes=2500):
    if type(max_bytes) is not int or not 512 <= max_bytes <= 65536:
        raise ValueError('explicit_utf8_budget_required')
    text = source.get('text','')
    if not isinstance(text,str) or len(text.encode()) > 512*1024:
        raise ValueError('bounded_source_text_required')
    if len(canonical(envelope(source,''))) > max_bytes:
        raise ValueError('metadata_exceeds_budget')
    lines = [(m.start(),m.end(),m.group()) for m in re.finditer(r'[^\n]+',text) if m.group().strip()]
    if not lines:
        return {'view':envelope(source,''),'source_spans':[],'view_bytes':len(canonical(envelope(source,''))),'method_version':VERSION}
    starts = [r[0] for r in lines]
    windows = {}

    def add(lo, hi, category, anchor_start, anchor_end, quality=1):
        lo,hi = max(0,lo),min(len(lines),hi)
        if lo >= hi:
            return
        a,b = lines[lo][0],lines[hi-1][1]
        # Never cut through the detected fact merely to make a wide unit fit.
        if b-a > 420:
            a = max(a,anchor_start-100); b = min(b,anchor_end+150)
        if a > anchor_start or b < anchor_end:
            return
        excerpt = text[a:b]
        nav = sum(bool(NAV.fullmatch(row[2].strip())) for row in lines[lo:hi])
        # Only the actual anchored fact establishes category coverage. Nearby
        # prices or trial words in a wide window cannot stand in for a separate
        # free-capacity statement elsewhere in the source.
        groups = {category}
        # Group identity excludes UI whitespace and punctuation. Repeated copies
        # of the same commercial sentence cannot buy more selection priority.
        identity = ' '.join(re.findall(r'\w+|[$€£¥]',excerpt.casefold()))
        key = (a,b)
        row = {'a':a,'b':b,'groups':groups,'identity':identity,'nav':nav,'quality':quality}
        if key in windows:
            windows[key]['groups'].update(groups)
            windows[key]['quality'] = max(windows[key]['quality'],quality)
        else:
            windows[key] = row

    # Global matches locate facts beyond the prefix. Narrow and wider original
    # text-node units compete on marginal information per actual encoded byte.
    for category,(pattern,_) in enumerate(CATEGORIES):
        matches = list(pattern.finditer(text))
        if category == 0:
            # A long preceding trial sentence must not consume an embedded
            # free-plan anchor. Inspect every free-word start as well.
            matches.extend(m for token in FREE.finditer(text) for m in [pattern.match(text,token.start())] if m)
        for number,m in enumerate(matches):
            if number >= 512:
                raise ValueError('source_fact_anchor_limit')
            if category == 0 and m.group().count('\n') > 2:
                continue
            i = max(0,bisect_right(starts,m.start())-1)
            j = max(i,bisect_right(starts,max(m.start(),m.end()-1))-1)
            if category == 1:
                # Currency and amount may be separate HTML nodes. Keep the
                # nearby preceding label and following billing-unit context.
                following = '\n'.join(row[2] for row in lines[j+1:j+4])
                billing = re.search(r'^(?:per\s+|/\s*)(?:user|seat|month|year|mo\b|yr\b)|\b(?:per month|per year|/month|/year|/mo\b|/yr\b)\b',following,re.I)
                quality = 4 if billing else 1
                if billing:
                    label=i-1 if i>0 and not NAV.fullmatch(lines[i-1][2].strip()) else i
                    add(label,j+2,category,m.start(),m.end(),quality)
                for before,after in ((2,3),(1,2),(3,4)):
                    add(i-before,j+after+1,category,m.start(),m.end(),quality)
            elif category == 0:
                for before,after in ((0,1),(1,2),(1,3)):
                    add(i-before,j+after+1,category,m.start(),m.end())
            else:
                for before,after in ((0,1),(1,2),(1,3)):
                    add(i-before,j+after+1,category,m.start(),m.end())
    # A free plan may be a compact two-node label rather than a sentence.
    for i,(_,_,line) in enumerate(lines):
        if FREE.fullmatch(line.strip()) and i > 0 and len(lines[i-1][2]) <= 65 and not NAV.fullmatch(lines[i-1][2].strip()):
            add(i-1,i+2,0,lines[i-1][0],lines[i][1])
    if len(windows) > 4096:
        raise ValueError('source_fact_window_limit')

    # Keep only a short identity line. Generic navigation does not get a fixed
    # 600-byte reservation before commercial evidence has been considered.
    first = lines[0]
    identity_end = first[0] + len(first[2].encode()[:140].decode('utf-8',errors='ignore'))
    chosen = [(first[0],identity_end)] if identity_end > first[0] else []
    while chosen and len(canonical(envelope(source,text[chosen[0][0]:chosen[0][1]]))) > max_bytes:
        chosen[0] = (chosen[0][0],chosen[0][1]-1)
        if chosen[0][0] == chosen[0][1]: chosen=[]
    represented,identities = set(),set()
    remaining = list(windows.values())
    for _ in range(64):
        if not remaining:
            break
        options = []
        current_text = JOIN.join(text[a:b] for a,b in chosen)
        current_size = len(canonical(envelope(source,current_text)))
        for row in remaining:
            if row['identity'] in identities:
                continue
            spans = merge(chosen+[(row['a'],row['b'])])
            body = JOIN.join(text[a:b] for a,b in spans)
            size = len(canonical(envelope(source,body)))
            if size > max_bytes or spans == chosen:
                continue
            delta = max(1,size-current_size)
            novel = sum(CATEGORIES[g][1] for g in row['groups'] if g not in represented)
            # Diversity has priority; within each coverage level short factual
            # units beat wide navigation-heavy passages. Earlier equal facts win.
            base = sum(CATEGORIES[g][1] for g in row['groups'])
            score = (1000*novel + base) * row['quality'] / delta / (1+row['nav'])
            # Establish canonical source-order category coverage before filling
            # residual bytes by density. Near-duplicate later wording should
            # not replace the first complete statement merely to save a token.
            priority = (novel,row['quality'],-row['a'],score) if novel else (0,0,0,score)
            options.append((priority,row,spans))
        if not options:
            break
        _,row,spans = max(options,key=lambda r:r[0])
        chosen=spans;represented.update(row['groups']);identities.add(row['identity']);remaining.remove(row)
    # With no commercial evidence, preserve a bounded original prefix instead.
    if not windows:
        raw=text.encode();lo,hi=0,len(raw)
        while lo<hi:
            mid=(lo+hi+1)//2
            if len(canonical(envelope(source,raw[:mid].decode('utf-8',errors='ignore'))))<=max_bytes:lo=mid
            else:hi=mid-1
        fallback=raw[:lo].decode('utf-8',errors='ignore');chosen=[(0,len(fallback))] if fallback else []
    result=envelope(source,JOIN.join(text[a:b] for a,b in chosen))
    return {'view':result,'source_spans':chosen,'view_bytes':len(canonical(result)),'method_version':VERSION}
