"""Source-bound campaign deliverables over the existing public-read receipts.

No model, ad account, publisher, event collector, or new execution authority is
introduced here. Creative text is a caller's unvalidated hypothesis. A quoted
publisher statement is proof of what that publisher said, not proof it is true.
"""
import hashlib
import html
import json
import re
from urllib.parse import urlsplit, parse_qsl

from .files import canonical, digest
from .errors import WorkflowError

SCHEMA = 'nuluno-campaign-v1'
KINDS = ('product', 'price', 'delivery', 'demo', 'limitation', 'feature')
RISKY_CLAIM = re.compile(r'\d|[$€£%]|\b(?:free|guarantee\w*|unlimited|double|triple|risk.free|always|never|proven)\b|免費|保證|無限|翻倍|零風險|必定|永遠|已證實', re.I)
CREDENTIAL_QUERY = re.compile(r'token|password|passwd|secret|apikey|session|credential|authorization|verification|signature|jwt|code$|^key$|^auth$|^sig$|^ticket$|^otp$|^cfchltk$')
PATTERNS = {
    'price': re.compile(r'(?:US\$|USD|EUR|GBP|TWD|NT\$|\$|€|£)\s*\d|\d(?:[\d,.]*\d)?\s*(?:USD|EUR|GBP|TWD|美元|台幣|元/月|元/年)', re.I),
    'delivery': re.compile(r'download|install|deploy|self.host|deliver|subscription|one.time|trial|license|下載|安裝|交付|訂閱|買斷|授權', re.I),
    'demo': re.compile(r'\bdemo\b|try (?:it|now|for)|interactive|示範|試用|體驗', re.I),
    'limitation': re.compile(r'not (?:include|support|guarantee)|requires?|subject to|excluding|unknown|限制|不含|不保證|尚未|需要', re.I),
}


def _text(value, maximum=500):
    return isinstance(value, str) and 0 < len(value.strip()) <= maximum and '\x00' not in value


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value)


def _exact(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or any(k not in value for k in required):
        raise ValueError('campaign_unknown_or_missing_fields')


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def extract_facts(result, receipt_id):
    """Select exact quotations throughout the original bounded visible text.

    Offsets are Unicode codepoint offsets into the receipt's text, not offsets
    into its raw HTML bytes. Both hashes are supplied so a consumer can verify
    the distinction. Category selection is a heuristic and is never a model.
    """
    text = result.get('text', '')
    if not isinstance(text, str):
        raise ValueError('campaign_source_text_required')
    fragments = []
    # HTML reads already have visible lines. Also handle long JSON/Markdown
    # lines: select around a relevant token, never cut only the document prefix.
    for line in re.finditer(r'[^\n]+', text):
        raw = line.group(); leading = len(raw) - len(raw.lstrip())
        raw = raw.strip(); start = line.start() + leading
        if len(raw) < 3 and not PATTERNS['price'].search(raw):
            continue
        if len(raw) <= 400:
            fragments.append((start, start + len(raw)))
        else:
            for pattern in PATTERNS.values():
                for match in list(pattern.finditer(raw))[:12]:
                    lo = max(0, match.start() - 100); hi = min(len(raw), match.end() + 180)
                    fragments.append((start + lo, start + hi))
            if start < 1000:
                fragments.append((start, start + 280))
    selected, seen = {}, set()
    for start, end in dict.fromkeys(fragments):
        quote = text[start:end]
        kinds = [kind for kind, pattern in PATTERNS.items() if pattern.search(quote)]
        if start < 1500:
            kinds += ['product' if start < 250 else 'feature']
        if not kinds:
            kinds = ['feature']
        # Exact quotes may be relevant to several fields; do not invent a
        # normalized price/currency, delivery promise, or a demo URL.
        for kind in kinds:
            if (kind, quote) in seen:
                continue
            seen.add((kind, quote))
            score = (60 if kind != 'feature' else 10) - min(start / 40000, 1)
            lo, hi = start, end
            if kind == 'price':
                # Include nearby plan/billing labels. A bare '$9' should not
                # silently become a USD/month promise without its context.
                before = [m.start() for m in re.finditer('\n', text[:start])]
                after = [end + m.start() for m in re.finditer('\n', text[end:])]
                context_lo = before[-4] + 1 if len(before) >= 4 else 0
                context_hi = after[3] if len(after) >= 4 else len(text)
                if context_hi - context_lo <= 400: lo, hi = context_lo, context_hi
            quoted = text[lo:hi]
            item = {'id': 'fact-' + digest([receipt_id, lo, hi, kind])[:20],
                'kind': kind, 'value': quoted, 'status': 'publisher_observed',
                'category_status': 'heuristic_requires_review',
                'source': {'receipt_id': receipt_id, 'url': result['url'],
                    'body_sha256': result['body_sha256'], 'text_sha256': _sha(text),
                    'fragment': {'start': lo, 'end': hi, 'quote': quoted,
                        'coordinate_system': 'visible_text', 'unit': 'unicode_codepoints', 'hash_encoding': 'utf-8'}},
                'independently_verified': False}
            selected[item['id']] = (score, start, item)
    # Per-kind coverage avoids all price lines displacing product information.
    chosen = []
    for kind in KINDS:
        chosen += sorted((v for v in selected.values() if v[2]['kind'] == kind), key=lambda v: (-v[0], v[1]))[:6]
    return [v[2] for v in sorted(chosen, key=lambda v: (v[1], v[2]['kind']))]


class Campaign:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        store.db.execute('''CREATE TABLE IF NOT EXISTS campaign_requests(
            run_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL,
            binding TEXT NOT NULL, response TEXT, PRIMARY KEY(run_id,id))''')

    def _request(self, run_id, request_id, kind, body):
        if not _identifier(request_id):
            raise ValueError('campaign_stable_request_id_required')
        if (self.store.root / '.transfer.lock').exists():
            raise WorkflowError('Workspace is frozen for transfer.', 'waiting_capability')
        binding = digest(body)
        with self.store.transaction():
            old = self.store.db.execute('SELECT * FROM campaign_requests WHERE run_id=? AND id=?', (run_id, request_id)).fetchone()
            if old:
                if old['kind'] != kind or old['binding'] != binding:
                    raise WorkflowError('Same campaign request changed; keep the original request.', 'payload_changed')
                if old['response']:
                    return {**json.loads(old['response']), 'reused_original_artifact': True}
            else:
                self.store.db.execute('INSERT INTO campaign_requests VALUES(?,?,?,?,NULL)', (run_id, request_id, kind, binding))
                self.store.event(run_id, None, 'campaign_' + kind + '_started', {'request_id': request_id})
        return None

    def _finish(self, run_id, request_id, value):
        # Serialize local commit only. Network reads and candidate generation
        # occur outside the transaction and use their original child receipts.
        with self.store.transaction():
            old = self.store.db.execute('SELECT response FROM campaign_requests WHERE run_id=? AND id=?', (run_id, request_id)).fetchone()
            if old['response']:
                return {**json.loads(old['response']), 'reused_original_artifact': True}
            self.store.artifact(run_id, 0, 'campaign.' + value['id'], value)
            self.store.db.execute('UPDATE campaign_requests SET response=? WHERE run_id=? AND id=?', (canonical(value), run_id, request_id))
            self.store.event(run_id, None, 'campaign_' + value['type'] + '_saved', {'artifact_id': value['id'], 'request_id': request_id,
                'state': value['state'], 'external_writes': 0})
        return value

    def research(self, run_id, request_id, urls, receipt_ids=None, fragments=None):
        from .standalone import read_public_source
        if not isinstance(urls, list) or not 1 <= len(urls) <= 6 or len(set(urls)) != len(urls) or any(not _text(u, 2048) for u in urls):
            raise ValueError('campaign_one_to_six_unique_source_urls_required')
        # Validate every URL before any read. read_public_source repeats the
        # actual pinned-route, DNS, redirect and byte-bound checks.
        route = self.settings.get('tool_routes', {}).get('public-source', {})
        for url in urls:
            parsed = urlsplit(url)
            if any(CREDENTIAL_QUERY.search(re.sub('[^a-z0-9]', '', key.lower())) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
                raise ValueError('campaign_public_source_credentials_query_forbidden')
            if parsed.scheme != 'https' or parsed.hostname not in route.get('allowed_hosts', []) or parsed.username or parsed.password or parsed.fragment:
                raise WorkflowError('Campaign source is outside the original public route.', 'scope_mismatch')
            if 'allowed_urls' in route and url not in route['allowed_urls']:
                raise WorkflowError('Campaign source is outside the original exact URL list.', 'scope_mismatch')
        if receipt_ids is not None and (not isinstance(receipt_ids, list) or len(receipt_ids) != len(urls) or any(not isinstance(r, str) or not re.fullmatch('[a-f0-9]{64}', r) for r in receipt_ids)):
            raise ValueError('campaign_original_receipt_ids_required')
        binding = {'urls': urls, 'route_sha256': digest(route)}
        if receipt_ids is not None: binding['receipt_ids'] = receipt_ids
        if fragments is not None:
            if not isinstance(fragments, list) or not 1 <= len(fragments) <= 24:
                raise ValueError('campaign_bounded_original_fragments_required')
            for f in fragments:
                _exact(f, ('source_index', 'kind', 'start', 'end'), ('source_index', 'kind', 'start', 'end'))
                if type(f['source_index']) is not int or not 0 <= f['source_index'] < len(urls) or f['kind'] not in KINDS or type(f['start']) is not int or type(f['end']) is not int or not 0 <= f['start'] < f['end'] <= f['start'] + 400:
                    raise ValueError('campaign_original_fragment_bounds_invalid')
            binding['fragments'] = fragments
        old = self._request(run_id, request_id, 'research', binding)
        if old:
            return old
        facts, sources = [], []
        for i, url in enumerate(urls):
            child = 'campaign-' + digest([request_id, i, url])[:40]
            if receipt_ids is None:
                read = read_public_source(self.store, self.settings, run_id, url, False, child)
            else:
                original = self.store.artifact(run_id, 0, 'public_source.' + receipt_ids[i])
                if not original or digest(original) != receipt_ids[i] or original['result'].get('requested_url', original['url']) != url:
                    raise ValueError('campaign_same_run_original_receipt_required')
                read = {**original, 'receipt_id': receipt_ids[i], 'reused_original_receipt': True}
            value = read['result']
            sources.append({'receipt_id': read['receipt_id'], 'url': value['url'], 'requested_url': url,
                'body_sha256': value['body_sha256'], 'text_sha256': _sha(value['text']),
                'observed_at': read['observed_at'], 'text_truncated': value.get('text_truncated', False),
                'classification': read['classification']})
            extracted = extract_facts(value, read['receipt_id'])
            if fragments is not None:
                extracted = []
                for f in fragments:
                    if f['source_index'] != i: continue
                    if f['end'] > len(value['text']): raise ValueError('campaign_original_fragment_bounds_invalid')
                    quote = value['text'][f['start']:f['end']]
                    if not quote.strip(): raise ValueError('campaign_nonempty_original_fragment_required')
                    extracted.append({'id': 'fact-' + digest([read['receipt_id'], f['start'], f['end'], f['kind']])[:20],
                        'kind': f['kind'], 'value': quote, 'status': 'publisher_observed',
                        'category_status': 'caller_selected_requires_review', 'independently_verified': False,
                        'source': {'receipt_id': read['receipt_id'], 'url': value['url'], 'body_sha256': value['body_sha256'],
                            'text_sha256': _sha(value['text']), 'fragment': {'start': f['start'], 'end': f['end'], 'quote': quote,
                                'coordinate_system': 'visible_text', 'unit': 'unicode_codepoints', 'hash_encoding': 'utf-8'}}})
            facts += extracted
            self.store.event(run_id, None, 'campaign_source_read', {'request_id': request_id, 'receipt_id': read['receipt_id'],
                'url': url, 'reused_original_receipt': read['reused_original_receipt']})
        # Keep the interactive result bounded while distributing choices over
        # fields/sources. Original full bounded text remains in read receipts.
        queues = [[f for f in facts if f['kind'] == kind] for kind in KINDS]
        facts = []
        for index in range(4):
            for queue in queues:
                if index < len(queue): facts.append(queue[index])
        value = {'schema': SCHEMA, 'type': 'research', 'id': 'research-' + digest([run_id, request_id])[:24],
            'run_id': run_id, 'request_id': request_id, 'product_url': self.store.config(run_id)['brand']['url'],
            'created_at': self.store.clock(), 'state': 'awaiting_candidate', 'sources': sources,
            'fact_candidates': facts, 'unknowns': [k for k in ('price', 'delivery', 'demo') if not any(f['kind'] == k for f in facts)],
            'extractor': 'caller_selected_original_quotations_v1' if fragments is not None else 'deterministic_visible_text_quotations_v1', 'model_calls': 0, 'external_writes': 0,
            'full_cost': None, 'sales_lift_verified': False, 'reused_original_artifact': False}
        return self._finish(run_id, request_id, value)

    def _research(self, run_id, request_id):
        row = self.store.db.execute("SELECT response FROM campaign_requests WHERE run_id=? AND id=? AND kind='research'", (run_id, request_id)).fetchone()
        if not row or not row['response']:
            raise ValueError('campaign_completed_original_research_required')
        return json.loads(row['response'])

    def build(self, run_id, request_id, source_request_id, candidate):
        _exact(candidate, ('selected_fact_ids', 'angles', 'variants', 'experiment'), ('selected_fact_ids', 'angles', 'variants', 'experiment'))
        if len(canonical(candidate).encode()) > 24000:
            raise ValueError('campaign_candidate_byte_limit')
        research = self._research(run_id, source_request_id)
        old = self._request(run_id, request_id, 'build', {'source_request_id': source_request_id, 'candidate': candidate})
        if old:
            return old
        by_id = {f['id']: f for f in research['fact_candidates']}
        ids = candidate['selected_fact_ids']
        if not isinstance(ids, list) or not 1 <= len(ids) <= 8 or len(set(ids)) != len(ids) or any(x not in by_id for x in ids):
            raise ValueError('campaign_original_fact_ids_required')
        facts = [by_id[x] for x in ids]
        # Revalidate every coordinate/hash against the actual stored receipt;
        # neither caller facts nor a changed manifest can certify a quotation.
        for fact in facts:
            source = fact['source']; original = self.store.artifact(run_id, 0, 'public_source.' + source['receipt_id'])
            if not original or original['result']['body_sha256'] != source['body_sha256']:
                raise ValueError('campaign_original_source_receipt_missing')
            text = original['result']['text']; fragment = source['fragment']
            if _sha(text) != source['text_sha256'] or text[fragment['start']:fragment['end']] != fact['value']:
                raise ValueError('campaign_original_fragment_mismatch')
        angles, variants = [], []
        if not isinstance(candidate['angles'], list) or not 1 <= len(candidate['angles']) <= 2:
            raise ValueError('campaign_one_to_two_angles_required')
        for angle in candidate['angles']:
            _exact(angle, ('id', 'audience', 'motive', 'hypothesis', 'fact_ids'), ('id', 'audience', 'motive', 'hypothesis', 'fact_ids'))
            refs = angle['fact_ids']
            if not _identifier(angle['id']) or angle['id'] in {a['id'] for a in angles} or any(not _text(angle[k], 700) for k in ('audience', 'motive', 'hypothesis')) or not isinstance(refs, list) or not 1 <= len(refs) <= 4 or len(set(refs)) != len(refs) or any(x not in ids for x in refs):
                raise ValueError('campaign_angle_original_fact_binding_required')
            self._claims([angle[k] for k in ('audience', 'motive', 'hypothesis')], [by_id[x]['value'] for x in refs])
            angles.append({**angle, 'status': 'unvalidated_hypothesis', 'causal_effect_proven': False,
                'fact_binding_is_not_entailment_or_sales_proof': True})
        if not isinstance(candidate['variants'], list) or len(candidate['variants']) != 2:
            raise ValueError('campaign_exactly_two_variants_required')
        for variant in candidate['variants']:
            _exact(variant, ('id', 'angle_id', 'headline', 'subheadline', 'ad_headline', 'cta_label'), ('id', 'angle_id', 'headline', 'subheadline', 'ad_headline', 'cta_label'))
            if not _identifier(variant['id']) or variant['id'] in {v['id'] for v in variants} or variant['angle_id'] not in {a['id'] for a in angles} or any(not _text(variant[k], 700) for k in ('headline', 'subheadline', 'ad_headline', 'cta_label')):
                raise ValueError('campaign_variant_invalid')
            angle = next(a for a in angles if a['id'] == variant['angle_id'])
            self._claims([variant[k] for k in ('headline', 'subheadline', 'ad_headline', 'cta_label')], [by_id[x]['value'] for x in angle['fact_ids']])
            sections = [{'fact_id': x, 'kind': by_id[x]['kind'], 'quote': by_id[x]['value'], 'source': by_id[x]['source']} for x in angle['fact_ids']]
            variants.append({'id': variant['id'], 'angle_id': variant['angle_id'], 'status': 'unvalidated_messaging_hypothesis',
                'ad': {'headline': variant['ad_headline'], 'body_quotes': [s['quote'] for s in sections], 'cta': variant['cta_label'], 'destination_url': research['product_url'], 'published': False},
                'landing': {'headline': variant['headline'], 'subheadline': variant['subheadline'], 'sections': sections,
                    'cta': {'label': variant['cta_label'], 'url': research['product_url']}}})
        spec = candidate['experiment']
        _exact(spec, ('control_variant', 'candidate_variant', 'primary_metric', 'assignment_unit', 'min_sample_per_arm'), ('control_variant', 'candidate_variant', 'primary_metric', 'assignment_unit', 'min_sample_per_arm'))
        if spec['control_variant'] == spec['candidate_variant'] or any(spec[k] not in {v['id'] for v in variants} for k in ('control_variant', 'candidate_variant')) or spec['primary_metric'] != 'qualified_conversion' or spec['assignment_unit'] != 'visitor' or type(spec['min_sample_per_arm']) is not int or not 20 <= spec['min_sample_per_arm'] <= 1000000:
            raise ValueError('campaign_preregistered_experiment_invalid')
        experiment = {**spec, 'state': 'awaiting_native_evidence', 'preregistered_at': self.store.clock(),
            'sample_plan_validated': False, 'conversion_source': 'owner_backend_verified', 'exposure_count': None,
            'conversion_count': None, 'causal_lift': None, 'adoption_authorized': False,
            'guardrails': ['same_product_and_original_receipts', 'sticky_assignment_before_exposure',
                'no_client_claimed_purchase', 'no_winner_before_sample_and_cost_verification', 'rollback_to_original_control'],
            'measurement_adapter_connected': False}
        value = {'schema': SCHEMA, 'type': 'build', 'id': 'campaign-' + digest([run_id, request_id])[:24],
            'run_id': run_id, 'request_id': request_id, 'source_request_id': source_request_id,
            'product_url': research['product_url'], 'created_at': self.store.clock(), 'state': 'ready_for_review',
            'sources': research['sources'], 'facts': facts, 'unknowns': [k for k in ('price', 'delivery', 'demo') if not any(f['kind'] == k for f in facts)],
            'angles': angles, 'variants': variants, 'experiment': experiment,
            'candidate_origin': 'caller_supplied_not_autonomous_model_learning', 'model_calls_by_engine': 0,
            'external_writes': 0, 'full_cost': None, 'sales_lift_verified': False,
            'reused_original_artifact': False}
        # Reuse the existing model-neutral commercial offer contract. Public
        # statements stay source_linked; no trusted-source registry is invented.
        from .commercial_contract import compileCommercialOffer
        commercial_facts = [{'id': f['id'], 'kind': f['kind'] if f['kind'] != 'feature' else 'benefit', 'value': f['value'],
            'source': {'reference': f['source']['url'], 'source_class': 'public_source',
                'verified_at': next(s['observed_at'] for s in value['sources'] if s['receipt_id'] == f['source']['receipt_id']),
                'content_sha256': f['source']['body_sha256']}} for f in facts if f['kind'] not in ('price', 'demo')]
        value['offer'] = compileCommercialOffer({'tenant_id': self.settings.get('commercial', {}).get('tenant_id', 'operator'),
            'brand_id': research['product_url'], 'product_id': research['product_url'], 'facts': commercial_facts,
            'value_propositions': [{'text': a['hypothesis'], 'fact_ids': a['fact_ids']} for a in angles if all(x in {f['id'] for f in commercial_facts} for x in a['fact_ids'])]}, self.store.clock())
        value['artifact_digest'] = digest(value)
        value['outputs'] = {'sales_brief_markdown': render_markdown(value),
            'landing_html_by_variant': {v['id']: render_landing(value, v) for v in variants}}
        if len(canonical(value).encode('utf-8')) > 60000:
            raise ValueError('campaign_artifact_byte_limit_select_fewer_facts')
        return self._finish(run_id, request_id, value)

    @staticmethod
    def _claims(copy, original_quotes):
        # A referenced source ID alone cannot certify a new price, quantity or
        # guarantee. Require the entire risky claim verbatim, not just a shared
        # number. This is conservative lexical validation, not LLM entailment.
        for text in copy:
            if RISKY_CLAIM.search(text) and not any(text in quote for quote in original_quotes):
                raise ValueError('campaign_quantified_or_guaranteed_claim_needs_exact_source_quote')

    def read(self, run_id, artifact_id):
        if not _identifier(artifact_id):
            raise ValueError('campaign_artifact_id_required')
        value = self.store.artifact(run_id, 0, 'campaign.' + artifact_id)
        if not value:
            raise ValueError('campaign_artifact_not_found')
        return value

    def events(self, run_id, after=0, limit=50):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('campaign_event_cursor_invalid')
        rows = self.store.db.execute("SELECT * FROM events WHERE run_id=? AND seq>? AND event LIKE 'campaign_%' ORDER BY seq LIMIT ?", (run_id, after, limit)).fetchall()
        return {'run_id': run_id, 'events': [{'seq': r['seq'], 'at': r['created'], 'event': r['event'], 'data': json.loads(r['data'])} for r in rows],
            'next_after': rows[-1]['seq'] if rows else after, 'activity_is_persisted_not_model_thoughts': True}


def render_markdown(campaign):
    lines = ['# Source-bound campaign brief', '', 'Status: ready for review. Creative copy is an unvalidated messaging hypothesis; publisher quotations are not independent truth or sales evidence.', '', '## Observed product statements']
    for f in campaign['facts']:
        lines += ['', f"- {f['id']} ({f['kind']}): {f['value']}", f"  Source: {f['source']['url']} | body SHA-256: {f['source']['body_sha256']}"]
    lines += ['', '## Audience hypotheses']
    for a in campaign['angles']:
        lines += ['', f"- {a['audience']} — {a['motive']}: {a['hypothesis']}"]
    lines += ['', 'Unknown fields: ' + (', '.join(campaign['unknowns']) or 'No required category is absent; category/meaning still needs review.'),
        '', 'Experiment: awaiting native exposure/conversion evidence. Costs, lift and economic adoption remain unknown/unverified.']
    return '\n'.join(lines) + '\n'


def render_landing(campaign, variant):
    e = lambda value: html.escape(str(value), quote=True)
    landing = variant['landing']
    sections = ''.join('<section data-nuluno-fact="' + e(s['fact_id']) + '"><h2>' + e(s['kind'].title()) + '</h2><blockquote>' + e(s['quote']) + '</blockquote><a rel="noreferrer noopener" href="' + e(s['source']['url']) + '">Publisher source</a></section>' for s in landing['sections'])
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + e(landing['headline']) + '</title><style>body{margin:0;background:#fff;color:#111;font:18px/1.6 system-ui,sans-serif}main{max-width:780px;margin:60px auto;padding:24px}aside{border-left:4px solid #b7f25b;padding:12px 18px;background:#f5f7f1;font-size:14px}h1{font-size:clamp(36px,7vw,64px);line-height:1.1}section{border-top:1px solid #ddd;margin:32px 0;padding-top:16px}blockquote{margin:0;white-space:pre-wrap}a{color:inherit}.cta{display:inline-block;background:#111;color:white;padding:14px 24px;text-decoration:none}.meta{font-size:12px;overflow-wrap:anywhere}</style></head><body><main data-nuluno-campaign="' + e(campaign['id']) + '" data-nuluno-digest="' + e(campaign['artifact_digest']) + '" data-nuluno-variant="' + e(variant['id']) + '"><aside>Messaging experiment draft. Headline and audience angle are unvalidated hypotheses. Publisher statements below are quoted from actual reads. No conversion or sales improvement has been demonstrated.</aside><h1>' + e(landing['headline']) + '</h1><p>' + e(landing['subheadline']) + '</p>' + sections + '<a class="cta" data-nuluno-cta="product" rel="noreferrer noopener" href="' + e(landing['cta']['url']) + '">' + e(landing['cta']['label']) + '</a><p class="meta">Artifact ' + e(campaign['artifact_digest']) + ' · Full cost: unknown · Measurement: awaiting native evidence</p></main></body></html>'
