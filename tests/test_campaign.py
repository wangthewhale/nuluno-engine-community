"""Real SQLite and loopback HTTP: source binding, artifacts and safe resume.

The controlled source is not a customer, a sales event or a learning result.
"""
import copy
import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from nuluno_engine_community.growth_loop.campaign import Campaign, extract_facts
from nuluno_engine_community.growth_loop.errors import WorkflowError
from nuluno_engine_community.growth_loop.service import Service, input_schema
from nuluno_engine_community.growth_loop.store import Store
from nuluno_engine_community.growth_loop.tooling import fetch_public as actual_fetch


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.store = Store(self.tmp.name)
        self.store.create_run({'mode': 'fixture', 'brand': {'url': 'https://product.example', 'name': 'Example'},
            'workflow': {'id': 'single', 'skill_id': 'S01'}, 'budget': {'currency': 'USD', 'total_minor': 0}}, 'original')
        self.settings = {'runs': ['original'], 'brand_urls': ['https://product.example'], 'commercial': {'tenant_id': 'operator'},
            'public_read_policy': {'read_limit': 2}, 'tool_routes': {'public-source': {'enabled': True,
                'transport': 'builtin_public_web', 'mode': 'read', 'tool_name': 'fetch_public',
                'brand_urls': ['https://product.example'], 'allowed_hosts': ['product.example'],
                'allowed_urls': ['https://product.example/']}}}
        self.reads = 0
        self.body = ('<h1>Example Product</h1><p>研究用公開產品。</p>' + '<p>Unrelated navigation padding</p>' * 200 +
            '<h2>Price</h2><p>USD 19 per month</p><p>Download the existing software after purchase.</p>' +
            '<p>Interactive demo is available.</p><p>Requires your own account.</p>').encode()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.reads += 1
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.end_headers(); self.wfile.write(owner.body)
            def log_message(self, *_): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        def fetch(url, hosts, fixture):
            self.assertEqual(url, 'https://product.example/')
            result = actual_fetch('http://127.0.0.1:' + str(self.server.server_port) + '/', ['127.0.0.1'], True)
            return {**result, 'url': url, 'requested_url': url}
        self.patch = patch('nuluno_engine_community.growth_loop.tooling.fetch_public', side_effect=fetch); self.patch.start()
        self.campaign = Campaign(self.store, self.settings)

    def tearDown(self):
        self.patch.stop(); self.store.close(); self.server.shutdown(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()

    def research(self):
        return self.campaign.research('original', 'original-research', ['https://product.example/'])

    def candidate(self, research):
        ids = [f['id'] for f in research['fact_candidates'] if f['kind'] in ('product', 'price', 'delivery', 'demo')]
        return {'selected_fact_ids': ids,
            'angles': [{'id': 'work', 'audience': 'Independent teams', 'motive': 'Understand current product terms',
                'hypothesis': 'Make product evaluation easier to complete', 'fact_ids': ids[:4]},
                {'id': 'clarity', 'audience': 'Technical buyers', 'motive': 'Reduce uncertainty',
                'hypothesis': 'Check the source before choosing software', 'fact_ids': ids[:4]}],
            'variants': [{'id': 'control', 'angle_id': 'work', 'headline': 'Evaluate the product with its original sources',
                'subheadline': 'Review publisher statements and decide what to check next.', 'ad_headline': 'A clearer product evaluation', 'cta_label': 'View the product'},
                {'id': 'candidate', 'angle_id': 'clarity', 'headline': 'Know which product details are actually published',
                'subheadline': 'Keep unknown conditions visible before purchasing.', 'ad_headline': 'Start with verifiable details', 'cta_label': 'Read original terms'}],
            'experiment': {'control_variant': 'control', 'candidate_variant': 'candidate',
                'primary_metric': 'qualified_conversion', 'assignment_unit': 'visitor', 'min_sample_per_arm': 100}}

    def test_reads_beyond_prefix_and_unicode_fragments_match_original_body(self):
        research = self.research(); self.assertEqual(self.reads, 1)
        price = next(f for f in research['fact_candidates'] if f['kind'] == 'price' and 'USD 19' in f['value'])
        fragment = price['source']['fragment']; self.assertGreater(fragment['start'], 2500)
        original = self.store.artifact('original', 0, 'public_source.' + price['source']['receipt_id'])['result']
        self.assertEqual(original['text'][fragment['start']:fragment['end']], fragment['quote'])
        self.assertEqual(fragment['unit'], 'unicode_codepoints')
        self.assertEqual(hashlib.sha256(self.body).hexdigest(), price['source']['body_sha256'])
        self.assertEqual(hashlib.sha256(original['text'].encode()).hexdigest(), price['source']['text_sha256'])
        self.assertEqual(research['full_cost'], None); self.assertEqual(research['model_calls'], 0)

    def test_restart_reuses_original_read_and_build_without_network_or_new_event(self):
        research = self.research(); result = self.campaign.build('original', 'original-build', 'original-research', self.candidate(research))
        count = self.store.db.execute('SELECT count(*) FROM events').fetchone()[0]
        self.store.close(); self.store = Store(self.tmp.name); self.campaign = Campaign(self.store, self.settings)
        again = self.research(); built = self.campaign.build('original', 'original-build', 'original-research', self.candidate(research))
        self.assertTrue(again['reused_original_artifact']); self.assertTrue(built['reused_original_artifact'])
        self.assertEqual(result['artifact_digest'], built['artifact_digest']); self.assertEqual(self.reads, 1)
        self.assertEqual(count, self.store.db.execute('SELECT count(*) FROM events').fetchone()[0])
        self.assertEqual(self.campaign.read('original', result['id']), result)
        with self.assertRaisesRegex(WorkflowError, 'changed'):
            altered = self.candidate(research); altered['variants'][0]['headline'] = 'Changed'
            self.campaign.build('original', 'original-build', 'original-research', altered)

    def test_foreign_url_and_all_urls_checked_before_any_read(self):
        with self.assertRaises(WorkflowError):
            self.campaign.research('original', 'different', ['https://product.example/', 'https://other.example/'])
        self.assertEqual(self.reads, 0)

    def test_public_campaign_rejects_secret_query_even_if_operator_listed_url(self):
        url = 'https://product.example/?client_secret=CONTROLLED_DO_NOT_ECHO'
        self.settings['tool_routes']['public-source']['allowed_urls'].append(url)
        with self.assertRaisesRegex(ValueError, 'credentials_query_forbidden') as caught:
            self.campaign.research('original', 'no-url-secret', [url])
        self.assertNotIn('CONTROLLED_DO_NOT_ECHO', str(caught.exception)); self.assertEqual(self.reads, 0)
        with self.assertRaises(WorkflowError): self.campaign.research('original', 'different', ['https://product.example/private'])
        with self.assertRaises(WorkflowError): self.campaign.research('original', 'different', ['http://product.example/'])
        self.assertEqual(self.reads, 0)

    def test_candidate_cannot_invent_price_guarantee_or_bind_foreign_fact(self):
        research = self.research()
        for text in ('Only USD 1 for this product', 'Guaranteed growth', 'Grow sales by 50%', 'Never fail again'):
            body = self.candidate(research); body['variants'][0]['headline'] = text
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'exact_source_quote'):
                self.campaign.build('original', 'bad-' + hashlib.sha256(text.encode()).hexdigest()[:8], 'original-research', body)
        body = self.candidate(research); body['selected_fact_ids'][0] = 'foreign-receipt-fact'
        with self.assertRaisesRegex(ValueError, 'original_fact_ids'): self.campaign.build('original', 'bad-foreign', 'original-research', body)
        body = self.candidate(research); body['angles'][0]['hypothesis'] = 'USD 19 per month'
        result = self.campaign.build('original', 'exact-quote', 'original-research', body)
        self.assertEqual(result['angles'][0]['status'], 'unvalidated_hypothesis')
        self.assertFalse(result['sales_lift_verified']); self.assertFalse(result['experiment']['adoption_authorized'])

    def test_html_is_escaped_and_no_candidate_script_or_event_endpoint(self):
        research = self.research(); body = self.candidate(research)
        body['variants'][0]['headline'] = '<img src=x onerror=alert("bad")>'
        result = self.campaign.build('original', 'escaped', 'original-research', body)
        page = result['outputs']['landing_html_by_variant']['control']
        self.assertNotIn('<img', page); self.assertIn('&lt;img', page)
        self.assertNotIn('<script', page); self.assertNotIn('/events', page)
        self.assertIn('data-nuluno-variant="control"', page)
        self.assertIn('href="https://product.example"', page)
        self.assertIn('No conversion or sales improvement', page)
        body['javascript'] = 'alert(1)'
        with self.assertRaisesRegex(ValueError, 'unknown_or_missing_fields'): self.campaign.build('original', 'script', 'original-research', body)

    def test_tampered_original_receipt_cannot_validate_fact(self):
        research = self.research(); body = self.candidate(research)
        f = research['fact_candidates'][0]; key = 'public_source.' + f['source']['receipt_id']
        original = self.store.artifact('original', 0, key); original['result']['text'] += 'tampered'
        self.store.artifact('original', 0, key, original)
        with self.assertRaisesRegex(ValueError, 'fragment_mismatch'): self.campaign.build('original', 'tampered', 'original-research', body)

    def test_service_scopes_tools_and_events_are_cursor_readable(self):
        service = Service(self.store, 'external', self.settings)
        research = service.run('campaign_research', {'run_id': 'original', 'request_id': 'original-research', 'urls': ['https://product.example/']})
        result = service.run('campaign_build', {'run_id': 'original', 'request_id': 'build', 'source_request_id': 'original-research', 'body': self.candidate(research)})
        first = service.run('campaign_events', {'run_id': 'original', 'limit': 2})
        second = service.run('campaign_events', {'run_id': 'original', 'after': first['next_after']})
        self.assertTrue(first['events']); self.assertTrue(second['events'])
        self.assertTrue(all(e['seq'] > first['next_after'] for e in second['events']))
        self.assertEqual(service.run('campaign_read', {'run_id': 'original', 'artifact_id': result['id']})['artifact_digest'], result['artifact_digest'])
        with self.assertRaises(WorkflowError): service.run('campaign_read', {'run_id': 'different', 'artifact_id': result['id']})
        restricted = Service(self.store, 'external', {**self.settings, 'methods': ['campaign_read']})
        with self.assertRaises(ValueError): restricted.run('campaign_build', {'run_id': 'original', 'request_id': 'other', 'source_request_id': 'original-research', 'body': self.candidate(research)})
        self.assertFalse(input_schema('campaign_build')['additionalProperties'])

    def test_missing_prices_remain_unknown_and_no_effectiveness_is_inferred(self):
        self.body = '<h1>公開產品</h1><p>Plain description without commercial conditions.</p>'.encode()
        research = self.research(); self.assertIn('price', research['unknowns'])
        result = self.campaign.build('original', 'unknown', 'original-research', self.candidate(research))
        self.assertIn('price', result['unknowns']); self.assertFalse(result['offer']['offer_complete'])
        self.assertIsNone(result['experiment']['conversion_count']); self.assertIsNone(result['experiment']['causal_lift'])
        self.assertIsNone(result['full_cost']); self.assertFalse(result['sales_lift_verified'])

    def test_trial_navigation_and_nonfinancial_numbers_do_not_displace_price(self):
        text = 'Analytics\n' + 'Start free trial\n' * 15 + 'Reduce 4 kg of CO2 per year\n' + 'padding\n' * 500 + '$9\n$14\n$19'
        facts = extract_facts({'text': text, 'url': 'https://product.example/', 'body_sha256': 'a' * 64}, 'b' * 64)
        prices = [f for f in facts if f['kind'] == 'price']
        self.assertEqual(len(prices), 3)
        self.assertTrue(all(money in fact['value'] for money, fact in zip(['$9', '$14', '$19'], prices)))
        self.assertTrue(all(f['source']['fragment']['start'] > 2500 for f in prices))
        self.assertEqual(len([f for f in facts if f['value'] == 'Start free trial' and f['kind'] == 'delivery']), 1)

    def test_reextract_original_receipt_without_another_get_or_cross_run_input(self):
        original = self.research(); ids = [s['receipt_id'] for s in original['sources']]
        fresh = self.campaign.research('original', 'new-extractor-review', ['https://product.example/'], ids)
        self.assertEqual(fresh['sources'][0]['receipt_id'], ids[0]); self.assertEqual(self.reads, 1)
        with self.assertRaisesRegex(ValueError, 'same_run_original_receipt'):
            self.campaign.research('original', 'foreign-receipt', ['https://product.example/'], ['0' * 64])

    def test_caller_can_select_short_exact_original_fragments_not_submit_new_facts(self):
        research = self.research(); receipt = research['sources'][0]['receipt_id']
        original = self.store.artifact('original', 0, 'public_source.' + receipt)['result']['text']
        start = original.index('USD 19'); fragments = [{'source_index': 0, 'kind': 'price', 'start': start, 'end': start + len('USD 19 per month')}]
        quoted = self.campaign.research('original', 'reviewed-short-quotes', ['https://product.example/'], [receipt], fragments)
        self.assertEqual(quoted['fact_candidates'][0]['value'], 'USD 19 per month'); self.assertEqual(self.reads, 1)
        self.assertEqual(quoted['fact_candidates'][0]['category_status'], 'caller_selected_requires_review')
        with self.assertRaisesRegex(ValueError, 'bounds_invalid'):
            self.campaign.research('original', 'bad-offset', ['https://product.example/'], [receipt], [{**fragments[0], 'end': len(original) + 1}])


if __name__ == '__main__': unittest.main()
