#!/usr/bin/env python3
"""Freeze a method, fetch public evidence, compare with a separate oracle.

Only public GETs occur. No product sales, private accounts or model billing.
"""
import argparse
import hashlib
import importlib.util
import ipaddress
import json
from pathlib import Path
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

HERE = Path(__file__).resolve().parent
MAX_BODY = 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError('receipt_exists_keep_original')
    path.write_bytes(canonical(value) + b'\n')


class Text(HTMLParser):
    """Matches the Engine 0.12 text-node extraction, including nav text."""
    def __init__(self):
        super().__init__(); self.parts = []; self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'): self.hidden += 1
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'): self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden and data.strip(): self.parts.append(data.strip())


def public_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment or p.port not in (None, 443):
        raise ValueError('public_https_url_required')
    if any(not ipaddress.ip_address(row[4][0]).is_global for row in socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)):
        raise ValueError('private_network_denied')
    return p


class Redirect(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        super().__init__(); self.trace = []
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p, n = public_url(req.full_url), public_url(newurl)
        if p.hostname != n.hostname: raise ValueError('cross_host_redirect_denied')
        self.trace.append({'status': code, 'from': req.full_url, 'to': newurl})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    public_url(url)
    request = urllib.request.Request(url, headers={'User-Agent': 'Nuluno-Method-Demo/1.0 public-read research', 'Accept': 'text/html,text/plain,application/json'})
    started = time.monotonic()
    redirects = Redirect()
    with urllib.request.build_opener(redirects).open(request, timeout=20) as response:
        raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY: raise ValueError('source_body_limit')
        kind = response.headers.get_content_type()
        if kind not in ('text/html', 'application/xhtml+xml', 'text/plain', 'application/json'): raise ValueError('unsupported_source_type')
        decoded = raw.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
        if kind in ('text/html', 'application/xhtml+xml'):
            parser = Text(); parser.feed(decoded); decoded = '\n'.join(parser.parts)
        source = {'url': response.url, 'status': response.status, 'content_type': kind,
                  'body_sha256': sha(raw), 'bytes': len(raw), 'observed_at': time.time(),
                  'text': decoded[:40000], 'text_truncated': len(decoded) > 40000,
                  'redirect_chain': redirects.trace, 'provider_gets': len(redirects.trace) + 1}
    return source, raw, round((time.monotonic() - started) * 1000, 3)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def baseline_path():
    return HERE.parent / 'engine/nuluno-engine-0.12.0/skills/growth-meta-ig-loop/runtime/growth_loop/public_sources.py'


def freeze(output):
    record = {'schema': 'nuluno-method-freeze-v1', 'candidate_version': 'context-selection-2',
              'candidate_sha256': sha((HERE / 'candidate.py').read_bytes()),
              'harness_sha256': sha(Path(__file__).read_bytes()),
              'baseline_sha256': sha(baseline_path().read_bytes()),
              'created_at': time.time(), 'training_urls': ['https://plausible.io/', 'https://usefathom.com/pricing', 'https://simpleanalytics.com/pricing', 'https://www.simpleanalytics.com/pricing', 'https://buttondown.com/pricing'],
              'unseen_test_urls': 'withheld by independent evaluator', 'max_view_bytes': 2500,
              'max_source_bytes': MAX_BODY, 'maximum_external_writes': 0,
              'hypothesis': 'Preserve a bounded identity/primary-offer lead, recognize zero-priced capacity offers without currency symbols, and diversify verbatim evidence across paid prices, trials and commercial conditions under the same complete JSON byte budget.',
              'proposal_origin': 'AI coding agent generated candidate after reading original failure reports; human/operator starts the run.',
              'engine_runtime_integration': False, 'economic_improvement_claim': False,
              'model_cost': None, 'complete_cost': None,
              'acceptance_rule': 'Every source retains all independent source-backed oracle claims with coverage at least baseline, complete view within byte budget, exact source spans, and at least one source has strictly more coverage than baseline.',
              'rollback_rule': 'After technical adoption, an independent new observation with lost required claim coverage or a non-source span rolls the head back to baseline.'}
    save(output, record)
    print(json.dumps({'freeze': str(Path(output).name), 'candidate_sha256': record['candidate_sha256'], 'baseline_sha256': record['baseline_sha256']}))


def check_freeze(path):
    record = json.loads(Path(path).read_text())
    for field, file in [('candidate_sha256', HERE / 'candidate.py'), ('harness_sha256', Path(__file__)), ('baseline_sha256', baseline_path())]:
        if sha(file.read_bytes()) != record[field]: raise ValueError('frozen_code_changed_' + field)
    return record


def normalize(value):
    return ' '.join(value.split()).casefold()


def compare(args):
    frozen = check_freeze(args.freeze)
    folder = Path(args.out)
    source_path = folder / 'private/source.json'
    if source_path.exists():
        source = json.loads(source_path.read_text()); network_ms = None; provider_gets = 0
        if args.url != source['url']: raise ValueError('original_source_url_mismatch')
    else:
        source, raw, network_ms = fetch(args.url); provider_gets = source['provider_gets']
        save(source_path, source)
        (folder / 'private/source.html').write_bytes(raw)
    baseline = load_module(baseline_path(), 'original_compactor').compact_public_source(source, frozen['max_view_bytes'])
    candidate = load_module(HERE / 'candidate.py', 'candidate_method').select(source, frozen['max_view_bytes'])
    save(folder / 'private/baseline-view.json', baseline)
    save(folder / 'private/candidate-view.json', candidate)
    receipt = {'schema': 'nuluno-method-observation-v1', 'url': source['url'], 'status': source['status'],
               'source_body_sha256': source['body_sha256'], 'source_text_sha256': sha(source['text'].encode()),
               'frozen_candidate_sha256': frozen['candidate_sha256'], 'freeze_sha256': sha(Path(args.freeze).read_bytes()),
               'fetch_ms': network_ms, 'actual_provider_gets_this_invocation': provider_gets,
               'baseline_view_bytes': len(canonical(baseline)), 'candidate_view_bytes': candidate['view_bytes'],
               'source_span_integrity': candidate['view']['text'] == '\n[…]\n'.join(source['text'][a:b] for a,b in candidate['source_spans']),
               'budget_pass': candidate['view_bytes'] <= frozen['max_view_bytes'],
               'engine_runtime_integration': False, 'economic_improvement_proven': False,
               'sales_result': None, 'model_cost': None, 'complete_cost': None}
    if args.oracle:
        oracle = json.loads(Path(args.oracle).read_text())
        if oracle.get('source_body_sha256') != source['body_sha256'] or oracle.get('independent_from_candidate') is not True:
            raise ValueError('original_source_independent_oracle_required')
        claims = oracle['claims']
        if not claims or len({c['id'] for c in claims}) != len(claims): raise ValueError('complete_unique_claim_denominator_required')
        original = normalize(source['text'])
        rows = []
        for claim in claims:
            quote = normalize(claim['quote'])
            if not quote or quote not in original: raise ValueError('oracle_claim_not_in_original_source')
            rows.append({'id': claim['id'], 'claim_quote_sha256': sha(quote.encode()),
                         'baseline_visible': quote in normalize(baseline['text']),
                         'candidate_visible': quote in normalize(candidate['view']['text'])})
        b = sum(c['baseline_visible'] for c in rows); c = sum(c['candidate_visible'] for c in rows)
        quality = receipt['source_span_integrity'] and receipt['budget_pass'] and c == len(rows)
        receipt.update(oracle_sha256=sha(Path(args.oracle).read_bytes()), claims=rows,
                       baseline_coverage=b, candidate_coverage=c, claim_denominator=len(rows),
                       independent_oracle_attestation='supplied by evaluator; authorship is not cryptographically proven',
                       gate='eligible_for_technical_adoption' if quality and c > b else 'rejected_or_no_improvement',
                       quality_pass=quality)
    else:
        receipt['gate'] = 'waiting_independent_oracle'
    save(folder / 'observation.json', receipt)
    print(json.dumps(receipt, sort_keys=True))


def decide(args):
    frozen = check_freeze(args.freeze)
    observation = json.loads(Path(args.observation).read_text())
    if observation['frozen_candidate_sha256'] != frozen['candidate_sha256']: raise ValueError('candidate_receipt_mismatch')
    path = Path(args.state); state = json.loads(path.read_text()) if path.exists() else {'head': 'baseline', 'events': []}
    event = {'observation_sha256': sha(Path(args.observation).read_bytes()), 'at': time.time()}
    if observation.get('gate') == 'eligible_for_technical_adoption':
        state['head'] = frozen['candidate_sha256']; event['decision'] = 'adopted_technical_sidecar'
    elif state['head'] == frozen['candidate_sha256'] and observation.get('quality_pass') is False:
        state['head'] = 'baseline'; event['decision'] = 'rolled_back_after_failed_observation'
    else:
        event['decision'] = 'rejected_or_no_change'
    state['events'].append(event); state['economic_adoption_proven'] = False; state['engine_runtime_integration'] = False
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical(state) + b'\n')
    print(json.dumps(state))


def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('freeze'); p.add_argument('--out', required=True)
    p = sub.add_parser('compare'); p.add_argument('--freeze', required=True); p.add_argument('--url', required=True); p.add_argument('--oracle'); p.add_argument('--out', required=True)
    p = sub.add_parser('decide'); p.add_argument('--freeze', required=True); p.add_argument('--observation', required=True); p.add_argument('--state', required=True)
    args = parser.parse_args()
    if args.command == 'freeze': freeze(args.out)
    elif args.command == 'compare': compare(args)
    else: decide(args)


if __name__ == '__main__': main()
