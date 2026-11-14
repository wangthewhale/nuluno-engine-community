import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {existsSync} from 'node:fs';
import {randomBytes} from 'node:crypto';
import {createEngineServer} from './server.mjs';
import {validateEndpoint, connectClient, probeCompatibility, unwrap} from './compatibility.mjs';

test('compatibility probe refuses endpoint credential/query leakage and insecure remote HTTP', () => {
  for (const url of ['https://secret@example.com/mcp', 'https://example.com/mcp?key=secret',
    'https://example.com/mcp#secret', 'http://example.com/mcp', 'https://example.com/not-mcp']) {
    assert.throws(() => validateEndpoint(url));
  }
  assert.equal(validateEndpoint('https://example.com/mcp').pathname, '/mcp');
  assert.equal(validateEndpoint('http://127.0.0.1:8788/mcp').hostname, '127.0.0.1');
});

test('real scoped MCP compatibility receipt needs no public-source call and contains no credential/path', async () => {
  const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
  const privateRoot = await mkdtemp(join(tmpdir(), 'nuluno-compat-test-'));
  const token = randomBytes(32).toString('base64url'), config = join(privateRoot, 'service.json');
  const runId = 'compat-original';
  const settings = {token_env: 'COMPAT_TEST_KEY', runs: [runId], brand_urls: ['https://product.example'],
    allow_run_create: true, capabilities: ['web.read'], methods: ['run_create', 'status', 'read_public_source']};
  await writeFile(config, JSON.stringify({workers: {external: settings}}), {mode: 0o600});
  // Resolve an available loopback port before creating the fixed-origin handler.
  const {createServer} = await import('node:net');
  const reservation = createServer(); await new Promise(done => reservation.listen(0, '127.0.0.1', done));
  const port = reservation.address().port; await new Promise(done => reservation.close(done));
  const origin = 'http://127.0.0.1:' + port;
  const python=process.env.ENGINE_PYTHON??(existsSync(join(root,'.venv/bin/python'))?join(root,'.venv/bin/python'):'python3');
  const server = await createEngineServer({python, config,
    workspace: join(privateRoot, 'state'), origin,
    env: {...process.env, COMPAT_TEST_KEY: token}});
  await new Promise(done => server.listen(port, '127.0.0.1', done));
  let client;
  try {
    client = await connectClient(origin + '/mcp', token);
    unwrap(await client.callTool({name: 'run_create', arguments: {run_id: runId,
      profile: {mode: 'prepare', brand: {name: 'Controlled fixture', url: 'https://product.example', facts: {}},
        workflow: {id: 'single', skill_id: 'S01', max_iterations: 1}, budget: {daily_minor: 0, total_minor: 0}}}}));
    const receipt = await probeCompatibility(client, runId);
    assert.equal(receipt.status_read_succeeded, true);
    assert.equal(receipt.read_public_source_annotated_read_only, true);
    assert.equal(receipt.external_source_reads_by_probe, 0);
    assert.equal(receipt.all_agents_compatible_claimed, false);
    const text = JSON.stringify(receipt); assert.equal(text.includes(token), false); assert.equal(text.includes(privateRoot), false);
    await assert.rejects(() => probeCompatibility(client, 'outside-original-scope'));
  } finally {
    await client?.close(); await new Promise(done => server.close(done)); await rm(privateRoot, {recursive: true, force: true});
  }
});
