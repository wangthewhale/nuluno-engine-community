"""Current, operator-bound tools. Discovery never creates an account or grant."""
import json
import math
from .files import digest
from .errors import WorkflowError


class CapabilityBroker:
    def __init__(self, store, config):
        self.store, self.config = store, config

    def routes(self, run_id):
        profile = self.store.config(run_id)
        fixture = profile.get('mode') == 'fixture'
        return {key: value for key, value in self.config.get('tool_routes', {}).items()
                if value.get('enabled') is True and profile['brand']['url'] in value.get('brand_urls', [])
                and bool(value.get('fixture_only')) == fixture}

    def discover(self, run_id, route_id):
        """Read tools/list from the configured process, without tools/call."""
        from .tooling import MCPStdioClient, ToolExecutor
        route = self.routes(run_id).get(route_id)
        if route is None:
            raise WorkflowError('工具未綁定本品牌或執行模式。', 'scope_mismatch')
        ToolExecutor(self.store, self.config).usage_gate(route)
        # Invalidate stale discovery before any fallible external operation.
        key = 'capability.schema.' + digest(route_id)
        self.store.artifact(run_id, 0, key, {'state': 'pending', 'route_sha256': digest(route)})
        if route['transport'] == 'mcp_stdio':
            client = MCPStdioClient(route)
            try:
                tools = client.tools()
            finally:
                client.close()
            names = {route['tool_name']}
            names.update(part['tool_name'] for part in (route.get('probe'), route.get('readback'),
                         route.get('uncertain_readback'), route.get('original_read')) if part)
            specs = {tool['name']: tool.get('inputSchema', {}) for tool in tools if tool.get('name') in names}
            missing = sorted(names - set(specs))
        elif route['transport'] in ('builtin_public_web', 'builtin_media'):
            specs = {'fetch_public': {'type': 'object', 'properties': {'url': {'type': 'string'}},
                     'required': ['url'], 'additionalProperties': False}} if route['transport'] == 'builtin_public_web' else {
                route['tool_name']: {'type': 'object', 'properties': {'source': {'type': 'string'},
                    'output': {'type': 'string'}, 'size': {'type': 'array'}, 'duration': {'type': 'number'}},
                    'required': ['source'] + ([] if route['tool_name'].startswith('verify_') else ['output'])}}
            missing = []
        else:
            raise ValueError('尚無可讀取schema的實際工具傳輸')
        result = {'state': 'schema_verified' if not missing else 'schema_missing', 'route_id': route_id,
                  'route_sha256': digest(route), 'schemas': specs, 'schema_sha256': digest(specs),
                  'missing_tools': missing, 'observed_at': self.store.clock(),
                  'account_permission_verified': False, 'external_text_is_untrusted_data': True}
        self.store.artifact(run_id, 0, key, result)
        return result

    def inventory(self, run_id):
        now = self.store.clock(); age = self.config.get('verification_max_age', 3600)
        if type(age) not in (int, float) or not math.isfinite(age) or not 0 < age <= 86400:
            raise ValueError('工具有效期需為0至86400秒內正數')
        result = []
        profile = self.store.config(run_id)
        for ident, route in sorted(self.routes(run_id).items()):
            probe = self.store.artifact(run_id, 0, 'tool.probe.' + digest(ident)) or {}
            native = probe.get('inventory', {})
            stamp = native.get('verified_at', 0)
            valid = (probe.get('state') == 'identity_or_source_read_verified'
                     and probe.get('route_sha256') == digest(route)
                     and type(stamp) in (int, float) and now - age <= stamp <= now + 300)
            channel = route.get('channel')
            if channel not in ('web', 'local', 'audience'):
                account = profile.get('bindings', {}).get(channel, {}).get('account_ref')
                valid = bool(valid and account and native.get('account_ref') == account)
            schema = self.store.artifact(run_id, 0, 'capability.schema.' + digest(ident)) or {}
            schema_valid = (schema.get('state') == 'schema_verified'
                            and schema.get('route_sha256') == digest(route)
                            and now - age <= schema.get('observed_at', 0) <= now + 300)
            result.append({'route_id': ident, 'route_sha256': digest(route), 'channel': channel,
                'skill_ids': route.get('skill_ids', []), 'capabilities': route.get('capabilities', []),
                'mode': route.get('mode'), 'actions': route.get('actions', []),
                'identity_verified': valid, 'schema_verified': schema_valid,
                'schema_sha256': schema.get('schema_sha256') if schema_valid else None,
                'state': 'verified' if valid and schema_valid else 'needs_probe_or_schema',
                'account_ref': native.get('account_ref') if valid else None,
                'verified_at': stamp if valid else None})
        return {'schema': 'growth-capability-inventory-v1', 'brand_url': profile['brand']['url'],
                'routes': result, 'inventory_sha256': digest({'routes':result,'host_capabilities':sorted(self.config.get('capabilities',[]))}), 'observed_at': now,
                'host_capabilities': sorted(self.config.get('capabilities', [])),
                'tool_registration_is_not_execution': True, 'account_grants_unchanged': True}

    def select(self, run_id, skill_id, verified_only=True):
        return [r for r in self.inventory(run_id)['routes'] if skill_id in r['skill_ids']
                and (not verified_only or r['state'] == 'verified')]

    def readiness(self, run_id):
        """All 99 original goal contracts, with actual probe/source evidence."""
        from .execution import readiness
        from .files import read_json
        inventory = self.inventory(run_id)
        traces = [json.loads(r[0]) for r in self.store.db.execute(
            "SELECT data FROM artifacts WHERE run_id=? AND kind LIKE 'tool.execution.%'", (run_id,))]
        grant = self.store.root / 'authorization.json'
        result = readiness(self.store.config(run_id), self.config, traces,
            connected=(self.store.artifact(run_id, 0, 'connected_accounts') or {}).get('accounts', []),
            authorization=read_json(grant) if grant.exists() else None, now=self.store.clock())
        return {'inventory': inventory, 'goals': result, 'live_business_success_inferred': False}
