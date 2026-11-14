/** Reproducible, read-only protocol probe. Never accepts a key on the command line. */
import {Client, StreamableHTTPClientTransport} from '@modelcontextprotocol/client';
import {readFile, writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';

export function validateEndpoint(value) {
  const url = new URL(value);
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/mcp') throw new Error('invalid_mcp_endpoint');
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname))) throw new Error('https_required');
  return url;
}
export function unwrap(result) {
  if (result.isError) throw new Error('engine_tool_rejected');
  return result.structuredContent ?? JSON.parse(result.content.find(part => part.type === 'text').text);
}
export async function connectClient(url, token) {
  if (!/^[A-Za-z0-9_-]{32,256}$/.test(token ?? '')) throw new Error('scoped_bearer_required');
  const client = new Client({name: 'nuluno-engine-public-compatibility', version: '1.0.0'});
  try {
    await client.connect(new StreamableHTTPClientTransport(validateEndpoint(String(url)), {
      requestInit: {headers: {Authorization: 'Bearer ' + token}}
    }));
    return client;
  } catch { await client.close().catch(() => {}); throw new Error('mcp_connection_failed'); }
}
export async function probeCompatibility(client, runId) {
  const tools = (await client.listTools()).tools;
  const names = tools.map(tool => tool.name).sort();
  if (!names.includes('status')) throw new Error('status_tool_not_exposed');
  const status = unwrap(await client.callTool({name: 'status', arguments: {run_id: runId}}));
  const packageJson = JSON.parse(await readFile(new URL('./package.json', import.meta.url), 'utf8'));
  return {
    schema_version: 1,
    observed_at: new Date().toISOString(),
    transport: 'MCP Streamable HTTP',
    tested_client: '@modelcontextprotocol/client',
    tested_client_version: packageJson.dependencies['@modelcontextprotocol/client'],
    node_version: process.versions.node,
    engine_gateway_version: packageJson.version,
    discovered_tools: names,
    read_public_source_annotated_read_only: tools.find(tool => tool.name === 'read_public_source')?.annotations?.readOnlyHint ?? null,
    status_read_succeeded: typeof status === 'object' && status !== null,
    nuluno_com_cookie_used: false,
    model_calls: 0,
    external_source_reads_by_probe: 0,
    other_clients_tested: [],
    all_agents_compatible_claimed: false
  };
}
async function main() {
  const args = process.argv.slice(2);
  if (args.length > 2 || (args.length && args[0] !== '--output')) throw new Error('usage_output_path_only');
  const client = await connectClient(process.env.ENGINE_MCP_URL, process.env.ENGINE_EXTERNAL_AGENT_TOKEN);
  let receipt;
  try { receipt = await probeCompatibility(client, process.env.ENGINE_RUN_ID); }
  finally { await client.close(); }
  if (args[1]) await writeFile(args[1], JSON.stringify(receipt, null, 2) + '\n', {flag: 'wx'});
  process.stdout.write(JSON.stringify(receipt, null, 2) + '\n');
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(() => { process.stderr.write('Compatibility probe failed. Check the endpoint, existing run and original scoped key. No successful receipt was issued.\n'); process.exitCode = 1; });
}
