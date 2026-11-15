"""Real loopback HTTP classification; no production grants or external writes."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from nuluno_engine_community.growth_loop.sdk import GrowthClient, GrowthTransportError, GrowthRPCError, USER_AGENT


class SDKIdentityTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.status, self.content_type, self.reply = 200, 'application/json', b'{"result":{"preserved":true}}'
        self.edge_server = 'cloudflare'
        self.challenge = None
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                owner.received.append({'path': self.path, 'headers': dict(self.headers), 'body': body})
                self.send_response_only(owner.status)
                self.send_header('Content-Type', owner.content_type)
                if owner.edge_server: self.send_header('Server', owner.edge_server)
                if owner.challenge: self.send_header('WWW-Authenticate', owner.challenge)
                self.send_header('Content-Length', str(len(owner.reply)))
                self.end_headers()
                self.wfile.write(owner.reply)
            def log_message(self, *_): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = GrowthClient('http://127.0.0.1:' + str(self.server.server_port), 'original-worker', 'CONTROLLED_SDK_TOKEN')
        self.env = patch.dict(os.environ, {'CONTROLLED_SDK_TOKEN': 'controlled-not-production-private-material'})
        self.env.start()
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.env.stop()
    def test_truthful_version_identity_preserves_original_auth_and_body(self):
        self.assertEqual(self.client.call('status', run_id='original'), {'preserved': True})
        request = self.received[0]
        self.assertEqual(request['headers']['User-Agent'], USER_AGENT)
        self.assertEqual(request['headers']['Authorization'], 'Bearer controlled-not-production-private-material')
        self.assertEqual(request['headers']['X-Growth-Worker'], 'original-worker')
        self.assertEqual(request['path'], '/v1/rpc')
        self.assertEqual(json.loads(request['body']), {'method': 'status', 'params': {'run_id': 'original'}})
    def test_cloudflare_1010_is_client_identity_not_new_authorization(self):
        self.status, self.content_type = 403, 'text/html; charset=UTF-8'
        self.reply = b'<html>Cloudflare <h1>Error <span>1010</span></h1>private-ip-192.0.2.42 controlled-not-production-private-material</html>'
        with self.assertRaises(GrowthTransportError) as caught: self.client.call('acquire', run_id='original')
        error = caught.exception
        self.assertEqual(error.code, 'transport_client_identity_rejected')
        self.assertFalse(error.requiresOwnerAuthorization)
        self.assertNotIn('192.0.2', str(error)); self.assertNotIn('private-material', str(error))
        self.assertEqual(len(self.received), 1, 'No automatic retry or replacement')
    def test_actual_cloudflare_plain_1010_requires_its_server_marker(self):
        self.status, self.content_type, self.reply = 403, 'text/plain; charset=UTF-8', b'error code: 1010\n'
        with self.assertRaises(GrowthTransportError) as caught: self.client.call('status')
        self.assertEqual(caught.exception.code, 'transport_client_identity_rejected')
        self.assertFalse(caught.exception.requiresOwnerAuthorization)
        self.edge_server = ''
        with self.assertRaisesRegex(ValueError, '^RPC request rejected$'): self.client.call('status')
        self.edge_server = 'cloudflare'
        self.reply = b'error code: 1010\nprivate-ip-192.0.2.42'
        with self.assertRaisesRegex(ValueError, '^RPC request rejected$'): self.client.call('status')
    def test_cloudflare_block_page_without_auth_challenge_is_edge_rejection(self):
        self.status, self.content_type = 403, 'text/html'
        self.reply = b'<html><title>Attention Required! | Cloudflare</title><h1 data-translate="block_headline">Sorry, you have been blocked</h1>private-ip-192.0.2.42</html>'
        with self.assertRaises(GrowthTransportError) as caught: self.client.call('status')
        self.assertEqual(caught.exception.code, 'transport_edge_rejected')
        self.assertFalse(caught.exception.requiresOwnerAuthorization)
        self.assertNotIn('192.0.2', str(caught.exception))
        self.challenge = 'Bearer realm="actual-auth-challenge"'
        with self.assertRaisesRegex(ValueError, '^RPC request rejected$'): self.client.call('status')
    def test_real_401_and_structured_403_remain_authorization_denials(self):
        self.status = 401
        self.reply = b'{"error":{"kind":"invalid_key"}}'
        with self.assertRaises(GrowthTransportError) as caught: self.client.call('status')
        self.assertTrue(caught.exception.requiresOwnerAuthorization)
        self.assertEqual(caught.exception.code, 'transport_authorization_rejected')
        self.status = 403
        self.reply = b'{"error":{"kind":"auth","message":"denied controlled-not-production-private-material"}}'
        with self.assertRaises(GrowthRPCError) as caught: self.client.call('status')
        self.assertTrue(caught.exception.requiresOwnerAuthorization)
        self.assertEqual(caught.exception.kind, 'auth')
        self.assertNotIn('controlled-not-production-private-material', str(caught.exception))
    def test_arbitrary_html_1010_or_json_mentions_are_not_edge_identity_evidence(self):
        self.status, self.content_type = 403, 'text/html'
        self.reply = b'<html>Cloudflare catalog has 1010 products</html>'
        with self.assertRaisesRegex(ValueError, '^RPC request rejected$'): self.client.call('status')
        self.content_type = 'application/json'
        self.reply = b'{"error":{"kind":"invalid_key","message":"Cloudflare Error 1010"}}'
        with self.assertRaises(GrowthRPCError) as caught: self.client.call('status')
        self.assertEqual(caught.exception.kind, 'invalid_key'); self.assertTrue(caught.exception.requiresOwnerAuthorization)

if __name__ == '__main__': unittest.main()
