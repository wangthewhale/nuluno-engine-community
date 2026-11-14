import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {request} from 'node:http';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {existsSync} from 'node:fs';
import {Client,StreamableHTTPClientTransport} from '@modelcontextprotocol/client';
import {authenticate,createEngineServer} from './server.mjs';
const ROOT=resolve(fileURLToPath(new URL('../..',import.meta.url)));
const PYTHON=process.env.ENGINE_PYTHON??(existsSync(join(ROOT,'.venv/bin/python'))?join(ROOT,'.venv/bin/python'):'python3');

test('official SDK remote MCP preserves worker scope, saved state, and revocation',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'nuluno-engine-gateway-')),config=join(dir,'service.json'),token='a'.repeat(43);
 const settings={token_env:'ENGINE_TEST_BEARER',runs:['external-original'],brand_urls:['https://product.example'],allow_run_create:true,capabilities:['web.read'],methods:['run_create','status','resume','catalog']};
 const policy={workers:{external:settings}};await writeFile(config,JSON.stringify(policy),{mode:0o600});
 const options={python:PYTHON,workspace:join(dir,'state'),config,origin:'http://127.0.0.1:18789',env:{...process.env,ENGINE_TEST_BEARER:token}};
 let server=await createEngineServer(options),client;
 await new Promise(r=>server.listen(18789,'127.0.0.1',r));
 try{
  assert.equal((await fetch(options.origin+'/mcp',{method:'POST',headers:{'content-type':'application/json'},body:'{}'})).status,401);
  assert.equal((await fetch(options.origin+'/healthz',{headers:{Origin:'https://evil.example'}})).status,403);
  const invalidHost=await new Promise((done,reject)=>{const req=request(options.origin+'/healthz',{headers:{Host:'evil.example'}},res=>{res.resume();done(res.statusCode);});req.on('error',reject);req.end();});
  assert.equal(invalidHost,403);
  client=new Client({name:'external-sdk-client',version:'1.0.0'});
  await client.connect(new StreamableHTTPClientTransport(new URL(options.origin+'/mcp'),{requestInit:{headers:{Authorization:'Bearer '+token}}}));
  const names=(await client.listTools()).tools.map(t=>t.name);assert.deepEqual(names.sort(),settings.methods.slice().sort());
  const profile={mode:'prepare',brand:{name:'Product',url:'https://product.example',facts:{}},workflow:{id:'single',skill_id:'S01',max_iterations:1},budget:{daily_minor:0,total_minor:0}};
  const created=await client.callTool({name:'run_create',arguments:{run_id:'external-original',profile}});assert.equal(created.isError,false);
  const denied=await client.callTool({name:'status',arguments:{run_id:'other-run'}});assert.equal(denied.isError,true);
  const status=await client.callTool({name:'status',arguments:{run_id:'external-original'}});assert.equal(status.isError,false);
  await client.close();await new Promise(r=>server.close(r));
  server=await createEngineServer(options);await new Promise(r=>server.listen(18789,'127.0.0.1',r));
  client=new Client({name:'reconnected-external-client',version:'1.0.0'});await client.connect(new StreamableHTTPClientTransport(new URL(options.origin+'/mcp'),{requestInit:{headers:{Authorization:'Bearer '+token}}}));
  const restored=await client.callTool({name:'status',arguments:{run_id:'external-original'}});assert.deepEqual(restored.structuredContent,status.structuredContent);
  settings.revoked=true;await writeFile(config,JSON.stringify(policy));
  await assert.rejects(()=>client.listTools());
  await assert.rejects(()=>authenticate(config,'Bearer '+token,options.env));
 }finally{await client?.close();await new Promise(r=>server.close(r));await rm(dir,{recursive:true,force:true});}
});
