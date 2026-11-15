/** Real public GET + original receipt reuse + process replacement + revocation.
 * No model, account login, platform write, payment or private inbox permission.
 */
import assert from 'node:assert/strict';
import {spawn, execFileSync} from 'node:child_process';
import {createServer} from 'node:net';
import {mkdtemp, chmod, writeFile, readFile, rename, rm, realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomBytes, createHash} from 'node:crypto';
import {connectClient, probeCompatibility, unwrap} from './compatibility.mjs';

async function main() {
const directory = fileURLToPath(new URL('.', import.meta.url));
const args = process.argv.slice(2);
let output;
if (args.length) {
  if (args.length !== 2 || args[0] !== '--output') throw new Error('Usage: node public-read-demo.mjs [--output receipt.json]');
  output = resolve(args[1]);
}
const source = new URL(process.env.ENGINE_DEMO_SOURCE ?? 'https://www.python.org/');
if (source.protocol !== 'https:' || source.username || source.password || source.search || source.hash) throw new Error('Use a public HTTPS source with no credentials, query or fragment.');
const brand = source.origin;
const python = process.env.ENGINE_PYTHON ?? 'python3';
const runId = 'public-read-demo', requestId = 'source-original';
const token = randomBytes(32).toString('base64url');
const privateRoot = await realpath(await mkdtemp(join(tmpdir(), 'nuluno-public-demo-')));
await chmod(privateRoot, 0o700);
const configPath = join(privateRoot, 'service.json'), workspace = join(privateRoot, 'state');
const childEnv = Object.fromEntries(['PATH','LANG','LC_ALL','SSL_CERT_FILE','SSL_CERT_DIR','SYSTEMROOT'].filter(name => process.env[name]).map(name => [name, process.env[name]]));
const methods = ['run_create', 'status', 'read_public_source', 'execution_metrics', 'cost_summary', 'memory_add', 'memory_search'];
const paidPack = process.env.NULUNO_DEMO_METHOD_PACK;
if (paidPack) methods.push('catalog');
let server, client, receipt, stage = 'initialize';
const delay = milliseconds => new Promise(done => setTimeout(done, milliseconds));
const reservePort = async () => {
  const reservation = createServer();
  await new Promise((done, reject) => { reservation.once('error', reject); reservation.listen(0, '127.0.0.1', done); });
  const port = reservation.address().port;
  await new Promise(done => reservation.close(done));
  return port;
};
const port = await reservePort(), origin = 'http://127.0.0.1:' + port;
const env = {...childEnv, ENGINE_PYTHON: python, ENGINE_CONFIG: configPath, ENGINE_WORKSPACE: workspace,
  ENGINE_ORIGIN: origin, ENGINE_BIND_HOST: '127.0.0.1', PORT: String(port), ENGINE_DEMO_BEARER: token};
if (paidPack) env.NULUNO_METHOD_PACK_DIR = join(privateRoot, 'method-packs');
const start = async () => {
  const process_ = spawn(process.execPath, [join(directory, 'server.mjs')], {env, stdio: ['ignore','ignore','ignore'], shell: false});
  let spawnFailed = false; process_.on('error', () => { spawnFailed = true; });
  server = process_;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (spawnFailed || process_.exitCode !== null) throw new Error('gateway_start_failed');
    try { const res = await fetch(origin + '/healthz', {signal: AbortSignal.timeout(1000)}); if (res.ok) return await res.json(); } catch {}
    await delay(100);
  }
  throw new Error('gateway_health_timeout');
};
const stop = async () => {
  if (!server) return;
  const original = server; server = null;
  if (original.exitCode !== null || !original.pid) return;
  await new Promise(done => {
    const timer = setTimeout(() => original.kill('SIGKILL'), 3000);
    original.once('exit', () => { clearTimeout(timer); done(); });
    original.kill('SIGTERM');
  });
};
const call = async (name, arguments_) => unwrap(await client.callTool({name, arguments: arguments_}));
try {
  const initialized = JSON.parse(execFileSync(python, ['-m', 'nuluno_engine_community.growth_loop.cli', 'init-service', '--workspace', workspace,
    '--config', configPath, '--brand-url', brand, '--brand-name', 'Public source demo', '--run-id', runId,
    '--token-env', 'ENGINE_DEMO_BEARER'], {env: childEnv, encoding: 'utf8', stdio: ['ignore','pipe','pipe'], timeout: 30000}));
  const config = JSON.parse(await readFile(configPath, 'utf8'));
  const settings = config.workers.external;
  settings.methods = methods;
  settings.capabilities = ['web.read', 'local.plan', 'agent.write', 'local.review'];
  settings.public_read_policy = {read_limit: 1, compact_bytes: 2500, max_response_bytes: 8192};
  settings.response_policy = {content_only: true, max_response_bytes: 8192};
  settings.tool_routes['public-source'].allowed_urls = [source.href];
  await writeFile(configPath, JSON.stringify(config), {mode: 0o600});
  stage = 'connect';
  const health = await start(); assert.equal(health.version, '0.13.0a2');
  client = await connectClient(origin + '/mcp', token);
  await call('run_create', {run_id: runId, profile: initialized.profile});
  const compatibility = await probeCompatibility(client, runId);
  assert.deepEqual(compatibility.discovered_tools, methods.slice().sort());
  assert.equal(compatibility.read_public_source_annotated_read_only, true);
  if (paidPack) {
    const catalog = await call('catalog', {run_id: runId});
    assert.equal(catalog.items.length, 1);
    assert.equal(catalog.library.goal_library_installed, false);
    assert.equal(catalog.library.active_pack, null);
  }
  stage = 'live_public_read';
  const input = {run_id: runId, url: source.href, compact: true, request_id: requestId};
  const first = await call('read_public_source', input);
  assert.equal(first.classification, 'live_public_source');
  assert.equal(first.result.status, 200); assert.equal(first.reused_original_receipt, false);
  const retried = await call('read_public_source', input);
  assert.equal(retried.receipt_id, first.receipt_id); assert.equal(retried.reused_original_receipt, true);
  await call('memory_add', {run_id: runId, kind: 'public_demo_review', body: {
    sources: [source.href], observed_at: Date.now() / 1000, body_sha256: first.result.body_sha256,
    verified: 'The public HTTP source was read and its original receipt was saved.',
    next_action: 'Inspect source-bound facts before planning an experiment.',
    sales_outcome_verified: false
  }});
  const beforeStatus = await call('status', {run_id: runId});
  const originalConfigHash = createHash('sha256').update(await readFile(configPath)).digest('hex');
  let installedPack;
  stage = 'process_replacement';
  await client.close(); client = null; await stop();
  if (paidPack) {
    stage = 'commercial_data_pack_install';
    installedPack = JSON.parse(execFileSync(python, ['-m', 'nuluno_engine_community.growth_loop.cli', 'method-pack-install', '--archive', paidPack,
      '--directory', env.NULUNO_METHOD_PACK_DIR, '--sha256', process.env.NULUNO_DEMO_METHOD_PACK_SHA256 ?? ''],
      {env: childEnv, encoding: 'utf8', stdio: ['ignore','pipe','pipe'], timeout: 30000}));
    assert.equal(installedPack.installed, true);
    assert.equal(createHash('sha256').update(await readFile(configPath)).digest('hex'), originalConfigHash);
  }
  await start(); client = await connectClient(origin + '/mcp', token);
  assert.deepEqual(await call('status', {run_id: runId}), beforeStatus);
  if (paidPack) {
    const catalog = await call('catalog', {run_id: runId});
    assert.equal(catalog.total, 186);
    assert.equal(catalog.library.goals, 99);
    assert.equal(catalog.library.channel_skills, 152);
    assert.equal(catalog.library.active_pack.counts.skill_folders, 188);
    const goals = await call('catalog', {run_id: runId, kind: 'goals', limit: 3});
    assert.equal(goals.total, 99); assert.equal(goals.items.length, 3);
    const skill = await call('catalog', {run_id: runId, kind: 'skill', skill_id: 'S01', limit: 1});
    assert.equal(skill.total_steps, 9); assert.equal(skill.steps.length, 1);
  }
  const resumed = await call('read_public_source', input);
  assert.equal(resumed.receipt_id, first.receipt_id); assert.equal(resumed.reused_original_receipt, true);
  const overQuota = await client.callTool({name: 'read_public_source', arguments: {...input, request_id: 'new-read-outside-consumed-quota'}});
  assert.equal(overQuota.isError, true);
  const memory = await call('memory_search', {run_id: runId, kind: 'public_demo_review'});
  assert.equal(memory.items.length, 1); assert.equal(memory.items[0].body.body_sha256, first.result.body_sha256);
  const metrics = await call('execution_metrics', {run_id: runId});
  assert.equal(metrics.public_reads.attempts, 1); assert.equal(metrics.public_reads.original_receipt_reuses, 2);
  const cost = await call('cost_summary', {run_id: runId}); assert.equal(cost.full_cost_minor, null);
  const crossRun = await client.callTool({name: 'status', arguments: {run_id: 'outside-original-scope'}});
  assert.equal(crossRun.isError, true);
  stage = 'revoke'; settings.revoked = true;
  await writeFile(configPath + '.next', JSON.stringify(config), {mode: 0o600}); await rename(configPath + '.next', configPath);
  let denied = false;
  try { denied = (await client.callTool({name: 'read_public_source', arguments: input})).isError === true; } catch { denied = true; }
  assert.equal(denied, true);
  const denial = await fetch(origin + '/mcp', {method: 'POST', headers: {Authorization: 'Bearer ' + token,
    'Content-Type': 'application/json', Accept: 'application/json, text/event-stream'},
    body: JSON.stringify({jsonrpc: '2.0', id: 10, method: 'tools/list', params: {}})});
  assert.equal(denial.status, 401);
  stage = 'native_receipt_verification';
  const persisted = JSON.parse(execFileSync(python, ['-c', `import sqlite3,json,sys
db=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)
rows=db.execute('SELECT state,attempts,response FROM public_read_requests').fetchall()
original=json.loads(rows[0][2]); chain=original['result'].get('redirect_chain',[])
print(json.dumps({'request_rows':len(rows),'attempts':sum(r[1] for r in rows),'states':[r[0] for r in rows], 'body_sha256':original['result']['body_sha256'],'redirect_hops':len(chain),'physical_gets':sum(r[1] for r in rows)+len(chain),'external_operations':db.execute('SELECT count(*) FROM operations').fetchone()[0]}))`, join(workspace, 'workflow.sqlite3')],
    {env: childEnv, encoding: 'utf8', stdio: ['ignore','pipe','pipe'], timeout: 10000}));
  assert.equal(persisted.request_rows, 1); assert.equal(persisted.attempts, 1);
  assert.equal(persisted.body_sha256, first.result.body_sha256); assert.equal(persisted.external_operations, 0);
  receipt = {
    schema_version: 1, observed_at: new Date().toISOString(), engine_version: health.version,
    implementation: Object.fromEntries(await Promise.all(['public-read-demo.mjs', 'compatibility.mjs', 'server.mjs'].map(async name =>
      [name, createHash('sha256').update(await readFile(join(directory, name))).digest('hex')]))),
    classification: 'live_public_read_with_operator_injected_process_restart',
    commercial_library: installedPack ? {...installedPack, scope_configuration_unchanged: true, original_run_preserved: true} : null,
    source: {url: source.href, http_status: first.result.status, body_sha256: first.result.body_sha256,
      receipt_id: first.receipt_id, observed_at_unix: first.observed_at},
    checks: {official_sdk_mcp: true, original_receipt_reused_before_restart: true, process_replaced: true,
      same_run_and_memory_preserved: true, original_receipt_reused_after_restart: true,
      exact_source_quota_preserved: true, cross_run_denied: true, original_key_revoked: true,
      revoked_key_http_status: denial.status, original_key_replaced: false},
    native_sqlite_verification: persisted, public_read_metrics: metrics.public_reads,
    first_public_source_seconds: metrics.first_public_source_seconds,
    costs: {model_invoice: null, hosting: null, full_cost_minor: cost.full_cost_minor, unknown_is_zero: false},
    compatibility,
    limits: {model_calls: 0, platform_writes: 0, private_inbox_access: false, sales_outcome_verified: false,
      self_created_method_verified: false, all_99_goals_live_accepted: false,
      natural_failure_recovery_rate_measured: false, complete_human_intervention_count: null}
  };
} catch {
  process.stderr.write('Public demo failed at ' + stage + '. No successful receipt was issued; no automatic re-run was performed.\n');
  process.exitCode = 1;
} finally {
  await client?.close().catch(() => {}); await stop(); await rm(privateRoot, {recursive: true, force: true});
}
if (receipt) {
  receipt.private_temporary_state_removed = true;
  const body = JSON.stringify(receipt, null, 2) + '\n';
  // Receipt fields above are an explicit allowlist; never serialize config, env, raw errors or local paths.
  assert.equal(body.includes(token), false); assert.equal(body.includes(privateRoot), false);
  if (output) await writeFile(output, body, {flag: 'wx'});
  process.stdout.write(body);
}
}
main().catch(() => {
  process.stderr.write('Public demo could not initialize or save its output. Check the arguments, runtime and unused output filename. No successful receipt was issued.\n');
  process.exitCode = 1;
});
