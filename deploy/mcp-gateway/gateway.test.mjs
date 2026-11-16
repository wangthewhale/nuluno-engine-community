import {test} from 'node:test';
import {execFileSync} from 'node:child_process';
import {createServer as createPortReservation} from 'node:net';
import {randomBytes,createHash} from 'node:crypto';
import {scopedHostContract} from './contract-view.mjs';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm,mkdir} from 'node:fs/promises';
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

test('scoped contract helper respects tiny budgets, empty authority and standard schema discovery without serializing secrets',()=>{
 const schema={type:'object',properties:{run_id:{type:'string'}},required:['run_id']};
 const fixture={schema:'growth-host-contract-v1',version:'fixture',engine_version:'fixture',product:'Nuluno Engine',model_neutral:true,capabilities:['web.read','private.write'],methods:{contract:schema,cost_summary:schema,private_action:schema},unrecognizedRootSecret:'FIXTURE_NATIVE_SECRET',work_cycle:['fixture documentation '.repeat(2000)]};
 for(const content_only of [true,false])for(const max_response_bytes of [2048,3000,5000,16000,65536]){
  const settings={methods:['contract','cost_summary'],capabilities:['web.read'],privateSecret:'FIXTURE_OPERATOR_SECRET',response_policy:{content_only,max_response_bytes}};
  const before=JSON.stringify({fixture,settings});const view=scopedHostContract(fixture,settings,'fixture-public-contract');
  const result={content:[{type:'text',text:JSON.stringify(view)}],isError:false,...(content_only?{}:{structuredContent:view})};
  assert.ok(Buffer.byteLength(JSON.stringify(result))<=max_response_bytes-256);
  assert.equal(view.contract_view.method_count,2);assert.equal(view.worker_scope.allow_evolution,false);assert.equal(view.schema_discovery.protocol_method,'tools/list');
  assert.equal(view.schema_discovery.only_authorized_tools,true);assert.ok(!JSON.stringify(view).includes('FIXTURE_OPERATOR_SECRET'));assert.ok(!JSON.stringify(view).includes('FIXTURE_NATIVE_SECRET'));
  assert.ok(view.capabilities.every(cap=>cap==='web.read'));assert.equal(view.capabilities.length+(view.contract_view.capability_names_omitted??0),1);assert.ok(view.next_actions.every(x=>x.tool==='cost_summary'));assert.equal(JSON.stringify({fixture,settings}),before);
 }
 const empty=scopedHostContract(fixture,{methods:[],capabilities:[],response_policy:{content_only:true,max_response_bytes:2048}},'fixture-public-contract');
 assert.deepEqual(empty.methods,{});assert.deepEqual(empty.capabilities,[]);assert.deepEqual(empty.next_actions,[]);
 assert.throws(()=>scopedHostContract(fixture,{methods:'contract'},'fixture'),/invalid_method_scope_policy/);
 assert.throws(()=>scopedHostContract(fixture,{methods:[],response_policy:{max_response_bytes:1024}},'fixture'),/invalid_response_byte_policy/);
});

test('actual installed public namespace contract and suggested costs use official SDK under the original byte cap with no outbound or ledger changes',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'nuluno-public-contract-')),config=join(dir,'service.json'),workspace=join(dir,'workspace'),guard=join(dir,'network-guard'),blocked=join(dir,'unexpected-network');
 let server,client;
 try{
 await mkdir(guard);await writeFile(join(guard,'sitecustomize.py'),`import sys\nfrom pathlib import Path\ndef audit(event,args):\n if event in ('socket.connect','socket.getaddrinfo','socket.sendto'):\n  Path(${JSON.stringify(blocked)}).write_text('blocked')\n  raise RuntimeError('fixture_external_network_forbidden')\nsys.addaudithook(audit)\n`);
 const token=randomBytes(32).toString('base64url'),env={...process.env,PYTHONPATH:guard,NULUNO_METHOD_PACK_DIR:'',ENGINE_PUBLIC_CONTRACT_TEST_KEY:token};
 const invoke=(code,args=[])=>JSON.parse(execFileSync(PYTHON,['-c',code,...args],{env,cwd:dir,encoding:'utf8',stdio:['ignore','pipe','pipe']}));
 const initial=invoke(`import json,sys\nfrom pathlib import Path\nimport nuluno_engine_community.growth_loop as kernel\nfrom nuluno_engine_community.growth_loop.standalone import initialize_service\nfrom nuluno_engine_community.growth_loop.onboarding import host_contract\nfrom nuluno_engine_community.growth_loop.store import Store\nfrom nuluno_engine_community.growth_loop.service import Service\nr=Path(sys.argv[1]);c=r/'service.json';w=r/'workspace'\ncreated=initialize_service(w,c,'https://public-contract.example','public-contract-fixture',worker='public-contract-worker',token_env='ENGINE_PUBLIC_CONTRACT_TEST_KEY')\nv=json.loads(c.read_text());s=v['workers']['public-contract-worker'];s.update(methods=['contract','cost_summary'],capabilities=['web.read','local.plan'],allow_run_create=False,tool_routes={},response_policy={'content_only':True,'max_response_bytes':16000})\nc.write_text(json.dumps(v));c.chmod(0o600)\nstore=Store(w)\ntry: Service(store,'public-contract-worker',{**s,'methods':['run_create'],'allow_run_create':True}).run('run_create',{'run_id':'public-contract-fixture','profile':created['profile']})\nfinally: store.close()\nprint(json.dumps({'namespace':kernel.__name__,'version':kernel.__version__,'native':host_contract()}))`,[dir]);
 assert.equal(initial.namespace,'nuluno_engine_community.growth_loop');
 const readLedger=()=>invoke(`import json,sys,sqlite3,hashlib\nfrom pathlib import Path\nr=Path(sys.argv[1]);d=sqlite3.connect('file:'+str(r/'workspace/workflow.sqlite3')+'?mode=ro',uri=True)\ntables={row[0] for row in d.execute("SELECT name FROM sqlite_master WHERE type='table'")};v={}\nfor table in ['runs','tasks','artifacts','strategy_memory','public_read_requests']:\n if table in tables:\n  rows=d.execute('SELECT * FROM '+table).fetchall();v[table]={'rows':len(rows),'sha256':hashlib.sha256(json.dumps(sorted(rows,key=repr),ensure_ascii=False,default=str).encode()).hexdigest()}\nd.close();print(json.dumps(v))`,[dir]);
 const before=readLedger(),configBefore=createHash('sha256').update(await readFile(config)).digest('hex');
 const reservation=createPortReservation();await new Promise(r=>reservation.listen(0,'127.0.0.1',r));const port=reservation.address().port;await new Promise(r=>reservation.close(r));
 const origin='http://127.0.0.1:'+port;server=await createEngineServer({python:PYTHON,workspace,config,origin,env});await new Promise(r=>server.listen(port,'127.0.0.1',r));
  client=new Client({name:'public-contract-installed-kernel-check',version:'1'});await client.connect(new StreamableHTTPClientTransport(new URL(origin+'/mcp'),{requestInit:{headers:{Authorization:'Bearer '+token},redirect:'error'},fetch:async(input,init)=>{const u=new URL(input instanceof Request?input.url:input);assert.equal(u.origin,origin);return fetch(input,init);}}));
  const names=(await client.listTools()).tools.map(x=>x.name).sort();assert.deepEqual(names,['contract','cost_summary']);
  const response=await client.callTool({name:'contract',arguments:{run_id:'public-contract-fixture'}});assert.equal(response.isError,false);
  const view=JSON.parse(response.content.find(x=>x.type==='text').text);assert.equal(view.engine_version,initial.version);assert.equal(view.schema,initial.native.schema);assert.deepEqual(Object.keys(view.methods).sort(),names);assert.deepEqual(view.methods.contract,initial.native.methods.contract);assert.deepEqual(view.capabilities,['web.read']);assert.deepEqual(view.library_inventory.installed_work_skill_ids,['S01']);assert.equal(view.library_inventory.channel_catalog_installed,false);
  assert.ok(Buffer.byteLength(JSON.stringify(response))<=16000-256);assert.equal(view.next_actions[0].tool,'cost_summary');
  const next=await client.callTool({name:view.next_actions[0].tool,arguments:view.next_actions[0].arguments});assert.equal(next.isError,false);
  const costs=JSON.parse(next.content.find(x=>x.type==='text').text);assert.equal(costs.full_cost_minor,null);assert.equal(costs.unknown_fields_are_not_zero,true);
  assert.deepEqual(readLedger(),before);assert.equal(createHash('sha256').update(await readFile(config)).digest('hex'),configBefore);assert.equal(existsSync(blocked),false);
  const receipt={schema:'nuluno-public-namespace-contract-sdk-acceptance-v1',observedAt:new Date().toISOString(),fixtureOnly:true,existingOperatorStateTouched:false,pythonNamespace:initial.namespace,engineVersion:initial.version,officialSdk:true,nativeContractMethods:Object.keys(initial.native.methods).length,grantedMethods:names,grantedCapabilities:view.capabilities,responseCap:16000,contractToolResultBytes:Buffer.byteLength(JSON.stringify(response)),costToolResultBytes:Buffer.byteLength(JSON.stringify(next)),actualNextAction:'cost_summary',fullCost:null,sourceRequests:0,outboundRequests:0,pythonOutboundBlocked:true,ledgerAndConfigUnchanged:true,status:'passed'};
  if(process.env.ENGINE_CONTRACT_ACCEPTANCE_OUT)await writeFile(process.env.ENGINE_CONTRACT_ACCEPTANCE_OUT,JSON.stringify(receipt,null,2)+'\n',{mode:0o600});
 }finally{await client?.close();if(server){server.closeAllConnections();if(server.listening)await new Promise(r=>server.close(r));}await rm(dir,{recursive:true,force:true});}
});
